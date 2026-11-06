import json

import pytest

from pg_backup.config import ConfigError, _errors, load, schema, validate

from conftest import EXAMPLE


def example():
    return json.loads(EXAMPLE.read_text())


def test_the_example_is_valid():
    assert validate(example()) == []


def test_a_typo_in_a_setting_name_is_caught():
    data = example()
    data["namespce"] = data.pop("namespace")
    problems = validate(data)
    assert "pg-backup.json: namespace is missing" in problems
    assert "pg-backup.json: namespce isn't a setting (a typo?)" in problems


def test_nested_settings_are_checked():
    data = example()
    data["wal"]["bucket"] = "gs://sustenaut-auth-wal"
    data["check"]["required"] = []
    data["app"] = "keycloak"
    problems = validate(data)
    assert any(p.startswith("pg-backup.json.wal.bucket: 'gs://") for p in problems)
    assert "pg-backup.json.check.required: needs at least 1" in problems
    assert "pg-backup.json.app: should be an object" in problems


def test_strings_and_items_are_checked():
    data = example()
    data["check"]["query"] = ""
    data["check"]["required"] = ["ok", "no spaces"]
    problems = validate(data)
    assert "pg-backup.json.check.query: is empty" in problems
    assert any(p.startswith("pg-backup.json.check.required[1]") for p in problems)
    assert validate([]) == ["pg-backup.json: should be an object"]


def test_a_schema_rule_this_release_cant_check_stops():
    with pytest.raises(ConfigError, match="doesn't check"):
        _errors(1, {"maximum": 3}, {}, "x")


def test_the_schema_ships_with_the_package():
    assert schema()["title"] == "pg-backup.json"


def test_load_reads_the_repository_file(config):
    assert config.pod == "postgres-0"
    assert config.is_scratch("auth-restore-20261106070216")
    assert not config.is_scratch("auth")
    assert not config.is_scratch("other-restore-20261106070216")
    assert config.scratch("auth-restore-00261106070216") == "auth-restore-00261106070216"
    assert config.scratch("auth-restore-2026110607021") is None
    assert config.scratch("auth-restore-20261106070216\n") is None
    assert config.scratch("--namespace=auth") is None


def test_load_says_what_is_wrong(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="no backups/pg-backup.json"):
        load()
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "pg-backup.json").write_text("{")
    with pytest.raises(ConfigError, match="isn't JSON"):
        load()
    (tmp_path / "backups" / "pg-backup.json").write_text('{"server": "sustenaut"}')
    with pytest.raises(ConfigError, match="namespace is missing"):
        load()


def test_a_schema_reference_is_kept(tmp_path, monkeypatch):
    data = example() | {"$schema": "https://example/pg-backup.schema.json"}
    monkeypatch.chdir(tmp_path)
    (tmp_path / "backups").mkdir()
    (tmp_path / "backups" / "pg-backup.json").write_text(json.dumps(data))
    assert load().namespace == "auth"
