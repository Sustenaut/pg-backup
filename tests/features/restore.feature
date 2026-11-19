Feature: Restoring and promoting a backup
  A restore lands beside live; promote replaces live in one transaction, after a dump.

  Background:
    Given a scratch directory
    And a file "backups/pg-backup.json" copied from "examples/pg-backup.json"
    And a healthy server
    And it is 2026-11-06 07:15 UTC

  Scenario Outline: A moment with its zone is read in UTC
    When the moment "<text>" is read
    Then the result is "2026-11-05 14:32:00+00:00"

    Examples:
      | text                      |
      | 2026-11-05 14:32:00+00    |
      | 2026-11-05T16:32:00+02:00 |
      | 2026-11-05T14:32:00Z      |

  Scenario: Latest is no moment
    When the moment "latest" is read
    Then the result is empty

  Scenario Outline: A moment that can't be read, or has no zone, stops with exit 2
    When the moment "<text>" is read
    Then it is refused, saying "<message>"
    And the refusal's "__class__.__name__" is "Stop"
    And the refusal's "code" is "2"

    Examples:
      | text                | message                                                                                |
      | 2026-11-05 14:32:00 | '2026-11-05 14:32:00' has no zone: add one, as +00                                     |
      | yesterday           | can't read 'yesterday' as a time: give it with its zone, as 2026-11-05 14:32:00+00 |

  Scenario Outline: The newest base backup made before the moment is chosen
    When the base backup for "<moment>" is chosen from the healthy archive
    Then the result is "<backup>"

    Examples:
      | moment                 | backup |
      | latest                 | base_2 |
      | 2026-11-06 07:00:00+00 | base_1 |

  Scenario: No base backup made before the moment is none
    When the base backup for "2026-11-06 06:00:00+00" is chosen from the healthy archive
    Then the result is empty

  Scenario: The oldest base backup is the earliest
    When the oldest base backup in the healthy archive is found
    Then the result is "2026-11-06T06:14:28.115Z"

  Scenario: An empty archive has no oldest base backup
    When the oldest base backup in an empty archive is found
    Then the result is empty

  Scenario: A restore goes into a closed scratch namespace, read-only and keyless
    When I restore to "2026-11-06 07:05:00+00"
    Then the result is "auth-restore-20261106071500"
    And "kubectl" was run with "create namespace auth-restore-20261106071500"
    And "kubectl" read the input:
      """
      {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "data": {"libsodium-key": "a2V5"}, "metadata": {"name": "auth-wal", "namespace": "auth-restore-20261106071500"}}
      """
    And "kubectl" read JSON with "items.0.spec" equal to "{"podSelector": {}, "policyTypes": ["Ingress"]}"
    And "kubectl" read JSON with "items.1.metadata" equal to "{"name": "postgres", "namespace": "auth-restore-20261106071500"}"
    And "kubectl" read JSON with "items.1.automountServiceAccountToken" equal to "false"
    And "kubectl" read JSON with "items.3.spec.serviceAccountName" equal to "postgres"
    And "kubectl" read JSON with "items.3.spec.automountServiceAccountToken" equal to "false"
    And "kubectl" read JSON with "items.3.spec.containers.0.env" equal to "[{"name": "PGDATA", "value": "/restore/pgdata"}, {"name": "WALG_GS_PREFIX", "value": "gs://sustenaut-auth-wal/keycloak"}, {"name": "GOOGLE_APPLICATION_CREDENTIALS", "value": "/etc/wal-google/credential.json"}, {"name": "WALG_LIBSODIUM_KEY", "valueFrom": {"secretKeyRef": {"name": "auth-wal", "key": "libsodium-key"}}}, {"name": "WALG_LIBSODIUM_KEY_TRANSFORM", "value": "base64"}]"
    And "kubectl" read JSON with "items.3.spec.containers.0.volumeMounts" equal to "[{"name": "restore", "mountPath": "/restore"}, {"name": "google-token", "mountPath": "/var/run/secrets/google", "readOnly": true}, {"name": "wal-google-credential", "mountPath": "/etc/wal-google", "readOnly": true}]"
    And "kubectl" read JSON with "items.3.spec.volumes" equal to "[{"name": "restore", "emptyDir": {}}, {"name": "google-token", "projected": {"sources": [{"serviceAccountToken": {"audience": "//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/services/providers/k3s", "path": "token", "expirationSeconds": 3600}}]}}, {"name": "wal-google-credential", "configMap": {"name": "wal-google-credential"}}]"
    And "kubectl" read JSON with "items.2.metadata" equal to "{"name": "wal-google-credential", "namespace": "auth-restore-20261106071500"}"
    And "kubectl" read input including "\"audience\": \"//iam.googleapis.com/projects/1/locations/global/workloadIdentityPools/services/providers/k3s\""
    And "kubectl" read input including "\"file\": \"/var/run/secrets/google/token\""
    And "kubectl" read input including "/serviceAccounts/sustenaut-auth-wal-read@sustenaut.iam.gserviceaccount.com:generateAccessToken\"\n}"
    And "kubectl" was run with arguments matching "-n auth-restore-20261106071500 exec restore -- sh -c wal-g backup-fetch "$PGDATA" base_2 >/dev/null"
    And "kubectl" was run with arguments matching "*recovery_target_time = *2026-11-06 07:05:00+00*"
    And "kubectl" was run with arguments matching "*restore_command = *wal-g st cat wal_005/%f.br --decrypt --decompress > %p.part && mv %p.part %p*"
    And "kubectl" was not run with arguments matching "-n auth scale *"
    And "kubectl" was not run with arguments matching "-n auth delete *"
    And "kubectl" was not run with arguments matching "-n auth create job *"
    And it prints a line matching "   team 2|team 2"
    And it prints a line matching "Then: ./backups/pg-backup clean auth-restore-20261106071500*"

  Scenario: Latest sets no target
    When I restore to "latest"
    Then the result is "auth-restore-20261106071500"
    And "kubectl" was not run with arguments matching "*recovery_target_time =*"

  Scenario: Too early is exit 3, with the oldest and the namespace to delete
    When I restore to "2026-11-06 06:00:00+00"
    Then it is refused, saying "no base backup made before 2026-11-06 06:00:00+00; the oldest is 2026-11-06T06:14:28.115Z (the archive keeps 14 days)"
    And the refusal's "code" is "3"
    And the refusal's "oldest" is "2026-11-06T06:14:28.115Z"
    And the refusal's "namespace" is "auth-restore-20261106071500"

  Scenario: An empty archive is too early too
    Given the server answers "*backup-list --json*" with nothing
    When I restore to "latest"
    Then it is refused, saying "the oldest is not there yet"
    And the refusal's "code" is "3"
    And the refusal's "oldest" is "null"

  Scenario: A failure before the namespace names none
    Given the server answers "*get statefulset postgres*" with nothing
    When I restore to "latest"
    Then it is refused, saying "no container postgres in statefulset postgres (namespace auth)"
    And the refusal's "__class__.__name__" is "Stop"
    And the refusal's "namespace" is "null"

  Scenario: An image without wal-g stops
    Given the server answers "*command -v wal-g*" with nothing
    When I restore to "latest"
    Then it is refused, saying "Has the deploy that moves it finished"
    And the refusal's "code" is "1"
    And the refusal's "old_image" is "ghcr.io/sustenaut/pg-backup:0.1.0-pg16"

  Scenario: A restore missing a required row fails
    Given the server answers "*exec restore -- psql *group by*" with:
      """
      family 4
      master 2
      """
    When I restore to "latest"
    Then it is refused, saying "the restore has no sustenaut, team"
    And the refusal's "__class__.__name__" is "Stop"

  Scenario: Recovery that never ends times out
    Given the server fails "*pg_is_in_recovery*" with "not up"
    And the clock moves 100 seconds at each reading
    When I restore to "latest"
    Then it is refused, saying "didn't finish recovery in 30 minutes"
    And the refusal's "__class__.__name__" is "Stop"

  Scenario: Clean deletes a scratch namespace
    When I clean "auth-restore-20261106070216"
    Then it is not refused
    And "kubectl" was run with "delete namespace auth-restore-20261106070216 --wait=false"

  Scenario: Clean refuses a namespace that isn't a scratch one
    When I clean "auth"
    Then it is refused, saying "auth isn't a scratch restore (auth-restore-<14 digits>)"
    And the refusal's "code" is "2"
    And "kubectl" was not run

  Scenario: Promote refuses a live namespace
    Given the person at the terminal types "promote auth"
    When I promote "auth"
    Then it is refused, saying "auth isn't a scratch restore"
    And the refusal's "code" is "2"
    And "kubectl" was not run

  Scenario: Promote needs a running restore
    Given the server fails "*pg_is_in_recovery*" with "gone"
    And the person at the terminal types "promote auth-restore-20261106070216"
    When I promote "auth-restore-20261106070216"
    Then it is refused, saying "auth-restore-20261106070216 has no promoted restore running"
    And the refusal's "__class__.__name__" is "Stop"

  Scenario: Promote needs every required row
    Given the server answers "*exec restore -- psql *group by*" with "family 4"
    And the person at the terminal types "promote auth-restore-20261106070216"
    When I promote "auth-restore-20261106070216"
    Then it is refused, saying "the restore has no sustenaut, team: not promoting it"
    And the refusal's "__class__.__name__" is "Stop"

  Scenario: Promote needs a terminal
    Given there is no terminal
    When I promote "auth-restore-20261106070216"
    Then it is refused, saying "promote asks for a typed confirmation: run it at a terminal"
    And the refusal's "code" is "2"

  Scenario: Promote needs the exact words
    Given the person at the terminal types "yes"
    When I promote "auth-restore-20261106070216"
    Then it is refused, saying "not promoted: nothing changed"
    And the refusal's "code" is "1"
    And "kubectl" was not run with arguments matching "*create job*"
    And "kubectl" was not run with arguments matching "*scale*"

  Scenario: Promote dumps first, and stops without the dump
    Given the server fails "*wait --for=condition=complete*" with "timed out"
    And the person at the terminal types "promote auth-restore-20261106070216"
    When I promote "auth-restore-20261106070216"
    Then it is refused, saying "the dump job pre-promote-20261106071500 didn't complete"
    And "kubectl" was run with "-n auth create job pre-promote-20261106071500 --from=cronjob/backup"
    And "kubectl" was not run with arguments matching "*scale*"

  Scenario: Promote replaces live in one transaction and starts the app
    Given the person at the terminal types "promote auth-restore-20261106070216"
    When I promote "auth-restore-20261106070216"
    Then it is not refused
    And "kubectl" was run, in order, with arguments matching:
      """
      -n auth create job pre-promote-20261106071500 --from=cronjob/backup
      -n auth scale deployment keycloak --replicas=0
      -n auth-restore-20261106070216 exec restore -- pg_dump *
      -n auth exec -i postgres-0 -c postgres -- pg_restore * --single-transaction *
      -n auth scale deployment keycloak --replicas=1
      """
    And it prints a line matching "   live now matches the restore"
    And it prints a line matching "When it looks right: ./backups/pg-backup clean auth-restore-20261106070216"

  Scenario: A failed pg_restore leaves live as it was and starts the app
    Given the server fails "*pg_restore*" with "bad", saying "pg_restore: error"
    And the person at the terminal types "promote auth-restore-20261106070216"
    When I promote "auth-restore-20261106070216"
    Then it is refused, saying:
      """
      pg_restore failed and rolled back: the live database is unchanged
      pg_restore: error
      """
    And "kubectl" was run, in order, with arguments matching:
      """
      * pg_restore *
      -n auth scale deployment keycloak --replicas=1
      -n auth rollout status deployment/keycloak --timeout=10m
      """

  Scenario: Live not matching the restore after promote fails, and starts the app
    Given the server answers "*exec postgres-0 -c postgres -- psql*" in turn:
      | answer                                  |
      | family 4\nmaster 2\nsustenaut 0\nteam 2 |
      | family 1                                |
    And the person at the terminal types "promote auth-restore-20261106070216"
    When I promote "auth-restore-20261106070216"
    Then it is refused, saying "after the promote, live doesn't match the restore"
    And "kubectl" was run with "-n auth scale deployment keycloak --replicas=1"

  Scenario: An idle database gets a segment closed, so the archive covers the moment
    Given the server answers "*extract(epoch from last_archived_time)*" with "1762412000"
    And the server answers "*pg_switch_wal*" with:
      """
      0/4000000
      000000010000000000000004
      """
    And the server answers "*select coalesce(last_archived_wal*" in turn:
      | answer                   |
      | 000000010000000000000003 |
      | 000000010000000000000004 |
    When I restore to "2026-11-06 07:05:00+00"
    Then the result is "auth-restore-20261106071500"
    And it prints a line matching "   the archive ends before the moment (nothing written since): closing a WAL segment on live"
    And "kubectl" was run with arguments matching "-n auth exec postgres-0 -c postgres -- psql * begin; select pg_logical_emit_message(true, 'pg-backup', 'drill'); commit; *"

  Scenario: A segment live never archives stops
    Given the server answers "*extract(epoch from last_archived_time)*" with "0"
    And the server answers "*pg_switch_wal*" with "000000010000000000000004"
    And the server answers "*select coalesce(last_archived_wal*" with "000000010000000000000003"
    And the clock moves 100 seconds at each reading
    When I restore to "2026-11-06 07:05:00+00"
    Then it is refused, saying "live didn't archive WAL segment 000000010000000000000004 in 3 minutes"
    And the refusal's "__class__.__name__" is "Stop"

  Scenario: Latest needs no segment closed
    Given the server answers "*extract(epoch from last_archived_time)*" with "0"
    When I restore to "latest"
    Then the result is "auth-restore-20261106071500"
    And "kubectl" was not run with arguments matching "*pg_switch_wal*"

  Scenario: A restore whose PostgreSQL stopped fails at once, with its log
    Given the server fails "*pg_is_in_recovery*" with "no server"
    And the server answers "*status >/dev/null && echo up*" with nothing
    And the server answers "*tail -n 8 /restore/postgres.log*" with "FATAL:  recovery ended before configured recovery target was reached"
    When I restore to "latest"
    Then it is refused, saying:
      """
      the restore's PostgreSQL stopped during recovery; the end of its log:
      FATAL:  recovery ended before configured recovery target was reached
      """
    And the refusal's "__class__.__name__" is "Stop"

  Scenario: A live credential that impersonates nothing stops the restore
    Given the server answers "*get configmap wal-google-credential -o json*" with "{"data": {"credential.json": "{}"}}"
    When I restore to "latest"
    Then it is refused, saying "the ConfigMap wal-google-credential holds no external_account credential that impersonates"
    And the refusal's "__class__.__name__" is "Stop"
