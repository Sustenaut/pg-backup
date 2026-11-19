Feature: The pg.database need type
  Against a fake deploy walk: a kubectl that answers by what it is asked and keeps each run and
  its input, a fixed clock (2026-11-08 06:00 UTC), and a Bitwarden writer.

  Background:
    Given a scratch directory
    And a file "backups/pg-backup.json" copied from "examples/pg-backup.json"
    And a deploy walk in "auth" with a healthy database

  Scenario: The entry point names the pg.database instance
    When the need type "pg-database" is found through pyproject.toml's entry point
    Then the result's "__class__.__name__" is "PgDatabase"
    And the result's "type" is "pg.database"

  Scenario: The type has what pubdeploy checks
    When the need type "pg-database" is found through pyproject.toml's entry point
    Then the result's "grade" is "auto"
    And the result's "offline" is "false"
    And the result's "budget" is "6"
    And the result's "fulfil_budget" is "300"
    And the result's "schema.type" is "object"
    And the result's "schema.additionalProperties" is "false"

  Scenario: Its settings are the statefulset, the backups file and the container
    When the need type's settings are listed
    Then the result is "["backups", "container", "statefulset"]"

  Scenario Outline: Its default name is the statefulset's
    When its default name for the settings "<settings>" is asked
    Then the result is "<name>"

    Examples:
      | settings              | name     |
      | {}                    | postgres |
      | {"statefulset": "db"} | db       |

  Scenario: It depends on no other need
    When what it depends on is asked
    Then the result is empty

  Scenario: Its states are pubrules's
    When the states a need may be in are listed
    Then the result is "["ready", "missing", "drift", "blocked", "unknown"]"

  Scenario: A healthy database is ready, with the drill in its details
    When the need is checked
    Then the result's "state" is "ready"
    And the result's "summary" is "postgres in auth is ready, archiving; last base backup 2026-11-08 02:47 UTC, with no bucket key"
    And the result's "details.3" is "drill: none recorded yet; run ./backups/pg-backup drill --wait and record it"
    And the result's "fix" is "run ./backups/pg-backup drill --wait and record it"
    And "kubectl" was run with "-n auth logs postgres-0 -c wal-g --tail=50"

  Scenario: Bad settings are drift, with their first line, and ask kubectl nothing
    Given a file "backups/pg-backup.json" holding "{}"
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "backups/pg-backup.json doesn't match pg-backup's schema:"
    And "kubectl" was not run

  Scenario: Missing settings are drift
    Given the need's "backups" is "backups/none.json"
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "no backups/none.json: pg-backup runs in the repository root, where backups/pg-backup.json is"

  Scenario: Settings for another namespace are drift
    Given the walk deploys to "other"
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "backups/pg-backup.json says namespace auth, the deploy other"

  Scenario: Settings for another statefulset are drift
    Given the need's "statefulset" is "db"
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "backups/pg-backup.json says statefulset postgres, the need db"

  Scenario Outline: A StatefulSet that isn't there, isn't ready or can't be read
    Given kubectl answers "get statefulset" with:
      | exit   | stdout   | stderr   |
      | <exit> | <stdout> | <stderr> |
    When the need is checked
    Then the result's "state" is "<state>"
    And the result's "summary" is "<summary>"
    And "kubectl" was not run with arguments matching "* logs *"

    Examples:
      | exit | stdout                                 | stderr                                                          | state   | summary                                                |
      | 1    |                                        | Error from server (NotFound): statefulsets.apps "postgres" not found | missing | StatefulSet postgres is not in auth                    |
      | 0    | {"spec": {"replicas": 1}, "status": {}} |                                                                 | missing | StatefulSet postgres in auth is not ready: 0 of 1      |
      | 1    |                                        | Error from server (Forbidden): statefulsets.apps is forbidden   | unknown | kubectl may not read statefulsets in auth              |
      | 127  |                                        | kubectl: not given to this check                                | unknown | kubectl did not run, to read statefulsets              |
      | 1    |                                        | connection refused                                              | unknown | kubectl could not read statefulsets in auth            |
      | 0    | not json                               |                                                                 | unknown | kubectl's answer for StatefulSet postgres is not JSON  |

  Scenario: A kubectl that raises is unknown
    Given the walk's kubectl cannot run
    When the need is checked
    Then the result's "state" is "unknown"
    And the result's "summary" is "kubectl did not run, to read statefulsets"

  Scenario: More failed archives since the status before is drift
    Given the wal-g sidecar's status lines are the healthy one with:
      | failed_count |
      | 2            |
      | 3            |
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "archiving fails: 1 more failed since the status before"

  Scenario: Nothing archived for a while as pg_wal grows is drift
    Given the wal-g sidecar's status lines are the healthy one with:
      | pg_wal_bytes | last_archived_time          |
      | 1            | "2026-11-08T05:40:00+00:00" |
      | 2            | "2026-11-08T05:40:00+00:00" |
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "archive lag: nothing archived since 2026-11-08 05:40 UTC while pg_wal grew"

  Scenario: Nothing ever archived as pg_wal grows is drift
    Given the wal-g sidecar's status lines are the healthy one with:
      | pg_wal_bytes | last_archived_time |
      | 1            | null               |
      | 2            | null               |
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "archive lag: nothing archived ever while pg_wal grew"

  Scenario: An old base backup is drift
    Given the wal-g sidecar's status lines are the healthy one with:
      | last_backup            |
      | "2026-11-07T03:00:00Z" |
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "no base backup since 2026-11-07 03:00 UTC"

  Scenario: No base backup yet is drift
    Given the wal-g sidecar's status lines are the healthy one with:
      | last_backup |
      | null        |
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "no base backup yet"

  Scenario: A sidecar that could not ask PostgreSQL is drift
    Given the wal-g sidecar prints:
      """
      {"fact": "wal_archive", "ok": false}
      """
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "the wal-g sidecar could not ask PostgreSQL how archiving goes"

  Scenario: A sidecar that prints no status is unknown
    Given the wal-g sidecar prints:
      """
      PostgreSQL is starting
      {not json
      {"fact": "wal_base_backup", "ok": false}
      """
    When the need is checked
    Then the result's "state" is "unknown"
    And the result's "summary" is "the sidecar prints no status in its last 50 lines: is the pod on pg-backup's image?"

  Scenario: An old archive time is fine while nothing is written
    Given the wal-g sidecar's status lines are the healthy one with:
      | last_archived_time          |
      | "2026-11-08T01:00:00+00:00" |
      | "2026-11-08T01:00:00+00:00" |
    When the archive's state is read from the status lines
    Then the result's "0" is "ready"

  Scenario: Logs kubectl may not read are unknown
    Given the need's "container" is "sidecar"
    And the StatefulSet's container "wal-g" is named "sidecar"
    And kubectl answers "logs" with:
      | exit | stdout | stderr                                                      |
      | 1    |        | pods "postgres-0" is forbidden: cannot get resource pods/log |
    When the need is checked
    Then the result's "state" is "unknown"
    And the result's "summary" is "kubectl may not read pods/log of postgres-0's sidecar in auth"

  Scenario Outline: The WAL Secret that isn't there, isn't right or can't be read
    Given kubectl answers "get secret" with:
      | exit   | stdout   | stderr   |
      | <exit> | <stdout> | <stderr> |
    When the need is checked
    Then the result's "state" is "<state>"
    And the result's "summary" is "<summary>"
    And the result's "details.2" is "<state>: <summary>"

    Examples:
      | exit | stdout                                                   | stderr                                                  | state   | summary                                                                  |
      | 1    |                                                          | Error from server (NotFound): secrets "auth-wal" not found | missing | the WAL Secret auth-wal is not in auth                                   |
      | 0    | {"data": {"gcs-key.json": "eA=="}}                        |                                                         | drift   | the WAL Secret auth-wal has no libsodium-key                             |
      | 0    | {"data": {"gcs-key.json": "eA==", "libsodium-key": "a2V5"}} |                                                         | drift   | the WAL Secret auth-wal also has gcs-key.json, which nothing reads now |
      | 1    |                                                          | secrets is forbidden                                    | unknown | kubectl may not read secrets in auth                                     |
      | 0    | []                                                       |                                                         | unknown | kubectl's answer for Secret auth-wal is not JSON                         |

  Scenario: The WAL Secret's values never show, only its key names
    Given kubectl answers "get secret" with:
      | exit | stdout                                                                        |
      | 0    | {"data": {"gcs-key.json": "Z2NzLWJ1Y2tldC1rZXk=", "libsodium-key": "bGlic29kaXVtLWtleQ=="}} |
    And the secret "Z2NzLWJ1Y2tldC1rZXk="
    And the secret "bGlic29kaXVtLWtleQ=="
    When the need is checked
    Then the result's "state" is "drift"
    And no secret appears in what it produced

  Scenario: The worst part wins
    Given kubectl answers "get secret" with:
      | exit | stderr    |
      | 1    | forbidden |
    And the wal-g sidecar's status lines are the healthy one with:
      | last_backup |
      | null        |
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "no base backup yet"
    And the result's "details" is "["ready: StatefulSet postgres in auth is ready", "drift: no base backup yet", "unknown: kubectl may not read secrets in auth", "drill: none recorded yet; run ./backups/pg-backup drill --wait and record it"]"

  Scenario: A fault in the check is a result
    Given the walk's clock is broken
    When the need is checked
    Then the result's "state" is "unknown"
    And the result's "summary" is "the check failed (TypeError)"

  Scenario: The result is of the walk's own class
    Given the walk's Context comes from a module with its own Result
    When the need is checked
    Then the result's "__class__.__module__" is "fake_pubrules_model"
    And the result's "__class__.__name__" is "Result"
    And the result's "state" is "ready"

  Scenario: The result is pg-backup's own where the walk's module has none
    When the need is checked
    Then the result's "__class__.__module__" is "pg_backup.need"
    And the result's "__class__.__name__" is "Result"

  Scenario: A long summary is cut as pubrules cuts it
    When a Result is made with a summary of 300 characters
    Then the result has 120 characters
    And the result matches "xxx*x…"

  Scenario: Fulfil makes the encryption key and puts the Secret on kubectl's input
    Given the walk can write to Bitwarden
    And each random byte is 1
    When the need is fulfilled
    Then the result's "state" is "ready"
    And the result's "summary" is "made auth-wal-key; put Secret auth-wal in auth"
    And "bitwarden" was run 1 times
    And "bitwarden" was run with arguments matching "write auth-wal-key WAL-G's encryption key for the write-ahead log archive gs://sustenaut-auth-wal/keycloak. *"
    And "bitwarden" read the input:
      """
      AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=
      """
    And "kubectl" was run, in order, with arguments matching:
      """
      apply -f -
      -n auth get secret auth-wal -o go-template=*
      """
    And "kubectl" was run 2 times
    And "kubectl" read the input:
      """
      {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "auth-wal", "namespace": "auth"}, "data": {"libsodium-key": "QVFFQkFRRUJBUUVCQVFFQkFRRUJBUUVCQVFFQkFRRUJBUUVCQVFFQkFRRT0="}}
      """

  Scenario: Fulfil keeps the key Bitwarden has, and puts the Secret back with it alone
    Given the walk can write to Bitwarden
    And Bitwarden has "auth-wal-key" as "a2V5"
    When the need is fulfilled
    Then the result's "state" is "ready"
    And the result's "summary" is "put Secret auth-wal in auth"
    And the result's "details" is "["== 1. WAL-G's encryption key, auth-wal-key", "   already in Bitwarden", "== 2. Into the cluster, as the Secret auth-wal", "   done"]"
    And "bitwarden" was not run
    And "kubectl" read the input:
      """
      {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "auth-wal", "namespace": "auth"}, "data": {"libsodium-key": "YTJWNQ=="}}
      """

  Scenario Outline: A Secret that apply leaves a bucket key in is replaced
    Given the walk can write to Bitwarden
    And Bitwarden has "auth-wal-key" as "a2V5"
    And kubectl answers "go-template" with:
      | exit | stdout                      |
      | 0    | gcs-key.json libsodium-key  |
    And kubectl answers "replace" with:
      | exit      | stdout                   | stderr    |
      | <replace> | secret/auth-wal replaced | <said>    |
    When the need is fulfilled
    Then the result's "state" is "<state>"
    And the result's "summary" is "<summary>"
    And "kubectl" was run with "replace -f -"
    And "kubectl" read the input:
      """
      {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "auth-wal", "namespace": "auth"}, "data": {"libsodium-key": "YTJWNQ=="}}
      """

    Examples:
      | replace | said    | state   | summary                                                 |
      | 0       |         | ready   | put Secret auth-wal in auth                             |
      | 1       | refused | unknown | kubectl replace refused the WAL Secret auth-wal (exit 1) |

  Scenario: Fulfil needs a Bitwarden writer
    When the need is fulfilled
    Then the result's "state" is "unknown"
    And the result's "summary" is "the keys can't be made in this walk"
    And the result's "fix" is "needs a Bitwarden write token: run the walk at a terminal"

  Scenario: Fulfil needs a kubectl that takes input
    Given the walk can write to Bitwarden
    And the walk's kubectl takes no input
    When the need is fulfilled
    Then the result's "state" is "unknown"
    And the result's "summary" is "this pubrules's kubectl takes no input, so the WAL Secret can't be put"
    And "kubectl" was not run

  Scenario: Fulfil with bad settings touches nothing
    Given the walk can write to Bitwarden
    And a file "backups/pg-backup.json" holding "{}"
    When the need is fulfilled
    Then the result's "state" is "drift"
    And "kubectl" was not run
    And "bitwarden" was not run

  Scenario: Bitwarden refusing the key is a result
    Given the walk can write to Bitwarden
    And Bitwarden refuses to write
    When the need is fulfilled
    Then the result's "state" is "unknown"
    And the result's "summary" is "Bitwarden refused auth-wal-key (RuntimeError)"

  Scenario: kubectl refusing the Secret is a result
    Given the walk can write to Bitwarden
    And kubectl answers "apply" with:
      | exit | stderr             |
      | 1    | the server refused |
    When the need is fulfilled
    Then the result's "state" is "unknown"
    And the result's "summary" is "kubectl apply refused the WAL Secret auth-wal (exit 1)"

  Scenario: A fault in fulfil is a result
    Given the walk can write to Bitwarden
    And the walk's known values can't be read
    When the need is fulfilled
    Then the result's "state" is "unknown"
    And the result's "summary" is "fulfil failed (AttributeError)"

  Scenario: A key made never shows
    Given the walk can write to Bitwarden
    And the secret "CANARY-6f1c3a-not-for-any-log"
    And the secret "Q0FOQVJZLTZmMWMzYS1u"
    When the need is fulfilled, then checked again
    Then the result's "0.state" is "ready"
    And no secret appears in what it produced

  Scenario: A key Bitwarden has never shows, and reaches kubectl on its input
    Given the walk can write to Bitwarden
    And Bitwarden has "auth-wal-key" as "CANARY-6f1c3a-not-for-any-log"
    And the secret "CANARY-6f1c3a-not-for-any-log"
    And the secret "Q0FOQVJZLTZmMWMzYS1u"
    When the need is fulfilled, then checked again
    Then the result's "0.state" is "ready"
    And no secret appears in what it produced
    And "kubectl" read JSON with "data.libsodium-key" equal to "Q0FOQVJZLTZmMWMzYS1ub3QtZm9yLWFueS1sb2c="

  Scenario: A key refused never shows, whatever kubectl says
    Given the walk can write to Bitwarden
    And Bitwarden refuses to write
    And kubectl answers "apply" with:
      | exit | stdout                        | stderr                        |
      | 1    | CANARY-6f1c3a-not-for-any-log | CANARY-6f1c3a-not-for-any-log |
    And the secret "CANARY-6f1c3a-not-for-any-log"
    And the secret "Q0FOQVJZLTZmMWMzYS1u"
    When the need is fulfilled, then checked again
    Then the result's "0.state" is "unknown"
    And no secret appears in what it produced

  Scenario: Make keys says what setup-secrets said, and does it in that order
    When the keys are made, with nothing in Bitwarden
    Then the result is "["made auth-wal-key", "put Secret auth-wal in auth"]"
    And it prints:
      """
      == 1. WAL-G's encryption key, auth-wal-key
      (made auth-wal-key)
      == 2. Into the cluster, as the Secret auth-wal
      (put the Secret)
         done
      """

  Scenario: A StatefulSet that runs as another account would need a bucket key
    Given the StatefulSet runs as "default"
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "StatefulSet postgres does not run as postgres"

  Scenario: A StatefulSet with no projected token would need a bucket key
    Given the StatefulSet has no volumes
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "StatefulSet postgres has no projected token or no ConfigMap wal-google-credential"

  Scenario: A StatefulSet with no wal-g sidecar is drift
    Given the StatefulSet has no container "wal-g"
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "StatefulSet postgres has no container wal-g"

  Scenario: A container reading a key file would need a bucket key
    Given the StatefulSet's container "postgres" reads its credential from "/var/run/secrets/wal/gcs-key.json"
    When the need is checked
    Then the result's "state" is "drift"
    And the result's "summary" is "postgres in StatefulSet postgres does not reach the bucket through wal-google-credential"
