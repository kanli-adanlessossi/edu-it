"""Bootstrap the Real_Stack for the real-stack end-to-end tests (api-route-wiring task 12.1).

Subcommands::

    python tools/e2e_stack.py up   [--dry-run]   # start everything, in order
    python tools/e2e_stack.py down [--dry-run]   # stop what ``up`` started
    python tools/e2e_stack.py env                # print the variables the tests need

``up`` runs, in order: PostgreSQL and Redis containers, their readiness probes, the
Alembic migrations, ``edu-it-content sync --database-url``, ``docker buildx bake`` of the
Lab_Images against ``lab-images/docker-bake.hcl`` (``ALPINE_MIRROR`` is passed through
when it is set), the worker, the API (``SANDBOX_PROVIDER=docker EDU_IT_ENV=development
MAILER_BACKEND=console``; its output goes to ``E2E_MAIL_LOG`` so the Journey_Test can read
the verification links), and the web app. ``--dry-run`` prints that plan and starts nothing.

Port rule: before starting anything, ``up`` looks for the container holding port 5432 and 6379.
A compose service (project label, matching image and, for PostgreSQL, the expected
credentials) is reused and reported; ``down`` leaves it running. Any other holder makes ``up``
fail early, naming the container and ``docker stop <name>``. ``up`` never stops or removes a
container it did not start; ``down`` removes only the containers ``up`` recorded in
``.e2e/stack.json``. A second ``up`` reuses its own labelled containers and restarts its
recorded processes.

Neither ``up`` nor ``down`` runs, installs, or requires ``trivy``; the image scan belongs to
``tools/run_verification.py --suite labs``.

Environment: ``E2E_MAIL_LOG`` (default ``.e2e/mail.log`` under the repository root),
``ALPINE_MIRROR`` (optional, build-time apk mirror), ``E2E_WEB_URL`` (default
``http://localhost:3000``). The Docker daemon is required by ``up`` and ``down``.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = REPO_ROOT / ".e2e"
STATE_FILE = STATE_DIR / "stack.json"

POSTGRES_CONTAINER = "edu-it-e2e-postgres"
REDIS_CONTAINER = "edu-it-e2e-redis"
# Label on every container ``up`` starts; ``down`` and a second ``up`` trust only labelled ones.
OWN_LABEL = "edu-it.e2e"
POSTGRES_PORT = 5432
REDIS_PORT = 6379
API_PORT = 8000
WORKER_HEALTH_PORT = 8001
WEB_PORT = 3000

DATABASE_URL = f"postgresql://edu_it:change-me@localhost:{POSTGRES_PORT}/edu_it"
REDIS_URL = f"redis://localhost:{REDIS_PORT}/0"
API_BASE_URL = f"http://localhost:{API_PORT}"
WEB_BASE_URL = f"http://localhost:{WEB_PORT}"

# The application environment shared by worker and API (values mirror .env.example).
APP_ENV: dict[str, str] = {
    "SANDBOX_PROVIDER": "docker",
    "EDU_IT_ENV": "development",
    "MAILER_BACKEND": "console",
    "MAIL_FROM_ADDRESS": "no-reply@example.test",
    "LLM_PROVIDER_BACKEND": "fake",
    "OBJECT_STORAGE_BACKEND": "memory",
    "DATABASE_URL": DATABASE_URL,
    "REDIS_URL": REDIS_URL,
    "API_SECRET_KEY": "change-me-dev-secret",
    "DEFAULT_LOCALE": "en",
    "WEB_BASE_URL": WEB_BASE_URL,
    "API_BASE_URL": API_BASE_URL,
    "EDU_IT_CONTENT_ROOT": str(REPO_ROOT / "content"),
    "EDU_IT_BUNDLE_ROOT": str(REPO_ROOT),
}


@dataclass(frozen=True)
class Step:
    """One action of ``up``.

    ``env`` holds only the variables the step adds to the inherited environment.
    ``background`` steps are long-running processes whose pid ``down`` stops; ``log`` is
    the file their stdout and stderr are appended to. ``retries`` re-runs a readiness probe
    once per second until it succeeds.
    """

    name: str
    argv: tuple[str, ...]
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Path = REPO_ROOT
    background: bool = False
    log: Path | None = None
    retries: int = 0

    def render(self) -> str:
        parts = [f"{k}={shlex.quote(v)}" for k, v in sorted(self.env.items())]
        parts.append(shlex.join(self.argv))
        text = f"{self.name}: {' '.join(parts)}"
        if self.log is not None:
            text += f" >> {self.log} 2>&1"
        if self.background:
            text += " &"
        return text


def mail_log_path(env: Mapping[str, str]) -> Path:
    return Path(env.get("E2E_MAIL_LOG") or STATE_DIR / "mail.log")


def stack_env(env: Mapping[str, str]) -> dict[str, str]:
    """The variables the real-stack tests and ``run_verification`` need (``env`` subcommand)."""
    return {
        "DATABASE_URL": DATABASE_URL,
        "REDIS_URL": REDIS_URL,
        "E2E_MAIL_LOG": str(mail_log_path(env)),
        "E2E_WEB_URL": env.get("E2E_WEB_URL", WEB_BASE_URL),
    }


def _python(code: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code)


def build_plan(env: Mapping[str, str]) -> list[Step]:
    """The ordered ``up`` plan. Pure: reads ``env`` only, so tests can pass any mapping."""
    mail_log = mail_log_path(env)
    web_dir = REPO_ROOT / "apps" / "web"

    bake_env: dict[str, str] = {}
    if env.get("ALPINE_MIRROR"):
        bake_env["ALPINE_MIRROR"] = env["ALPINE_MIRROR"]

    api_env = {**APP_ENV, "API_HOST": "127.0.0.1", "API_PORT": str(API_PORT)}
    worker_env = {
        **APP_ENV,
        "WORKER_HEALTH_HOST": "127.0.0.1",
        "WORKER_HEALTH_PORT": str(WORKER_HEALTH_PORT),
    }
    web_env = {
        "NEXT_PUBLIC_API_BASE_URL": f"{API_BASE_URL}/v1",
        "NEXT_PUBLIC_DEFAULT_LOCALE": "en",
        "PORT": str(WEB_PORT),
    }

    return [
        Step(
            "postgres",
            (
                "docker",
                "run",
                "-d",
                "--name",
                POSTGRES_CONTAINER,
                "--label",
                f"{OWN_LABEL}=1",
                "-e",
                "POSTGRES_USER=edu_it",
                "-e",
                "POSTGRES_PASSWORD=change-me",
                "-e",
                "POSTGRES_DB=edu_it",
                "-p",
                f"{POSTGRES_PORT}:5432",
                "postgres:17",
            ),
        ),
        Step(
            "redis",
            (
                "docker",
                "run",
                "-d",
                "--name",
                REDIS_CONTAINER,
                "--label",
                f"{OWN_LABEL}=1",
                "-p",
                f"{REDIS_PORT}:6379",
                "redis:7",
            ),
        ),
        Step(
            "postgres-ready",
            ("docker", "exec", POSTGRES_CONTAINER, "pg_isready", "-U", "edu_it", "-d", "edu_it"),
            retries=60,
        ),
        Step(
            "redis-ready",
            ("docker", "exec", REDIS_CONTAINER, "redis-cli", "ping"),
            retries=60,
        ),
        Step(
            "migrate",
            _python(
                "import sys; from edu_it_infrastructure.persistence.runner import main; "
                "sys.exit(main(['upgrade']))"
            ),
            env={"DATABASE_URL": DATABASE_URL},
        ),
        Step(
            "content-sync",
            _python(
                "import sys; from edu_it_content.cli import main; "
                f"sys.exit(main(['sync', '--database-url', {DATABASE_URL!r}]))"
            ),
            env={"EDU_IT_CONTENT_ROOT": str(REPO_ROOT / "content")},
        ),
        Step(
            "lab-images",
            ("docker", "buildx", "bake", "--load"),
            env=bake_env,
            cwd=REPO_ROOT / "lab-images",
        ),
        Step(
            "worker",
            (sys.executable, "-m", "edu_it_worker.serve"),
            env=worker_env,
            background=True,
            log=STATE_DIR / "worker.log",
        ),
        Step(
            "api",
            (sys.executable, "-m", "edu_it_api.serve"),
            env=api_env,
            background=True,
            log=mail_log,
        ),
        Step("web-build", ("npm", "run", "build"), env=web_env, cwd=web_dir),
        Step(
            "web",
            ("npx", "next", "start", "-p", str(WEB_PORT)),
            env=web_env,
            cwd=web_dir,
            background=True,
            log=STATE_DIR / "web.log",
        ),
    ]


def build_down_plan(containers: Sequence[str] | None = None) -> list[Step]:
    """Remove only the containers ``up`` recorded as started by it (default: both, for display)."""
    names = [POSTGRES_CONTAINER, REDIS_CONTAINER] if containers is None else list(containers)
    return [Step(f"{n}-rm", ("docker", "rm", "-f", n)) for n in names]


# -- port and container detection ---------------------------------------------------------


@dataclass(frozen=True)
class Holder:
    """A running container that publishes a host port."""

    name: str
    image: str
    labels: Mapping[str, str]
    env: Mapping[str, str]
    running: bool = True


@dataclass(frozen=True)
class ServiceSpec:
    key: str  # plan step name: "postgres" or "redis"
    container: str  # the name ``up`` gives its own container
    port: int
    image_prefix: str
    compose_service: str
    expected_env: Mapping[str, str]


SERVICES: tuple[ServiceSpec, ...] = (
    ServiceSpec(
        "postgres",
        POSTGRES_CONTAINER,
        POSTGRES_PORT,
        "postgres:",
        "postgres",
        {"POSTGRES_USER": "edu_it", "POSTGRES_PASSWORD": "change-me", "POSTGRES_DB": "edu_it"},
    ),
    ServiceSpec("redis", REDIS_CONTAINER, REDIS_PORT, "redis:", "redis", {}),
)


@dataclass(frozen=True)
class Decision:
    action: str  # "start" | "reuse" | "fail"
    container: str
    owned: bool  # True when ``down`` may remove the container
    message: str


def decide(spec: ServiceSpec, holders: Sequence[Holder], *, port_busy: bool) -> Decision:
    """Rule: start our own container on a free port; reuse our own labelled container (a second
    ``up``) or a compose service with the expected image and credentials; otherwise fail early,
    naming the holder and the command that stops it. Never touches a container we did not start."""
    if not holders:
        if port_busy:
            return Decision(
                "fail",
                spec.container,
                False,
                f"port {spec.port} ({spec.key}) is in use by a process that is not a Docker "
                "container; stop that process and run `up` again",
            )
        return Decision("start", spec.container, True, f"{spec.key}: starting {spec.container}")
    holder = holders[0]
    stop = f"docker stop {holder.name}"
    if holder.name == spec.container and holder.labels.get(OWN_LABEL):
        return Decision(
            "reuse", holder.name, True, f"{spec.key}: reusing {holder.name} (started by a prior up)"
        )
    is_compose = (
        bool(holder.labels.get("com.docker.compose.project"))
        and holder.labels.get("com.docker.compose.service") == spec.compose_service
        and holder.image.startswith(spec.image_prefix)
    )
    if is_compose:
        if all(holder.env.get(k) == v for k, v in spec.expected_env.items()):
            return Decision(
                "reuse",
                holder.name,
                False,
                f"{spec.key}: reusing compose container {holder.name} on port {spec.port} "
                "(down leaves it running)",
            )
        return Decision(
            "fail",
            spec.container,
            False,
            f"port {spec.port}: compose container {holder.name} does not have the expected "
            f"credentials (user, password, database). Stop it with `{stop}` and run `up` again",
        )
    return Decision(
        "fail",
        spec.container,
        False,
        f"port {spec.port} ({spec.key}) is held by container {holder.name} ({holder.image}), "
        f"which this stack did not start. Stop it with `{stop}` and run `up` again",
    )


def _docker_out(argv: Sequence[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else ""


def find_holders(port: int, run: Callable[[Sequence[str]], str] = _docker_out) -> list[Holder]:
    """Running containers publishing ``port`` on the host (``run`` is the injected docker call)."""
    names = run(["docker", "ps", "--filter", f"publish={port}", "--format", "{{.Names}}"]).split()
    if not names:
        return []
    holders: list[Holder] = []
    for item in json.loads(run(["docker", "inspect", *names]) or "[]"):
        config = item.get("Config", {})
        env = dict(e.split("=", 1) for e in config.get("Env") or [] if "=" in e)
        holders.append(
            Holder(
                name=item["Name"].lstrip("/"),
                image=config.get("Image", ""),
                labels=config.get("Labels") or {},
                env=env,
                running=bool(item.get("State", {}).get("Running", True)),
            )
        )
    return holders


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def apply_decisions(plan: Sequence[Step], decisions: Mapping[str, Decision]) -> list[Step]:
    """Drop the start step of reused services and point their readiness probes at the container."""
    out: list[Step] = []
    for step in plan:
        own = decisions.get(step.name)
        if own is not None and own.action == "reuse":
            continue
        base = step.name.removesuffix("-ready")
        decision = decisions.get(base)
        if step.name.endswith("-ready") and decision is not None and decision.action == "reuse":
            spec = next(s for s in SERVICES if s.key == base)
            step = replace(
                step,
                argv=tuple(decision.container if a == spec.container else a for a in step.argv),
            )
        out.append(step)
    return out


def resolve_services(
    holders_for: Callable[[int], list[Holder]] = find_holders,
    busy: Callable[[int], bool] = port_in_use,
) -> dict[str, Decision]:
    return {
        spec.key: decide(spec, holders_for(spec.port), port_busy=busy(spec.port))
        for spec in SERVICES
    }


def _remove_stale_own(spec: ServiceSpec) -> None:
    """A stopped container of ours (labelled) blocks the name; remove it. Unlabelled: refuse."""
    out = _docker_out(["docker", "inspect", spec.container])
    if not out.strip():
        return
    labels = (json.loads(out)[0].get("Config", {}).get("Labels")) or {}
    if not labels.get(OWN_LABEL):
        raise SystemExit(
            f"a container named {spec.container} exists but was not started by this stack; "
            f"remove it yourself (`docker rm {spec.container}`) and run `up` again"
        )
    subprocess.run(["docker", "rm", "-f", spec.container], check=False)


# -- execution ----------------------------------------------------------------------------


def _npm_argv(argv: Sequence[str]) -> list[str]:
    # npm and npx are .cmd shims on Windows; resolve them through the shell there.
    return list(argv)


def _run_step(step: Step, base_env: Mapping[str, str], pids: dict[str, int]) -> None:
    child_env = {**base_env, **step.env}
    shell = os.name == "nt" and step.argv[0] in ("npm", "npx")
    argv: str | list[str] = shlex.join(step.argv) if shell else _npm_argv(step.argv)
    if step.background:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        assert step.log is not None
        step.log.parent.mkdir(parents=True, exist_ok=True)
        with step.log.open("ab") as log:
            proc = subprocess.Popen(
                argv,
                env=child_env,
                cwd=step.cwd,
                stdout=log,
                stderr=subprocess.STDOUT,
                shell=shell,
            )
        pids[step.name] = proc.pid
        return
    attempts = max(step.retries, 1)
    for attempt in range(attempts):
        result = subprocess.run(argv, env=child_env, cwd=step.cwd, shell=shell, check=False)
        if result.returncode == 0:
            return
        if attempt + 1 < attempts:
            time.sleep(1)
    raise SystemExit(f"step {step.name!r} failed (exit {result.returncode})")


def _load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def _save_state(pids: Mapping[str, int], containers: Sequence[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"pids": dict(pids), "containers": list(containers)}))


def _kill(pid: int) -> None:
    if os.name == "nt":
        # A recorded process may already be gone; taskkill's "not found" is not an error here.
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, 15)


def cmd_up(env: Mapping[str, str], *, dry_run: bool) -> int:
    plan = build_plan(env)
    if dry_run:
        for index, step in enumerate(plan, start=1):
            print(f"{index}. {step.render()}")
        return 0
    # Detect port holders before starting anything, so a conflict fails early and cleanly.
    decisions = resolve_services()
    failures = [d.message for d in decisions.values() if d.action == "fail"]
    if failures:
        raise SystemExit("cannot start the stack:\n  " + "\n  ".join(failures))
    for decision in decisions.values():
        print(decision.message, flush=True)
    plan = apply_decisions(plan, decisions)
    # Idempotent: a second ``up`` restarts the processes the first one recorded and keeps
    # owning the containers it started.
    previous = _load_state()
    _stop(previous.get("pids", {}))
    containers = [d.container for d in decisions.values() if d.owned and d.action == "reuse"]
    pids: dict[str, int] = {}
    mail_log_path(env).parent.mkdir(parents=True, exist_ok=True)
    _save_state(pids, containers)
    try:
        for step in plan:
            print(f"==> {step.name}", flush=True)
            decision = decisions.get(step.name)
            if decision is not None and decision.action == "start":
                spec = next(s for s in SERVICES if s.key == step.name)
                _remove_stale_own(spec)
            _run_step(step, env, pids)
            if decision is not None and decision.action == "start":
                containers.append(decision.container)
                _save_state(pids, containers)
            if step.background:
                _save_state(pids, containers)
    except BaseException:
        _stop(pids)
        raise
    print("Real_Stack is up. Variables for the tests:")
    for key, value in stack_env(env).items():
        print(f"{key}={value}")
    return 0


def _stop(pids: Mapping[str, int]) -> None:
    for pid in pids.values():
        _kill(pid)


def cmd_down(env: Mapping[str, str], *, dry_run: bool) -> int:
    plan = build_down_plan()
    if dry_run:
        print("0. stop recorded worker, api, and web processes")
        for index, step in enumerate(plan, start=1):
            print(f"{index}. {step.render()}")
        return 0
    state = _load_state()
    _stop(state.get("pids", {}))
    if STATE_FILE.exists():
        STATE_FILE.unlink()
    # Only containers this stack's ``up`` recorded; compose or foreign containers stay up.
    for step in build_down_plan(state.get("containers", [])):
        subprocess.run(step.argv, env=dict(env), check=False)
    return 0


def cmd_env(env: Mapping[str, str]) -> int:
    for key, value in stack_env(env).items():
        print(f"{key}={value}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="e2e_stack", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("up", "down"):
        p = sub.add_parser(name)
        p.add_argument("--dry-run", action="store_true", help="print the plan, run nothing")
    sub.add_parser("env")
    args = parser.parse_args(argv)
    if args.command == "up":
        return cmd_up(os.environ, dry_run=args.dry_run)
    if args.command == "down":
        return cmd_down(os.environ, dry_run=args.dry_run)
    return cmd_env(os.environ)


if __name__ == "__main__":
    sys.exit(main())
