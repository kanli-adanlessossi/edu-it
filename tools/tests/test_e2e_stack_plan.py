"""Dry-run tests for ``tools/e2e_stack.py`` (api-route-wiring task 12.1, Req 17.1, 17.4, 17.5).

Nothing here starts a process or contacts Docker: the plan is pure data.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "e2e_stack.py"


def _load():
    spec = importlib.util.spec_from_file_location("edu_it_e2e_stack", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["edu_it_e2e_stack"] = module
    spec.loader.exec_module(module)
    return module


stack = _load()


def _names(plan):
    return [s.name for s in plan]


def _step(plan, name):
    return next(s for s in plan if s.name == name)


def test_plan_order() -> None:
    names = _names(stack.build_plan({}))
    order = ["postgres", "redis", "migrate", "content-sync", "lab-images", "worker", "api", "web"]
    positions = [names.index(n) for n in order]
    assert positions == sorted(positions)
    assert names.index("postgres-ready") < names.index("migrate")
    assert names.index("redis-ready") < names.index("migrate")


def test_content_sync_uses_database_url() -> None:
    step = _step(stack.build_plan({}), "content-sync")
    code = step.argv[-1]
    assert "'sync'" in code and "'--database-url'" in code and stack.DATABASE_URL in code


def test_lab_images_use_bake_against_the_bake_file() -> None:
    step = _step(stack.build_plan({}), "lab-images")
    assert step.argv[:3] == ("docker", "buildx", "bake")
    assert step.cwd == stack.REPO_ROOT / "lab-images"
    assert (step.cwd / "docker-bake.hcl").is_file()


def test_alpine_mirror_pass_through() -> None:
    assert "ALPINE_MIRROR" not in _step(stack.build_plan({}), "lab-images").env
    assert "ALPINE_MIRROR" not in _step(stack.build_plan({"ALPINE_MIRROR": ""}), "lab-images").env
    mirror = "https://uk.alpinelinux.org/alpine"
    step = _step(stack.build_plan({"ALPINE_MIRROR": mirror}), "lab-images")
    assert step.env["ALPINE_MIRROR"] == mirror


def test_api_and_worker_environment() -> None:
    plan = stack.build_plan({})
    for name in ("api", "worker"):
        env = _step(plan, name).env
        assert env["SANDBOX_PROVIDER"] == "docker"
        assert env["EDU_IT_ENV"] == "development"
        assert env["MAILER_BACKEND"] == "console"
        assert env["DATABASE_URL"] == stack.DATABASE_URL
        assert env["REDIS_URL"] == stack.REDIS_URL
        assert _step(plan, name).background


def test_api_and_worker_listen_on_distinct_ports() -> None:
    """Both run on one host and default their metrics to 9090; Linux refuses the second bind."""
    plan = stack.build_plan({})
    api, worker = _step(plan, "api").env, _step(plan, "worker").env
    ports = [
        api["API_PORT"],
        api["API_METRICS_PORT"],
        worker["WORKER_HEALTH_PORT"],
        worker["WORKER_METRICS_PORT"],
    ]
    assert len(set(ports)) == len(ports), ports


def test_api_output_goes_to_e2e_mail_log(tmp_path: Path) -> None:
    log = tmp_path / "mail.log"
    assert _step(stack.build_plan({"E2E_MAIL_LOG": str(log)}), "api").log == log
    assert stack.stack_env({"E2E_MAIL_LOG": str(log)})["E2E_MAIL_LOG"] == str(log)
    assert _step(stack.build_plan({}), "api").log is not None


def test_web_targets_the_api() -> None:
    env = _step(stack.build_plan({}), "web").env
    assert env["NEXT_PUBLIC_API_BASE_URL"] == f"{stack.API_BASE_URL}/v1"


@pytest.mark.parametrize("env", [{}, {"ALPINE_MIRROR": "https://m.example/alpine"}])
def test_no_trivy_step(env, capsys) -> None:
    plan = stack.build_plan(env)
    assert all("trivy" not in " ".join((s.name, *s.argv)).lower() for s in plan)
    assert stack.main(["up", "--dry-run"]) == 0
    assert stack.main(["down", "--dry-run"]) == 0
    assert "trivy" not in capsys.readouterr().out.lower()


def test_dry_run_prints_plan_and_starts_nothing(monkeypatch, capsys) -> None:
    def boom(*_a, **_k):
        raise AssertionError("dry run must not start processes")

    monkeypatch.setattr(stack.subprocess, "run", boom)
    monkeypatch.setattr(stack.subprocess, "Popen", boom)
    monkeypatch.setenv("ALPINE_MIRROR", "https://m.example/alpine")
    assert stack.main(["up", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "ALPINE_MIRROR=https://m.example/alpine" in out
    assert "docker buildx bake" in out
    assert "SANDBOX_PROVIDER=docker" in out
    assert "MAILER_BACKEND=console" in out


def test_env_subcommand(capsys, monkeypatch) -> None:
    monkeypatch.setenv("E2E_MAIL_LOG", "x.log")
    assert stack.main(["env"]) == 0
    out = capsys.readouterr().out
    assert "E2E_MAIL_LOG=x.log" in out and "DATABASE_URL=" in out
