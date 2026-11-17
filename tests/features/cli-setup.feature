Feature: pg-backup's command line and setup-secrets
  The command line names each command's arguments, and setup-secrets fills the WAL Secret.

  Scenario: Check reads the file
    Then check reads the file

  Scenario: A bad file is exit 2
    Then a bad file is exit 2

  Scenario: Each command runs its function
    Then each command runs its function

  Scenario: Stops and server errors become exit codes
    Then stops and server errors become exit codes

  Scenario: The module runs as a program
    Then the module runs as a program

  Scenario: The server quotes each argument
    Then the server quotes each argument

  Scenario: The secret is made from the environment
    Then the secret is made from the environment

  Scenario: Setup makes the missing key then pushes
    Then setup makes the missing key then pushes

  Scenario: Setup keeps the key already there
    Then setup keeps the key already there

  Scenario: Setup stops on each failure
    Then setup stops on each failure

  Scenario: Setup needs the repository scripts and the server
    Then setup needs the repository scripts and the server

  Scenario: The server pipes one kubectl into another
    Then the server pipes one kubectl into another
