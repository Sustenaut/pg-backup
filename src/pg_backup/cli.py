"""pg-backup's command line."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .config import ConfigError, load
from .drill import drill, wait_until_ready
from .restore import Stop, clean, promote, restore
from .server import Server, ServerError
from .setup import push, setup

USAGE = """pg-backup: a PostgreSQL database's WAL-G archive, restored, drilled and put back.
It reads backups/pg-backup.json in the repository root, checked against the schema first."""


def parser() -> argparse.ArgumentParser:
    top = argparse.ArgumentParser(prog="pg-backup", description=USAGE)
    top.add_argument("--version", action="version", version=f"pg-backup {__version__}")
    commands = top.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="check backups/pg-backup.json, touching nothing")
    one = commands.add_parser("restore", help="restore into a scratch namespace, and check it")
    one.add_argument("--to", required=True, help="a moment with its zone, such as '2026-11-05 14:32:00+00', or latest")
    one = commands.add_parser("drill", help="restore to 10 minutes ago, timed, then delete it")
    one.add_argument("--to", help="another moment than 10 minutes ago")
    one.add_argument("--wait", action="store_true",
                     help="right after a merge: wait for the rollout and a base backup old enough, then drill")
    one = commands.add_parser("clean", help="delete a scratch restore")
    one.add_argument("namespace")
    one = commands.add_parser("promote", help="put a checked scratch restore back as the live database")
    one.add_argument("namespace")
    one = commands.add_parser("setup-secrets", help="make the archive's keys in Bitwarden, once, and put them in the cluster")
    one.add_argument("--push", action="store_true", help=argparse.SUPPRESS)
    return top


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load()
        if args.command == "check":
            print(f"backups/pg-backup.json is valid: {config.namespace} on {config.server}")
            return 0
        server = Server(config.server)
        if args.command == "restore":
            restore(config, server, args.to)
        elif args.command == "drill":
            if args.wait:
                wait_until_ready(config, server)
            return drill(config, server, args.to)
        elif args.command == "clean":
            clean(config, server, args.namespace)
        elif args.command == "promote":
            promote(config, server, args.namespace)
        elif args.push:
            push(config, server)
        else:
            setup(config, server)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    except Stop as error:
        print(error, file=sys.stderr)
        return error.code
    except ServerError as error:
        print(f"{error}\n{error.output}".rstrip(), file=sys.stderr)
        return 1
    return 0
