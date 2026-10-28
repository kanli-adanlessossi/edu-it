#!/usr/bin/env sh
# EDU-IT task runner (POSIX shell wrapper).
#
# Cross-platform companion to the Makefile that exposes the same uniform entry
# points — lint, format, typecheck, test — for environments without `make`.
# CI and pre-commit invoke these so every caller uses the same commands.
#
# Usage:  ./tasks.sh <lint|format|typecheck|test>
#
# The concrete per-language tool invocations are wired by a later task; until
# then each command fans out to no-op sub-steps that print what they will run,
# so the entry points exist and are callable now.
#
# Convention: a command exits non-zero (reporting the offending location) on a
# lint violation, type error, or test failure, and exits zero when clean.
set -eu

usage() {
    echo "Usage: ./tasks.sh <lint|format|typecheck|test>" >&2
    echo "  lint       Run all linters (TypeScript + Python)" >&2
    echo "  format     Run all formatters (TypeScript + Python)" >&2
    echo "  typecheck  Run all type checks (TypeScript + Python)" >&2
    echo "  test       Run all test suites (TypeScript + Python)" >&2
}

# web = TypeScript tooling in apps/web (real, wired to its package.json
# scripts); py = Python tooling in apps/api, apps/worker, packages/* (wired by a
# later task). The web step exits non-zero (reporting the offending file path
# and rule/location) on a violation, type error, or test failure.
WEB_DIR="apps/web"

run_web() {
    case "$1" in
        format) npm --prefix "$WEB_DIR" run format:check ;;
        *)      npm --prefix "$WEB_DIR" run "$1" ;;
    esac
}

# UV is the command prefix that runs the workspace's pinned Python toolchain
# (ruff, mypy, pytest). Override with UV= to use tools already on PATH.
UV="${UV:-uv run}"

# run_py fans the given task out to the real Python tooling across all five
# components (apps/api, apps/worker, packages/domain, packages/application,
# packages/infrastructure). Tool configuration and the pinned versions live in
# the root pyproject.toml, which scopes ruff/mypy/pytest to those directories.
# Exit-status contract (Req 1.4-1.7): each command exits non-zero and reports
# the offending location on a violation / type error / test failure, and exits
# zero when clean. `format` rewrites in place; CI/pre-commit use ruff's
# --check mode.
run_py() {
    case "$1" in
        lint)      $UV ruff check . ;;
        format)    $UV ruff format . ;;
        typecheck) $UV mypy ;;
        test)      $UV pytest ;;
    esac
}

run() {
    task="$1"
    run_web "$task"
    echo "[$task-py] Python $task (apps/api, apps/worker, packages/*)"
    run_py "$task"
}

if [ "$#" -ne 1 ]; then
    usage
    exit 2
fi

case "$1" in
    lint|format|typecheck|test) run "$1" ;;
    -h|--help|help) usage ;;
    *) echo "Unknown task: $1" >&2; usage; exit 2 ;;
esac
