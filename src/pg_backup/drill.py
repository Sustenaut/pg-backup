"""The drill: a restore to 10 minutes ago, timed, then deleted. It passes if the check finds
everything and the restore took under 15 minutes."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from .config import Config
from .restore import Stop, backup_time, clean, parse_target, pg_time, restore
from .server import Server, ServerError

LIMIT = 900  # seconds
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
    stamp = now().strftime("%Y-%m-%dT%H:%M:%SZ")
    if failure and failure.code == 3:
        oldest = getattr(failure, "oldest", None)
        if oldest and target_text:
            say(f"DRILL NOT RUN: the archive starts at {oldest}, after {target}. Pick a moment after that with --to.")
        elif oldest:
            retry = backup_time(oldest) + timedelta(minutes=11)
            say(f"DRILL NOT RUN: no base backup before {target} yet; the oldest is from {oldest}. Run it again after {retry:%H:%M} UTC.")
        else:
            say("DRILL NOT RUN: the archive has no base backup yet. Run it again 11 minutes after the first one (the wal-g container's log says when).")
        return NOT_YET
    if failure is None and seconds < LIMIT:
        say(f"DRILL PASSED {stamp}: restored to {target} in {seconds} s")
        return 0
    reason = str(failure) if failure else f"took {seconds} s"
    say(f"DRILL FAILED {stamp}: {reason} (the target is under {LIMIT} s)")
    return 1
