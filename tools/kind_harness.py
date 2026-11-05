#!/usr/bin/env python3
"""kind test harness (kubernetes-deployment task 6.1, Req 16.1, 16.3, 16.6, 16.9).

Creates the ``edu-it`` kind cluster from ``deploy/kind/kind-config.yaml`` and installs, in order:

1. Calico (the default kind CNI does not enforce NetworkPolicy, Req 16.3),
2. metrics-server (for the HorizontalPodAutoscalers),
3. ingress-nginx,
4. the CRDs for ``ExternalSecret``, Gateway API and ``ServiceMonitor`` (``--crds`` selects the
   kinds the Chart renders; all three by default),
5. a local registry at ``localhost:5001``.

It then preloads the lab images into every node's containerd store, so that Sandbox_Ready_Time
excludes registry pulls, and prints the Kubernetes API server address for the kind values override
(``--set networkPolicy.kubernetesApi.cidrs=...``). The bundled PostgreSQL and Redis of
``values-dev.yaml`` are rendered by the Chart, not installed here.

Usage::

    python tools/kind_harness.py up [--dry-run] [--crds external-secrets,gateway-api,servicemonitor]
    python tools/kind_harness.py api-address
    python tools/kind_harness.py down

Pure planning and parsing functions are unit tested (``tools/tests/test_kind_harness.py``); the
``up`` command needs ``kind``, ``kubectl`` and ``docker`` on the PATH.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CLUSTER_NAME",
    "CRD_SOURCES",
    "KIND_TEST_VALUES",
    "LAB_IMAGES",
    "REGISTRY_PORT",
    "Step",
    "api_address_cidr",
    "build_plan",
    "check_kind_config",
    "helm_values_args",
    "main",
    "parse_api_endpoints",
    "pid_limit_from_config",
    "pid_limit_from_kind_config",
]

ROOT = Path(__file__).resolve().parents[1]
KIND_CONFIG = ROOT / "deploy" / "kind" / "kind-config.yaml"
DOMAIN_CONFIG = ROOT / "packages" / "domain" / "edu_it_domain" / "config.py"
KIND_TEST_VALUES = ROOT / "deploy" / "helm" / "edu-it" / "ci" / "values-kind-test.yaml"

CLUSTER_NAME = "edu-it"
REGISTRY_NAME = "kind-registry"
REGISTRY_PORT = 5001
REGISTRY_IMAGE = "registry:2"

# Pinned upstream versions. Bump deliberately.
CALICO_URL = "https://raw.githubusercontent.com/projectcalico/calico/v3.28.2/manifests/calico.yaml"
# Applied with ``kubectl apply -k`` (a git clone of the tag). The release asset URL
# (github.com/.../releases/download/...) redirects to a CDN that some networks block.
METRICS_SERVER_URL = "https://github.com/kubernetes-sigs/metrics-server/manifests/base?ref=v0.7.2"
INGRESS_NGINX_URL = (
    "https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.2/"
    "deploy/static/provider/kind/deploy.yaml"
)

#: CRD sources per kind that the Chart may render (Req 16.9).
_GATEWAY_API_RAW = (
    "https://raw.githubusercontent.com/kubernetes-sigs/gateway-api/v1.1.0/config/crd/standard/"
    "gateway.networking.k8s.io_"
)
CRD_SOURCES: Mapping[str, tuple[str, ...]] = {
    "external-secrets": (
        "https://raw.githubusercontent.com/external-secrets/external-secrets/v0.10.4/"
        "deploy/crds/bundle.yaml",
    ),
    # The standard channel of v1.1.0, file by file from raw.githubusercontent.com (the release
    # asset standard-install.yaml sits behind a CDN redirect that some networks block).
    "gateway-api": tuple(
        f"{_GATEWAY_API_RAW}{kind}.yaml"
        for kind in ("gatewayclasses", "gateways", "grpcroutes", "httproutes", "referencegrants")
    ),
    "servicemonitor": (
        "https://raw.githubusercontent.com/prometheus-operator/prometheus-operator/v0.77.1/"
        "example/prometheus-operator-crd/monitoring.coreos.com_servicemonitors.yaml",
    ),
}

#: Lab images built by ``lab-images/docker-bake.hcl``.
LAB_IMAGES: tuple[str, ...] = ("edu-it/lab-linux-base:dev", "edu-it/lab-python:dev")

_PID_LIMIT_CONSTANT = "SANDBOX_PID_LIMIT"


@dataclass(frozen=True)
class Step:
    """One command of the harness plan."""

    name: str
    argv: tuple[str, ...]
    # Steps that tolerate failure (for example "already exists") are not used: the harness is
    # idempotent through the explicit checks in ``_run_up``.


def _kubectl(*args: str) -> tuple[str, ...]:
    return ("kubectl", "--context", f"kind-{CLUSTER_NAME}", *args)


#: Components that ``--skip`` may leave out (a network that cannot pull their images). Nothing the
#: walking skeleton tests depends on ``ingress-nginx``; the skip is reported in the output.
SKIPPABLE: Mapping[str, tuple[str, ...]] = {
    "ingress-nginx": ("ingress-nginx", "ingress-nginx-ready")
}


def build_plan(
    crds: Sequence[str] = tuple(CRD_SOURCES),
    lab_images: Sequence[str] = LAB_IMAGES,
    skip: Sequence[str] = (),
) -> list[Step]:
    """Return the ordered install steps (cluster creation excluded; see ``_run_up``)."""
    unknown = [c for c in crds if c not in CRD_SOURCES]
    if unknown:
        raise ValueError(f"unknown CRD set(s): {', '.join(unknown)}")
    bad_skip = [s for s in skip if s not in SKIPPABLE]
    if bad_skip:
        raise ValueError(f"cannot skip: {', '.join(bad_skip)} (allowed: {', '.join(SKIPPABLE)})")
    plan = [
        Step("calico", _kubectl("apply", "-f", CALICO_URL)),
        Step(
            "calico-ready",
            _kubectl(
                "rollout", "status", "daemonset/calico-node", "-n", "kube-system", "--timeout=300s"
            ),
        ),
        Step("metrics-server", _kubectl("apply", "-k", METRICS_SERVER_URL)),
        Step(
            "metrics-server-insecure-tls",
            _kubectl(
                "patch",
                "deployment",
                "metrics-server",
                "-n",
                "kube-system",
                "--type=json",
                "-p",
                '[{"op":"add","path":"/spec/template/spec/containers/0/args/-",'
                '"value":"--kubelet-insecure-tls"}]',
            ),
        ),
        Step(
            "metrics-server-ready",
            _kubectl(
                "rollout",
                "status",
                "deployment/metrics-server",
                "-n",
                "kube-system",
                "--timeout=300s",
            ),
        ),
        Step("ingress-nginx", _kubectl("apply", "-f", INGRESS_NGINX_URL)),
        Step(
            "ingress-nginx-ready",
            _kubectl(
                "wait",
                "-n",
                "ingress-nginx",
                "--for=condition=ready",
                "pod",
                "--selector=app.kubernetes.io/component=controller",
                "--timeout=300s",
            ),
        ),
    ]
    for crd in crds:
        # Server-side apply: the ExternalSecret bundle exceeds the client-side annotation limit.
        plan.append(
            Step(
                f"crds-{crd}",
                _kubectl(
                    "apply",
                    "--server-side",
                    "--force-conflicts",
                    *(arg for url in CRD_SOURCES[crd] for arg in ("-f", url)),
                ),
            )
        )
    plan.extend(registry_steps())
    plan.extend(
        Step(f"preload-{img}", ("kind", "load", "docker-image", img, "--name", CLUSTER_NAME))
        for img in lab_images
    )
    skipped = {name for component in skip for name in SKIPPABLE[component]}
    return [step for step in plan if step.name not in skipped]


def helm_values_args(short_ttl: bool = False) -> tuple[str, ...]:
    """Extra ``helm`` arguments for a kind suite (Req 16.4, 16.11).

    The kind test override is passed only when a test needs a short session TTL; otherwise
    nothing is added, so the production Values_Files apply unchanged.
    """
    return ("-f", str(KIND_TEST_VALUES)) if short_ttl else ()


def registry_steps() -> list[Step]:
    """Steps for the local registry at ``localhost:5001`` (container ``kind-registry``)."""
    return [
        Step(
            "registry",
            (
                "docker",
                "run",
                "-d",
                "--restart=always",
                "-p",
                f"127.0.0.1:{REGISTRY_PORT}:5000",
                "--network",
                "bridge",
                "--name",
                REGISTRY_NAME,
                REGISTRY_IMAGE,
            ),
        ),
        Step("registry-network", ("docker", "network", "connect", "kind", REGISTRY_NAME)),
    ]


def registry_hosts_toml() -> str:
    """containerd ``hosts.toml`` that maps ``localhost:5001`` to the registry container."""
    return f'[host."http://{REGISTRY_NAME}:5000"]\n'


def registry_hosting_configmap() -> str:
    """The ``local-registry-hosting`` ConfigMap (KEP-1755) that advertises the registry."""
    return (
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: local-registry-hosting\n"
        "  namespace: kube-public\ndata:\n  localRegistryHosting.v1: |\n"
        f'    host: "localhost:{REGISTRY_PORT}"\n'
        f'    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"\n'
    )


def parse_api_endpoints(endpoints: Mapping[str, Any]) -> list[tuple[str, int]]:
    """Return ``(ip, port)`` pairs from ``kubectl get endpoints kubernetes -o json``."""
    result: list[tuple[str, int]] = []
    for subset in endpoints.get("subsets") or []:
        ports = [p["port"] for p in subset.get("ports") or [] if "port" in p]
        for address in subset.get("addresses") or []:
            ip = address.get("ip")
            if ip:
                result.extend((ip, int(port)) for port in ports)
    return result


def api_address_cidr(endpoints: Mapping[str, Any]) -> tuple[str, int]:
    """Return ``("<ip>/32", port)`` for the first API server endpoint.

    The value is the one for ``networkPolicy.kubernetesApi.cidrs`` (Req 11.13).
    """
    pairs = parse_api_endpoints(endpoints)
    if not pairs:
        raise ValueError("the kubernetes endpoints object lists no API server address")
    ip, port = pairs[0]
    return f"{ip}/32", port


def pid_limit_from_config(text: str) -> int:
    """Read ``SANDBOX_PID_LIMIT`` from the text of ``domain/config.py``."""
    match = re.search(rf"^{_PID_LIMIT_CONSTANT}\s*:[^=]*=\s*(\d+)\s*$", text, re.MULTILINE)
    if not match:
        raise ValueError(f"{_PID_LIMIT_CONSTANT} not found in domain config")
    return int(match.group(1))


def pid_limit_from_kind_config(text: str) -> int:
    """Read ``podPidsLimit`` from the text of the kind cluster config."""
    match = re.search(r"^\s*podPidsLimit:\s*(\d+)\s*(?:#.*)?$", text, re.MULTILINE)
    if not match:
        raise ValueError("podPidsLimit not found in the kind config")
    return int(match.group(1))


def check_kind_config(kind_config_text: str, domain_config_text: str) -> list[str]:
    """Return violations of the kind config against the domain constant and Req 16.3."""
    errors: list[str] = []
    try:
        limit = pid_limit_from_config(domain_config_text)
        if pid_limit_from_kind_config(kind_config_text) != limit:
            errors.append(f"podPidsLimit differs from {_PID_LIMIT_CONSTANT} ({limit})")
    except ValueError as exc:
        errors.append(str(exc))
    if not re.search(r"^\s*singleProcessOOMKill:\s*true\s*$", kind_config_text, re.MULTILINE):
        errors.append("singleProcessOOMKill must be true (an OOM kills one process, as in Docker)")
    if not re.search(r"^\s*disableDefaultCNI:\s*true\s*$", kind_config_text, re.MULTILINE):
        errors.append("disableDefaultCNI must be true (Calico enforces NetworkPolicy)")
    return errors


def _run(argv: Sequence[str], *, input_text: str | None = None, check: bool = True) -> str:
    proc = subprocess.run(
        list(argv),
        input=input_text,
        text=True,
        encoding="utf-8",  # kubectl and docker print UTF-8; the Windows default is cp1252
        errors="replace",
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(argv)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def _cluster_exists() -> bool:
    return CLUSTER_NAME in _run(("kind", "get", "clusters"), check=False).split()


def _registry_running() -> bool:
    out = _run(
        ("docker", "inspect", "-f", "{{.State.Running}}", REGISTRY_NAME), check=False
    ).strip()
    return out == "true"


def _configure_registry_on_nodes() -> None:
    nodes = _run(("kind", "get", "nodes", "--name", CLUSTER_NAME)).split()
    directory = f"/etc/containerd/certs.d/localhost:{REGISTRY_PORT}"
    for node in nodes:
        _run(("docker", "exec", node, "mkdir", "-p", directory))
        _run(
            ("docker", "exec", "-i", node, "sh", "-c", f"cat > {directory}/hosts.toml"),
            input_text=registry_hosts_toml(),
        )
    _run(_kubectl("apply", "-f", "-"), input_text=registry_hosting_configmap())


def _api_address() -> tuple[str, int]:
    out = _run(_kubectl("get", "endpoints", "kubernetes", "-n", "default", "-o", "json"))
    return api_address_cidr(json.loads(out))


def _check_prerequisites(lab_images: Sequence[str]) -> None:
    errors = check_kind_config(
        KIND_CONFIG.read_text(encoding="utf-8"), DOMAIN_CONFIG.read_text(encoding="utf-8")
    )
    for image in lab_images:
        if (
            subprocess.run(
                ["docker", "image", "inspect", image], capture_output=True, check=False
            ).returncode
            != 0
        ):
            errors.append(
                f"lab image {image} is not built (docker buildx bake -f lab-images/docker-bake.hcl)"
            )
    if errors:
        raise RuntimeError("; ".join(errors))


def _run_up(
    crds: Sequence[str], lab_images: Sequence[str], dry_run: bool, skip: Sequence[str] = ()
) -> int:
    plan = build_plan(crds, lab_images, skip)
    for component in skip:
        print(f"SKIPPED {component} (--skip): not installed, not verified", flush=True)
    if dry_run:
        print(f"kind create cluster --config {KIND_CONFIG}")
        for step in plan:
            print(f"[{step.name}] {' '.join(step.argv)}")
        return 0
    _check_prerequisites(lab_images)
    if not _cluster_exists():
        _run(("kind", "create", "cluster", "--config", str(KIND_CONFIG)))
    for step in plan:
        if step.name == "registry":
            if _registry_running():
                continue
            _run(("docker", "rm", "-f", REGISTRY_NAME), check=False)
        if step.name == "registry-network":
            # Already connected is not an error.
            _run(step.argv, check=False)
            _configure_registry_on_nodes()
            continue
        print(f"==> {step.name}", flush=True)
        _run(step.argv)
    cidr, port = _api_address()
    print(f"Kubernetes API server: {cidr} (port {port})")
    print(f"Values override: --set networkPolicy.kubernetesApi.cidrs={{{cidr}}}")
    return 0


def main(argv: Sequence[str] | None = None, runner: Callable[..., int] = _run_up) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    up = sub.add_parser("up", help="create the cluster and install the prerequisites")
    up.add_argument("--dry-run", action="store_true", help="print the plan only")
    up.add_argument("--crds", default=",".join(CRD_SOURCES), help="comma-separated CRD sets")
    up.add_argument("--skip-lab-images", action="store_true", help="do not preload lab images")
    up.add_argument(
        "--skip",
        default="",
        help=f"comma-separated components to leave out ({', '.join(SKIPPABLE)}), for a network "
        "that cannot pull their images",
    )
    sub.add_parser("api-address", help="print the Kubernetes API server address")
    sub.add_parser("down", help="delete the cluster and the local registry")
    values = sub.add_parser("values-args", help="print extra helm arguments for a kind suite")
    values.add_argument(
        "--short-ttl", action="store_true", help="add the kind test values override (short TTL)"
    )
    args = parser.parse_args(argv)
    try:
        if args.command == "values-args":
            print(" ".join(helm_values_args(args.short_ttl)))
            return 0
        if args.command == "up":
            crds = [c for c in args.crds.split(",") if c]
            images = () if args.skip_lab_images else LAB_IMAGES
            skip = [s for s in args.skip.split(",") if s]
            return runner(crds, images, args.dry_run, skip)
        if args.command == "api-address":
            cidr, port = _api_address()
            print(f"{cidr} {port}")
            return 0
        _run(("kind", "delete", "cluster", "--name", CLUSTER_NAME))
        _run(("docker", "rm", "-f", REGISTRY_NAME), check=False)
        return 0
    except (RuntimeError, ValueError) as exc:
        print(f"kind_harness: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
