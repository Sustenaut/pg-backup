"""The pg.database need type, against a fake Context: a fake kubectl that keeps each argv and
input, a fixed clock, and a fake Bitwarden writer."""

import base64
import json
import subprocess
import sys
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pg_backup import need as module
from pg_backup.need import PgDatabase, Result, archive_state, pg_database
from pg_backup.setup import Keys, make_keys

from conftest import EXAMPLE

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)
CANARY = "CANARY-6f1c3a-not-for-any-log"


@dataclass
class Ran:
    returncode: int
    stdout: str = ""
    stderr: str = ""


def fact(**kw):
    out = {"fact": "wal_archive", "last_archived_wal": "000000010000000000000042",
           "last_archived_time": "2026-11-08T05:58:30.12+00:00", "failed_count": 0, "last_failed_time": None,
           "pg_wal_bytes": 33554432, "last_backup": "2026-11-08T02:47:10Z"}
    return json.dumps(out | kw)


STATEFULSET = json.dumps({"spec": {"replicas": 1}, "status": {"readyReplicas": 1}})
SECRET = json.dumps({"data": {"gcs-key.json": "Z2Nz", "libsodium-key": "a2V5"}})
LOGS = "\n".join(["waiting", fact(), "wal-g: INFO: pushed", fact()])


class FakeKubectl:
    """Answers by the first matching argv prefix; keeps each argv and input."""

    def __init__(self, **answers):
        self.answers = {"get statefulset": Ran(0, STATEFULSET), "logs": Ran(0, LOGS),
                        "get secret": Ran(0, SECRET), "apply": Ran(0, "secret/auth-wal configured")} \
            | {key.replace("_", " "): answer for key, answer in answers.items()}
        self.argv: list[list[str]] = []
        self.inputs: list[bytes | None] = []

    def __call__(self, args, input=None):
        self.argv.append(list(args))
        self.inputs.append(input)
        line = " ".join(args)
        for key, answer in self.answers.items():
            if key in line:
                return answer
        return Ran(1, "", "unexpected")


class OldKubectl(FakeKubectl):
    """pubkit 0.31.1's: args alone."""

    def __call__(self, args):
        return super().__call__(args)


class Writer:
    def __init__(self, fails=False):
        self.written: dict[str, tuple[str, str]] = {}
        self.fails = fails

    def write(self, name, value, note):
        if self.fails:
            raise RuntimeError(f"bws said no to {value}")  # a message holding the value
        self.written[name] = (value, note)


@dataclass
class Context:
    root: Path
    namespace: str = "auth"
    kubectl: object = field(default_factory=FakeKubectl)
    now: object = lambda: NOW
    env: dict = field(default_factory=dict)


@dataclass
class Need:
    type: str = "pg.database"
    name: str = "postgres"
    params: dict = field(default_factory=dict)


@pytest.fixture
def ctx(tmp_path):
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "pg-backup.json").write_text(EXAMPLE.read_text())
    return Context(tmp_path)


def test_the_type_has_what_pubkit_checks():
    kind = pg_database
    assert kind.type == "pg.database" and kind.grade == "auto" and kind.offline is False
    assert kind.budget == 6 and kind.fulfil_budget == 300
    assert kind.schema["type"] == "object" and kind.schema["additionalProperties"] is False
    assert set(kind.schema["properties"]) == {"statefulset", "backups", "container"}
    assert kind.default_name({}) == "postgres" and kind.default_name({"statefulset": "db"}) == "db"
    assert kind.depends_on(Need(), {}) == []
    assert module.STATES == ("ready", "missing", "drift", "blocked", "unknown")


def test_a_healthy_database_is_ready_with_the_drill_in_details(ctx):
    got = pg_database.check(Need(), ctx)
    assert got.state == "ready", got
    assert got.summary == "postgres in auth is ready, archiving; last base backup 2026-11-08 02:47 UTC, and its WAL Secret has both keys"
    assert any(d.startswith("drill: none recorded yet") for d in got.details)
    assert got.fix == "run ./backups/pg-backup drill --wait and record it"
    assert ["-n", "auth", "logs", "postgres-0", "-c", "wal-g", "--tail=50"] in ctx.kubectl.argv


def test_bad_settings_are_drift_with_their_first_line(ctx):
    (ctx.root / "backups" / "pg-backup.json").write_text("{}")
    got = pg_database.check(Need(), ctx)
    assert got.state == "drift" and got.summary.startswith("backups/pg-backup.json doesn't match")
    assert ctx.kubectl.argv == []
    got = pg_database.check(Need(params={"backups": "backups/none.json"}), ctx)
    assert got.state == "drift" and "no backups/none.json" in got.summary


def test_settings_for_another_namespace_or_statefulset_are_drift(ctx):
    ctx.namespace = "other"
    assert "says namespace auth, the deploy other" in pg_database.check(Need(), ctx).summary
    ctx.namespace = "auth"
    assert "says statefulset postgres, the need db" in pg_database.check(Need(params={"statefulset": "db"}), ctx).summary


@pytest.mark.parametrize("answer, state, words", [
    (Ran(1, "", 'Error from server (NotFound): statefulsets.apps "postgres" not found'), "missing", "is not in auth"),
    (Ran(0, json.dumps({"spec": {"replicas": 1}, "status": {}})), "missing", "not ready: 0 of 1"),
    (Ran(1, "", "Error from server (Forbidden): statefulsets.apps is forbidden"), "unknown", "may not read statefulsets"),
    (Ran(127, "", "kubectl: not given to this check"), "unknown", "kubectl did not run"),
    (Ran(1, "", "connection refused"), "unknown", "could not read statefulsets"),
    (Ran(0, "not json"), "unknown", "is not JSON"),
])
def test_the_statefulset(ctx, answer, state, words):
    ctx.kubectl = FakeKubectl(get_statefulset=answer)
    got = pg_database.check(Need(), ctx)
    assert got.state == state and words in got.summary
    assert not any(a[2] == "logs" for a in ctx.kubectl.argv)  # no pod to read


def test_kubectl_that_raises_is_unknown(ctx):
    def broken(args):
        raise OSError("no such file")
    ctx.kubectl = broken
    got = pg_database.check(Need(), ctx)
    assert got.state == "unknown" and "kubectl did not run" in got.summary


@pytest.mark.parametrize("lines, state, words", [
    ([fact(failed_count=2), fact(failed_count=3)], "drift", "archiving fails"),
    ([fact(pg_wal_bytes=1, last_archived_time="2026-11-08T05:40:00+00:00"),
      fact(pg_wal_bytes=2, last_archived_time="2026-11-08T05:40:00+00:00")], "drift", "archive lag"),
    ([fact(pg_wal_bytes=1, last_archived_time=None), fact(pg_wal_bytes=2, last_archived_time=None)], "drift", "archived ever"),
    ([fact(last_backup="2026-11-07T03:00:00Z")], "drift", "no base backup since 2026-11-07 03:00 UTC"),
    ([fact(last_backup=None)], "drift", "no base backup yet"),
    (['{"fact": "wal_archive", "ok": false}'], "drift", "could not ask PostgreSQL"),
    (["PostgreSQL is starting", "{not json", '{"fact": "wal_base_backup", "ok": false}'], "unknown", "prints no status"),
])
def test_the_sidecars_status(ctx, lines, state, words):
    ctx.kubectl = FakeKubectl(logs=Ran(0, "\n".join(lines)))
    got = pg_database.check(Need(), ctx)
    assert got.state == state and words in got.summary, got


def test_an_old_archive_time_is_fine_while_nothing_is_written():
    quiet = [json.loads(fact(last_archived_time="2026-11-08T01:00:00+00:00"))] * 2
    assert archive_state(quiet, NOW)[0] == "ready"


def test_logs_kubectl_may_not_read_is_unknown(ctx):
    ctx.kubectl = FakeKubectl(logs=Ran(1, "", "pods \"postgres-0\" is forbidden: cannot get resource pods/log"))
    got = pg_database.check(Need(params={"container": "sidecar"}), ctx)
    assert got.state == "unknown" and "pods/log of postgres-0's sidecar" in got.summary


@pytest.mark.parametrize("answer, state, words", [
    (Ran(1, "", 'Error from server (NotFound): secrets "auth-wal" not found'), "missing", "WAL Secret auth-wal is not in auth"),
    (Ran(0, json.dumps({"data": {"gcs-key.json": "x"}})), "drift", "has no libsodium-key"),
    (Ran(1, "", "secrets is forbidden"), "unknown", "may not read secrets"),
    (Ran(0, "[]"), "unknown", "is not JSON"),
])
def test_the_wal_secret(ctx, answer, state, words):
    ctx.kubectl = FakeKubectl(get_secret=answer)
    got = pg_database.check(Need(), ctx)
    assert got.state == state and words in got.summary
    assert "x" not in " ".join(got.details).split()


def test_the_worst_part_wins(ctx):
    ctx.kubectl = FakeKubectl(get_secret=Ran(1, "", "forbidden"), logs=Ran(0, fact(last_backup=None)))
    got = pg_database.check(Need(), ctx)
    assert got.state == "drift" and got.summary == "no base backup yet"
    assert [d.split(":")[0] for d in got.details] == ["ready", "drift", "unknown", "drill"]


def test_a_fault_in_the_check_is_a_result(ctx):
    ctx.now = None  # calling it raises TypeError
    got = pg_database.check(Need(), ctx)
    assert got.state == "unknown" and got.summary == "the check failed (TypeError)"


def test_the_result_is_the_walks_own_class(tmp_path, monkeypatch):
    """pubkit's walk keeps only its own Result: the one in its Context's module."""
    fake = types.ModuleType("fake_pubkit_model")

    @dataclass
    class TheirResult:
        state: str
        summary: str
        fix: str = ""
        details: list = field(default_factory=list)
        checked_at: str = ""
        seconds: float = 0.0
    TheirResult.__name__ = "Result"
    fake.Result = TheirResult

    class TheirContext(Context):
        pass
    TheirContext.__module__ = "fake_pubkit_model"
    monkeypatch.setitem(sys.modules, "fake_pubkit_model", fake)
    got = pg_database.check(Need(), TheirContext(tmp_path))
    assert type(got) is TheirResult and got.state == "drift"
    assert type(pg_database.check(Need(), Context(tmp_path))) is Result


def test_a_long_summary_is_cut_as_pubkit_cuts_it():
    assert len(Result("ready", "x" * 300).summary) == 120


class Gcloud:
    def __init__(self, code=0, printed=None):
        self.calls = []
        self.code = code
        self.printed = printed if printed is not None else json.dumps({"private_key_id": "k1", "private_key": CANARY})

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        out = self.printed if argv[4] == "create" else ""
        return subprocess.CompletedProcess(argv, self.code, out, "ERROR: denied" if self.code else "")


@pytest.fixture
def walk(ctx):
    ctx.bitwarden_writer = Writer()
    ctx.values = {}
    return ctx


def test_fulfil_makes_both_keys_and_puts_the_secret_on_stdin(walk):
    gcloud = Gcloud()
    got = PgDatabase(run=gcloud).fulfil(Need(), walk)
    assert got.state == "ready", got
    assert got.summary == "made auth-wal-gcs-key; made auth-wal-key; put Secret auth-wal in auth"
    assert gcloud.calls[0][:6] == ["gcloud", "iam", "service-accounts", "keys", "create", "-"]
    assert "--project=sustenaut" in gcloud.calls[0]
    gcs, note = walk.bitwarden_writer.written["auth-wal-gcs-key"]
    assert note == "Key k1 of sustenaut-auth-wal@sustenaut.iam.gserviceaccount.com"
    key, _ = walk.bitwarden_writer.written["auth-wal-key"]
    assert len(base64.b64decode(key)) == 32
    assert walk.kubectl.argv == [["apply", "-f", "-"]]
    sent = json.loads(walk.kubectl.inputs[0])
    assert sent["metadata"] == {"name": "auth-wal", "namespace": "auth"}
    assert sent["data"] == {"gcs-key.json": gcs, "libsodium-key": base64.b64encode(key.encode()).decode()}


def test_fulfil_skips_keys_bitwarden_has(walk):
    walk.values = {"auth-wal-gcs-key": "Z2Nz", "auth-wal-key": "a2V5"}
    gcloud = Gcloud()
    got = PgDatabase(run=gcloud).fulfil(Need(), walk)
    assert got.state == "ready" and got.summary == "put Secret auth-wal in auth"
    assert gcloud.calls == [] and walk.bitwarden_writer.written == {}
    assert got.details.count("   already in Bitwarden") == 2
    assert json.loads(walk.kubectl.inputs[0])["data"]["gcs-key.json"] == "Z2Nz"


def test_fulfil_needs_a_writer_and_a_kubectl_that_takes_input(ctx):
    got = pg_database.fulfil(Need(), ctx)
    assert got.state == "unknown" and got.fix == "needs a Bitwarden write token: run the walk at a terminal"
    ctx.bitwarden_writer, ctx.values, ctx.kubectl = Writer(), {}, OldKubectl()
    got = pg_database.fulfil(Need(), ctx)
    assert got.state == "unknown" and "takes no input" in got.summary
    assert ctx.kubectl.argv == []


def test_fulfil_with_bad_settings_touches_nothing(walk):
    (walk.root / "backups" / "pg-backup.json").write_text("{}")
    gcloud = Gcloud()
    assert PgDatabase(run=gcloud).fulfil(Need(), walk).state == "drift"
    assert gcloud.calls == [] and walk.kubectl.argv == []


@pytest.mark.parametrize("gcloud, kubectl, words", [
    (Gcloud(code=1), FakeKubectl(), "gcloud refused: ERROR: denied"),
    (Gcloud(printed="Created key, but here it is: " + CANARY), FakeKubectl(), "printed something other than a key"),
    (Gcloud(), FakeKubectl(apply=Ran(1, "", "the server refused")), "kubectl apply refused the WAL Secret auth-wal"),
])
def test_fulfil_failures_are_results(walk, gcloud, kubectl, words):
    walk.kubectl = kubectl
    got = PgDatabase(run=gcloud).fulfil(Need(), walk)
    assert got.state == "unknown" and words in got.summary


def test_a_key_bitwarden_refuses_is_deleted_from_google(walk):
    walk.bitwarden_writer = Writer(fails=True)
    gcloud = Gcloud()
    got = PgDatabase(run=gcloud).fulfil(Need(), walk)
    assert got.state == "unknown" and "key k1 was deleted from Google" in got.summary
    assert gcloud.calls[1][4:7] == ["delete", "k1", "--quiet"]
    assert walk.kubectl.argv == []


def test_no_gcloud_is_a_result(walk):
    def missing(argv, **kw):
        raise FileNotFoundError("gcloud")
    got = PgDatabase(run=missing).fulfil(Need(), walk)
    assert got.state == "unknown" and got.summary == "gcloud is not installed"


def test_a_fault_in_fulfil_is_a_result(walk):
    walk.values = types.SimpleNamespace()  # no .get
    got = pg_database.fulfil(Need(), walk)
    assert got.state == "unknown" and got.summary == "fulfil failed (AttributeError)"


def every_field(got) -> str:
    return json.dumps([got.state, got.summary, got.fix, got.details, got.checked_at, got.seconds])


@pytest.mark.parametrize("case", ["made", "known", "refused"])
def test_the_canary_never_shows_and_reaches_kubectl_on_stdin(walk, capsys, case):
    """A key's value is in no Result field, no output, and no argv: kubectl gets it on its input."""
    gcloud = Gcloud()
    if case == "known":
        walk.values = {"auth-wal-gcs-key": CANARY, "auth-wal-key": CANARY}
    if case == "refused":
        walk.values = {"auth-wal-gcs-key": CANARY}
        walk.bitwarden_writer = Writer(fails=True)
        walk.kubectl = FakeKubectl(apply=Ran(1, CANARY, CANARY))
    got = PgDatabase(run=gcloud).fulfil(Need(), walk)
    checked = pg_database.check(Need(), walk)
    out = capsys.readouterr()
    for text in (every_field(got), every_field(checked), out.out, out.err):
        assert CANARY not in text
        assert base64.b64encode(CANARY.encode()).decode()[:20] not in text
    for argv in walk.kubectl.argv + gcloud.calls:
        assert not any(CANARY in arg for arg in argv)
    if case != "refused":
        sent = walk.kubectl.inputs[walk.kubectl.argv.index(["apply", "-f", "-"])]
        data = json.loads(sent)["data"]
        if case == "known":
            assert data["gcs-key.json"] == CANARY
        else:
            assert CANARY in base64.b64decode(data["gcs-key.json"]).decode()


def test_make_keys_says_what_setup_secrets_said():
    from pg_backup.config import load
    config = load(EXAMPLE)
    out, calls = [], []
    keys = Keys(lambda name: name == "auth-wal-key", lambda name: calls.append(("account", name)),
                lambda name, note: calls.append(("encryption", name)), lambda: calls.append(("put",)))
    assert make_keys(config, keys, say=out.append) == ["made auth-wal-gcs-key", "put Secret auth-wal in auth"]
    assert out == ["== 1. The bucket's key, auth-wal-gcs-key", "== 2. WAL-G's encryption key, auth-wal-key",
                   "   already in Bitwarden", "== 3. Into the cluster, as the Secret auth-wal", "   done"]
    assert calls == [("account", "auth-wal-gcs-key"), ("put",)]


def test_the_entry_point_names_the_instance():
    text = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text()
    assert 'pg-database = "pg_backup.need:pg_database"' in text
    assert isinstance(module.pg_database, PgDatabase)
