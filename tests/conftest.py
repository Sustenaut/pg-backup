"""A fake server: answers kubectl and shell lines by the first matching pattern, and keeps
what it was asked, so the tests read like the server's side of a run."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from pg_backup.config import load
from pg_backup.server import ServerError

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "pg-backup.json"


class FakeServer:
    def __init__(self):
        self.answers: list[tuple[str, object]] = []
        self.calls: list[str] = []
        self.inputs: list[str | None] = []

    def on(self, pattern: str, answer) -> "FakeServer":
        """answer: text to print, a ServerError to raise, or a function of the command line."""
        self.answers.insert(0, (pattern, answer))
        return self

    def run(self, command: str, input=None, check: bool = True) -> str:
        self.calls.append(command)
        self.inputs.append(input.decode() if isinstance(input, bytes) else input)
        for pattern, answer in self.answers:
            if re.search(pattern, command):
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

    def ran(self, pattern: str) -> bool:
        return any(re.search(pattern, call) for call in self.calls)


ROWS = "family 4\nmaster 2\nsustenaut 0\nteam 2"
SECRET = json.dumps({"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "data": {"libsodium-key": "a2V5"},
                     "metadata": {"name": "auth-wal", "namespace": "auth", "uid": "x"}})
BACKUPS = json.dumps([{"backup_name": "base_1", "time": "2026-11-06T06:14:28.115Z"},
                      {"backup_name": "base_2", "time": "2026-11-06T07:01:00Z"}])


@pytest.fixture
def config(tmp_path, monkeypatch):
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "pg-backup.json").write_text(EXAMPLE.read_text())
    monkeypatch.chdir(tmp_path)
    return load()


@pytest.fixture
def server():
    """A healthy server: one live database, an archive with two base backups."""
    fake = FakeServer()
    fake.on(r"get statefulset postgres", "ghcr.io/sustenaut/pg-backup:0.1.0-pg16")
    fake.on(r"get secret auth-wal -o json", SECRET)
    fake.on(r"command -v wal-g", "/usr/local/bin/wal-g\n")
    fake.on(r"backup-list --json", BACKUPS)
    fake.on(r"pg_is_in_recovery", "f\n")
    fake.on(r"group by r\.name", ROWS + "\n")
    fake.on(r"get deployment keycloak", "1")
    fake.on(r"recovery_target_time", "'2026-11-06 07:05:00+00'\n")
    fake.on(r"extract\(epoch from last_archived_time\)", "4102444800\n")  # 2100: the archive covers any moment
    fake.on(r"status >/dev/null && echo up", "up\n")
    return fake


@pytest.fixture(autouse=True)
def utc(monkeypatch):
    """People's times are this machine's zone: UTC in the tests, unless one sets another."""
    import time
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()
