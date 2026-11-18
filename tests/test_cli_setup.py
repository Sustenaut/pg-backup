"""Steps for pg-backup's command line, the server it reaches over ssh, and setup-secrets."""

import shlex
import sys

from pytest_bdd import given, parsers, scenarios, when

from pg_backup import cli
from pg_backup.config import load
from pg_backup.restore import Stop
from pg_backup.server import Server, ServerError
from pg_backup.setup import push

scenarios("cli-setup.feature", "setup-secrets.feature")


@when(parsers.parse('pg-backup runs "{command}"'))
def pg_backup_runs(ctx, command):
    ctx.attempt(lambda: sys.exit(cli.main(shlex.split(command))))


@given("every command's function only says its name")
def functions_say_names(monkeypatch):
    for name in ("restore", "drill", "clean", "promote", "setup", "push", "wait_until_ready"):
        monkeypatch.setattr(cli, name, lambda *a, _n=name, **k: print(_n) or 0)


@given(parsers.parse('restoring stops with exit {code:d}, saying "{reason}"'))
def restoring_stops(monkeypatch, code, reason):
    def stop(*a, **k):
        raise Stop(code, reason)

    monkeypatch.setattr(cli, "restore", stop)


@given(parsers.parse('restoring fails on the server with "{what}", saying "{output}"'))
def restoring_fails(monkeypatch, what, output):
    def broken(*a, **k):
        raise ServerError(what, output)

    monkeypatch.setattr(cli, "restore", broken)


@when(parsers.parse('kubectl runs on the server "{host}" with "{words}"'))
def kubectl_runs(ctx, host, words):
    ctx.attempt(lambda: Server(host).kubectl(*shlex.split(words)))


@when(parsers.parse('the server "{host}" is asked whether kubectl "{words}" is ok'))
def kubectl_ok(ctx, host, words):
    ctx.attempt(lambda: Server(host).ok(*shlex.split(words)))


@when(parsers.parse('the server "{host}" runs "{command}" with the input "{text}"'))
def server_runs_with_input(ctx, host, command, text):
    ctx.attempt(lambda: Server(host).run(command, input=text))


@when(parsers.parse('the server "{host}" runs "{command}" unchecked'))
def server_runs_unchecked(ctx, host, command):
    ctx.attempt(lambda: Server(host).run(command, check=False))


@when(parsers.parse('the server "{host}" pipes "{source}" into "{sink}"'))
def server_pipes(ctx, host, source, sink):
    ctx.attempt(lambda: Server(host).pipe(tuple(shlex.split(source)), tuple(shlex.split(sink))))


@when("the WAL Secret is put in the cluster")
def secret_put(ctx):
    ctx.attempt(lambda: push(load(), ctx.server))
