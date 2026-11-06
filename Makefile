# pg-backup: a PostgreSQL database's WAL-G archive, restored, drilled and put back.
.PHONY: help venv functional lock sync-check check

help: ## Available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

venv: ## Create .venv from the hash-locked test requirements
	python3 -m venv .venv
	.venv/bin/pip install --quiet --require-hashes -r tests/requirements.lock

functional: ## The suite against src/, with its coverage in coverage/coverage.xml
	rm -rf coverage && mkdir coverage
	cd tests && PG_BACKUP_BIN_DIR=$(CURDIR)/bin PYTHONPATH=$(CURDIR)/src COVERAGE_RCFILE=$(CURDIR)/pyproject.toml COVERAGE_FILE=$(CURDIR)/coverage/.coverage \
	  ../.venv/bin/coverage run -m pytest -q $(PYTEST_ARGS)
	COVERAGE_FILE=coverage/.coverage .venv/bin/coverage combine -q
	COVERAGE_FILE=coverage/.coverage .venv/bin/coverage xml -q

lock: ## Pin tests/requirements.txt with hashes
	.venv/bin/pubkit lock

sync-check: ## Fail if pubkit's managed files drifted
	.venv/bin/pubkit sync --check

check: sync-check functional ## Everything CI runs
