from datetime import datetime, timedelta, timezone

import pytest

from pg_backup.drill import drill
from pg_backup.restore import Stop
from pg_backup.server import ServerError

NOW = datetime(2026, 11, 6, 7, 15, tzinfo=timezone.utc)


def run(config, server, to=None, clock=None):
    out = []
    code = drill(config, server, to, say=out.append, now=lambda: NOW, clock=clock or iter([0, 15]).__next__)
    return code, out


def test_a_drill_restores_ten_minutes_ago_and_cleans_up(config, server):
    code, out = run(config, server)
    assert code == 0
    assert out[0].startswith("== Restoring to 2026-11-06 07:05:00+00")
    assert out[-1] == "DRILL PASSED 2026-11-06 07:15 UTC: restored to 2026-11-06 07:05:00 UTC in 15 s"
    assert server.ran(r"delete namespace auth-restore-\d{14}")


def test_a_slow_restore_fails(config, server):
    code, out = run(config, server, clock=iter([0, 901]).__next__)
    assert code == 1
    assert out[-1].endswith("took 901 s (the target is under 900 s)")


def test_too_early_is_not_run_with_when_to_retry(config, server):
    server.on(r"backup-list --json", '[{"backup_name": "base_1", "time": "2026-11-06T07:08:00Z"}]')
    code, out = run(config, server)
    assert code == 2
    assert out[-1].endswith("the oldest is from 07:08 UTC. Run it again after 07:19 UTC, or now with --wait.")
    assert server.ran(r"delete namespace auth-restore-\d{14}")


def test_too_early_for_a_chosen_moment_asks_for_a_later_one(config, server):
    code, out = run(config, server, "2026-11-06 06:00:00+00")
    assert code == 2
    assert "Pick a moment after that with --to." in out[-1]


def test_an_empty_archive_is_not_run(config, server):
    server.on(r"backup-list --json", "")
    code, out = run(config, server)
    assert code == 2
    assert out[-1].startswith("DRILL NOT RUN: the archive has no base backup yet")


def test_a_server_failure_fails_and_still_cleans_up(config, server):
    server.on(r"backup-fetch", ServerError("fetch failed", "403"))
    code, out = run(config, server)
    assert code == 1
    assert out[-1].startswith("DRILL FAILED 2026-11-06 07:15 UTC: fetch failed")
    assert server.ran(r"delete namespace auth-restore-\d{14}")


def test_a_latest_drill(config, server):
    code, out = run(config, server, "latest")
    assert code == 0
    assert "restored to latest" in out[-1]


def test_a_namespace_still_on_the_old_image_is_not_run(config, server):
    server.on(r"get statefulset postgres", "docker.io/library/postgres:16.14")
    server.on(r"command -v wal-g", "")
    code, out = run(config, server)
    assert code == 2
    assert out[-1].startswith("DRILL NOT RUN: auth still runs docker.io/library/postgres:16.14")
    assert server.ran(r"delete namespace auth-restore-\d{14}")


class Clock:
    """now() and sleep() over one fake time, starting at 07:15 UTC."""

    def __init__(self):
        self.at = NOW
        self.slept = []

    def now(self):
        return self.at

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.at += timedelta(seconds=seconds)


def test_wait_returns_once_the_first_backup_is_old_enough(config, server):
    from pg_backup.drill import wait_until_ready
    clock, out = Clock(), []
    server.on(r"exec postgres-0 -c postgres -- sh -c 'wal-g backup-list", '[{"backup_name": "b", "time": "2026-11-06T07:10:00Z"}]')
    wait_until_ready(config, server, say=out.append, sleep=clock.sleep, now=clock.now)
    assert server.ran(r"rollout status statefulset/postgres --timeout=10m")
    assert clock.slept == [360.0]
    assert out[-1] == "   the first base backup is from 07:10:00 UTC; waiting until 07:21:00 UTC"


def test_people_read_times_in_this_machine_s_zone(config, server, monkeypatch):
    import time as clock
    monkeypatch.setenv("TZ", "Europe/Tallinn")
    clock.tzset()
    server.on(r"backup-list --json", '[{"backup_name": "base_1", "time": "2026-11-06T07:08:00Z"}]')
    code, out = run(config, server)
    assert out[0].startswith("== Restoring to 2026-11-06 07:05:00+00 (09:05:00 EET)")
    assert out[-1].endswith("the oldest is from 09:08 EET. Run it again after 09:19 EET, or now with --wait.")


def test_wait_asks_again_while_there_is_no_backup(config, server):
    from pg_backup.drill import wait_until_ready
    clock, answers = Clock(), iter(["", '[{"backup_name": "b", "time": "2026-11-06T07:00:00Z"}]'])
    server.on(r"exec postgres-0 -c postgres -- sh -c 'wal-g backup-list", lambda command: next(answers))
    wait_until_ready(config, server, say=lambda s: None, sleep=clock.sleep, now=clock.now)
    assert clock.slept == [60]


def test_wait_gives_up_after_forty_minutes(config, server):
    from pg_backup.drill import wait_until_ready
    clock = Clock()
    server.on(r"exec postgres-0 -c postgres -- sh -c 'wal-g backup-list", "")
    with pytest.raises(Stop, match="40 minutes"):
        wait_until_ready(config, server, say=lambda s: None, sleep=clock.sleep, now=clock.now)
