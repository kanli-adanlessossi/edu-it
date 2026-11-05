"""Shared helpers of the Upgrade_Test (task 14.4) and the rollback test (task 14.5).

Nothing here needs a cluster to import. The pure parts (resolving the previous release, exporting
its chart, reading digests, the signature gate) are unit tested without one; the cluster parts
(:class:`UpgradeHarness`) shell out to ``helm`` and ``kubectl`` and talk to the Api through the
ingress, so they only run in the kind job.

Previous release (Req 25.8, risk R9): the most recent release tag, or the base commit when the
repository has no release tag. Its chart is exported with ``git archive`` into a temporary
directory. Its image digests come from
``EDU_IT_UPGRADE_PREVIOUS_DIGEST_{API,WORKER,WEB,MIGRATIONS}`` or, when those are unset,
from the pinned digests of the exported chart's ``values-dev.yaml``
(release tags carry real digests, the all-zero placeholders are rejected).

Candidate: the working-tree chart with the digests of ``EDU_IT_KIND_DIGEST_{API,WORKER,MIGRATIONS}``
(the variables of the smoke test; ``EDU_IT_KIND_DIGEST_WEB`` is optional and falls back to the
previous web digest). The candidate migrations image must hold at least one migration that the
previous one lacks (Req 25.1); its expected schema version is the Alembic head of the working tree
or ``EDU_IT_UPGRADE_CANDIDATE_SCHEMA_VERSION``.

Signature gate (Req 12.14, 15.8, task 13.2): every install is preceded by
``tools/verify_image_signatures.py`` over the exact digests about to be installed. The default mode
is ``pull-request`` (local-registry digests only, the skip is recorded in the output); set
``EDU_IT_VERIFY_SIGNATURES_MODE=cosign`` and ``EDU_IT_COSIGN_IDENTITY_REGEXP`` on the default branch
and release tags.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.k8s.conftest import LABS_NAMESPACE, PSA_LABELS

__all__ = [
    "CANDIDATE_RECONNECT_MAX_S",
    "IMAGE_NAMES",
    "PLATFORM_NAMESPACE",
    "PodIdentity",
    "PreviousRelease",
    "ReleaseNotResolvable",
    "SignatureGate",
    "SignatureGateFailed",
    "TerminalClient",
    "UpgradeHarness",
    "base_commit",
    "candidate_digests",
    "candidate_schema_version",
    "export_chart",
    "latest_release_tag",
    "previous_digests",
    "resolve_previous_release",
    "run_signature_gate",
    "wait_until",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
CHART_RELATIVE = Path("deploy") / "helm" / "edu-it"
CHART = REPO_ROOT / CHART_RELATIVE
HARNESS = REPO_ROOT / "tools" / "kind_harness.py"

RELEASE = "edu-it"
PLATFORM_NAMESPACE = "platform"
IMAGE_NAMES = ("web", "api", "worker", "migrations")
ZERO_DIGEST = "sha256:" + "0" * 64
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
_RELEASE_TAG = re.compile(r"^v?\d+\.\d+\.\d+([-+].+)?$")

# Time limits of Requirement 25.
SESSIONS_RUNNING_MAX_S = 120.0  # 25.1 each Lab_Session reports a running Lab_Pod
POST_UPGRADE_SESSIONS_MAX_S = 120.0  # 25.2 same Lab_Pod and an attachable terminal
POST_UPGRADE_READY_MAX_S = 300.0  # 25.3 migration applied and Api/Worker pods ready
CANDIDATE_RECONNECT_MAX_S = 30.0  # 25.4 terminal client reconnects after its socket closed
HELM_TIMEOUT = "600s"

ENV_PREVIOUS_DIGEST = "EDU_IT_UPGRADE_PREVIOUS_DIGEST_{}"
ENV_CANDIDATE_DIGEST = "EDU_IT_KIND_DIGEST_{}"
ENV_FAILING_MIGRATION_DIGEST = "EDU_IT_UPGRADE_FAILING_MIGRATION_DIGEST"
ENV_BASE_REF = "EDU_IT_UPGRADE_BASE_REF"
ENV_API_BASE = "EDU_IT_UPGRADE_API_BASE"
ENV_SCHEMA_VERSION = "EDU_IT_UPGRADE_CANDIDATE_SCHEMA_VERSION"
ENV_SIGNATURE_MODE = "EDU_IT_VERIFY_SIGNATURES_MODE"
ENV_COSIGN_IDENTITY = "EDU_IT_COSIGN_IDENTITY_REGEXP"
DEFAULT_BASE_REF = "origin/develop"
DEFAULT_API_BASE = "http://edu-it.localtest.me"

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


class ReleaseNotResolvable(RuntimeError):
    """The previous release cannot be built from the repository or the environment."""


# --------------------------------------------------------------------------- generic helpers


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float = 900,
    input_text: str | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a fixed argv (no shell). With ``check`` a non-zero exit raises with the output tail."""

    done = subprocess.run(
        list(argv),
        cwd=cwd,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if check and done.returncode != 0:
        raise AssertionError(
            f"{' '.join(argv)} failed ({done.returncode}):\n"
            f"{done.stdout[-3000:]}\n{done.stderr[-3000:]}"
        )
    return done


def wait_until[T](
    probe: Callable[[], T | None],
    *,
    timeout_s: float,
    what: str,
    interval_s: float = 2.0,
) -> T:
    """Poll ``probe`` until it returns a truthy value; raise ``AssertionError`` on timeout."""

    deadline = time.monotonic() + timeout_s
    last: Exception | None = None
    while True:
        try:
            value = probe()
            if value:
                return value
        except (AssertionError, OSError, RuntimeError) as exc:  # transient while rolling
            last = exc
        if time.monotonic() >= deadline:
            suffix = f" (last error: {last})" if last else ""
            raise AssertionError(f"{what} did not happen within {timeout_s:.0f} s{suffix}")
        time.sleep(interval_s)


# --------------------------------------------------------------------------- previous release


@dataclass(frozen=True)
class PreviousRelease:
    """Where the previous release comes from: ``source`` is ``tag`` or ``base-commit``."""

    ref: str
    source: str


def _git(repo: Path, *args: str) -> str:
    return run(["git", "-C", str(repo), *args], timeout=120).stdout.strip()


def latest_release_tag(repo: Path = REPO_ROOT) -> str | None:
    """The most recent release tag (semantic-version order), or ``None`` when there is none."""

    out = run(
        ["git", "-C", str(repo), "tag", "--list", "--sort=-v:refname"], timeout=60, check=False
    ).stdout
    for line in out.splitlines():
        if _RELEASE_TAG.match(line.strip()):
            return line.strip()
    return None


def base_commit(repo: Path = REPO_ROOT, base_ref: str | None = None) -> str:
    """The merge base of ``HEAD`` and ``base_ref``; the parent commit when HEAD is the base.

    Without a base ref that resolves (a shallow clone for instance) the parent of ``HEAD`` is used.
    """

    ref = base_ref or os.environ.get(ENV_BASE_REF) or DEFAULT_BASE_REF
    head = _git(repo, "rev-parse", "HEAD")
    candidate = run(["git", "-C", str(repo), "merge-base", "HEAD", ref], check=False, timeout=60)
    base = candidate.stdout.strip() if candidate.returncode == 0 else ""
    if not base or base == head:
        parent = run(["git", "-C", str(repo), "rev-parse", "HEAD^"], check=False, timeout=60)
        if parent.returncode != 0 or not parent.stdout.strip():
            raise ReleaseNotResolvable("no release tag and no base commit before HEAD")
        base = parent.stdout.strip()
    return base


def resolve_previous_release(
    repo: Path = REPO_ROOT, base_ref: str | None = None
) -> PreviousRelease:
    """Req 25.8: the most recent release tag, else the base commit."""

    tag = latest_release_tag(repo)
    if tag is not None:
        return PreviousRelease(tag, "tag")
    return PreviousRelease(base_commit(repo, base_ref), "base-commit")


def export_chart(ref: str, dest: Path, repo: Path = REPO_ROOT) -> Path:
    """Export the Helm chart as of ``ref`` into ``dest`` and return the chart directory."""

    done = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", ref, CHART_RELATIVE.as_posix()],
        capture_output=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise ReleaseNotResolvable(
            f"git archive {ref} failed: {done.stderr.decode('utf-8', 'replace')[-500:]}"
        )
    with tarfile.open(fileobj=io.BytesIO(done.stdout)) as archive:
        archive.extractall(dest, filter="data")
    chart = dest / CHART_RELATIVE
    if not (chart / "Chart.yaml").is_file():
        raise ReleaseNotResolvable(f"{ref} has no chart at {CHART_RELATIVE.as_posix()}")
    return chart


def _pinned(value: object) -> str | None:
    return (
        value if isinstance(value, str) and _DIGEST.match(value) and value != ZERO_DIGEST else None
    )


def previous_digests(chart: Path, env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Image digests of the previous release: environment first, then the chart's pinned values."""

    import yaml

    environ = os.environ if env is None else env
    values = yaml.safe_load((chart / "values-dev.yaml").read_text(encoding="utf-8")) or {}
    images = values.get("images") or {}
    found: dict[str, str] = {}
    for name in IMAGE_NAMES:
        digest = _pinned(environ.get(ENV_PREVIOUS_DIGEST.format(name.upper()))) or _pinned(
            (images.get(name) or {}).get("digest")
        )
        if digest:
            found[name] = digest
    missing = [n for n in IMAGE_NAMES if n not in found]
    if missing:
        raise ReleaseNotResolvable(
            "previous release image digests not available for "
            f"{', '.join(missing)}: set "
            + ", ".join(ENV_PREVIOUS_DIGEST.format(n.upper()) for n in missing)
        )
    return found


def candidate_digests(
    previous: Mapping[str, str], env: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Candidate digests from ``EDU_IT_KIND_DIGEST_*``; the web image defaults to the previous."""

    environ = os.environ if env is None else env
    found: dict[str, str] = {}
    for name in IMAGE_NAMES:
        digest = _pinned(environ.get(ENV_CANDIDATE_DIGEST.format(name.upper())))
        if digest:
            found[name] = digest
    if "web" not in found and "web" in previous:
        found["web"] = previous["web"]
    missing = [n for n in IMAGE_NAMES if n not in found]
    if missing:
        raise ReleaseNotResolvable(
            "candidate image digests not provided: set "
            + ", ".join(ENV_CANDIDATE_DIGEST.format(n.upper()) for n in missing)
        )
    return found


def candidate_schema_version(env: Mapping[str, str] | None = None) -> str:
    """The Alembic head of the working tree, or the value of the override variable."""

    environ = os.environ if env is None else env
    if environ.get(ENV_SCHEMA_VERSION):
        return environ[ENV_SCHEMA_VERSION]
    from edu_it_infrastructure.persistence.runner import Migration_Runner

    head = Migration_Runner("sqlite://").head()
    if not head:
        raise ReleaseNotResolvable("the working tree defines no migration head")
    return head


# --------------------------------------------------------------------------- signature gate


class SignatureGateFailed(AssertionError):
    """The signature verification of task 13.2 failed, so nothing may be installed."""


@dataclass(frozen=True)
class SignatureGate:
    exit_code: int
    output: str
    skipped: bool


def _chart_repositories(chart: Path) -> dict[str, str]:
    import yaml

    values = yaml.safe_load((chart / "values-dev.yaml").read_text(encoding="utf-8")) or {}
    return {
        name: (entry or {}).get("repository", "")
        for name, entry in (values.get("images") or {}).items()
    }


def run_signature_gate(
    chart: Path,
    digests: Mapping[str, str],
    *,
    mode: str | None = None,
    identity_regexp: str | None = None,
) -> SignatureGate:
    """Run ``tools/verify_image_signatures.py`` over ``chart``'s repositories at ``digests``.

    ``mode`` is ``pull-request`` (default) or ``cosign``. The result is returned, not raised, so a
    test can assert the failure; :meth:`UpgradeHarness.install` raises :class:`SignatureGateFailed`.
    """

    import yaml

    selected = mode or os.environ.get(ENV_SIGNATURE_MODE) or "pull-request"
    repositories = _chart_repositories(chart)
    values = {
        "images": {
            name: {"repository": repositories.get(name, ""), "digest": digest}
            for name, digest in digests.items()
        }
    }
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "images.yaml"
        path.write_text(yaml.safe_dump(values), encoding="utf-8")
        argv = [str(path)]
        if selected == "pull-request":
            argv.insert(0, "--pull-request")
        else:
            identity = identity_regexp or os.environ.get(ENV_COSIGN_IDENTITY, "")
            argv = ["--certificate-identity-regexp", identity, *argv]
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        try:
            import verify_image_signatures as tool
        finally:
            sys.path.pop(0)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = tool.main(argv)
    return SignatureGate(code, out.getvalue(), skipped=selected == "pull-request")


# --------------------------------------------------------------------------- lab pods


@dataclass(frozen=True)
class PodIdentity:
    """What makes a Lab_Pod "the same pod" (Req 25.2): name, uid, node and restart count."""

    name: str
    uid: str
    node: str
    restarts: int
    phase: str
    ready: bool


def pod_identity(pod: Mapping[str, Any]) -> PodIdentity:
    meta, status = pod.get("metadata") or {}, pod.get("status") or {}
    containers = status.get("containerStatuses") or []
    conditions = {c.get("type"): c.get("status") for c in status.get("conditions") or []}
    return PodIdentity(
        name=meta.get("name", ""),
        uid=meta.get("uid", ""),
        node=(pod.get("spec") or {}).get("nodeName", ""),
        restarts=sum(int(c.get("restartCount", 0)) for c in containers),
        phase=status.get("phase", ""),
        ready=conditions.get("Ready") == "True",
    )


# --------------------------------------------------------------------------- terminal client


@dataclass
class TerminalClient:
    """One terminal WebSocket client that survives an Api rollout (Req 25.4).

    ``watch`` reads until the socket closes, records the instant, reconnects (retrying until the
    deadline) and runs a command on the new socket. ``closed_at`` stays ``None`` when the socket was
    never closed (the pod it was connected to was not replaced).
    """

    learner: Any
    session_id: str
    stack: Any
    ws: Any = None
    closed_at: float | None = None
    reconnected_at: float | None = None
    reconnect_output_ok: bool = False
    error: str | None = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: threading.Thread | None = None

    def connect(self) -> None:
        self.ws = self.stack.ws_connect(self.learner, self.session_id)
        self.stack.drain_until_quiet(self.ws)

    def run_command(self, timeout_s: float = 20.0) -> bool:
        """Type an ``echo`` whose output differs from its echoed input; True when it comes back."""

        token = uuid.uuid4().hex[:8]
        self.ws.send(f"echo up$((40+2))-{token}\n".encode())
        try:
            self.stack.read_until(self.ws, f"up42-{token}".encode(), timeout_s=timeout_s)
        except (TimeoutError, OSError, RuntimeError):
            return False
        return True

    def watch(self, give_up_after_s: float = 600.0) -> None:
        self._thread = threading.Thread(target=self._watch, args=(give_up_after_s,), daemon=True)
        self._thread.start()

    def _watch(self, give_up_after_s: float) -> None:
        deadline = time.monotonic() + give_up_after_s
        try:
            while not self._stop.is_set() and time.monotonic() < deadline:
                try:
                    self.ws.recv(timeout=1.0)
                except TimeoutError:
                    continue
                except Exception:
                    break
            else:
                return
            self.closed_at = time.monotonic()
            while not self._stop.is_set() and time.monotonic() < deadline:
                try:
                    self.ws = self.stack.ws_connect(self.learner, self.session_id)
                except Exception as exc:
                    self.error = f"reconnect attempt failed: {exc}"
                    time.sleep(0.5)
                    continue
                self.reconnected_at = time.monotonic()
                self.stack.drain_until_quiet(self.ws)
                self.reconnect_output_ok = self.run_command()
                return
        except Exception as exc:
            self.error = repr(exc)

    def join(self, timeout_s: float) -> None:
        if self._thread is not None:
            self._thread.join(timeout_s)
        self._stop.set()

    @property
    def reconnect_seconds(self) -> float | None:
        if self.closed_at is None or self.reconnected_at is None:
            return None
        return self.reconnected_at - self.closed_at

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            self.ws.close()


@dataclass
class LabSessionHandle:
    learner: Any
    session_id: str
    pod: PodIdentity | None = None


# --------------------------------------------------------------------------- port forward


@contextlib.contextmanager
def port_forward(context: str, namespace: str, target: str, remote_port: int) -> Iterator[int]:
    """``kubectl port-forward`` to ``target`` (for example ``pod/x``) on a free local port."""

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        local = probe.getsockname()[1]
    proc = subprocess.Popen(
        [
            "kubectl",
            "--context",
            context,
            "-n",
            namespace,
            "port-forward",
            target,
            f"{local}:{remote_port}",
            "--address",
            "127.0.0.1",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            with contextlib.suppress(OSError), socket.create_connection(("127.0.0.1", local), 1):
                break
            time.sleep(0.5)
        else:
            raise AssertionError(f"port-forward to {target} did not open")
        yield local
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(10)


# --------------------------------------------------------------------------- the harness


@dataclass
class UpgradeHarness:
    """helm and kubectl operations of one Upgrade_Test run on a kind cluster."""

    context: str
    workdir: Path
    api_base: str = field(default_factory=lambda: os.environ.get(ENV_API_BASE, DEFAULT_API_BASE))
    sessions: list[LabSessionHandle] = field(default_factory=list)
    clients: list[TerminalClient] = field(default_factory=list)

    # ---- plumbing

    def kubectl(
        self, *args: str, input_text: str | None = None, check: bool = True, timeout: float = 300
    ) -> subprocess.CompletedProcess[str]:
        return run(
            ["kubectl", "--context", self.context, *args],
            input_text=input_text,
            check=check,
            timeout=timeout,
        )

    def kubectl_json(self, *args: str) -> Any:
        return json.loads(self.kubectl(*args, "-o", "json").stdout)

    def helm(
        self, *args: str, check: bool = True, timeout: float = 900
    ) -> subprocess.CompletedProcess[str]:
        return run(["helm", "--kube-context", self.context, *args], check=check, timeout=timeout)

    def api_cidr(self) -> str:
        out = run([sys.executable, str(HARNESS), "api-address"], timeout=60).stdout.split()
        assert out, "kind_harness api-address printed nothing"
        return out[0]

    def prepare_namespaces(self) -> None:
        for namespace in (PLATFORM_NAMESPACE, LABS_NAMESPACE):
            manifest = json.dumps(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": namespace, "labels": PSA_LABELS},
                }
            )
            self.kubectl("apply", "-f", "-", input_text=manifest)

    # ---- helm

    def release_args(
        self, chart: Path, digests: Mapping[str, str], extra_sets: Sequence[str] = ()
    ) -> list[str]:
        args = [
            str(chart),
            "--namespace",
            PLATFORM_NAMESPACE,
            "-f",
            str(chart / "values-dev.yaml"),
            "--set",
            "namespaces.manage=operator-managed",
            "--set",
            f"networkPolicy.kubernetesApi.cidrs={{{self.api_cidr()}}}",
            "--set",
            "api.replicas=2",  # Req 25.6: at least 2 Api replicas
        ]
        for name, digest in digests.items():
            args += ["--set", f"images.{name}.digest={digest}"]
        for item in extra_sets:
            args += ["--set", item]
        return args

    def install(self, chart: Path, digests: Mapping[str, str]) -> None:
        """Verify signatures (task 13.2), then ``helm install --wait``."""

        gate = run_signature_gate(chart, digests)
        if gate.exit_code != 0:
            raise SignatureGateFailed(
                f"signature verification failed (exit {gate.exit_code}):\n{gate.output}"
            )
        self.prepare_namespaces()
        self.helm(
            "install",
            RELEASE,
            *self.release_args(chart, digests),
            "--wait",
            "--timeout",
            HELM_TIMEOUT,
            timeout=700,
        )

    def start_upgrade(
        self,
        chart: Path,
        digests: Mapping[str, str],
        extra_sets: Sequence[str] = (),
        *,
        wait: bool = True,
        verify_signatures: bool = True,
    ) -> subprocess.Popen[str]:
        """Verify signatures, then start ``helm upgrade`` without waiting for it to finish.

        ``verify_signatures=False`` is only for the failed-migration variant, whose test-only
        migrations image is unsigned by design; the real candidate digests are verified first.
        """

        gate = (
            run_signature_gate(chart, digests) if verify_signatures else SignatureGate(0, "", True)
        )
        if gate.exit_code != 0:
            raise SignatureGateFailed(
                f"signature verification failed (exit {gate.exit_code}):\n{gate.output}"
            )
        argv = [
            "helm",
            "--kube-context",
            self.context,
            "upgrade",
            RELEASE,
            *self.release_args(chart, digests, extra_sets),
        ]
        if wait:
            argv += ["--wait", "--timeout", HELM_TIMEOUT]
        return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def revision(self) -> int:
        out = json.loads(
            self.helm("status", RELEASE, "-n", PLATFORM_NAMESPACE, "-o", "json").stdout
        )
        return int(out["version"])

    def deployed_revision(self) -> int:
        history = json.loads(
            self.helm("history", RELEASE, "-n", PLATFORM_NAMESPACE, "-o", "json").stdout
        )
        deployed = [int(h["revision"]) for h in history if h.get("status") == "deployed"]
        assert deployed, f"no deployed revision in {history}"
        return max(deployed)

    def uninstall(self) -> None:
        self.helm(
            "uninstall", RELEASE, "-n", PLATFORM_NAMESPACE, "--wait", check=False, timeout=300
        )
        self.kubectl(
            "delete",
            "namespace",
            PLATFORM_NAMESPACE,
            "--ignore-not-found",
            "--wait=false",
            check=False,
        )

    # ---- workloads

    def deployment_snapshot(self) -> dict[str, dict[str, Any]]:
        """Per Deployment: generation, replicas, images and the pod uids (unchanged = untouched)."""

        snapshot: dict[str, dict[str, Any]] = {}
        deployments = self.kubectl_json("get", "deployments", "-n", PLATFORM_NAMESPACE)["items"]
        pods = self.kubectl_json("get", "pods", "-n", PLATFORM_NAMESPACE)["items"]
        for dep in deployments:
            name = dep["metadata"]["name"]
            component = dep["spec"]["selector"]["matchLabels"].get("app.kubernetes.io/component")
            if component not in ("api", "worker", "web"):
                continue
            uids = sorted(
                p["metadata"]["uid"]
                for p in pods
                if (p["metadata"].get("labels") or {}).get("app.kubernetes.io/component")
                == component
            )
            snapshot[name] = {
                "generation": dep["metadata"]["generation"],
                "replicas": dep["spec"].get("replicas"),
                "images": [c["image"] for c in dep["spec"]["template"]["spec"]["containers"]],
                "pod_uids": uids,
            }
        return snapshot

    def workloads_ready(self) -> bool:
        """Every Api, Worker and Web Deployment fully rolled out with all pods Ready (Req 25.3)."""

        deployments = self.kubectl_json("get", "deployments", "-n", PLATFORM_NAMESPACE)["items"]
        relevant = [
            d
            for d in deployments
            if d["metadata"]["name"].rsplit("-", 1)[-1] in ("api", "worker", "web")
        ]
        if not relevant:
            return False
        for dep in relevant:
            status, want = dep.get("status") or {}, dep["spec"].get("replicas", 1)
            if not (
                status.get("readyReplicas", 0) == want == status.get("updatedReplicas", 0)
                and status.get("replicas", 0) == want
            ):
                return False
        pods = self.kubectl_json("get", "pods", "-n", PLATFORM_NAMESPACE)["items"]
        return all(
            pod_identity(p).ready
            for p in pods
            if (p["metadata"].get("labels") or {}).get("app.kubernetes.io/component")
            in ("api", "worker", "web")
            and not p["metadata"].get("deletionTimestamp")
        )

    def schema_version(self) -> str:
        done = self.kubectl(
            "exec",
            "-n",
            PLATFORM_NAMESPACE,
            f"{RELEASE}-dev-postgres-0",
            "--",
            "psql",
            "-U",
            "edu_it",
            "-d",
            "edu_it",
            "-tAc",
            "select version_num from alembic_version",
        )
        return done.stdout.strip()

    def migration_job_failed(self) -> bool:
        done = self.kubectl(
            "get",
            "job",
            f"{RELEASE}-migration",
            "-n",
            PLATFORM_NAMESPACE,
            "-o",
            "json",
            check=False,
        )
        if done.returncode != 0:
            return False
        conditions = (json.loads(done.stdout).get("status") or {}).get("conditions") or []
        return any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)

    def set_workload_images(
        self, repositories: Mapping[str, str], digests: Mapping[str, str]
    ) -> None:
        """Point the Api and Worker Deployments at ``digests`` and wait for the rollout.

        Used to run the previous Api and Worker against the candidate schema (Req 21.6) without
        touching the Helm release; the caller restores the images afterwards.
        """

        for component in ("api", "worker"):
            image = f"{repositories[component]}@{digests[component]}"
            self.kubectl(
                "set",
                "image",
                f"deployment/{RELEASE}-{component}",
                f"{component}={image}",
                "-n",
                PLATFORM_NAMESPACE,
            )
        for component in ("api", "worker"):
            self.kubectl(
                "rollout",
                "status",
                f"deployment/{RELEASE}-{component}",
                "-n",
                PLATFORM_NAMESPACE,
                "--timeout=300s",
                timeout=330,
            )

    # ---- sessions

    def _stack(self) -> Any:
        sys.path.insert(0, str(REPO_ROOT))
        try:
            from tools.perf import stack
        finally:
            sys.path.pop(0)
        stack.API_BASE = self.api_base
        return stack

    def start_sessions(self, count: int = 3) -> list[LabSessionHandle]:
        """Seed the catalog, mint ``count`` learners and start one ready Lab_Session each.

        Each Lab_Session must report a running Lab_Pod within 120 s (Req 25.1). One learner per
        session keeps clear of the per-learner provisioning rate limit.
        """

        stack = self._stack()
        pg_pod, redis_pod = f"pod/{RELEASE}-dev-postgres-0", f"pod/{RELEASE}-dev-redis-0"
        with (
            port_forward(self.context, PLATFORM_NAMESPACE, pg_pod, 5432) as pg_port,
            port_forward(self.context, PLATFORM_NAMESPACE, redis_pod, 6379) as redis_port,
        ):
            env = {
                "DATABASE_URL": f"postgresql://edu_it:edu-it-dev-placeholder@127.0.0.1:{pg_port}/edu_it",
                "REDIS_URL": f"redis://127.0.0.1:{redis_port}/0",
                "PERF_CONTENT_ROOT": str(REPO_ROOT / "content"),
            }
            previous = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                stack.seed_database()
                learners = [stack.mint_learner() for _ in range(count)]
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
        handles = []
        for learner in learners:
            created = stack.start_session(learner)
            handles.append(LabSessionHandle(learner, created["stable_id"]))
        started = time.monotonic()
        for handle in handles:
            stack.wait_ready(
                handle.learner,
                handle.session_id,
                timeout_s=max(1.0, SESSIONS_RUNNING_MAX_S - (time.monotonic() - started)),
            )
        self.sessions = handles
        for handle in handles:
            handle.pod = wait_until(
                lambda h=handle: (
                    self.lab_pod(h.session_id)
                    if self.lab_pod(h.session_id) and self.lab_pod(h.session_id).phase == "Running"
                    else None
                ),  # type: ignore[union-attr]
                timeout_s=SESSIONS_RUNNING_MAX_S,
                what=f"running Lab_Pod of {handle.session_id}",
            )
        return handles

    def lab_pod(self, session_id: str) -> PodIdentity | None:
        pods = self.kubectl_json("get", "pods", "-n", LABS_NAMESPACE)["items"]
        for pod in pods:
            labels = pod["metadata"].get("labels") or {}
            if labels.get("edu-it.session") == session_id:
                return pod_identity(pod)
        return None

    def attach_clients(self) -> list[TerminalClient]:
        """Open a terminal for every session, prove it works and start watching for closes."""

        stack = self._stack()
        self.clients = []
        for handle in self.sessions:
            client = TerminalClient(handle.learner, handle.session_id, stack)
            client.connect()
            assert client.run_command(), f"terminal of {handle.session_id} returned no output"
            client.watch()
            self.clients.append(client)
        return self.clients

    def terminal_round_trip(self, handle: LabSessionHandle) -> bool:
        """A fresh terminal accepts a command and returns its output (Req 25.2)."""

        stack = self._stack()
        client = TerminalClient(handle.learner, handle.session_id, stack)
        try:
            client.connect()
            return client.run_command()
        finally:
            client.close()

    def session_status(self, handle: LabSessionHandle) -> str:
        status, data = self._stack().api(
            "GET", f"/lab-sessions/{handle.session_id}", handle.learner
        )
        assert status == 200, f"session read failed: {status} {data}"
        return str(data["status"])

    def close_clients(self) -> None:
        for client in self.clients:
            client.close()

    def end_sessions(self) -> None:
        stack = self._stack()
        for handle in self.sessions:
            with contextlib.suppress(Exception):
                stack.end_session(handle.learner, handle.session_id)

    def cleanup(self) -> None:
        self.close_clients()
        self.end_sessions()
        self.uninstall()
        shutil.rmtree(self.workdir, ignore_errors=True)
