Feature: The pg-backup.json configuration
  A configuration is read and checked against its schema; each error names its place.

  Background:
    Given a scratch directory
    And a file "backups/pg-backup.json" copied from "examples/pg-backup.json"

  Scenario: The example is valid
    When pg-backup.json is checked against the schema
    Then the result is empty

  Scenario: A typo in a setting name is caught
    Given pg-backup.json's "namespace" is renamed "namespce"
    When pg-backup.json is checked against the schema
    Then the result is:
      """
      ["pg-backup.json: namespace is missing", "pg-backup.json: namespce isn't a setting (a typo?)"]
      """

  Scenario: Nested settings are checked
    Given pg-backup.json's "wal.bucket" is "gs://sustenaut-auth-wal"
    And pg-backup.json's "check.required" is "[]"
    And pg-backup.json's "app" is "keycloak"
    When pg-backup.json is checked against the schema
    Then the result is:
      """
      ["pg-backup.json.wal.bucket: 'gs://sustenaut-auth-wal' doesn't match ^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$", "pg-backup.json.app: should be an object", "pg-backup.json.check.required: needs at least 1"]
      """

  Scenario: Strings and items are checked
    Given pg-backup.json's "check.query" is empty
    And pg-backup.json's "check.required" is "["ok", "no spaces"]"
    When pg-backup.json is checked against the schema
    Then the result is:
      """
      ["pg-backup.json.check.query: is empty", "pg-backup.json.check.required[1]: 'no spaces' doesn't match ^[A-Za-z0-9_.-]+$"]
      """

  Scenario: A file that isn't an object is caught
    Given a file "backups/pg-backup.json" holding "[]"
    When pg-backup.json is checked against the schema
    Then the result is:
      """
      ["pg-backup.json: should be an object"]
      """

  Scenario: A schema rule this release can't check stops
    When the value "1" is checked against the rule "{"maximum": 3}"
    Then it is refused, saying "the schema uses ['maximum'], which this release doesn't check"
    And the refusal's "__class__.__name__" is "ConfigError"

  Scenario: The schema ships with the package
    When the package's schema is read
    Then the result's "title" is "pg-backup.json"

  Scenario: Load reads the repository file
    When the settings are loaded
    Then the result's "namespace" is "auth"
    And the result's "pod" is "postgres-0"

  Scenario Outline: Only the file's namespace and 14 digits are a scratch namespace
    When I ask whether "<namespace>" is a scratch namespace
    Then the result is "<scratch>"

    Examples:
      | namespace                          | scratch |
      | auth-restore-20261106070216        | true    |
      | auth                               | false   |
      | other-restore-20261106070216       | false   |

  Scenario: A scratch namespace named on the command line is made again from the file
    When the scratch namespace "auth-restore-00261106070216" is named
    Then the result is "auth-restore-00261106070216"

  Scenario Outline: A name that isn't a scratch namespace names none
    When the scratch namespace "<namespace>" is named
    Then the result is empty

    Examples:
      | namespace                  |
      | auth-restore-2026110607021 |
      | --namespace=auth           |

  Scenario: A scratch namespace with a new line after it names none
    When the scratch namespace named is:
      """
      auth-restore-20261106070216

      """
    Then the result is empty

  Scenario: Load says the file is missing
    Given the file "backups/pg-backup.json" is removed
    When the settings are loaded
    Then it is refused, saying "no backups/pg-backup.json"
    And the refusal's "__class__.__name__" is "ConfigError"

  Scenario: Load says the file isn't JSON
    Given a file "backups/pg-backup.json" holding "{"
    When the settings are loaded
    Then it is refused, saying "backups/pg-backup.json isn't JSON"
    And the refusal's "__class__.__name__" is "ConfigError"

  Scenario: Load says what the schema finds
    Given a file "backups/pg-backup.json" holding "{"server": "sustenaut"}"
    When the settings are loaded
    Then it is refused, saying "backups/pg-backup.json doesn't match pg-backup's schema:"
    And it is refused, saying "pg-backup.json: namespace is missing"
    And the refusal's "__class__.__name__" is "ConfigError"

  Scenario: A schema reference is kept out of the settings
    Given pg-backup.json's "$schema" is "https://example/pg-backup.schema.json"
    When the settings are loaded
    Then the result's "namespace" is "auth"

  Scenario: A namespace holding -restore- is refused, as only a drill's scratch one may
    Given pg-backup.json's "namespace" is "auth-restore-x"
    When the settings are loaded
    Then it is refused, saying "pg-backup.json.namespace: 'auth-restore-x' holds -restore-, which only a drill's scratch namespace may"
    And the refusal's "__class__.__name__" is "ConfigError"
