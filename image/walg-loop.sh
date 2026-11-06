#!/bin/sh
# The WAL-G container beside PostgreSQL. It takes a base backup when it starts if it has none
# from today, and each night at PGBACKUP_BASE_AT (HHMM, UTC, by default 0247), and every
# PGBACKUP_STATUS_EVERY minutes (by default 5) prints one line of JSON saying how the archive is
# doing, which the server's logs keep:
#   {"fact": "wal_archive", "last_archived_time": ..., "failed_count": ..., "last_backup": ...}
# WAL itself is archived by PostgreSQL's archive_command, not here.
set -u
base_at=${PGBACKUP_BASE_AT:-0247}
every=${PGBACKUP_STATUS_EVERY:-5}
state=/walg-state
mkdir -p "$state"

until pg_isready -q; do sleep 5; done

backup() {
  if wal-g backup-push "$PGDATA"; then
    date -u +%Y-%m-%d > "$state/last-backup-day"
    date -u +%Y-%m-%dT%H:%M:%SZ > "$state/last-backup"
  else
    echo '{"fact": "wal_base_backup", "ok": false}'
  fi
}

minute=0
while true; do
  today=$(date -u +%Y-%m-%d)
  if [ "$(cat "$state/last-backup-day" 2>/dev/null)" != "$today" ] &&
     { [ ! -f "$state/last-backup" ] || [ "$(date -u +%H%M)" -ge "$base_at" ]; }; then
    backup
  fi
  if [ $((minute % every)) -eq 0 ]; then
    psql -Atc "select json_build_object('fact', 'wal_archive', 'last_archived_wal', last_archived_wal,
      'last_archived_time', last_archived_time, 'failed_count', failed_count, 'last_failed_time', last_failed_time,
      'pg_wal_bytes', (select sum(size) from pg_ls_waldir()),
      'last_backup', nullif('$(cat "$state/last-backup" 2>/dev/null)', ''))::text from pg_stat_archiver" ||
      echo '{"fact": "wal_archive", "ok": false}'
  fi
  minute=$((minute + 1))
  sleep 60
done
