# EDU-IT top-level task runner.
#
# This is the single top-level task runner (per the foundations design). It
# exposes uniform `lint`, `format`, `typecheck`, and `test` entry points that CI
# and the pre-commit hooks invoke, so every caller uses the same commands.
#
# Each top-level target fans out to the per-language sub-targets:
#   - web  : TypeScript tooling in apps/web
#   - py   : Python tooling in apps/api, apps/worker, and packages/*
#
# The concrete tool invocations behind each sub-target are wired by a later
# task (TypeScript tooling and Python tooling with pinned versions). Until then
# the sub-targets are no-ops that print what they will run, so the entry points
# exist and are callable now and CI/pre-commit can target them from day one.
#
# Convention: every command exits non-zero (reporting the offending location) on
# a lint violation, type error, or test failure, and exits zero when clean.

.DEFAULT_GOAL := help

# -- Uniform top-level entry points -------------------------------------------

.PHONY: lint format typecheck test
lint: lint-web lint-py lint-domain-boundary  ## Run all linters (TypeScript + Python + domain boundary)
format: format-web format-py    ## Run all formatters (TypeScript + Python)
typecheck: typecheck-web typecheck-py  ## Run all type checks (TypeScript + Python)
test: test-web test-py          ## Run all test suites (TypeScript + Python)

# -- TypeScript (apps/web) ----------------------------------------------------
# Real apps/web TypeScript tooling, invoked through its package.json scripts so
# the exact tool invocations and pinned versions live in one place. Each script
# exits non-zero (reporting the offending file path and rule/location) on a
# violation, type error, or test failure, and exits zero when clean (Req 1.1,
# 1.3-1.7). `format-web` runs the non-mutating check so it is safe in CI and
# pre-commit; `npm --prefix` runs in apps/web without a directory change.

WEB_DIR := apps/web

.PHONY: lint-web format-web typecheck-web test-web
lint-web:
	npm --prefix $(WEB_DIR) run lint
format-web:
	npm --prefix $(WEB_DIR) run format:check
typecheck-web:
	npm --prefix $(WEB_DIR) run typecheck
test-web:
	npm --prefix $(WEB_DIR) run test

# -- Python (apps/api, apps/worker, packages/*) -------------------------------
# Real Python lint/format/typecheck/test commands over all five components
# (apps/api, apps/worker, packages/domain, packages/application,
# packages/infrastructure). Tools and their configuration live in the root
# pyproject.toml, which scopes ruff/mypy/pytest to exactly those five
# directories; versions are pinned exactly per docs/adr/0001-versions.md.
#
# UV = uv run (preferred: uses the workspace's pinned toolchain). It is
# overridable (e.g. `make lint-py UV=` to use tools already on PATH).
#
# Exit-status contract (Req 1.4-1.7): ruff check / ruff format --check / mypy /
# pytest each exit non-zero and report the offending location on a violation,
# type error, or test failure, and exit zero when clean. `format-py` rewrites
# files in place; `format-check-py` verifies formatting without writing and is
# what CI / pre-commit invoke.

UV ?= uv run

.PHONY: lint-py format-py format-check-py typecheck-py test-py
lint-py:
	$(UV) ruff check .
format-py:
	$(UV) ruff format .
format-check-py:
	$(UV) ruff format --check .
typecheck-py:
	$(UV) mypy
test-py:
	$(UV) pytest

# -- Domain import-boundary check (Req 1.8) -----------------------------------
# Self-contained, dedicated check: asserts packages/domain (the framework-free
# shared domain) imports no web or API framework. Standard-library only (no new
# pinned tool). Exits non-zero naming the offending file+import on a violation,
# zero when the domain is clean.

.PHONY: lint-domain-boundary
lint-domain-boundary:  ## Assert packages/domain imports no web or API framework (Req 1.8)
	python tools/check_domain_boundary.py

# -- Help ---------------------------------------------------------------------

.PHONY: help
help:  ## List the available targets
	@echo "EDU-IT task runner. Uniform entry points used by CI and pre-commit:"
	@echo "  make lint        Run all linters (TypeScript + Python)"
	@echo "  make format      Run all formatters (TypeScript + Python)"
	@echo "  make typecheck   Run all type checks (TypeScript + Python)"
	@echo "  make test        Run all test suites (TypeScript + Python)"
	@echo "  make lint-domain-boundary   Assert packages/domain imports no web/API framework"
