"""pg-backup.json: read, and checked against the schema this release ships, before anything
touches the server."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

FILE = Path("backups/pg-backup.json")


class ConfigError(Exception):
    """pg-backup.json is missing, isn't JSON, or doesn't match the schema."""


def schema() -> dict:
    return json.loads(resources.files(__package__).joinpath("pg-backup.schema.json").read_text())


def _errors(value, rule: dict, root: dict, where: str) -> list[str]:
    """What is wrong with value under rule: the part of JSON Schema the shipped schema uses
    ($ref within the file, type, required, properties, additionalProperties false, pattern,
    minLength, items, minItems). A rule it doesn't know is an error, so the schema can't
    quietly ask for more than is checked."""
    if "$ref" in rule:
        name = rule["$ref"].removeprefix("#/$defs/")
        return _errors(value, root["$defs"][name], root, where)
    known = {"type", "required", "properties", "additionalProperties", "pattern", "minLength",
             "items", "minItems", "description", "title", "$schema", "$id", "$defs"}
    unknown = set(rule) - known
    if unknown:
        raise ConfigError(f"the schema uses {sorted(unknown)}, which this release doesn't check")
    kind = rule.get("type")
    types = {"object": dict, "string": str, "array": list}
    if kind and not isinstance(value, types[kind]):
        return [f"{where}: should be {'an' if kind in ('object', 'array') else 'a'} {kind}"]
    found: list[str] = []
    if kind == "object":
        for key in rule.get("required", []):
            if key not in value:
                found.append(f"{where}: {key} is missing")
        properties = rule.get("properties", {})
        for key, item in value.items():
            if key in properties:
                found += _errors(item, properties[key], root, f"{where}.{key}")
            elif rule.get("additionalProperties") is False:
                found.append(f"{where}: {key} isn't a setting (a typo?)")
    elif kind == "string":
        if len(value) < rule.get("minLength", 0):
            found.append(f"{where}: is empty")
        if "pattern" in rule and not re.search(rule["pattern"], value):
            found.append(f"{where}: {value!r} doesn't match {rule['pattern']}")
    elif kind == "array":
        if len(value) < rule.get("minItems", 0):
            found.append(f"{where}: needs at least {rule['minItems']}")
        for index, item in enumerate(value):
            found += _errors(item, rule.get("items", {}), root, f"{where}[{index}]")
    return found


def validate(data) -> list[str]:
    rules = schema()
    return _errors(data, rules, rules, "pg-backup.json")


@dataclass(frozen=True)
class Config:
    server: str
    namespace: str
    statefulset: str
    container: str
    database: str
    user: str
    wal: dict
    dump: dict
    app: dict
    check: dict

    @property
    def pod(self) -> str:
        return f"{self.statefulset}-0"

    @property
    def scratch_pattern(self) -> str:
        return rf"^{re.escape(self.namespace)}-restore-[0-9]{{14}}$"

    def is_scratch(self, namespace: str) -> bool:
        return re.match(self.scratch_pattern, namespace) is not None


def load(path: Path = FILE) -> Config:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        raise ConfigError(f"no {path}: pg-backup runs in the repository root, where backups/pg-backup.json is") from None
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} isn't JSON: {error}") from None
    problems = validate(data)
    if problems:
        raise ConfigError("\n".join([f"{path} doesn't match pg-backup's schema:", *problems]))
    data.pop("$schema", None)
    return Config(**data)
