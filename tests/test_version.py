"""Steps for pg-backup's version, and the one the runner consumer's copy pins."""

import re
import tomllib
from pathlib import Path

import pg_backup
from pytest_bdd import scenarios, when

scenarios("version.feature")

ROOT = Path(__file__).resolve().parent.parent


@when("the version consumer/pg-backup pins is compared with pyproject.toml's")
def pins_compared(ctx):
    """consumer/pg-backup is what a repository copies to backups/pg-backup: it must run the
    release it ships in. pubrel sets pyproject.toml's version on a release pull request, so
    this fails there until consumer/pg-backup's version= line is moved to match."""

    def compare():
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        pinned = re.search(r"^version=(\S+)$", (ROOT / "consumer" / "pg-backup").read_text(), re.M).group(1)
        return {"consumer/pg-backup": pinned, "pyproject.toml": project, "the same": pinned == project}

    ctx.attempt(compare)


@when("pg-backup's version is read")
def version_read(ctx):
    ctx.attempt(lambda: pg_backup.__version__)
