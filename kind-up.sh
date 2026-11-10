#!/usr/bin/env bash
# Create (or re-apply) the local kind cluster used by the Kubernetes and Helm tests.
#
# Usage:  ./kind-up.sh [harness options]
#
# Builds the lab images when they are missing, then runs `tools/kind_harness.py up`: the `edu-it`
# cluster from deploy/kind/kind-config.yaml, Calico, metrics-server, ingress-nginx, the CRDs, the
# local registry at localhost:5001 and the lab images preloaded into every node. It is idempotent:
# an existing cluster is kept and the components are re-applied.
#
# Options are passed to the harness, for example:
#   ./kind-up.sh --dry-run                  print the plan, change nothing
#   ./kind-up.sh --skip ingress-nginx       leave ingress-nginx out (a network that cannot pull it)
#   ./kind-up.sh --skip-lab-images          do not preload the lab images
# Set ALPINE_MIRROR (for example https://mirror.leaseweb.com/alpine) when the Alpine CDN is
# unreachable. This is separate from start.sh: it does not start the local app stack.
# Remove the cluster with ./kind-down.sh. Windows: kind-up.bat does the same.
set -euo pipefail

cd "$(dirname "$0")"

CLUSTER=edu-it
LAB_IMAGES=(edu-it/lab-linux-base:dev edu-it/lab-python:dev)

fail() {
    echo "kind-up: $*" >&2
    exit 1
}

for tool in uv kind kubectl docker; do
    command -v "$tool" >/dev/null 2>&1 || fail "$tool is required but not on the PATH"
done
docker info >/dev/null 2>&1 || fail "the Docker daemon is not reachable; start Docker and retry"

# The harness preloads the lab images and refuses to run without them, so build what is missing
# (not for a dry run, and not when the preload is skipped).
build_images=true
for arg in "$@"; do
    case "$arg" in
        --dry-run | --skip-lab-images) build_images=false ;;
    esac
done
if [ "$build_images" = true ]; then
    for image in "${LAB_IMAGES[@]}"; do
        if ! docker image inspect "$image" >/dev/null 2>&1; then
            echo "==> lab images (docker buildx bake --load)"
            (cd lab-images && docker buildx bake --load)
            break
        fi
    done
fi

uv run --frozen python tools/kind_harness.py up "$@"

for arg in "$@"; do
    [ "$arg" = "--dry-run" ] && exit 0
done

echo
kubectl --context "kind-$CLUSTER" get nodes
cat <<'EOF'

The kind cluster is ready.
  Context:  kind-edu-it
  Registry: localhost:5001 (container kind-registry)
Run the kind tests with: uv run pytest -m kind --no-cov tests/k8s
Remove the cluster with ./kind-down.sh
EOF
