#!/usr/bin/env bash
# Delete the local kind cluster and its registry.
#
# Usage:  ./kind-down.sh [-y|--yes]
#
# Runs `tools/kind_harness.py down`, which deletes the `edu-it` kind cluster and the `kind-registry`
# container. Everything in them is lost: the Helm releases, the lab pods and the images pushed to
# localhost:5001. Recreating them takes several minutes, so this asks first. Pass --yes to skip the
# question (required when there is no terminal). It does not stop the local app stack: use
# ./stop.sh for that. Windows: kind-down.bat does the same.
set -euo pipefail

cd "$(dirname "$0")"

CLUSTER=edu-it
REGISTRY=kind-registry

fail() {
    echo "kind-down: $*" >&2
    exit 1
}

assume_yes=false
for arg in "$@"; do
    case "$arg" in
        -y | --yes) assume_yes=true ;;
        *) fail "unknown option: $arg (usage: ./kind-down.sh [-y|--yes])" ;;
    esac
done

for tool in uv kind docker; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required but not on the PATH"
done
docker info >/dev/null 2>&1 || fail "the Docker daemon is not reachable; start Docker and retry"

cluster_exists=false
kind get clusters 2>/dev/null | grep -qx "$CLUSTER" && cluster_exists=true
registry_exists=false
docker inspect "$REGISTRY" >/dev/null 2>&1 && registry_exists=true
if [ "$cluster_exists" = false ] && [ "$registry_exists" = false ]; then
    echo "kind-down: nothing to remove (no '$CLUSTER' cluster, no '$REGISTRY' container)."
    exit 0
fi

if [ "$assume_yes" = false ]; then
    [ -t 0 ] || fail "no terminal to ask on; pass --yes to confirm the deletion"
    echo "This deletes the kind cluster '$CLUSTER' and the registry container '$REGISTRY'."
    echo "Helm releases, lab pods and images pushed to localhost:5001 are lost."
    read -r -p "Continue? [y/N] " reply
    case "$reply" in
        y | Y | yes | YES) ;;
        *) echo "kind-down: cancelled."; exit 1 ;;
    esac
fi

uv run --frozen python tools/kind_harness.py down
echo "The kind cluster and its registry are removed."
