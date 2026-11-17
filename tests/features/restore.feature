Feature: Restoring and promoting a backup
  A restore lands beside live; promote replaces live in one transaction, after a dump.

  Scenario: Targets need a zone
    Then targets need a zone

  Scenario: The newest backup before the moment is chosen
    Then the newest backup before the moment is chosen

  Scenario: A restore goes into a closed scratch namespace
    Then a restore goes into a closed scratch namespace

  Scenario: Latest sets no target
    Then latest sets no target

  Scenario: Too early is exit 3 with the oldest
    Then too early is exit 3 with the oldest

  Scenario: An empty archive is too early too
    Then an empty archive is too early too

  Scenario: A failure before the namespace names none
    Then a failure before the namespace names none

  Scenario: An image without wal-g stops
    Then an image without wal-g stops

  Scenario: A restore missing a required row fails
    Then a restore missing a required row fails

  Scenario: Recovery that never ends times out
    Then recovery that never ends times out

  Scenario: Clean deletes only scratch namespaces
    Then clean deletes only scratch namespaces

  Scenario: Promote refuses a live namespace
    Then promote refuses a live namespace

  Scenario: Promote needs a running restore
    Then promote needs a running restore

  Scenario: Promote needs every required row
    Then promote needs every required row

  Scenario: Promote needs a terminal and the exact words
    Then promote needs a terminal and the exact words

  Scenario: Promote dumps first and stops without the dump
    Then promote dumps first and stops without the dump

  Scenario: Promote replaces live in one transaction and starts the app
    Then promote replaces live in one transaction and starts the app

  Scenario: A failed pg_restore leaves live and starts the app
    Then a failed pg_restore leaves live and starts the app

  Scenario: Live not matching after promote fails and starts the app
    Then live not matching after promote fails and starts the app

  Scenario: An idle database gets a segment closed so the archive covers the moment
    Then an idle database gets a segment closed so the archive covers the moment

  Scenario: A segment live never archives stops
    Then a segment live never archives stops

  Scenario: Latest needs no segment closed
    Then latest needs no segment closed

  Scenario: A restore whose PostgreSQL stopped fails at once with its log
    Then a restore whose PostgreSQL stopped fails at once with its log

  Scenario: A live credential that impersonates nothing stops the restore
    Then a live credential that impersonates nothing stops the restore
