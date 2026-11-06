from datetime import datetime, timezone

from pg_backup.drill import drill
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
    assert out[-1] == "DRILL PASSED 2026-11-06T07:15:00Z: restored to 2026-11-06 07:05:00+00 in 15 s"
    assert server.ran(r"delete namespace auth-restore-\d{14}")


def test_a_slow_restore_fails(config, server):
    code, out = run(config, server, clock=iter([0, 901]).__next__)
    assert code == 1
    assert out[-1].endswith("took 901 s (the target is under 900 s)")


def test_too_early_is_not_run_with_when_to_retry(config, server):
    server.on(r"backup-list --json", '[{"backup_name": "base_1", "time": "2026-11-06T07:08:00Z"}]')
    code, out = run(config, server)
    assert code == 2
    assert out[-1].endswith("the oldest is from 2026-11-06T07:08:00Z. Run it again after 07:19 UTC.")
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
    assert out[-1].startswith("DRILL FAILED 2026-11-06T07:15:00Z: fetch failed")
    assert server.ran(r"delete namespace auth-restore-\d{14}")


def test_a_latest_drill(config, server):
    code, out = run(config, server, "latest")
    assert code == 0
    assert "restored to latest" in out[-1]
