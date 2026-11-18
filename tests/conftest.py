"""The world every feature shares: the k3s server pg-backup reaches over ssh, faked in-process,
its fixed moment, and the few checks pubtest has no step for.

The fake server answers each command line by the first pattern it was told that matches (`*`
for anything, as in pubtest's patterns; the newest pattern first), and keeps each kubectl it
was asked, with its input, as a run of the stand-in "kubectl", so pubtest's stand-in steps read
the server's side of a run."""

from __future__ import annotations

import json
import shlex
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pubtest.core import dig, matches, same, show
from pubtest.steps.standins import standins
from pytest_bdd import given, parsers, then

from pg_backup.server import ServerError

ROOT = Path(__file__).resolve().parent.parent
ROWS = "family 4\nmaster 2\nsustenaut 0\nteam 2"
SECRET = json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "data": {"libsodium-key": "a2V5"},
                     "metadata": {"name": "auth-wal", "namespace": "auth", "uid": "x"}})
LIVE_CREDENTIAL = {
    "type": "external_account",
    "audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/services/providers/k3s",
    "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
    "token_url": "https://sts.googleapis.com/v1/token",
    "credential_source": {"file": "/var/run/secrets/google/token"},
    "service_account_impersonation_url": "https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                                         "sustenaut-auth-wal@sustenaut.iam.gserviceaccount.com:generateAccessToken"}
CREDENTIAL = json.dumps({"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "wal-google-credential"},
                         "data": {"credential.json": json.dumps(LIVE_CREDENTIAL)}})
BACKUPS = json.dumps([{"backup_name": "base_1", "time": "2026-11-06T06:14:28.115Z"},
                      {"backup_name": "base_2", "time": "2026-11-06T07:01:00Z"}])


def record(ctx, tmp_path, name: str, args: list[str], input: str | bytes | None = None) -> None:
    """One run of the stand-in `name`, in pubtest's log of stand-in runs."""
    if isinstance(input, bytes):
        input = input.decode()
    with standins(ctx, tmp_path).log.open("a", encoding="utf-8") as log:
        log.write(json.dumps({"name": name, "args": args, "input": input}) + "\n")


class FakeServer:
    def __init__(self, keep):
        self.answers: list[tuple[str, object]] = []
        self.keep = keep

    def on(self, pattern: str, answer) -> "FakeServer":
        """answer: text to print, a ServerError to raise, or a function of the command line."""
        self.answers.insert(0, (pattern, answer))
        return self

    def run(self, command: str, input=None, check: bool = True) -> str:
        words = shlex.split(command)
        if words[:2] == ["k3s", "kubectl"]:
            self.keep("kubectl", words[2:], input)
        else:
            self.keep(words[0], words[1:], input)
        for pattern, answer in self.answers:
            if matches(pattern, command):
                if callable(answer) and not isinstance(answer, ServerError):
                    answer = answer(command)
                if isinstance(answer, ServerError):
                    if check:
                        raise answer
                    return ""
                return answer
        return ""

    def kubectl(self, *args, input=None, check=True):
        return self.run("k3s kubectl " + shlex.join(args), input=input, check=check)

    def pipe(self, source, sink):
        self.run("k3s kubectl " + shlex.join(source))
        self.run("k3s kubectl " + shlex.join(sink))

    def ok(self, *args):
        try:
            self.kubectl(*args)
        except ServerError:
            return False
        return True


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    """People's times are this machine's zone: UTC in the tests, unless one sets another."""
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


# The world


@given("a healthy server")
def healthy_server(ctx, tmp_path):
    """One live database, an archive with two base backups."""
    ctx.server = FakeServer(lambda name, args, input: record(ctx, tmp_path, name, args, input))
    ctx.server.on("*get statefulset postgres*", "ghcr.io/sustenaut/pg-backup:0.1.0-pg16")
    ctx.server.on("*get secret auth-wal -o json*", SECRET)
    ctx.server.on("*get configmap wal-google-credential -o json*", CREDENTIAL)
    ctx.server.on("*command -v wal-g*", "/usr/local/bin/wal-g\n")
    ctx.server.on("*backup-list --json*", BACKUPS)
    ctx.server.on("*pg_is_in_recovery*", "f\n")
    ctx.server.on("*group by r.name*", ROWS + "\n")
    ctx.server.on("*get deployment keycloak*", "1")
    ctx.server.on("*recovery_target_time*", "'2026-11-06 07:05:00+00'\n")
    ctx.server.on("*extract(epoch from last_archived_time)*", "4102444800\n")  # 2100: the archive covers any moment
    ctx.server.on("*status >/dev/null && echo up*", "up\n")


@given(parsers.re(r'the server answers "(?P<pattern>[^"]+)" with "(?P<text>.*)"'))
def server_answers(ctx, pattern, text):
    ctx.server.on(pattern, text)


@given(parsers.parse('the server answers "{pattern}" with:'))
def server_answers_text(ctx, pattern, docstring):
    ctx.server.on(pattern, docstring)


@given(parsers.parse('the server answers "{pattern}" with nothing'))
def server_answers_nothing(ctx, pattern):
    ctx.server.on(pattern, "")


@given(parsers.parse('the server answers "{pattern}" in turn:'))
def server_answers_in_turn(ctx, pattern, datatable):
    """A row per answer, `\\n` for a new line; the last is given again once all are."""
    answers = [row[0].replace("\\n", "\n") for row in datatable[1:]]
    ctx.server.on(pattern, lambda command: answers.pop(0) if len(answers) > 1 else answers[0])


@given(parsers.re(r'the server fails "(?P<pattern>[^"]+)" with "(?P<what>[^"]+)"'))
def server_fails(ctx, pattern, what):
    ctx.server.on(pattern, ServerError(what))


@given(parsers.re(r'the server fails "(?P<pattern>[^"]+)" with "(?P<what>[^"]+)", saying "(?P<output>[^"]+)"'))
def server_fails_saying(ctx, pattern, what, output):
    ctx.server.on(pattern, ServerError(what, output))


FROZEN = datetime(2026, 11, 6, 7, 15, tzinfo=timezone.utc)


@given("it is 2026-11-06 07:15 UTC")
def frozen(ctx, monkeypatch):
    """The moment a scratch namespace and a pre-promote job are named by."""
    import pg_backup.restore

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return FROZEN.astimezone(tz)

    monkeypatch.setattr(pg_backup.restore, "datetime", Frozen)
    ctx.now = lambda: FROZEN


@given(parsers.parse("the clock moves {seconds:d} seconds at each reading"))
def ticking(ctx, seconds):
    ctx.clock = iter(range(0, 100000, seconds)).__next__


# The checks pubtest has no step for (each a candidate pubtest step)


def _refusal(ctx):
    ctx.refusal_read = True
    assert ctx.refused is not None, f"it was not refused: the result is {show(ctx.result)}"
    return ctx.refused.error


@then(parsers.re(r'the refusal\'s "(?P<key>[^"]+)" is "(?P<wanted>.*)"'))
def refusal_key(ctx, key, wanted):
    """The refusal's attribute at the dotted key: its code, or `__class__.__name__` for its class."""
    got = dig(_refusal(ctx), key)
    assert same(got, wanted), f"the refusal's {key} is {show(got)}, not {wanted}"


@then(parsers.parse('the refusal\'s "{key}" is:'))
def refusal_key_text(ctx, key, docstring):
    got = dig(_refusal(ctx), key)
    assert same(got, docstring), f"the refusal's {key} is {show(got)}, not:\n{docstring}"


@then("it is refused, saying:")
def refused_saying_text(ctx, docstring):
    """The refusal's message holds the docstring: for one of several lines."""
    said = str(_refusal(ctx))
    assert docstring in said, f"it was refused, saying {said!r}"


def _runs(ctx, name):
    found = getattr(ctx, "standins", None)
    return found.entries(name) if found else []


@then(parsers.parse('"{name}" read JSON with "{key}" equal to "{wanted}"'))
def read_json_key(ctx, name, key, wanted):
    """Some input the stand-in read is JSON whose value at the dotted key is the step's."""
    seen = []
    for run in _runs(ctx, name):
        try:
            got = dig(json.loads(run["input"] or ""), key)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            continue
        if same(got, wanted):
            return
        seen.append(got)
    raise AssertionError(f"no input {name} read has {key} {wanted}: {show(seen)}")


@then(parsers.parse('"{name}" was not run with arguments matching "{pattern}"'))
def not_run_matching(ctx, name, pattern):
    runs = [" ".join(run["args"]) for run in _runs(ctx, name)]
    assert not any(matches(pattern, args) for args in runs), f"{name} ran with {runs}"


@then(parsers.parse('"{name}" was run, in order, with arguments matching:'))
def run_in_order(ctx, name, docstring):
    """Each line a pattern: some runs, in this order, match them, with any others between."""
    runs = [" ".join(run["args"]) for run in _runs(ctx, name)]
    patterns = [line.strip() for line in docstring.splitlines() if line.strip()]
    at = 0
    for pattern in patterns:
        while at < len(runs) and not matches(pattern, runs[at]):
            at += 1
        assert at < len(runs), f"no run of {name} after the ones before matches {pattern!r}: {runs}"
        at += 1
