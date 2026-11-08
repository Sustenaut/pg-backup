"""pg.database: a need type for pubkit's deploy walk, offered under the entry point group
`sustenaut.needs`. It checks that a database pg-backup looks after is sound (its settings, its
StatefulSet, the wal-g sidecar's last status lines, and the WAL Secret), and fulfils it by making
the archive's keys, as setup-secrets does, through the walk's Bitwarden writer and kubectl.

It has what pubkit's NeedType has by duck typing and imports nothing from pubkit, so pg-backup
needs no pubkit to install. Every failure is a Result, never an exception. No value of a key
reaches a Result, a log or a command line: the WAL Secret goes to kubectl on its input."""

from __future__ import annotations

import base64
import inspect
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .config import ConfigError, load
from .restore import Stop
from .setup import Keys, make_keys, secret_manifest

STATES = ("ready", "missing", "drift", "blocked", "unknown")
SUMMARY = 120  # pubkit's longest summary, in characters
WORST = ("missing", "drift", "blocked", "unknown", "ready")  # what a need's state is, of its parts'
ARCHIVE_LAG = timedelta(minutes=10)
BASE_BACKUP_AGE = timedelta(hours=26)
TAIL = 50
SECRET_KEYS = ("gcs-key.json", "libsodium-key")
DRILL_FIX = "run ./backups/pg-backup drill --wait and record it"
NO_WRITER = "needs a Bitwarden write token: run the walk at a terminal"


@dataclass
class Result:
    """pubkit's Result, field for field, for a walk whose Context comes from a module without one."""
    state: str
    summary: str
    fix: str = ""
    details: list[str] = field(default_factory=list)
    checked_at: str = ""
    seconds: float = 0.0

    def __post_init__(self) -> None:
        line = " ".join(self.summary.split())
        self.summary = line if len(line) <= SUMMARY else line[:SUMMARY - 1] + "…"


def result(ctx: Any, state: str, summary: str, fix: str = "", details: list[str] | None = None) -> Any:
    """A Result of the class the walk expects. pubkit 0.31.1's walk keeps a check's answer only if
    it is an instance of its own Result, which sits in the module its Context comes from: that
    class, found there without importing pubkit, or this module's own."""
    kind = getattr(sys.modules.get(type(ctx).__module__), "Result", None)
    if not isinstance(kind, type) or kind.__name__ != "Result":
        kind = Result
    return kind(state, summary, fix, list(details or []))


def worst(parts: list[tuple[str, str, str]]) -> tuple[str, str, str]:
    """Of (state, summary, fix) parts, the first of the worst state."""
    return min(parts, key=lambda part: WORST.index(part[0]))


def when(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def refused(ran: Any, what: str, ns: str) -> tuple[str, str, str]:
    """`unknown`, naming the access kubectl lacks, or what it said."""
    err = str(getattr(ran, "stderr", "") or "")
    last = (err.strip().splitlines() or [f"exit {getattr(ran, 'returncode', '?')}"])[-1][:200]
    if getattr(ran, "returncode", None) == 127:
        return "unknown", f"kubectl did not run, to read {what}", "install kubectl, or give --kubectl CMD"
    if "forbidden" in err.lower():
        return "unknown", f"kubectl may not read {what} in {ns}", f"use a kubeconfig that may get {what} in {ns} ({last})"
    return "unknown", f"kubectl could not read {what} in {ns}", f"kubectl said: {last}"


def not_found(ran: Any) -> bool:
    err = str(getattr(ran, "stderr", "") or "")
    return "(NotFound)" in err or " not found" in err


def kubectl(ctx: Any, args: list[str]) -> Any:
    """ctx.kubectl, with any failure to run it as kubectl's own exit 127."""
    try:
        return ctx.kubectl(args)
    except Exception as error:  # noqa: BLE001 - every failure is a Result
        return _Ran(127, "", f"kubectl did not run ({type(error).__name__})")


@dataclass
class _Ran:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def takes_input(run: Callable) -> bool:
    """Whether ctx.kubectl takes the input= pubkit's phase 2 gives it."""
    try:
        parameters = inspect.signature(run).parameters
    except (TypeError, ValueError):
        return False
    return "input" in parameters or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())


def archive_facts(logs: str) -> list[dict]:
    """The sidecar's wal_archive status lines, oldest first."""
    facts = []
    for line in logs.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            fact = json.loads(line)
        except ValueError:
            continue
        if isinstance(fact, dict) and fact.get("fact") == "wal_archive":
            facts.append(fact)
    return facts


def archive_state(facts: list[dict], now: datetime) -> tuple[str, str, str]:
    """What the last two status lines say about the archive."""
    see_log = "see the wal-g container's log, and PostgreSQL's archive_command"
    if not facts:
        return ("unknown", f"the sidecar prints no status in its last {TAIL} lines: is the pod on pg-backup's image?",
                "deploy the pod with pg-backup's image and walg-loop beside PostgreSQL (pg-backup's README, The pod)")
    last = facts[-1]
    previous = facts[-2] if len(facts) > 1 else None
    if last.get("ok") is False:
        return "drift", "the wal-g sidecar could not ask PostgreSQL how archiving goes", see_log
    failed, failed_before = last.get("failed_count"), previous and previous.get("failed_count")
    if isinstance(failed, int) and isinstance(failed_before, int) and failed > failed_before:
        return "drift", f"archiving fails: {failed - failed_before} more failed since the status before", see_log
    archived = when(last.get("last_archived_time"))
    wal, wal_before = last.get("pg_wal_bytes"), previous and previous.get("pg_wal_bytes")
    grew = isinstance(wal, int | float) and isinstance(wal_before, int | float) and wal > wal_before
    if grew and (archived is None or now - archived > ARCHIVE_LAG):
        since = f"since {archived:%Y-%m-%d %H:%M} UTC" if archived else "ever"
        return "drift", f"archive lag: nothing archived {since} while pg_wal grew", see_log
    backup = when(last.get("last_backup"))
    if backup is None:
        return "drift", "no base backup yet", "see the wal-g container's log: walg-loop takes one when it starts"
    if now - backup > BASE_BACKUP_AGE:
        return "drift", f"no base backup since {backup:%Y-%m-%d %H:%M} UTC", "see the wal-g container's log"
    return "ready", f"archiving; last base backup {backup:%Y-%m-%d %H:%M} UTC", ""


class PgDatabase:
    """The pg.database need type."""
    type = "pg.database"
    grade = "auto"
    budget = 6
    fulfil_budget = 300
    offline = False
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "statefulset": {"description": "The database's StatefulSet, as backups/pg-backup.json names it.",
                            "type": "string", "default": "postgres",
                            "pattern": "^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$"},
            "backups": {"description": "pg-backup's settings, in the repository.", "type": "string",
                        "default": "backups/pg-backup.json", "pattern": r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9_./-]+\.json$"},
            "container": {"description": "The wal-g sidecar's container, whose log has the archive's status.",
                          "type": "string", "default": "wal-g", "pattern": "^[a-z0-9]([a-z0-9-]{0,40}[a-z0-9])?$"},
        },
    }

    def __init__(self, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.run = run  # gcloud, for fulfil's account key

    def default_name(self, params: Mapping[str, Any]) -> str | None:
        return params.get("statefulset", "postgres")

    def depends_on(self, need: Any, needs: Mapping[str, Any]) -> list[str]:
        return []

    @staticmethod
    def params(need: Any) -> tuple[str, str, str]:
        params = getattr(need, "params", None) or {}
        return (params.get("statefulset", "postgres"), params.get("backups", "backups/pg-backup.json"),
                params.get("container", "wal-g"))

    def settings(self, need: Any, ctx: Any) -> tuple[Any, tuple[str, str, str] | None]:
        """pg-backup.json as pg-backup check reads it, or why it can't be used (drift)."""
        statefulset, backups, _ = self.params(need)
        path = Path(ctx.root) / backups
        try:
            config = load(path)
        except ConfigError as error:
            first = (str(error).splitlines() or [f"{backups} is not valid"])[0].replace(str(path), backups)
            return None, ("drift", first, f"fix {backups}; ./backups/pg-backup check says what is wrong")
        except Exception as error:  # noqa: BLE001
            return None, ("drift", f"{backups} could not be read ({type(error).__name__})", f"fix {backups}")
        if config.namespace != ctx.namespace:
            return None, ("drift", f"{backups} says namespace {config.namespace}, the deploy {ctx.namespace}",
                          f"make {backups}'s namespace the deploy block's")
        if config.statefulset != statefulset:
            return None, ("drift", f"{backups} says statefulset {config.statefulset}, the need {statefulset}",
                          f"give the need statefulset {config.statefulset}, or fix {backups}")
        return config, None

    def check(self, need: Any, ctx: Any) -> Any:
        try:
            return self._check(need, ctx)
        except Exception as error:  # noqa: BLE001 - every failure is a Result, named by its type alone
            return result(ctx, "unknown", f"the check failed ({type(error).__name__})",
                          fix="this is a fault in pg-backup's pg.database check: report it")

    def _check(self, need: Any, ctx: Any) -> Any:
        statefulset, _, container = self.params(need)
        ns = ctx.namespace
        config, bad = self.settings(need, ctx)
        if bad:
            return result(ctx, *bad)
        parts = [self.statefulset_state(ctx, ns, statefulset)]
        if parts[0][0] == "ready":
            parts.append(self.archive_part(ctx, ns, statefulset, container))
        parts.append(self.secret_state(ctx, ns, config.wal["secret"]))
        details = [f"{state}: {summary}" for state, summary, _ in parts]
        details.append(f"drill: none recorded yet; {DRILL_FIX}")
        state, summary, fix = worst(parts)
        if state == "ready":
            summary = f"{statefulset} in {ns} is ready, {parts[1][1]}, and its WAL Secret has both keys"
        return result(ctx, state, summary, fix or DRILL_FIX, details)

    def statefulset_state(self, ctx: Any, ns: str, name: str) -> tuple[str, str, str]:
        ran = kubectl(ctx, ["-n", ns, "get", "statefulset", name, "-o", "json"])
        if ran.returncode != 0:
            if not_found(ran):
                return "missing", f"StatefulSet {name} is not in {ns}", "deploy it: the overlay's manifests make it"
            return refused(ran, "statefulsets", ns)
        try:
            found = json.loads(ran.stdout)
            wanted = found.get("spec", {}).get("replicas", 1)
            ready = found.get("status", {}).get("readyReplicas", 0) or 0
        except (ValueError, AttributeError):
            return "unknown", f"kubectl's answer for StatefulSet {name} is not JSON", "run it again"
        if wanted < 1 or ready < wanted:
            return ("missing", f"StatefulSet {name} in {ns} is not ready: {ready} of {wanted}",
                    f"kubectl -n {ns} rollout status statefulset/{name}, and its pod's events")
        return "ready", f"StatefulSet {name} in {ns} is ready", ""

    def archive_part(self, ctx: Any, ns: str, statefulset: str, container: str) -> tuple[str, str, str]:
        ran = kubectl(ctx, ["-n", ns, "logs", f"{statefulset}-0", "-c", container, f"--tail={TAIL}"])
        if ran.returncode != 0:
            return refused(ran, f"pods/log of {statefulset}-0's {container}", ns)
        return archive_state(archive_facts(str(ran.stdout)), ctx.now())

    def secret_state(self, ctx: Any, ns: str, name: str) -> tuple[str, str, str]:
        ran = kubectl(ctx, ["-n", ns, "get", "secret", name, "-o", "json"])
        fix = "./backups/pg-backup setup-secrets, or the deploy walk's fulfil"
        if ran.returncode != 0:
            if not_found(ran):
                return "missing", f"the WAL Secret {name} is not in {ns}", fix
            return refused(ran, "secrets", ns)
        try:
            keys = set((json.loads(ran.stdout).get("data") or {}).keys())  # the names alone are kept
        except (ValueError, AttributeError):
            return "unknown", f"kubectl's answer for Secret {name} is not JSON", "run it again"
        lacking = [key for key in SECRET_KEYS if key not in keys]
        if lacking:
            return "drift", f"the WAL Secret {name} has no {', '.join(lacking)}", fix
        return "ready", f"the WAL Secret {name} has {', '.join(SECRET_KEYS)}", ""

    def fulfil(self, need: Any, ctx: Any) -> Any:
        try:
            return self._fulfil(need, ctx)
        except Exception as error:  # noqa: BLE001 - its message might hold a value: its type alone
            return result(ctx, "unknown", f"fulfil failed ({type(error).__name__})",
                          fix="this is a fault in pg-backup's pg.database fulfil: report it")

    def _fulfil(self, need: Any, ctx: Any) -> Any:
        writer, values = getattr(ctx, "bitwarden_writer", None), getattr(ctx, "values", None)
        if writer is None or values is None:
            return result(ctx, "unknown", "the keys can't be made in this walk", fix=NO_WRITER)
        if not takes_input(ctx.kubectl):
            return result(ctx, "unknown", "this pubkit's kubectl takes no input, so the WAL Secret can't be put",
                          fix="use a pubkit whose walk fulfils (phase 2), or ./backups/pg-backup setup-secrets")
        config, bad = self.settings(need, ctx)
        if bad:
            return result(ctx, *bad)
        made: dict[str, str] = {}  # values made in this run, in memory alone

        def value(name: str) -> str | None:
            return made.get(name) or values.get(name)

        def write(name: str, text: str, note: str) -> None:
            try:
                writer.write(name, text, note)
            except Exception as error:  # noqa: BLE001
                raise Stop(1, f"Bitwarden refused {name} ({type(error).__name__})") from None
            made[name] = text

        def make_account_key(name: str) -> None:
            account = config.wal["account"]
            project = account.split("@", 1)[1].split(".", 1)[0]
            done = self.gcloud(account, project, "create", "-")
            try:
                key_id = json.loads(done)["private_key_id"]
            except (ValueError, KeyError, TypeError):
                raise Stop(1, f"gcloud printed something other than a key: look for a new key on {account}, and delete it") from None
            try:
                write(name, base64.b64encode(done.encode()).decode(), f"Key {key_id} of {account}")
            except Stop:
                self.gcloud(account, project, "delete", key_id, "--quiet")
                raise Stop(1, f"Bitwarden refused {name}; key {key_id} was deleted from Google, as nothing holds it") from None

        def make_encryption_key(name: str, note: str) -> None:
            write(name, base64.b64encode(os.urandom(32)).decode(), note)

        def put_secret() -> None:
            gcs, key = value(config.wal["gcs_key_secret"]), value(config.wal["encryption_key_secret"])
            if not gcs or not key:
                raise Stop(1, "a key Bitwarden has could not be read back to put in the Secret")
            manifest = json.dumps(secret_manifest(config, gcs, key)).encode()
            ran = ctx.kubectl(["apply", "-f", "-"], input=manifest)
            if ran.returncode != 0:
                raise Stop(1, f"kubectl apply refused the WAL Secret {config.wal['secret']} (exit {ran.returncode})")

        said: list[str] = []
        try:
            done = make_keys(config, Keys(lambda name: bool(value(name)), make_account_key, make_encryption_key, put_secret),
                             say=said.append)
        except Stop as stop:
            return result(ctx, "unknown", str(stop), fix="./backups/pg-backup setup-secrets, at a terminal", details=said)
        return result(ctx, "ready", "; ".join(done), details=said)

    def gcloud(self, account: str, project: str, *args: str) -> str:
        """gcloud iam service-accounts keys ARGS: what it printed, or Stop with its last line."""
        try:
            done = self.run(["gcloud", "iam", "service-accounts", "keys", *args, f"--iam-account={account}",
                             f"--project={project}"], capture_output=True, text=True, check=False)
        except FileNotFoundError:
            raise Stop(1, "gcloud is not installed") from None
        if done.returncode != 0:
            last = (re.sub(r"\s+", " ", done.stderr or "").strip() or f"exit {done.returncode}")[:200]
            raise Stop(1, f"gcloud refused: {last}")
        return done.stdout


pg_database = PgDatabase()
