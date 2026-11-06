import json
import re
from datetime import datetime, timezone

import pytest

from pg_backup.restore import Stop, choose_backup, clean, oldest_backup, parse_target, promote, restore
from pg_backup.server import ServerError

from conftest import BACKUPS, ROWS


def lines():
    out = []
    return out, out.append


def test_targets_need_a_zone():
    assert parse_target("latest") is None
    assert parse_target("2026-11-05 14:32:00+00") == datetime(2026, 11, 5, 14, 32, tzinfo=timezone.utc)
    assert parse_target("2026-11-05T16:32:00+02:00") == datetime(2026, 11, 5, 14, 32, tzinfo=timezone.utc)
    assert parse_target("2026-11-05T14:32:00Z").hour == 14
    with pytest.raises(Stop, match="no zone") as stop:
        parse_target("2026-11-05 14:32:00")
    assert stop.value.code == 2
    with pytest.raises(Stop, match="can't read"):
        parse_target("yesterday")


def test_the_newest_backup_before_the_moment_is_chosen():
    backups = json.loads(BACKUPS)
    assert choose_backup(backups, None) == "base_2"
    assert choose_backup(backups, parse_target("2026-11-06 07:00:00+00")) == "base_1"
    assert choose_backup(backups, parse_target("2026-11-06 06:00:00+00")) is None
    assert oldest_backup(backups) == "2026-11-06T06:14:28.115Z"
    assert oldest_backup([]) is None


def test_a_restore_goes_into_a_closed_scratch_namespace(config, server):
    out, say = lines()
    namespace = restore(config, server, "2026-11-06 07:05:00+00", say=say, sleep=lambda s: None)
    assert config.is_scratch(namespace)
    assert server.ran(rf"create namespace {namespace}")
    spec = json.loads(next(i for c, i in zip(server.calls, server.inputs) if i and '"List"' in i))
    policy, pod = spec["items"]
    assert policy["spec"] == {"podSelector": {}, "policyTypes": ["Ingress"]}
    env = {e["name"]: e.get("value") for e in pod["spec"]["containers"][0]["env"]}
    assert env["WALG_GS_PREFIX"] == "gs://sustenaut-auth-wal/keycloak"
    secret = json.loads(next(i for i in server.inputs if i and '"Secret"' in i))
    assert secret["metadata"] == {"name": "auth-wal", "namespace": namespace}
    assert server.ran(r"backup-fetch .* base_2")
    assert server.ran(r"recovery_target_time = .*2026-11-06 07:05:00\+00")
    assert server.ran(r"wal-g st cat wal_005/%f\.br")
    assert not server.ran(r"-n auth (scale|delete|create job)")
    assert "   team 2|team 2" in out
    assert out[-1].startswith(f"Then: ./backups/pg-backup clean {namespace}")


def test_latest_sets_no_target(config, server):
    restore(config, server, "latest", say=lambda s: None, sleep=lambda s: None)
    assert not server.ran("recovery_target_time =")


def test_too_early_is_exit_3_with_the_oldest(config, server):
    with pytest.raises(Stop) as stop:
        restore(config, server, "2026-11-06 06:00:00+00", say=lambda s: None)
    assert stop.value.code == 3
    assert stop.value.oldest == "2026-11-06T06:14:28.115Z"
    assert config.is_scratch(stop.value.namespace)


def test_an_empty_archive_is_too_early_too(config, server):
    server.on(r"backup-list --json", "")
    with pytest.raises(Stop, match="not there yet") as stop:
        restore(config, server, "latest", say=lambda s: None)
    assert stop.value.oldest is None


def test_a_failure_before_the_namespace_names_none(config, server):
    server.on(r"get statefulset postgres", "")
    with pytest.raises(Stop, match="no container postgres") as stop:
        restore(config, server, "latest", say=lambda s: None)
    assert stop.value.namespace is None


def test_an_image_without_wal_g_stops(config, server):
    server.on(r"command -v wal-g", "")
    with pytest.raises(Stop, match="no wal-g"):
        restore(config, server, "latest", say=lambda s: None)


def test_a_restore_missing_a_required_row_fails(config, server):
    server.on(r"exec restore -- psql .*group by", "family 4\nmaster 2\n")
    with pytest.raises(Stop, match="no sustenaut, team"):
        restore(config, server, "latest", say=lambda s: None, sleep=lambda s: None)


def test_recovery_that_never_ends_times_out(config, server):
    server.on(r"pg_is_in_recovery", ServerError("not up"))
    ticks = iter(range(0, 10000, 100))
    with pytest.raises(Stop, match="30 minutes"):
        restore(config, server, "latest", say=lambda s: None, clock=lambda: next(ticks), sleep=lambda s: None)


def test_clean_deletes_only_scratch_namespaces(config, server):
    clean(config, server, "auth-restore-20261106070216")
    assert server.ran("delete namespace auth-restore-20261106070216 --wait=false")
    with pytest.raises(Stop) as stop:
        clean(config, server, "auth")
    assert stop.value.code == 2


NS = "auth-restore-20261106070216"


def run_promote(config, server, answer=None, interactive=True):
    out, say = lines()
    promote(config, server, NS, say=say, ask=lambda prompt: answer or f"promote {NS}", interactive=lambda: interactive)
    return out


def test_promote_refuses_a_live_namespace(config, server):
    with pytest.raises(Stop) as stop:
        run_promote(config, server, NS.replace("-restore-20261106070216", ""))
    with pytest.raises(Stop) as stop:
        promote(config, server, "auth", ask=lambda p: "", interactive=lambda: True)
    assert stop.value.code == 2


def test_promote_needs_a_running_restore(config, server):
    server.on(r"pg_is_in_recovery", ServerError("gone"))
    with pytest.raises(Stop, match="no promoted restore"):
        run_promote(config, server)


def test_promote_needs_every_required_row(config, server):
    server.on(r"exec restore -- psql .*group by", "family 4\n")
    with pytest.raises(Stop, match="not promoting it"):
        run_promote(config, server)


def test_promote_needs_a_terminal_and_the_exact_words(config, server):
    with pytest.raises(Stop, match="at a terminal") as stop:
        run_promote(config, server, interactive=False)
    assert stop.value.code == 2
    with pytest.raises(Stop, match="nothing changed"):
        run_promote(config, server, answer="yes")
    assert not server.ran(r"create job|scale")


def test_promote_dumps_first_and_stops_without_the_dump(config, server):
    server.on(r"wait --for=condition=complete", ServerError("timed out"))
    with pytest.raises(Stop, match="didn't complete"):
        run_promote(config, server)
    assert server.ran(r"-n auth create job pre-promote-\d{14} --from=cronjob/backup")
    assert not server.ran("scale")


def test_promote_replaces_live_in_one_transaction_and_starts_the_app(config, server):
    out = run_promote(config, server)
    order = [c for c in server.calls if re.search(r"create job|scale|pg_dump|pg_restore", c)]
    assert "create job" in order[0]
    assert "--replicas=0" in order[1]
    assert f"-n {NS} exec restore -- pg_dump" in order[2]
    assert "-n auth exec -i postgres-0 -c postgres -- pg_restore" in order[3] and "--single-transaction" in order[3]
    assert "--replicas=1" in order[4]
    assert "   live now matches the restore" in out
    assert out[-1] == f"When it looks right: ./backups/pg-backup clean {NS}"


def test_a_failed_pg_restore_leaves_live_and_starts_the_app(config, server):
    server.on(r"pg_restore", ServerError("bad", "pg_restore: error"))
    with pytest.raises(Stop, match="rolled back"):
        run_promote(config, server)
    assert server.calls[-2].endswith("--replicas=1")


def test_live_not_matching_after_promote_fails_and_starts_the_app(config, server):
    seen = {"n": 0}

    def live(command):
        seen["n"] += 1
        return ROWS if seen["n"] == 1 else "family 1"

    server.on(r"exec postgres-0 -c postgres -- psql", live)
    with pytest.raises(Stop, match="doesn't match"):
        run_promote(config, server)
    assert server.ran("--replicas=1")

