# Managed by pubkit 0.37.0: `pubkit sync` rewrites this file, and
# `pubkit sync --check` fails when it differs. Include it from the Makefile,
# and set the variables below there, before the include; targets of your own
# go there too. It is the same in every Python package, so a rule set here
# (the suite on every core, its coverage in coverage/coverage.xml) holds in each.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help
export PATH := $(CURDIR)/.venv/bin:$(PATH)

# Arguments to pytest: the suite runs on every core (pytest-xdist, which
# tests/requirements-pubkit.txt pins); PYTEST_ARGS = runs it on one.
PYTEST_ARGS ?= -n auto
# Variables the suite needs, as NAME=value words, such as
# PYTHONPATH=$(CURDIR)/src for a package that is not installed.
FUNCTIONAL_ENV ?=
# What venv installs editable, with no dependencies: the checkout, or nothing.
EDITABLE ?= .

.PHONY: help venv functional lock sync-check wheel clean

help: ## Available targets
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  %-12s %s\n", $$1, $$2}'

venv: ## Hash-locked environment, with this checkout installed editable
	python3 -m venv .venv
	tests/install-lock .venv/bin/pip
	$(if $(EDITABLE),.venv/bin/pip install --quiet --no-deps -e $(EDITABLE))

functional: ## The Gherkin suite on every core, with its coverage in coverage/coverage.xml
	rm -rf coverage && mkdir coverage
	cd tests && $(FUNCTIONAL_ENV) COVERAGE_RCFILE=$(CURDIR)/pyproject.toml COVERAGE_FILE=$(CURDIR)/coverage/.coverage \
	  ../.venv/bin/coverage run -m pytest -q $(PYTEST_ARGS)
	COVERAGE_FILE=coverage/.coverage .venv/bin/coverage combine -q
	COVERAGE_FILE=coverage/.coverage .venv/bin/coverage xml -q

lock: ## Pin tests/requirements.txt, with hashes, in tests/requirements.lock
	./pubkit lock

sync-check: ## Fail if pubkit's managed files differ from the pinned version's
	./pubkit sync --check

wheel: ## Build the wheel a release carries
	.venv/bin/pip wheel --quiet --no-deps . -w dist

clean: ## Remove build artifacts
	rm -rf build dist coverage src/*.egg-info .pytest_cache tests/.pytest_cache
