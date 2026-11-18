Feature: pg-backup's command line, and the server it reaches
  The command line names each command's arguments; the server is reached as root over ssh,
  with each argument quoted for the server's shell.

  Background:
    Given a scratch directory
    And a file "backups/pg-backup.json" copied from "examples/pg-backup.json"

  Scenario: Check says the file is valid
    Given the program under test is "pg-backup"
    When I run "pg-backup check"
    Then it succeeds
    And it prints "backups/pg-backup.json is valid: auth on sustenaut"

  Scenario: A missing file is exit 2
    Given the program under test is "pg-backup"
    And the file "backups/pg-backup.json" is removed
    When I run "pg-backup drill"
    Then the exit code is 2
    And stderr mentions "no backups/pg-backup.json: pg-backup runs in the repository root"

  Scenario Outline: Each command runs its own function
    Given every command's function only says its name
    When pg-backup runs "<command>"
    Then it succeeds
    And it prints:
      """
      <called>
      """

    Examples:
      | command                             | called  |
      | restore --to latest                 | restore |
      | drill                               | drill   |
      | clean auth-restore-20261106070216   | clean   |
      | promote auth-restore-20261106070216 | promote |
      | setup-secrets                       | setup   |
      | setup-secrets --push                | push    |

  Scenario: Drill --wait waits, then drills
    Given every command's function only says its name
    When pg-backup runs "drill --wait"
    Then it succeeds
    And it prints:
      """
      wait_until_ready
      drill
      """

  Scenario: A stop is the command's exit code, with its reason
    Given restoring stops with exit 3, saying "too early"
    When pg-backup runs "restore --to latest"
    Then the exit code is 3
    And stderr mentions "too early"

  Scenario: A server error is exit 1, with what the server said
    Given restoring fails on the server with "ssh failed", saying "Permission denied"
    When pg-backup runs "restore --to latest"
    Then the exit code is 1
    And stderr mentions "ssh failed"
    And stderr mentions "Permission denied"

  Scenario: The module runs as a program
    When I run "python3 -m pg_backup --version"
    Then it succeeds
    And it prints a line matching "pg-backup *"

  Scenario: The server quotes each argument for its shell, and returns what kubectl printed
    Given a stand-in "ssh" that answers in turn:
      | args | exit | stdout | stderr |
      | *    | 0    | out    | err    |
    When kubectl runs on the server "sustenaut" with "-n auth exec x -- psql -c "select 'a b'""
    Then the result is:
      """
      out

      """
    And "ssh" was run with "-o BatchMode=yes root@sustenaut "k3s kubectl -n auth exec x -- psql -c 'select '\"'\"'a b'\"'\"''""

  Scenario: A kubectl that succeeds is ok
    Given a stand-in "ssh"
    When the server "sustenaut" is asked whether kubectl "get pods" is ok
    Then the result is "true"
    And "ssh" was run with "-o BatchMode=yes root@sustenaut "k3s kubectl get pods""

  Scenario: A kubectl that fails is not ok
    Given a stand-in "ssh" that fails, saying "err"
    When the server "sustenaut" is asked whether kubectl "get pods" is ok
    Then the result is "false"

  Scenario: A command that fails is a server error, with all it printed
    Given a stand-in "ssh" that answers in turn:
      | args | exit | stdout | stderr |
      | *    | 1    | out    | err    |
    When the server "sustenaut" runs "false" with the input "x"
    Then it is refused, saying "on sustenaut: false failed (1)"
    And the refusal's "__class__.__name__" is "ServerError"
    And the refusal's "output" is:
      """
      out
      err

      """

  Scenario: A command run unchecked returns what it printed, though it failed
    Given a stand-in "ssh" that answers in turn:
      | args | exit | stdout | stderr |
      | *    | 1    | out    | err    |
    When the server "sustenaut" runs "false" unchecked
    Then the result is:
      """
      out

      """

  Scenario: The server pipes one kubectl into another
    Given a stand-in "ssh" that answers in turn:
      | args         | exit |
      | * pg_dump    | 0    |
      | * pg_restore | 0    |
    When the server "sustenaut" pipes "-n ns exec restore -- pg_dump" into "-n auth exec -i p -- pg_restore"
    Then it is not refused
    And "ssh" was run with "-o BatchMode=yes root@sustenaut "k3s kubectl -n ns exec restore -- pg_dump""
    And "ssh" was run with "-o BatchMode=yes root@sustenaut "k3s kubectl -n auth exec -i p -- pg_restore""

  Scenario Outline: A pipe fails when either kubectl does, with what it said
    Given a stand-in "ssh" that answers in turn:
      | args         | exit     | stderr            |
      | * pg_dump    | <dump>   | broke             |
      | * pg_restore | <load>   | pg_restore: error |
    When the server "sustenaut" pipes "-n ns exec restore -- pg_dump" into "-n auth exec -i p -- pg_restore"
    Then it is refused, saying "on sustenaut: <message>"
    And the refusal's "__class__.__name__" is "ServerError"
    And the refusal's "output" is:
      """
      <output>

      """

    Examples:
      | dump | load | message                                | output            |
      | 1    | 0    | kubectl -n ns exec restore … failed (1) | broke             |
      | 0    | 1    | kubectl -n auth exec -i … failed (1)    | pg_restore: error |
