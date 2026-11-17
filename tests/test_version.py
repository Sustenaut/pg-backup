import re
import tomllib
from pathlib import Path

import pg_backup
from pytest_bdd import scenarios, then

scenarios("version.feature")

ROOT = Path(__file__).resolve().parent.parent


@then("the runner consumer's copy pins this release")
def the_runner_consumers_copy_pins_this_release():
    """consumer/pg-backup is what a repository copies to backups/pg-backup: it must run the
    release it ships in. pubrel sets pyproject.toml's version on a release pull request, so
    this fails there until consumer/pg-backup's version= line is moved to match."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    pinned = re.search(r"^version=(\S+)$", (ROOT / "consumer" / "pg-backup").read_text(), re.M).group(1)
    assert pinned == project, f"consumer/pg-backup pins {pinned}; set version={project} in it"


@then("the version is the installed package's")
def the_version_is_the_installed_package_s():
    assert pg_backup.__version__ == "unreleased"  # from src/, not installed
