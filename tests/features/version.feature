Feature: pg-backup's version
  The installed package's version is the one released, and the runner consumer's copy pins it.

  Scenario: The runner consumer's copy pins this release
    When the version consumer/pg-backup pins is compared with pyproject.toml's
    Then the result's "the same" is "true"

  Scenario: The version is the installed package's, and unreleased from src/
    When pg-backup's version is read
    Then the result is "unreleased"

  Scenario: The program says its version
    Given the program under test is "pg-backup"
    When I run "pg-backup --version"
    Then it succeeds
    And it prints:
      """
      pg-backup unreleased
      """
