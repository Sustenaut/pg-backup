"""pg-backup: a PostgreSQL database's WAL-G archive on a k3s server, restored, drilled and put back."""

from importlib.metadata import PackageNotFoundError, version

try:
    # The installed wheel's own version: the one pubrel set in pyproject.toml when it released it.
    __version__ = version("pg-backup")
except PackageNotFoundError:  # run from a checkout's src/, as bin/pg-backup and the tests do
    __version__ = "unreleased"
