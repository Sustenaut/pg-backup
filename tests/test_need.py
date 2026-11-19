"""Steps for the pg.database need type, against a fake deploy walk: a kubectl that answers by
the first key its arguments hold and keeps each run and its input as a run of the stand-in
"kubectl", a fixed clock, and a Bitwarden writer whose writes are runs of "bitwarden"."""

import importlib
import json
import sys
import tomllib
import types
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pubtest.core import value
from pytest_bdd import given, parsers, scenarios, when

from pg_backup import need as module
from pg_backup.config import load
from pg_backup.need import Result, archive_facts, archive_state, pg_database
from pg_backup.setup import Keys, make_keys

from conftest import ROOT, record

scenarios("need.feature")

NOW = datetime(2026, 11, 8, 6, 0, tzinfo=timezone.utc)


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


def keyless():
    """The StatefulSet's pod spec reaching the bucket with no key, as pg-backup's README has it."""
    mounts = [{"name": "google-token", "mountPath": "/var/run/secrets/google"},
              {"name": "wal-google-credential", "mountPath": "/etc/wal-google"}]
    env = [{"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/etc/wal-google/credential.json"}]
    return {"serviceAccountName": "postgres",
            "volumes": [{"name": "google-token", "projected": {"sources": [{"serviceAccountToken": {
                "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/services/"
                            "providers/k3s", "path": "token"}}]}},
                        {"name": "wal-google-credential", "configMap": {"name": "wal-google-credential"}}],
            "containers": [{"name": n, "env": list(env), "volumeMounts": list(mounts)} for n in ("postgres", "wal-g")]}


class FakeKubectl:
    """Answers by the first key its arguments, joined, hold; keeps each run and its input."""

    def __init__(self, keep, spec):
        self.keep = keep
        self.answers = {
            "go-template": Ran(0, "libsodium-key "), "replace": Ran(0, "secret/auth-wal replaced"),
            "get statefulset": lambda: Ran(0, json.dumps({"spec": {"replicas": 1, "template": {"spec": spec}},
                                                          "status": {"readyReplicas": 1}})),
            "logs": Ran(0, "\n".join(["waiting", fact(), "wal-g: INFO: pushed", fact()])),
            "get secret": Ran(0, json.dumps({"data": {"libsodium-key": "a2V5"}})),
            "apply": Ran(0, "secret/auth-wal configured")}

    def __call__(self, args, input=None):
        self.keep("kubectl", list(args), input)
        line = " ".join(args)
        for key, answer in self.answers.items():
            if key in line:
                return answer() if callable(answer) else answer
        return Ran(1, "", "unexpected")


class OldKubectl(FakeKubectl):
    """pubrules 0.31.1's: args alone."""

    def __call__(self, args):
        return super().__call__(args)


class Writer:
    def __init__(self, keep, fails=False):
        self.keep = keep
        self.fails = fails

    def write(self, name, text, note):
        if self.fails:
            raise RuntimeError(f"bws said no to {text}")  # a message holding the value
        self.keep("bitwarden", ["write", name, note], text)


@dataclass
class Context:
    root: Path
    namespace: str = "auth"
    kubectl: object = None
    now: object = lambda: NOW
    env: dict = field(default_factory=dict)


@dataclass
class Need:
    type: str = "pg.database"
    name: str = "postgres"
    params: dict = field(default_factory=dict)


# The walk


@given(parsers.parse('a deploy walk in "{namespace}" with a healthy database'))
def walk(ctx, tmp_path, namespace):
    ctx.keep = lambda name, args, input: record(ctx, tmp_path, name, args, input)
    ctx.spec = keyless()
    ctx.walk = Context(ctx.directory, namespace, FakeKubectl(ctx.keep, ctx.spec))
    ctx.need = Need()


@given(parsers.parse('the walk deploys to "{namespace}"'))
def deploys_to(ctx, namespace):
    ctx.walk.namespace = namespace


@given(parsers.parse('the need\'s "{key}" is "{text}"'))
def need_param(ctx, key, text):
    ctx.need.params[key] = text


@given(parsers.parse('kubectl answers "{key}" with:'))
def kubectl_answers(ctx, key, datatable):
    row = dict(zip(datatable[0], datatable[1], strict=True))
    ctx.walk.kubectl.answers[key] = Ran(int(row["exit"]), row.get("stdout", ""), row.get("stderr", ""))


@given("the walk's kubectl cannot run")
def kubectl_cannot_run(ctx):
    def broken(args):
        raise OSError("no such file")
    ctx.walk.kubectl = broken


@given("the walk's kubectl takes no input")
def kubectl_takes_no_input(ctx):
    ctx.walk.kubectl = OldKubectl(ctx.keep, ctx.spec)


@given("the walk's clock is broken")
def clock_broken(ctx):
    ctx.walk.now = None  # calling it raises TypeError


@given(parsers.parse("the wal-g sidecar's status lines are the healthy one with:"))
def status_lines(ctx, datatable):
    keys = datatable[0]
    ctx.status = [fact(**{k: value(v) for k, v in zip(keys, row, strict=True)}) for row in datatable[1:]]
    ctx.walk.kubectl.answers["logs"] = Ran(0, "\n".join(ctx.status))


@given("the wal-g sidecar prints:")
def sidecar_prints(ctx, docstring):
    ctx.walk.kubectl.answers["logs"] = Ran(0, docstring)


@given(parsers.parse('the StatefulSet runs as "{account}"'))
def runs_as(ctx, account):
    ctx.spec["serviceAccountName"] = account


@given("the StatefulSet has no volumes")
def no_volumes(ctx):
    ctx.spec["volumes"] = []


def _container(ctx, name):
    return next(c for c in ctx.spec["containers"] if c["name"] == name)


@given(parsers.parse('the StatefulSet has no container "{name}"'))
def no_container(ctx, name):
    ctx.spec["containers"].remove(_container(ctx, name))


@given(parsers.parse('the StatefulSet\'s container "{name}" is named "{new}"'))
def container_named(ctx, name, new):
    _container(ctx, name)["name"] = new


@given(parsers.parse('the StatefulSet\'s container "{name}" reads its credential from "{path}"'))
def container_credential(ctx, name, path):
    _container(ctx, name)["env"] = [{"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": path}]


@given("the walk's Context comes from a module with its own Result")
def their_context(ctx, monkeypatch):
    """pubrules's walk keeps only its own Result: the one in its Context's module."""
    fake = types.ModuleType("fake_pubrules_model")

    @dataclass
    class TheirResult:
        state: str
        summary: str
        fix: str = ""
        details: list = field(default_factory=list)
        checked_at: str = ""
        seconds: float = 0.0
    TheirResult.__name__ = "Result"
    TheirResult.__module__ = "fake_pubrules_model"
    fake.Result = TheirResult

    class TheirContext(Context):
        pass
    TheirContext.__module__ = "fake_pubrules_model"
    monkeypatch.setitem(sys.modules, "fake_pubrules_model", fake)
    walk = ctx.walk
    ctx.walk = TheirContext(walk.root, walk.namespace, walk.kubectl)


@given("the walk can write to Bitwarden")
def can_write(ctx):
    ctx.walk.bitwarden_writer = Writer(ctx.keep)
    ctx.walk.values = {}


@given(parsers.parse('Bitwarden has "{name}" as "{text}"'))
def bitwarden_has(ctx, name, text):
    ctx.walk.values[name] = text


@given("Bitwarden refuses to write")
def bitwarden_refuses(ctx):
    ctx.walk.bitwarden_writer = Writer(ctx.keep, fails=True)


@given("the walk's known values can't be read")
def values_unreadable(ctx):
    ctx.walk.values = types.SimpleNamespace()  # no .get


@given("each random byte is 1")
def random_ones(monkeypatch):
    monkeypatch.setattr(module, "os", types.SimpleNamespace(urandom=lambda n: b"\x01" * n))


# The actions


@when(parsers.parse('the need type "{name}" is found through pyproject.toml\'s entry point'))
def found_by_entry_point(ctx, name):
    def resolve():
        target = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["entry-points"]["sustenaut.needs"][name]
        where, attribute = target.split(":")
        return getattr(importlib.import_module(where), attribute)
    ctx.attempt(resolve)


@when("the need type's settings are listed")
def settings_listed(ctx):
    ctx.attempt(lambda: sorted(pg_database.schema["properties"]))


@when(parsers.parse('its default name for the settings "{settings}" is asked'))
def default_name(ctx, settings):
    ctx.attempt(lambda: pg_database.default_name(json.loads(settings)))


@when("what it depends on is asked")
def depends_on(ctx):
    ctx.attempt(lambda: pg_database.depends_on(Need(), {}))


@when("the states a need may be in are listed")
def states(ctx):
    ctx.attempt(lambda: list(module.STATES))


@when("the need is checked")
def checked(ctx):
    ctx.attempt(lambda: pg_database.check(ctx.need, ctx.walk))


@when("the need is fulfilled")
def fulfilled(ctx):
    ctx.attempt(lambda: pg_database.fulfil(ctx.need, ctx.walk))


@when("the need is fulfilled, then checked again")
def fulfilled_and_checked(ctx):
    ctx.attempt(lambda: [pg_database.fulfil(ctx.need, ctx.walk), pg_database.check(ctx.need, ctx.walk)])


@when("the archive's state is read from the status lines")
def archive_read(ctx):
    ctx.attempt(lambda: list(archive_state(archive_facts("\n".join(ctx.status)), NOW)))


@when("a Result is made with a summary of 300 characters")
def long_summary(ctx):
    ctx.attempt(lambda: Result("ready", "x" * 300).summary)


@when("the keys are made, with nothing in Bitwarden")
def keys_made(ctx):
    keys = Keys(lambda name: False, lambda name, note: print(f"(made {name})"), lambda: print("(put the Secret)"))
    ctx.attempt(lambda: make_keys(load(), keys))
