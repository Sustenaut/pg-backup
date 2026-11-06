import base64
import json
import subprocess
import sys
from pathlib import Path

import pytest

from pg_backup import cli
from pg_backup.restore import Stop
from pg_backup.server import Server, ServerError
from pg_backup.setup import env_name, push, pubkit_wheel, secret_manifest, setup

from conftest import EXAMPLE


def test_check_reads_the_file(config, capsys):
    assert cli.main(["check"]) == 0
    assert "valid: auth on sustenaut" in capsys.readouterr().out


def test_a_bad_file_is_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["drill"]) == 2
    assert "no backups/pg-backup.json" in capsys.readouterr().err


@pytest.mark.parametrize("argv, called", [
    (["restore", "--to", "latest"], "restore"),
    (["drill"], "drill"),
    (["clean", "auth-restore-20261106070216"], "clean"),
    (["promote", "auth-restore-20261106070216"], "promote"),
    (["setup-secrets"], "setup"),
    (["setup-secrets", "--push"], "push"),
])
def test_each_command_runs_its_function(config, monkeypatch, argv, called):
    seen = []
    for name in ("restore", "drill", "clean", "promote", "setup", "push"):
        monkeypatch.setattr(cli, name, lambda *a, _n=name, **k: seen.append(_n) or 0)
    assert cli.main(argv) == 0
    assert seen == [called]


def test_stops_and_server_errors_become_exit_codes(config, monkeypatch, capsys):
    def stop(*a, **k):
        raise Stop(3, "too early")

    def broken(*a, **k):
        raise ServerError("ssh failed", "Permission denied")

    monkeypatch.setattr(cli, "restore", stop)
    assert cli.main(["restore", "--to", "latest"]) == 3
    monkeypatch.setattr(cli, "restore", broken)
    assert cli.main(["restore", "--to", "latest"]) == 1
    assert "Permission denied" in capsys.readouterr().err


def test_the_module_runs_as_a_program(config):
    src = Path(__file__).resolve().parent.parent / "src"
    done = subprocess.run([sys.executable, "-m", "pg_backup", "--version"], capture_output=True, text=True,
                          env={"PYTHONPATH": str(src)})
    assert done.stdout.startswith("pg-backup ")


def test_the_server_quotes_each_argument(monkeypatch):
    seen = {}

    def fake_run(argv, input=None, capture_output=True):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, seen.get("code", 0), b"out", b"err")

    monkeypatch.setattr(subprocess, "run", fake_run)
    server = Server("sustenaut")
    assert server.kubectl("-n", "auth", "exec", "x", "--", "psql", "-c", "select 'a b'") == "out"
    assert seen["argv"][:4] == ["ssh", "-o", "BatchMode=yes", "root@sustenaut"]
    assert seen["argv"][4] == "k3s kubectl -n auth exec x -- psql -c 'select '\"'\"'a b'\"'\"''"
    assert server.ok("get", "pods")
    seen["code"] = 1
    assert not server.ok("get", "pods")
    with pytest.raises(ServerError) as error:
        server.run("false", input="x")
    assert error.value.output == "outerr"
    assert server.run("false", check=False) == "out"


def test_the_secret_is_made_from_the_environment(config, server):
    environ = {"AUTH_WAL_GCS_KEY": "Z2Nz", "AUTH_WAL_KEY": "secret"}
    push(config, server, environ)
    sent = json.loads(server.inputs[-1])
    assert sent["metadata"] == {"name": "auth-wal", "namespace": "auth"}
    assert sent["data"] == {"gcs-key.json": "Z2Nz", "libsodium-key": base64.b64encode(b"secret").decode()}
    assert server.calls[-1] == "k3s kubectl apply -f -"
    with pytest.raises(Stop, match="AUTH_WAL_KEY is not set"):
        push(config, server, {"AUTH_WAL_GCS_KEY": "x"})
    assert env_name("auth-wal-gcs-key") == "AUTH_WAL_GCS_KEY"
    assert secret_manifest(config, "a", "b")["type"] == "Opaque"


class Runs:
    def __init__(self, failing=()):
        self.failing = failing
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        failed = any(word in " ".join(argv) for word in self.failing)
        return subprocess.CompletedProcess(argv, 1 if failed else 0)


@pytest.fixture
def repo(tmp_path, config):
    (tmp_path / "pubkit").write_text('exec uvx --from "https://github.com/Sustenaut/pubkit/releases/download/v0.30.0/pubkit-0.30.0-py3-none-any.whl" pubkit "$@"\n')
    (tmp_path / "with-secrets").write_text("")
    return tmp_path


def test_setup_makes_missing_keys_then_pushes(config, server, repo):
    runs = Runs(failing=["test -n"])  # neither key is in Bitwarden yet
    out = []
    setup(config, server, repo, say=out.append, run=runs, which=lambda t: "/usr/bin/" + t)
    joined = [" ".join(c) for c in runs.calls]
    assert any("service-account-key create --account sustenaut-auth-wal@" in c for c in joined)
    assert any(c.startswith("uvx --from https://github.com/Sustenaut/pubkit/") and c.endswith("auth-wal-key " + c.split("auth-wal-key ", 1)[1]) for c in joined)
    assert joined[-1] == "./with-secrets ./backups/pg-backup setup-secrets --push"
    assert out[-1] == "   done"


def test_setup_keeps_keys_already_there(config, server, repo):
    runs = Runs()
    out = []
    setup(config, server, repo, say=out.append, run=runs, which=lambda t: t)
    assert out.count("   already in Bitwarden") == 2
    assert pubkit_wheel(repo).endswith("pubkit-0.30.0-py3-none-any.whl")


@pytest.mark.parametrize("failing, which, message", [
    ([], lambda t: None if t == "bws" else t, "bws is not installed"),
    (["buckets describe"], lambda t: t, "apply the Terraform"),
    (["test -n", "service-account-key"], lambda t: t, "couldn't make auth-wal-gcs-key"),
    (["test -n", "uvx"], lambda t: t, "couldn't make auth-wal-key"),
    (["--push"], lambda t: t, "couldn't put the Secret"),
])
def test_setup_stops_on_each_failure(config, server, repo, failing, which, message):
    with pytest.raises(Stop, match=message):
        setup(config, server, repo, say=lambda s: None, run=Runs(failing), which=which)


def test_setup_needs_the_repository_scripts_and_the_server(config, server, tmp_path):
    with pytest.raises(Stop, match="no ./pubkit"):
        setup(config, server, tmp_path, say=lambda s: None, run=Runs(), which=lambda t: t)
    (tmp_path / "pubkit").write_text("no wheel here")
    (tmp_path / "with-secrets").write_text("")
    server.on(r"^true$", ServerError("down"))
    with pytest.raises(Stop, match="doesn't answer"):
        setup(config, server, tmp_path, say=lambda s: None, run=Runs(), which=lambda t: t)
    with pytest.raises(Stop, match="no pubkit wheel"):
        pubkit_wheel(tmp_path)


class FakeProcess:
    def __init__(self, code, err=b""):
        import io
        self.stdout = io.BytesIO(b"dump")
        self.stderr = io.BytesIO(err)
        self.returncode = code

    def wait(self):
        return self.returncode


@pytest.mark.parametrize("first, second, message", [
    (0, 0, None),
    (1, 0, "kubectl -n ns exec restore … failed"),
    (0, 1, "kubectl -n auth exec -i … failed"),
])
def test_the_server_pipes_one_kubectl_into_another(monkeypatch, first, second, message):
    seen = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **k: seen.append(argv) or FakeProcess(first, b"broke"))

    def fake_run(argv, stdin=None, capture_output=True):
        seen.append(argv)
        return subprocess.CompletedProcess(argv, second, b"", b"pg_restore: error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    server = Server("sustenaut")
    source, sink = ("-n", "ns", "exec", "restore", "--", "pg_dump"), ("-n", "auth", "exec", "-i", "p", "--", "pg_restore")
    if message is None:
        server.pipe(source, sink)
    else:
        with pytest.raises(ServerError, match=message) as error:
            server.pipe(source, sink)
        assert error.value.output in ("broke", "pg_restore: error")
    assert seen[0][4] == "k3s kubectl -n ns exec restore -- pg_dump"
    assert seen[1][4] == "k3s kubectl -n auth exec -i p -- pg_restore"
