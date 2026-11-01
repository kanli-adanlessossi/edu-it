"""labs-docker is blocked, never failed or passed, while a Worker_Service is up (api-route-wiring
14.2; Requirements 17.2, 17.3).

The Real_Stack worker's ReapSessions orphan sweep destroys every ``edu-it.managed`` container
that has no known session, which includes the pytest harness containers. Process and Docker
calls are injected: nothing here starts a process or talks to a daemon.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_verification as rv


def _labs_docker() -> rv.Check:
    return next(c for c in rv.build_labs_checks(skip_perf=True) if c.check_id == "labs-docker")


@pytest.fixture
def no_subprocess(monkeypatch: pytest.MonkeyPatch) -> list:
    calls: list = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr(rv.subprocess, "run", fake_run)
    return calls


def test_only_the_docker_checks_are_marked_exclusive_of_the_worker() -> None:
    marked = {c.check_id for c in rv.build_labs_checks() if c.exclusive_of_worker}
    assert marked == {"labs-docker"}


def test_labs_docker_is_blocked_with_cause_and_command_when_a_worker_listens(no_subprocess) -> None:
    check = _labs_docker()
    rv.run_check(check, worker_probe=lambda port: port == rv.WORKER_HEALTH_PORT)
    assert check.status == "blocked"  # not "failed", not "passed", not "not-run"
    assert no_subprocess == []  # the Docker tests did not run
    for needle in (
        str(rv.WORKER_HEALTH_PORT),
        "ReapSessions",
        "edu-it.managed",
        "tools/e2e_stack.py down",
        "--suite labs --only labs-docker",
    ):
        assert needle in check.detail


def test_labs_docker_runs_when_no_worker_listens(no_subprocess) -> None:
    check = _labs_docker()
    rv.run_check(check, worker_probe=lambda port: False)
    assert check.status == "passed"
    assert len(no_subprocess) == 1 and "docker" in no_subprocess[0]


def test_other_checks_still_run_with_a_worker_listening(no_subprocess) -> None:
    check = rv.Check("x", "1", "d", ["python", "-c", "pass"])
    rv.run_check(check, worker_probe=lambda port: True)
    assert check.status == "passed" and len(no_subprocess) == 1


def test_a_blocked_check_makes_the_labs_run_exit_non_zero(
    monkeypatch: pytest.MonkeyPatch, no_subprocess
) -> None:
    monkeypatch.setattr(rv, "_worker_listening", lambda port: True)
    assert rv.main(["--suite", "labs", "--only", "labs-docker"]) == 1


def test_docker_marked_group_files_are_recognised(tmp_path: Path) -> None:
    marked = tmp_path / "test_a.py"
    marked.write_text("import pytest\npytestmark = pytest.mark.docker\n", encoding="utf-8")
    single = tmp_path / "test_b.py"
    single.write_text("@pytest.mark.docker\ndef test_x(): ...\n", encoding="utf-8")
    plain = tmp_path / "test_c.py"
    plain.write_text("def test_x(): ...\n", encoding="utf-8")
    assert rv.is_docker_test_file(marked) and rv.is_docker_test_file(single)
    assert not rv.is_docker_test_file(plain)
    assert not rv.is_docker_test_file(tmp_path / "missing.py")


def test_worker_listening_sees_a_real_listener_and_a_closed_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert rv._worker_listening(port) is True
    assert rv._worker_listening(port) is False
