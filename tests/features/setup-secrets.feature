Feature: setup-secrets
  setup-secrets makes the WAL archive's encryption key in Bitwarden, once, through the
  repository's ./pubsec, and puts the WAL Secret in the cluster through ./with-secrets.

  Background:
    Given a scratch directory
    And a file "backups/pg-backup.json" copied from "examples/pg-backup.json"
    And a file "pubsec" holding:
      """
      exec uvx --from "https://github.com/Sustenaut/pubsec/releases/download/v0.1.2/pubsec-0.1.2-py3-none-any.whl" pubsec "$@"
      """
    And a file "with-secrets" holding:
      """
      #!/bin/sh
      exec with-secrets "$@"
      """
    And the file "with-secrets" is executable
    And a stand-in "gcloud"
    And a stand-in "ssh"
    And a stand-in "uvx"
    And a stand-in "bws"

  Scenario: Setup makes the missing key, then puts the Secret
    Given a stand-in "with-secrets" that answers in turn:
      | args                                     | exit |
      | sh -c test -n *                          | 1    |
      | ./backups/pg-backup setup-secrets --push | 0    |
    When pg-backup runs "setup-secrets"
    Then it succeeds
    And it prints:
      """
      == 1. WAL-G's encryption key, auth-wal-key
      == 2. Into the cluster, as the Secret auth-wal
         done
      """
    And "gcloud" was run with "storage buckets describe gs://sustenaut-auth-wal"
    And "ssh" was run with "-o BatchMode=yes root@sustenaut true"
    And "uvx" was run with arguments matching "--from https://github.com/Sustenaut/pubsec/releases/download/v0.1.2/pubsec-0.1.2-py3-none-any.whl python -c * auth-wal-key WAL-G's encryption key for the write-ahead log archive gs://sustenaut-auth-wal/keycloak. Without it the archive can't be read."
    And "with-secrets" was run, in order, with arguments matching:
      """
      sh -c test -n "${AUTH_WAL_KEY:-}"
      ./backups/pg-backup setup-secrets --push
      """
    And "gcloud" was run 1 times
    And "uvx" was run 1 times
    And "with-secrets" was run 2 times

  Scenario: Setup keeps the key already in Bitwarden
    Given a stand-in "with-secrets"
    When pg-backup runs "setup-secrets"
    Then it succeeds
    And it prints:
      """
      == 1. WAL-G's encryption key, auth-wal-key
         already in Bitwarden
      == 2. Into the cluster, as the Secret auth-wal
         done
      """
    And "uvx" was not run
    And "with-secrets" was run with "./backups/pg-backup setup-secrets --push"

  Scenario: Setup needs bws
    Given a stand-in "with-secrets"
    And "bws" is not installed
    When pg-backup runs "setup-secrets"
    Then the exit code is 1
    And stderr mentions "bws is not installed"
    And "gcloud" was not run

  Scenario: Setup needs the bucket
    Given a stand-in "with-secrets"
    And a stand-in "gcloud" that fails, saying "not found"
    When pg-backup runs "setup-secrets"
    Then the exit code is 1
    And stderr mentions "no gs://sustenaut-auth-wal yet: apply the Terraform that makes it first"

  Scenario: Setup stops when the key can't be made
    Given a stand-in "with-secrets" that fails, saying "no such secret"
    And a stand-in "uvx" that fails, saying "refused"
    When pg-backup runs "setup-secrets"
    Then the exit code is 1
    And stderr mentions "couldn't make auth-wal-key"
    And "with-secrets" was not run with arguments matching "*--push"

  Scenario: Setup stops when the Secret can't be put
    Given a stand-in "with-secrets" that answers in turn:
      | args                                     | exit |
      | sh -c test -n *                          | 0    |
      | ./backups/pg-backup setup-secrets --push | 1    |
    When pg-backup runs "setup-secrets"
    Then the exit code is 1
    And stderr mentions "couldn't put the Secret in the cluster"

  Scenario: Setup needs the repository's ./pubsec
    Given a stand-in "with-secrets"
    And the file "pubsec" is removed
    When pg-backup runs "setup-secrets"
    Then the exit code is 1
    And stderr mentions "no ./pubsec: run this from the repository's root"

  Scenario: Setup needs the server to answer
    Given a stand-in "with-secrets"
    And a stand-in "ssh" that fails, saying "no route to host"
    When pg-backup runs "setup-secrets"
    Then the exit code is 1
    And stderr mentions "root@sustenaut doesn't answer: are you on the tailnet?"

  Scenario: Setup needs a pubsec wheel in ./pubsec to make the key
    Given a stand-in "with-secrets" that fails, saying "no such secret"
    And a file "pubsec" holding "no wheel here"
    When pg-backup runs "setup-secrets"
    Then the exit code is 1
    And stderr mentions "no pubsec wheel in ./pubsec"
    And "uvx" was not run

  Scenario: The WAL Secret is put from the environment, with the encryption key alone
    Given a healthy server
    And the server answers "*go-template*" with "libsodium-key "
    And the environment sets "AUTH_WAL_KEY" to "k3y-v4lue"
    And the secret "k3y-v4lue"
    When the WAL Secret is put in the cluster
    Then it is not refused
    And "kubectl" read the input:
      """
      {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "auth-wal", "namespace": "auth"}, "data": {"libsodium-key": "azN5LXY0bHVl"}}
      """
    And "kubectl" was run, in order, with arguments matching:
      """
      apply -f -
      -n auth get secret auth-wal -o go-template=*
      """
    And "kubectl" was run 2 times
    And no secret appears in what it produced

  Scenario: A Secret that still holds a bucket key is replaced
    Given a healthy server
    And the server answers "*go-template*" with "gcs-key.json libsodium-key "
    And the environment sets "AUTH_WAL_KEY" to "k3y-v4lue"
    When the WAL Secret is put in the cluster
    Then it is not refused
    And "kubectl" was run, in order, with arguments matching:
      """
      apply -f -
      -n auth get secret auth-wal -o go-template=*
      replace -f -
      """
    And "kubectl" read the input:
      """
      {"apiVersion": "v1", "kind": "Secret", "type": "Opaque", "metadata": {"name": "auth-wal", "namespace": "auth"}, "data": {"libsodium-key": "azN5LXY0bHVl"}}
      """

  Scenario: The WAL Secret is not put without its key in the environment
    Given a healthy server
    And the environment does not set "AUTH_WAL_KEY"
    When the WAL Secret is put in the cluster
    Then it is refused, saying "AUTH_WAL_KEY is not set: is it in Bitwarden?"
    And the refusal's "__class__.__name__" is "Stop"
    And "kubectl" was not run
