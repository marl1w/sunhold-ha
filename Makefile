# Every way of running this project, in one place. `make` on its own lists them.

VENV   ?= .venv
PYTHON ?= python3
PY     := $(VENV)/bin/python
PORT   ?= 8124

.DEFAULT_GOAL := help
.PHONY: help setup check test lint format preview

help: ## List the targets
	@grep -hE '^[a-z][a-z-]*:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "} {printf "  %-10s %s\n", $$1, $$2}'

setup: ## Create the virtualenv and install the test dependencies
	@scripts/check.sh --setup

check: ## Everything CI runs: lint, format, tests, and the panel checks
	@scripts/check.sh

test: ## The Python test suite
	@$(PY) -m pytest tests -q

lint: ## Ruff lint
	@$(PY) -m ruff check .

format: ## Ruff format, writing the files
	@$(PY) -m ruff format .

preview: ## The panel in a browser with sample data, no Home Assistant needed
	@$(PY) scripts/preview.py --port $(PORT)
