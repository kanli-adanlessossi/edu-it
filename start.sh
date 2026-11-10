#!/usr/bin/env bash
# Start the whole EDU-IT stack locally: PostgreSQL and Redis containers, migrations, content
# sync, the lab images, the worker, the API (Docker sandbox provider) and the web app.
#
# Usage:  ./start.sh
#
# This wraps `tools/e2e_stack.py up`, which records what it started in .e2e/stack.json so that
# ./stop.sh removes exactly that and nothing else, then `tools/dev_seed.py`, which creates the dev
# admin and approves the synced content. The admin's email and password come from DEV_ADMIN_EMAIL
# and DEV_ADMIN_PASSWORD or the git-ignored .env.dev-admin (template: .env.dev-admin.example);
# without them the seeding is skipped. Set ALPINE_MIRROR (for example
# https://mirror.leaseweb.com/alpine) when the Alpine CDN is unreachable.
# Windows: start.bat does the same.
set -euo pipefail

cd "$(dirname "$0")"

READY_TIMEOUT_S="${READY_TIMEOUT_S:-180}"

fail() {
    echo "start: $*" >&2
    exit 1
}

command -v uv >/dev/null 2>&1 || fail "uv is required (https://docs.astral.sh/uv/)"
command -v npm >/dev/null 2>&1 || fail "Node.js with npm is required"
command -v curl >/dev/null 2>&1 || fail "curl is required"
docker info >/dev/null 2>&1 || fail "the Docker daemon is not reachable; start Docker and retry"

if [ ! -d apps/web/node_modules ]; then
    echo "==> web dependencies (npm ci)"
    npm ci --prefix apps/web
fi

# `uv run` alone installs only the root project; the stack imports every workspace package
# (domain, application, infrastructure, content, api, worker), as CI does.
echo "==> Python workspace (uv sync --all-packages)"
uv sync --frozen --all-packages

uv run --frozen python tools/e2e_stack.py up

# Dev admin and content approval (credentials from DEV_ADMIN_* or the git-ignored .env.dev-admin;
# skipped with a message when none are configured).
uv run --frozen python tools/dev_seed.py

# `up` returns once the processes are started; wait until each one answers its probe.
wait_ready() {
    local name="$1" url="$2" waited=0
    printf '==> waiting for %s (%s)' "$name" "$url"
    until curl -fsS -o /dev/null --max-time 5 "$url"; do
        if [ "$waited" -ge "$READY_TIMEOUT_S" ]; then
            echo
            fail "$name did not become ready within ${READY_TIMEOUT_S}s; see the logs under .e2e/ and run ./stop.sh"
        fi
        printf '.'
        sleep 2
        waited=$((waited + 2))
    done
    echo " ready"
}

wait_ready api http://localhost:8000/ready
wait_ready worker http://localhost:8001/ready
wait_ready web http://localhost:3000/api/healthz

cat <<'EOF'

EDU-IT is running.
  Web:    http://localhost:3000  (/fr and /en)
  API:    http://localhost:8000
  Worker: http://localhost:8001/ready
Logs: .e2e/mail.log (API and the console mailer's verification links), .e2e/worker.log, .e2e/web.log
Stop everything with ./stop.sh
EOF
