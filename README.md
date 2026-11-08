# pg-backup

A PostgreSQL database's backups on a k3s server, in two layers, and the tools to use them:

- **Every change:** PostgreSQL archives each finished write-ahead log segment with WAL-G, at
  least once a minute while anything changes, and a base backup is made each night. Both go to
  a Cloud Storage bucket, encrypted before they leave the server. Together they restore to any
  moment in the last 14 days.
- **Nightly:** the consumer's own `pg_dump` job, which needs neither WAL-G nor the PostgreSQL
  version. pg-backup runs it once more before it changes anything.

This repository holds the pieces a database needs, each released together:

| Piece | What it is |
|---|---|
| `image/` | `ghcr.io/sustenaut/pg-backup:X.Y.Z-pg16`: PostgreSQL 16.14 with WAL-G 3.0.9, CA certificates, and `walg-loop`, which takes the nightly base backup and prints a status line |
| `terraform/wal-archive/` | The bucket, its account (create and read only, plus deleting WAL-G's own upload chunks), and the lifecycle that deletes by age |
| `src/pg_backup/` | `pg-backup`: restore to a moment into a scratch namespace, drill, put a restore back, and make the archive's keys |
| `consumer/pg-backup` | The script a consuming repository copies to `backups/pg-backup`, pinning one release |

Nothing here knows which database it looks after. A consuming repository says so in
`backups/pg-backup.json`, which pg-backup checks against the schema its release ships
([`src/pg_backup/pg-backup.schema.json`](src/pg_backup/pg-backup.schema.json)) before it
touches the server, so a typo fails at once. [`examples/pg-backup.json`](examples/pg-backup.json)
is Sustenaut's sign-in database.

## Using it in a repository

1. **The bucket:** in the repository's Terraform:

   ```hcl
   module "wal" {
     source = "github.com/Sustenaut/pg-backup//terraform/wal-archive?ref=v0.1.0"

     project              = "sustenaut"
     region               = "europe-north1"
     bucket               = "sustenaut-auth-wal"
     prefix               = "keycloak"
     account_id           = "sustenaut-auth-wal"
     account_display_name = "Sustenaut auth WAL archive"
     account_description  = "WAL-G on the k3s server: archives the write-ahead log and nightly base backups."
     role_id              = "walChunkDelete" # a custom role is the project's: one per archive
   }
   ```

2. **The settings:** `backups/pg-backup.json` (the schema says what each one is), and
   `consumer/pg-backup` copied to `backups/pg-backup`. Check them with
   `./backups/pg-backup check`, which touches nothing.
3. **The keys,** once: `./backups/pg-backup setup-secrets`, from the repository's root, which
   needs its `./pubkit` and `./with-secrets`, gcloud, and a Bitwarden access token with write
   access, asked for at a hidden prompt. It makes the account's key and WAL-G's encryption key in
   Bitwarden and puts both in the cluster as the WAL Secret. A key already in Bitwarden is not
   made again, so after a rebuild it only puts the Secret back. **Losing the encryption key makes
   the archive unreadable.**
4. **The pod:** PostgreSQL from the image, archiving, and `walg-loop` beside it:

   ```yaml
   containers:
     - name: postgres
       image: ghcr.io/sustenaut/pg-backup:0.1.0-pg16
       args: [postgres, -c, archive_mode=on, -c, archive_timeout=60,
              -c, "archive_command=/usr/local/bin/wal-g wal-push %p"]
       env: &walg       # and the image's own POSTGRES_* and PGDATA
         - {name: WALG_GS_PREFIX, value: gs://sustenaut-auth-wal/keycloak}
         - {name: GOOGLE_APPLICATION_CREDENTIALS, value: /var/run/secrets/wal/gcs-key.json}
         - {name: WALG_LIBSODIUM_KEY, valueFrom: {secretKeyRef: {name: auth-wal, key: libsodium-key}}}
         - {name: WALG_LIBSODIUM_KEY_TRANSFORM, value: base64}
         - {name: WALG_PREVENT_WAL_OVERWRITE, value: "true"}
     - name: wal-g
       image: ghcr.io/sustenaut/pg-backup:0.1.0-pg16
       command: [/usr/local/bin/walg-loop]   # with PGHOST, PGUSER, PGDATABASE, PGPASSWORD too
   ```

   Both run as uid 999 and mount the data volume and the Secret's `gcs-key.json`.
   `PGBACKUP_BASE_AT` (HHMM, UTC, by default `0247`) sets the nightly base backup, and
   `PGBACKUP_STATUS_EVERY` (minutes, by default 5) the status line:
   `{"fact": "wal_archive", "last_archived_time": …, "failed_count": …, "last_backup": …}`.
5. **A drill,** right after the change that switches the pod deploys: `./backups/pg-backup drill --wait`.

Times meant for people (retry times, what a drill restored to) are in the laptop's own time zone,
with the zone named; the restore target PostgreSQL gets, and WAL-G's own times, stay in UTC.

## In pubkit's deploy walk: the `pg.database` need

pg-backup offers a need type to pubkit's deploy walk, under the entry point group
`sustenaut.needs`, so a repository's `pubkit.json` can say its deploy needs a database pg-backup
looks after:

```json
"deploy": {"namespace": "auth", "needs": [{"type": "pg.database"}]}
```

| Key | Default | What it is |
|---|---|---|
| `statefulset` | `postgres` | The database's StatefulSet; it names the need, and must be the one `backups/pg-backup.json` names |
| `backups` | `backups/pg-backup.json` | pg-backup's settings, in the repository |
| `container` | `wal-g` | The sidecar running `walg-loop`, whose log has the archive's status line |

`pubkit deploy types` lists it once pg-backup's wheel is installed beside pubkit. pg-backup imports
nothing from pubkit: it has what pubkit's need types have by their names.

**The check** (6 s, read-only) is the worst of:

1. `backups/pg-backup.json` as `check` reads it, in the deploy's namespace: else drift, with its first problem.
2. The StatefulSet, there and ready: else missing.
3. The last `wal_archive` status lines in the sidecar's last 50 log lines: drift when more archiving
   failed since the line before, when nothing was archived for 10 minutes while `pg_wal` grew, or
   when the last base backup is more than 26 hours old; unknown when there is no such line (the pod
   isn't on pg-backup's image).
4. The WAL Secret, with keys `gcs-key.json` and `libsodium-key` (their names alone are read).

kubectl that may not read something is unknown, naming the access it needs. The drill isn't in the
state yet, since no drill leaves a fact the check can read: a healthy database is ready, with the
drill in the details and "run `./backups/pg-backup drill --wait` and record it" as what to do.

**Fulfil** (grade auto, up to 300 s) does what `setup-secrets` does, through the walk's Bitwarden
writer and kubectl: the account's key (from gcloud) and the encryption key are made unless Bitwarden
has them, and the WAL Secret is applied with its manifest on kubectl's input, never on a command
line. A walk without a Bitwarden write token says it needs one, and is run at a terminal.

## The commands

Run from the repository's root, signed in to the server's tailnet. Everything reaches the
server as `root@<server>` over ssh and runs `k3s kubectl` there.

| Command | When | Exit codes |
|---|---|---|
| `drill [--to T] [--wait]` | Monthly, and after any change to the backups or PostgreSQL: restores to 10 minutes ago (or T), times it, deletes it. `--wait`, right after a merge: first waits for the StatefulSet's rollout and for a base backup old enough (up to 40 minutes) | 0 `DRILL PASSED`, under 15 minutes; 2 `DRILL NOT RUN`, no base backup that old yet or the database not on pg-backup's image yet, with what to do; 1 `DRILL FAILED` |
| `restore --to T` | To look at, or get back, the database at a moment (`latest`, or a time with its zone) | 0; 3 no base backup before T; 2 bad arguments; 1 anything else |
| `promote NS` | To put a restore you've checked back as the live database | 0; 1 not promoted, for the reason given; 2 no terminal, or not a scratch namespace |
| `clean NS` | To delete a scratch restore | 0; 2 not a scratch namespace |
| `setup-secrets` | Once, and after a rebuild | 0; 1 |
| `check` | Any time: checks `backups/pg-backup.json`, touching nothing | 0; 2 |

A bad `backups/pg-backup.json` is exit 2 for every command.

- **restore** makes `<namespace>-restore-<time>`, closed to all traffic by a network policy,
  copies the WAL Secret into it, and runs the image the live StatefulSet runs. It fetches the
  newest base backup from before the moment, replays WAL to it, promotes, and compares the
  check query's rows with live. It never touches the live namespace.
- **promote** runs only at a terminal, after you type `promote <namespace>`. It runs the dump
  CronJob once and waits for it, stops the app, and replaces the live database with
  `pg_dump | pg_restore --clean` in one transaction, so a failure leaves live as it was. Then it
  checks live against the restore and starts the app again, however it ends.
- **WAL-G v3.0.9's `wal-fetch` isn't used:** on Cloud Storage it gives up on the first
  compression it tries (`.lz4`), so restore reads each segment by its exact name with
  `wal-g st cat`. Segments are brotli (the image sets `WALG_COMPRESSION_METHOD`).

## Working on it

```sh
make venv && make check        # the suite, as CI runs it, with coverage
bin/pg-backup check            # this checkout's pg-backup, in a repository with backups/
```

The tests run against a fake server, so they need neither ssh nor a cluster.

## Releases

pubrel versions it by what changed: each pull request that changes code records it with
`pubrel add`, and `pubrel prepare` cuts a release pull request. On that pull request, set
`consumer/pg-backup`'s `version=` to the new version (pubrel moves only pyproject.toml; the suite
fails until they match), so a repository copying it from the tag runs that release. Merging it tags the release,
and the publish workflow attaches the wheel `backups/pg-backup` pins and pushes the image.
[CHANGELOG.md](CHANGELOG.md) lists every release.

Apache-2.0.
