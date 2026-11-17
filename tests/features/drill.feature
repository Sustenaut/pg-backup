Feature: The restore drill
  A drill restores the backup beside the live database, checks it, and says how it went.

  Scenario: A drill restores ten minutes ago and cleans up
    Then a drill restores ten minutes ago and cleans up

  Scenario: A slow restore fails
    Then a slow restore fails

  Scenario: Too early is not run with when to retry
    Then too early is not run with when to retry

  Scenario: Too early for a chosen moment asks for a later one
    Then too early for a chosen moment asks for a later one

  Scenario: An empty archive is not run
    Then an empty archive is not run

  Scenario: A server failure fails and still cleans up
    Then a server failure fails and still cleans up

  Scenario: A latest drill
    Then a latest drill

  Scenario: A namespace still on the old image is not run
    Then a namespace still on the old image is not run

  Scenario: Wait returns once the first backup is old enough
    Then wait returns once the first backup is old enough

  Scenario: People read times in this machine's zone
    Then people read times in this machine's zone

  Scenario: Wait asks again while there is no backup
    Then wait asks again while there is no backup

  Scenario: Wait gives up after forty minutes
    Then wait gives up after forty minutes
