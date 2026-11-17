Feature: The pg-backup.json configuration
  A configuration is read and checked against its schema; each error names its place.

  Scenario: The example is valid
    Then the example is valid

  Scenario: A typo in a setting name is caught
    Then a typo in a setting name is caught

  Scenario: Nested settings are checked
    Then nested settings are checked

  Scenario: Strings and items are checked
    Then strings and items are checked

  Scenario: A schema rule this release can't check stops
    Then a schema rule this release can't check stops

  Scenario: The schema ships with the package
    Then the schema ships with the package

  Scenario: Load reads the repository file
    Then load reads the repository file

  Scenario: Load says what is wrong
    Then load says what is wrong

  Scenario: A schema reference is kept
    Then a schema reference is kept

  Scenario: A namespace holding -restore- is refused, as only a drill's scratch one may
    Given pg-backup.json's namespace is "auth-restore-x"
    Then load says "holds -restore-, which only a drill's scratch namespace may"
