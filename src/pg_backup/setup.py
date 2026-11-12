"""The WAL archive's two keys, made once into Bitwarden, and put in the cluster as the WAL
Secret: the bucket account's key, which `./pubsec service-account-key create` writes itself,
and WAL-G's libsodium key, 32 random bytes written with pubsec's own Bitwarden code, in memory.
Losing the second makes the archive unreadable, so it is kept nowhere but Bitwarden and the
cluster. A key already in Bitwarden is not made again, so running this again (after a rebuild)
only puts the Secret back. No value is printed or put on a command line.

It runs in the consuming repository's root, through its ./pubsec and ./with-secrets."""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import Config
from .restore import Stop
from .server import Server, ServerError

WRITE_KEY = '''
import base64, getpass, os, sys
from pathlib import Path
from pubsec.bitwarden import WriteFailed, ensure, write
from pubsec.github_app import Stop
from pubsec.service_account_key import bitwarden_project, find_project, find_secret
secret, note = sys.argv[1], sys.argv[2]
problem = ensure()
if problem:
    sys.exit(problem)
try:
    name = bitwarden_project(Path.cwd(), None)
    token = getpass.getpass(f"   A Bitwarden access token with write access to project {name} (hidden): ")
    project, organization = find_project(token, name)
    find_secret(token, organization, project, name, secret, False)  # stops if it is there
    write(token, organization, project, secret, base64.b64encode(os.urandom(32)).decode(), note)
except (Stop, WriteFailed) as error:
    sys.exit(f"   {error}")
print(f"   written to {name}")
'''


def env_name(secret: str) -> str:
    """The variable ./with-secrets puts a Bitwarden secret in."""
    return secret.upper().replace("-", "_")


def secret_manifest(config: Config, gcs_key_b64: str, encryption_key: str) -> dict:
    return {
        "apiVersion": "v1", "kind": "Secret", "type": "Opaque",
        "metadata": {"name": config.wal["secret"], "namespace": config.namespace},
        "data": {"gcs-key.json": gcs_key_b64, "libsodium-key": base64.b64encode(encryption_key.encode()).decode()},
    }


def push(config: Config, server: Server, environ: dict = os.environ) -> None:
    """Run through ./with-secrets: both keys are in the environment."""
    names = [env_name(config.wal["gcs_key_secret"]), env_name(config.wal["encryption_key_secret"])]
    for name in names:
        if not environ.get(name):
            raise Stop(1, f"{name} is not set: is it in Bitwarden?")
    manifest = secret_manifest(config, environ[names[0]], environ[names[1]])
    server.run("k3s kubectl apply -f -", input=json.dumps(manifest))


def pubsec_wheel(root: Path) -> str:
    """The pubsec release ./pubsec runs, whose Bitwarden code writes the encryption key."""
    found = re.search(r"https://github\.com/Sustenaut/pubsec/releases/download/[^\"\s]*\.whl", (root / "pubsec").read_text())
    if not found:
        raise Stop(1, "no pubsec wheel in ./pubsec")
    return found.group(0)


def setup(config: Config, server: Server, root: Path = Path("."), say: Callable[[str], None] = print,
          run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
          which: Callable[[str], str | None] = shutil.which) -> None:
    for tool in ("gcloud", "ssh", "uvx", "bws"):
        if not which(tool):
            raise Stop(1, f"{tool} is not installed")
    for script in ("pubsec", "with-secrets"):
        if not (root / script).exists():
            raise Stop(1, f"no ./{script}: run this from the repository's root")
    bucket = f"gs://{config.wal['bucket']}"
    if run(["gcloud", "storage", "buckets", "describe", bucket], capture_output=True).returncode != 0:
        raise Stop(1, f"no {bucket} yet: apply the Terraform that makes it first")
    try:
        server.run("true")
    except ServerError:
        raise Stop(1, f"root@{config.server} doesn't answer: are you on the tailnet?") from None

    def in_bitwarden(secret: str) -> bool:
        check = run(["./with-secrets", "sh", "-c", f'test -n "${{{env_name(secret)}:-}}"'], capture_output=True, cwd=root)
        return check.returncode == 0

    def make_account_key(secret: str) -> None:
        if run(["./pubsec", "service-account-key", "create", "--account", config.wal["account"], "--secret", secret], cwd=root).returncode != 0:
            raise Stop(1, f"couldn't make {secret}")

    def make_encryption_key(secret: str, note: str) -> None:
        if run(["uvx", "--from", pubsec_wheel(root), "python", "-c", WRITE_KEY, secret, note], cwd=root).returncode != 0:
            raise Stop(1, f"couldn't make {secret}")

    def put_secret() -> None:
        if run(["./with-secrets", "./backups/pg-backup", "setup-secrets", "--push"], cwd=root).returncode != 0:
            raise Stop(1, "couldn't put the Secret in the cluster")

    make_keys(config, Keys(in_bitwarden, make_account_key, make_encryption_key, put_secret), say)


@dataclass(frozen=True)
class Keys:
    """How make_keys reaches Bitwarden and the cluster: through ./pubsec and ./with-secrets for
    setup-secrets, through the deploy walk's Bitwarden writer and kubectl for the pg.database need.
    Each make or put raises Stop when it fails, with no value in its message."""
    in_bitwarden: Callable[[str], bool]
    make_account_key: Callable[[str], None]  # the Bitwarden secret's name
    make_encryption_key: Callable[[str, str], None]  # the name, and the secret's note
    put_secret: Callable[[], None]


def make_keys(config: Config, keys: Keys, say: Callable[[str], None] = print) -> list[str]:
    """setup-secrets' three steps: each key made unless Bitwarden has it, then the WAL Secret put
    in the cluster. Returns what it did, in words, with no value."""
    done = []
    gcs, key = config.wal["gcs_key_secret"], config.wal["encryption_key_secret"]
    say(f"== 1. The bucket's key, {gcs}")
    if keys.in_bitwarden(gcs):
        say("   already in Bitwarden")
    else:
        keys.make_account_key(gcs)
        done.append(f"made {gcs}")
    say(f"== 2. WAL-G's encryption key, {key}")
    if keys.in_bitwarden(key):
        say("   already in Bitwarden")
    else:
        keys.make_encryption_key(key, encryption_note(config))
        done.append(f"made {key}")
    say(f"== 3. Into the cluster, as the Secret {config.wal['secret']}")
    keys.put_secret()
    done.append(f"put Secret {config.wal['secret']} in {config.namespace}")
    say("   done")
    return done


def encryption_note(config: Config) -> str:
    return f"WAL-G's encryption key for the write-ahead log archive gs://{config.wal['bucket']}/{config.wal['prefix']}. Without it the archive can't be read."
