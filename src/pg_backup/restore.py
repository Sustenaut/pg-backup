"""Restoring from the WAL archive into a scratch namespace, deleting one, and putting one back.

A restore never touches the live namespace. It makes <namespace>-restore-<when it ran>, which a
network policy closes to all traffic, copies the WAL Secret into it, and starts the image the
live StatefulSet runs. There it fetches the newest base backup made before the moment, replays
the archived WAL up to it, and promotes. Then it compares the restore with the live database by
the check query, and prints how long each step took. It leaves the namespace for a look.

Promote replaces the live database with a restore's, after a typed confirmation at a terminal:
the dump job first, then the app stopped, then pg_dump | pg_restore --clean in one transaction,
so a failure leaves live as it was, and the app started again however it ends.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
import time
from datetime import datetime, timezone
from typing import Callable

from .config import Config
from .server import Server, ServerError


class Stop(Exception):
    """Ends a command with an exit code and a reason: 1 failed, 2 can't run as asked,
    3 no base backup made before the moment."""

    def __init__(self, code: int, reason: str):
        super().__init__(reason)
        self.code = code


SOCKET = "/var/run/postgresql"

# Not wal-fetch: in WAL-G v3.0.9 on Cloud Storage it looks for a segment as .lz4 first, takes
# the 404 for an error and gives up, so it can't fetch anything. st cat reads the one name the
# archive uses (.br, the image's WALG_COMPRESSION_METHOD), and fails for a name that isn't
# there, which is how recovery learns it has reached the end. The .part keeps a failed fetch
# from leaving half a segment where PostgreSQL would read it.
RESTORE_COMMAND = "wal-g st cat wal_005/%f.br --decrypt --decompress > %p.part && mv %p.part %p"


def parse_target(text: str) -> datetime | None:
    """A moment with its zone, as PostgreSQL's recovery_target_time takes it; None for latest."""
    if text == "latest":
        return None
    normal = re.sub(r"([+-]\d\d)$", r"\1:00", text.strip().replace("Z", "+00:00"))
    try:
        moment = datetime.fromisoformat(normal)
    except ValueError:
        raise Stop(2, f"can't read {text!r} as a time: give it with its zone, as 2026-11-05 14:32:00+00") from None
    if moment.tzinfo is None:
        raise Stop(2, f"{text!r} has no zone: add one, as +00")
    return moment.astimezone(timezone.utc)


def pg_time(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S+00")


def stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")


def backup_time(text: str) -> datetime:
    """WAL-G's time for a backup, as 2026-11-06T06:14:28.153025Z."""
    return datetime.fromisoformat(re.sub(r"\.\d+", "", text).replace("Z", "+00:00"))


def choose_backup(backups: list[dict], target: datetime | None) -> str | None:
    """The newest base backup made at or before the moment, or the newest of all for latest."""
    fit = [entry for entry in backups if target is None or backup_time(entry["time"]) <= target]
    return max(fit, key=lambda entry: backup_time(entry["time"]))["backup_name"] if fit else None


def oldest_backup(backups: list[dict]) -> str | None:
    return min((entry["time"] for entry in backups), default=None)


def scratch_spec(config: Config, namespace: str, image: str) -> dict:
    """The closed network policy and the restore pod, as one List for kubectl apply."""
    wal = config.wal
    env = [
        {"name": "PGDATA", "value": "/restore/pgdata"},
        {"name": "WALG_GS_PREFIX", "value": f"gs://{wal['bucket']}/{wal['prefix']}"},
        {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/var/run/secrets/wal/gcs-key.json"},
        {"name": "WALG_LIBSODIUM_KEY", "valueFrom": {"secretKeyRef": {"name": wal["secret"], "key": "libsodium-key"}}},
        {"name": "WALG_LIBSODIUM_KEY_TRANSFORM", "value": "base64"},
    ]
    pod = {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": "restore", "namespace": namespace},
        "spec": {
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "securityContext": {"runAsUser": 999, "runAsGroup": 999, "fsGroup": 999},
            "containers": [{
                "name": "postgres", "image": image, "command": ["sleep", "86400"], "env": env,
                "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"memory": "512Mi"}},
                "volumeMounts": [{"name": "restore", "mountPath": "/restore"},
                                 {"name": "wal-key", "mountPath": "/var/run/secrets/wal", "readOnly": True}],
            }],
            "volumes": [{"name": "restore", "emptyDir": {}},
                        {"name": "wal-key", "secret": {"secretName": wal["secret"],
                                                       "items": [{"key": "gcs-key.json", "path": "gcs-key.json"}]}}],
        },
    }
    policy = {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy",
        "metadata": {"name": "closed", "namespace": namespace},
        "spec": {"podSelector": {}, "policyTypes": ["Ingress"]},
    }
    return {"apiVersion": "v1", "kind": "List", "items": [policy, pod]}


def copied_secret(secret: dict, namespace: str) -> dict:
    return {key: secret[key] for key in ("apiVersion", "kind", "type", "data")} | {
        "metadata": {"name": secret["metadata"]["name"], "namespace": namespace}}


class Database:
    """The check query, run in the live database or in a scratch restore."""

    def __init__(self, config: Config, server: Server):
        self.config = config
        self.server = server

    def live(self, query: str) -> str:
        c = self.config
        return self.server.kubectl("-n", c.namespace, "exec", c.pod, "-c", c.container, "--",
                                   "psql", "-U", c.user, "-d", c.database, "-Atc", query).strip()

    def scratch(self, namespace: str, query: str) -> str:
        c = self.config
        return self.server.kubectl("-n", namespace, "exec", "restore", "--",
                                   "psql", "-h", SOCKET, "-U", c.user, "-d", c.database, "-Atc", query).strip()

    def missing(self, rows: str) -> list[str]:
        names = {line.split(" ", 1)[0] for line in rows.splitlines() if line}
        return [name for name in self.config.check["required"] if name not in names]


def side_by_side(restored: str, live: str) -> list[str]:
    left, right = restored.splitlines(), live.splitlines()
    width = max(len(left), len(right))
    left += [""] * (width - len(left))
    right += [""] * (width - len(right))
    return [f"   {a}|{b}" for a, b in zip(left, right)]


def restore(config: Config, server: Server, target_text: str, say: Callable[[str], None] = print,
            clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> str:
    """Restores into a new scratch namespace and returns its name. A failure after the
    namespace exists carries its name as .namespace, so the drill can delete it."""
    namespace = f"{config.namespace}-restore-{stamp()}"
    created = False

    def made() -> None:
        nonlocal created
        created = True

    try:
        return _restore(config, server, target_text, namespace, made, say, clock, sleep)
    except (Stop, ServerError) as error:
        error.namespace = namespace if created else None
        raise


def _restore(config: Config, server: Server, target_text: str, namespace: str, made: Callable[[], None],
             say: Callable[[str], None], clock: Callable[[], float], sleep: Callable[[float], None]) -> str:
    target = parse_target(target_text)
    shown = "latest" if target is None else pg_time(target)
    jsonpath = f'{{.spec.template.spec.containers[?(@.name=="{config.container}")].image}}'
    image = server.kubectl("-n", config.namespace, "get", "statefulset", config.statefulset, "-o", f"jsonpath={jsonpath}").strip()
    if not image:
        raise Stop(1, f"no container {config.container} in statefulset {config.statefulset} (namespace {config.namespace})")
    start = clock()
    say(f"== Restoring to {shown} into {namespace}")

    def took(what: str, since: float) -> None:
        say(f"   {what}: {round(clock() - since)} s")

    step = clock()
    server.kubectl("create", "namespace", namespace)
    made()
    secret = json.loads(server.kubectl("-n", config.namespace, "get", "secret", config.wal["secret"], "-o", "json"))
    server.kubectl("apply", "-f", "-", input=json.dumps(copied_secret(secret, namespace)))
    server.kubectl("apply", "-f", "-", input=json.dumps(scratch_spec(config, namespace, image)))
    server.kubectl("-n", namespace, "wait", "--for=condition=Ready", "pod/restore", "--timeout=5m")
    took("namespace and pod", step)

    def in_pod(command: str, check: bool = True) -> str:
        return server.kubectl("-n", namespace, "exec", "restore", "--", "sh", "-c", command, check=check)

    if not in_pod("command -v wal-g", check=False).strip():
        raise Stop(1, f"the image {image} has no wal-g: is it pg-backup's image?")

    step = clock()
    listed = in_pod("wal-g backup-list --json 2>/dev/null", check=False).strip()
    backups = json.loads(listed) if listed.startswith("[") else []
    backup = choose_backup(backups, target)
    if backup is None:
        # 3, not 1: nothing is broken, the archive just doesn't reach back that far.
        oldest = oldest_backup(backups)
        stop = Stop(3, f"no base backup made before {shown}; the oldest is {oldest or 'not there yet'} (the archive keeps 14 days)")
        stop.oldest = oldest
        raise stop
    say(f"   base backup {backup}")
    in_pod(f'wal-g backup-fetch "$PGDATA" {shlex.quote(backup)} >/dev/null')
    took("base backup fetched", step)

    step = clock()
    settings = [f"restore_command = '{RESTORE_COMMAND}'", "recovery_target_action = 'promote'", "archive_mode = 'off'"]
    if target is not None:
        settings.append(f"recovery_target_time = '{shown}'")
    lines = shlex.quote("\n".join(settings) + "\n")
    in_pod(f'printf %s {lines} >> "$PGDATA/postgresql.auto.conf" && touch "$PGDATA/recovery.signal" && '
           f'chmod 0700 "$PGDATA" && pg_ctl -D "$PGDATA" -l /restore/postgres.log -w -t 1800 start >/dev/null')
    deadline = clock() + 1800
    db = Database(config, server)
    while True:
        try:
            if db.scratch(namespace, "select pg_is_in_recovery()") == "f":
                break
        except ServerError:
            pass
        if clock() > deadline:
            raise Stop(1, f"the restore in {namespace} didn't finish recovery in 30 minutes: see /restore/postgres.log there")
        sleep(2)
    took("WAL replayed, promoted", step)

    query = config.check["query"]
    restored = db.scratch(namespace, query)
    live = db.live(query)
    say("== Restored | live now")
    for line in side_by_side(restored, live):
        say(line)
    missing = db.missing(restored)
    if missing:
        raise Stop(1, f"the restore has no {', '.join(missing)}")
    took("in all", start)
    say(f"Look with: ssh root@{config.server} k3s kubectl -n {namespace} exec -it restore -- psql -h {SOCKET} -U {config.user} -d {config.database}")
    say(f"Then: ./backups/pg-backup clean {namespace}, or to put it back: ./backups/pg-backup promote {namespace}")
    return namespace


def clean(config: Config, server: Server, namespace: str) -> None:
    if not config.is_scratch(namespace):
        raise Stop(2, f"{namespace} isn't a scratch restore ({config.namespace}-restore-<14 digits>)")
    server.kubectl("delete", "namespace", namespace, "--wait=false")


def promote(config: Config, server: Server, namespace: str, say: Callable[[str], None] = print,
            ask: Callable[[str], str] = input, interactive: Callable[[], bool] = sys.stdin.isatty) -> None:
    if not config.is_scratch(namespace):
        raise Stop(2, f"{namespace} isn't a scratch restore ({config.namespace}-restore-<14 digits>)")
    db = Database(config, server)
    try:
        promoted = db.scratch(namespace, "select pg_is_in_recovery()") == "f"
    except ServerError:
        promoted = False
    if not promoted:
        raise Stop(1, f"{namespace} has no promoted restore running (see ./backups/pg-backup restore)")
    query = config.check["query"]
    restored = db.scratch(namespace, query)
    live = db.live(query)
    missing = db.missing(restored)
    if missing:
        raise Stop(1, f"the restore has no {', '.join(missing)}: not promoting it")
    target = server.kubectl("-n", namespace, "exec", "restore", "--", "sh", "-c",
                            "sed -n \"s/^recovery_target_time = //p\" \"$PGDATA/postgresql.auto.conf\"").strip().strip("'")
    say(f"== Promote {namespace}, restored to {target or 'the latest change'}, over the live database")
    say("   restore | live now")
    for line in side_by_side(restored, live):
        say(line)
    say(f"   {config.app['deployment']} stops for this, and everything since that moment is replaced.")
    if not interactive():
        raise Stop(2, "promote asks for a typed confirmation: run it at a terminal")
    if ask(f"   Type 'promote {namespace}' to go on: ").strip() != f"promote {namespace}":
        raise Stop(1, "not promoted: nothing changed")

    c = config
    job = f"pre-promote-{stamp()}"
    say(f"== The live database to gs://{c.dump['bucket']} first (job {job})")
    server.kubectl("-n", c.namespace, "create", "job", job, f"--from=cronjob/{c.dump['cronjob']}")
    if not server.ok("-n", c.namespace, "wait", "--for=condition=complete", f"job/{job}", "--timeout=10m"):
        raise Stop(1, f"the dump job {job} didn't complete: not promoting (k3s kubectl -n {c.namespace} logs job/{job})")

    deployment = c.app["deployment"]
    replicas = server.kubectl("-n", c.namespace, "get", "deployment", deployment, "-o", "jsonpath={.spec.replicas}").strip() or "1"
    say(f"== Stopping {deployment}")
    try:
        server.kubectl("-n", c.namespace, "scale", "deployment", deployment, "--replicas=0")
        server.kubectl("-n", c.namespace, "wait", "--for=delete", "pod", "-l", c.app["selector"], "--timeout=5m")
        say("== Replacing the live database with the restore's")
        dump = shlex.join(["k3s", "kubectl", "-n", namespace, "exec", "restore", "--",
                           "pg_dump", "-h", SOCKET, "-U", c.user, "-d", c.database, "-Fc"])
        load = shlex.join(["k3s", "kubectl", "-n", c.namespace, "exec", "-i", c.pod, "-c", c.container, "--",
                           "pg_restore", "-U", c.user, "-d", c.database, "--clean", "--if-exists", "--no-owner",
                           "--single-transaction", "--exit-on-error"])
        try:
            # One transaction: on any error pg_restore stops, it rolls back, and live is as it was.
            server.run(f"set -o pipefail; {dump} | {load}")
        except ServerError as error:
            raise Stop(1, f"pg_restore failed and rolled back: the live database is unchanged\n{error.output}") from None
        after = db.live(query)
        if after != restored:
            raise Stop(1, "after the promote, live doesn't match the restore:\n" + "\n".join(side_by_side(restored, after)))
        say("   live now matches the restore")
    finally:
        say(f"== Starting {deployment}")
        server.kubectl("-n", c.namespace, "scale", "deployment", deployment, f"--replicas={replicas}")
        server.kubectl("-n", c.namespace, "rollout", "status", f"deployment/{deployment}", "--timeout=10m", check=False)
    say(f"Promoted. The database as it was before is the newest dump in gs://{c.dump['bucket']} (job {job}).")
    say(f"When it looks right: ./backups/pg-backup clean {namespace}")
