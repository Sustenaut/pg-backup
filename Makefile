# pg-backup: a PostgreSQL database's WAL-G archive, restored, drilled and put back.

# The suite runs against src/ and bin/, with nothing installed.
FUNCTIONAL_ENV = PYTHONPATH=$(CURDIR)/src PG_BACKUP_BIN_DIR=$(CURDIR)/bin
EDITABLE =
include pubkit-python.mk

check: sync-check functional ## Everything CI runs
