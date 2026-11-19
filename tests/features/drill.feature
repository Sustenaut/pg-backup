Feature: The restore drill
  A drill restores the backup beside the live database, checks it, and says how it went.

  Background:
    Given a scratch directory
    And a file "backups/pg-backup.json" copied from "examples/pg-backup.json"
    And a healthy server
    And it is 2026-11-06 07:15 UTC

  Scenario: A drill restores to ten minutes ago and cleans up
    When the drill runs
    Then it succeeds
    And it prints a line matching "== Restoring to 2026-11-06 07:05:00+00 (07:05:00 UTC) into auth-restore-20261106071500"
    And it prints a line matching "DRILL PASSED 2026-11-06 07:15 UTC: restored to 2026-11-06 07:05:00 UTC in 15 s"
    And "kubectl" was run with "delete namespace auth-restore-20261106071500 --wait=false"

  Scenario: A slow restore fails
    Given the drill's restore takes 901 seconds
    When the drill runs
    Then the exit code is 1
    And it prints a line matching "DRILL FAILED 2026-11-06 07:15 UTC: took 901 s (the target is under 900 s)"

  Scenario: Too early is not run, and says when to retry
    Given the server answers "*backup-list --json*" with "[{"backup_name": "base_1", "time": "2026-11-06T07:08:00Z"}]"
    When the drill runs
    Then the exit code is 2
    And it prints a line matching "DRILL NOT RUN: no base backup before 2026-11-06 07:05:00 UTC yet; the oldest is from 07:08 UTC. Run it again after 07:19 UTC, or now with --wait."
    And "kubectl" was run with "delete namespace auth-restore-20261106071500 --wait=false"

  Scenario: Too early for a chosen moment asks for a later one
    When the drill runs to "2026-11-06 06:00:00+00"
    Then the exit code is 2
    And it prints a line matching "DRILL NOT RUN: the archive starts at 06:14 UTC, after 2026-11-06 06:00:00 UTC. Pick a moment after that with --to."

  Scenario: An empty archive is not run
    Given the server answers "*backup-list --json*" with nothing
    When the drill runs
    Then the exit code is 2
    And it prints a line matching "DRILL NOT RUN: the archive has no base backup yet*"

  Scenario: A server failure fails, and still cleans up
    Given the server fails "*backup-fetch*" with "fetch failed", saying "403"
    When the drill runs
    Then the exit code is 1
    And it prints a line matching "DRILL FAILED 2026-11-06 07:15 UTC: fetch failed"
    And it prints a line matching "403 (the target is under 900 s)"
    And "kubectl" was run with "delete namespace auth-restore-20261106071500 --wait=false"

  Scenario: A latest drill
    When the drill runs to "latest"
    Then it succeeds
    And it prints a line matching "DRILL PASSED 2026-11-06 07:15 UTC: restored to latest in 15 s"

  Scenario: A namespace still on the old image is not run
    Given the server answers "*get statefulset postgres*" with "docker.io/library/postgres:16.14"
    And the server answers "*command -v wal-g*" with nothing
    When the drill runs
    Then the exit code is 2
    And it prints a line matching "DRILL NOT RUN: auth still runs docker.io/library/postgres:16.14, not pg-backup's image.*"
    And "kubectl" was run with "delete namespace auth-restore-20261106071500 --wait=false"

  Scenario: People read times in this machine's zone
    Given the environment sets "TZ" to "Europe/Tallinn"
    And the server answers "*backup-list --json*" with "[{"backup_name": "base_1", "time": "2026-11-06T07:08:00Z"}]"
    When the drill runs
    Then the exit code is 2
    And it prints a line matching "== Restoring to 2026-11-06 07:05:00+00 (09:05:00 EET) into *"
    And it prints a line matching "*the oldest is from 09:08 EET. Run it again after 09:19 EET, or now with --wait."

  Scenario: Wait returns once the first base backup is old enough
    Given a clock that moves only when slept
    And the server answers "*-c postgres -- sh -c *wal-g backup-list*" with "[{"backup_name": "b", "time": "2026-11-06T07:10:00Z"}]"
    When the drill waits until it can run
    Then it is not refused
    And it prints:
      """
      == Waiting for statefulset/postgres in auth to finish rolling out
         the first base backup is from 07:10:00 UTC; waiting until 07:21:00 UTC
      (slept 360.0 s)
      """
    And "kubectl" was run with "-n auth rollout status statefulset/postgres --timeout=10m"

  Scenario: Wait asks again while there is no base backup
    Given a clock that moves only when slept
    And the server answers "*-c postgres -- sh -c *wal-g backup-list*" in turn:
      | answer                                                  |
      |                                                         |
      | [{"backup_name": "b", "time": "2026-11-06T07:00:00Z"}] |
    When the drill waits until it can run
    Then it is not refused
    And it prints:
      """
      == Waiting for statefulset/postgres in auth to finish rolling out
         no base backup yet; asking again in a minute
      (slept 60 s)
      """

  Scenario: Wait gives up after forty minutes
    Given a clock that moves only when slept
    And the server answers "*-c postgres -- sh -c *wal-g backup-list*" with nothing
    When the drill waits until it can run
    Then it is refused, saying "no base backup old enough after 40 minutes: see the wal-g container's log"
    And the refusal's "__class__.__name__" is "Stop"
