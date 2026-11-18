"""Steps for restoring a backup, cleaning a scratch namespace and promoting one."""

import json
import time

from pytest_bdd import given, parsers, scenarios, when

from pg_backup.config import load
from pg_backup.restore import choose_backup, clean, oldest_backup, parse_target, promote, restore

from conftest import BACKUPS

scenarios("restore.feature")


@when(parsers.parse('the moment "{text}" is read'))
def moment_read(ctx, text):
    ctx.attempt(lambda: parse_target(text))


@when(parsers.parse('the base backup for "{moment}" is chosen from the healthy archive'))
def backup_chosen(ctx, moment):
    ctx.attempt(lambda: choose_backup(json.loads(BACKUPS), parse_target(moment)))


@when("the oldest base backup in the healthy archive is found")
def oldest_in_healthy(ctx):
    ctx.attempt(lambda: oldest_backup(json.loads(BACKUPS)))


@when("the oldest base backup in an empty archive is found")
def oldest_in_empty(ctx):
    ctx.attempt(lambda: oldest_backup([]))


@when(parsers.parse('I restore to "{moment}"'))
def restore_to(ctx, moment):
    time.tzset()
    clock = getattr(ctx, "clock", time.monotonic)
    ctx.attempt(lambda: restore(load(), ctx.server, moment, clock=clock, sleep=lambda s: None))


@when(parsers.parse('I clean "{namespace}"'))
def clean_namespace(ctx, namespace):
    ctx.attempt(lambda: clean(load(), ctx.server, namespace))


@given(parsers.parse('the person at the terminal types "{answer}"'))
def types(ctx, answer):
    ctx.terminal, ctx.answer = True, answer


@given("there is no terminal")
def no_terminal(ctx):
    ctx.terminal, ctx.answer = False, ""


@when(parsers.parse('I promote "{namespace}"'))
def promote_namespace(ctx, namespace):
    ctx.attempt(lambda: promote(load(), ctx.server, namespace, ask=lambda prompt: ctx.answer,
                                interactive=lambda: ctx.terminal))
