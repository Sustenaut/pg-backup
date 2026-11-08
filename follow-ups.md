# Open follow-ups

Small pieces of work left open on purpose when a larger change landed: none blocks anything, and each is its own
pull request when someone takes it. When one is done, remove its row in that pull request.

| Follow-up | Where | Why it matters | Done when |
|---|---|---|---|
| **A drill fact the pg.database check can read** | Sustenaut/pg-backup: `src/pg_backup/drill.py`, `src/pg_backup/need.py` (`DRILL_FIX`) | The `pg.database` need can't tell whether the archive restores: a drill prints `DRILL PASSED` at a laptop and leaves nothing the check can read, so the check puts the drill in its details and leaves it out of the state. | A drill records its outcome where the check reads it (for example a ConfigMap or an annotation in the namespace, with its time and the moment restored to), and the check is drift when the last passed drill is older than a month or the last one failed. |
