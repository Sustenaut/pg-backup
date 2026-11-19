"""Steps for pg-backup.json: changed setting by setting, checked, and loaded."""

import json
from pathlib import Path

from pubtest.core import value
from pytest_bdd import given, parsers, scenarios, when

from pg_backup.config import _errors, load, schema, validate

scenarios("config.feature")

FILE = Path("backups/pg-backup.json")


def _change(change):
    data = json.loads(FILE.read_text())
    change(data)
    FILE.write_text(json.dumps(data))


def _at(data, key):
    *path, last = key.split(".")
    for part in path:
        data = data[part]
    return data, last


@given(parsers.re(r'pg-backup\.json\'s "(?P<key>[^"]+)" is "(?P<text>.*)"'))
def setting_is(key, text):
    def change(data):
        where, last = _at(data, key)
        where[last] = value(text)
    _change(change)


@given(parsers.parse('pg-backup.json\'s "{key}" is empty'))
def setting_is_empty(key):
    def change(data):
        where, last = _at(data, key)
        where[last] = ""
    _change(change)


@given(parsers.parse('pg-backup.json\'s "{key}" is renamed "{name}"'))
def setting_renamed(key, name):
    def change(data):
        where, last = _at(data, key)
        where[name] = where.pop(last)
    _change(change)


@when("pg-backup.json is checked against the schema")
def checked(ctx):
    ctx.attempt(lambda: validate(json.loads(FILE.read_text())))


@when(parsers.parse('the value "{text}" is checked against the rule "{rule}"'))
def checked_against_rule(ctx, text, rule):
    ctx.attempt(lambda: _errors(json.loads(text), json.loads(rule), {}, "x"))


@when("the package's schema is read")
def schema_read(ctx):
    ctx.attempt(schema)


@when("the settings are loaded")
def loaded(ctx):
    ctx.attempt(load)


@when(parsers.parse('I ask whether "{namespace}" is a scratch namespace'))
def is_scratch(ctx, namespace):
    ctx.attempt(lambda: load().is_scratch(namespace))


@when(parsers.parse('the scratch namespace "{namespace}" is named'))
def scratch_named(ctx, namespace):
    ctx.attempt(lambda: load().scratch(namespace))


@when("the scratch namespace named is:")
def scratch_named_text(ctx, docstring):
    ctx.attempt(lambda: load().scratch(docstring))
