#!/usr/bin/env bash
# Stop the local EDU-IT stack started by ./start.sh.
#
# Usage:  ./stop.sh
#
# Thin wrapper around `tools/e2e_stack.py down`: it stops the worker, API and web processes and
# removes the PostgreSQL and Redis containers recorded in .e2e/stack.json. Containers it did not
# start (for example a Docker Compose stack) are left running. Windows: stop.bat does the same.
set -euo pipefail

cd "$(dirname "$0")"

command -v uv >/dev/null 2>&1 || { echo "stop: uv is required (https://docs.astral.sh/uv/)" >&2; exit 1; }

uv run --frozen python tools/e2e_stack.py down
echo "EDU-IT is stopped."
