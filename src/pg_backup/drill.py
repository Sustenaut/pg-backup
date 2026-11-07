"""The drill: a restore to 10 minutes ago, timed, then deleted. It passes if the check finds
everything and the restore took under 15 minutes."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from .config import Config
from .restore import Stop, backup_time, clean, parse_target, pg_time, restore
from .server import Server, ServerError

LIMIT = 900  # seconds


def local(moment: datetime, seconds: bool = False) -> str:
    """A time for people: in this machine's zone, with the zone named, as 07:17 EET. What a
    program reads (the restore target, the archive's own times) stays in UTC."""
    return moment.astimezone().strftime("%H:%M:%S %Z" if seconds else "%H:%M %Z")
NOT_YET = 2


def drill(config: Config, server: Server, target_text: str | None = None, say: Callable[[str], None] = print,
          clock: Callable[[], float] = time.monotonic, now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> int:
    moment = parse_target(target_text) if target_text else now() - timedelta(minutes=10)
    target = "latest" if moment is None else pg_time(moment)
    start = clock()
    namespace = None
    failure = None
    try:
        namespace = restore(config, server, target, say=say)
    except Stop as error:
        namespace = getattr(error, "namespace", None)
        failure = error
    except ServerError as error:
        namespace = getattr(error, "namespace", None)
        failure = Stop(1, f"{error}\n{error.output}".strip())
    seconds = round(clock() - start)
    if namespace:
        clean(config, server, namespace)
    finished = now()
    stamp = f"{finished.astimezone():%Y-%m-%d} {local(finished)}"
    shown = "latest" if moment is None else f"{moment.astimezone():%Y-%m-%d} {local(moment, seconds=True)}"
    old_image = getattr(failure, "old_image", None)
    if old_image:
        say(f"DRILL NOT RUN: {config.namespace} still runs {old_image}, not pg-backup's image. "
            "Run it again once the deploy has finished and the first base backup is 10 minutes old, or now with --wait.")
        return NOT_YET
    if failure and failure.code == 3:
        oldest = getattr(failure, "oldest", None)
        if oldest and target_text:
            say(f"DRILL NOT RUN: the archive starts at {local(backup_time(oldest))}, after {shown}. Pick a moment after that with --to.")
        elif oldest:
            retry = backup_time(oldest) + timedelta(minutes=11)
            say(f"DRILL NOT RUN: no base backup before {shown} yet; the oldest is from {local(backup_time(oldest))}. Run it again after {local(retry)}, or now with --wait.")
        else:
            say("DRILL NOT RUN: the archive has no base backup yet. Run it again 11 minutes after the first one (the wal-g container's log says when).")
        return NOT_YET
    if failure is None and seconds < LIMIT:
        say(f"DRILL PASSED {stamp}: restored to {shown} in {seconds} s")
        return 0
    reason = str(failure) if failure else f"took {seconds} s"
    say(f"DRILL FAILED {stamp}: {reason} (the target is under {LIMIT} s)")
    return 1


WAIT_LIMIT = 2400  # seconds: a deploy, then a first base backup ten minutes old


def wait_until_ready(config: Config, server: Server, say: Callable[[str], None] = print,
                     sleep: Callable[[float], None] = time.sleep,
                     now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> None:
    """For `drill --wait`, right after a merge: waits for the StatefulSet's rollout, then for a
    base backup old enough for a drill to 10 minutes ago, asking the live pod's WAL-G."""
    c = config
    say(f"== Waiting for statefulset/{c.statefulset} in {c.namespace} to finish rolling out")
    server.kubectl("-n", c.namespace, "rollout", "status", f"statefulset/{c.statefulset}", "--timeout=10m")
    started = now()
    while True:
        listed = server.kubectl("-n", c.namespace, "exec", c.pod, "-c", c.container, "--",
                                "sh", "-c", "wal-g backup-list --json 2>/dev/null", check=False).strip()
        times = [backup_time(entry["time"]) for entry in json.loads(listed)] if listed.startswith("[") else []
        ready = min(times) + timedelta(minutes=11) if times else None
        if ready is not None and ready <= now():
            return
        if now() - started > timedelta(seconds=WAIT_LIMIT):
            raise Stop(1, "no base backup old enough after 40 minutes: see the wal-g container's log")
        if ready is None:
            say("   no base backup yet; asking again in a minute")
            sleep(60)
        else:
            say(f"   the first base backup is from {local(min(times), seconds=True)}; waiting until {local(ready, seconds=True)}")
            sleep(max(1.0, (ready - now()).total_seconds()))
