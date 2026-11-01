"""Port and container detection for ``e2e_stack.py up`` (api-route-wiring task 13.9, Req 17.2).

Docker is never contacted: the ``docker`` calls are injected as fake callables.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "e2e_stack.py"
_spec = importlib.util.spec_from_file_location("edu_it_e2e_stack_ports", _SCRIPT)
assert _spec is not None and _spec.loader is not None
stack = importlib.util.module_from_spec(_spec)
sys.modules["edu_it_e2e_stack_ports"] = stack
_spec.loader.exec_module(stack)

PG = next(s for s in stack.SERVICES if s.key == "postgres")
RD = next(s for s in stack.SERVICES if s.key == "redis")

PG_ENV = {"POSTGRES_USER": "edu_it", "POSTGRES_PASSWORD": "change-me", "POSTGRES_DB": "edu_it"}
COMPOSE_PG = stack.Holder(
    name="edu-it-postgres-1",
    image="postgres:17",
    labels={"com.docker.compose.project": "edu-it", "com.docker.compose.service": "postgres"},
    env=PG_ENV,
)
COMPOSE_RD = stack.Holder(
    name="edu-it-redis-1",
    image="redis:7",
    labels={"com.docker.compose.project": "edu-it", "com.docker.compose.service": "redis"},
    env={},
)


def test_free_port_starts_our_container() -> None:
    d = stack.decide(PG, [], port_busy=False)
    assert (d.action, d.container, d.owned) == ("start", stack.POSTGRES_CONTAINER, True)


def test_non_docker_process_on_port_fails_early() -> None:
    d = stack.decide(PG, [], port_busy=True)
    assert d.action == "fail" and "5432" in d.message and "not a Docker container" in d.message


def test_healthy_compose_service_is_reused_and_not_owned() -> None:
    d = stack.decide(PG, [COMPOSE_PG], port_busy=True)
    assert (d.action, d.container, d.owned) == ("reuse", "edu-it-postgres-1", False)
    assert "reusing" in d.message and "edu-it-postgres-1" in d.message
    d = stack.decide(RD, [COMPOSE_RD], port_busy=True)
    assert (d.action, d.container, d.owned) == ("reuse", "edu-it-redis-1", False)


def test_compose_postgres_with_other_credentials_fails() -> None:
    other = stack.Holder(**{**COMPOSE_PG.__dict__, "env": {**PG_ENV, "POSTGRES_PASSWORD": "x"}})
    d = stack.decide(PG, [other], port_busy=True)
    assert d.action == "fail" and "credentials" in d.message
    assert "docker stop edu-it-postgres-1" in d.message


def test_foreign_container_fails_naming_it_and_the_stop_command() -> None:
    foreign = stack.Holder("dme-db-1", "postgis/postgis:16-3.4", {}, {})
    d = stack.decide(PG, [foreign], port_busy=True)
    assert d.action == "fail"
    assert "dme-db-1" in d.message and "docker stop dme-db-1" in d.message
    assert "5432" in d.message


def test_second_up_reuses_its_own_labelled_container_and_keeps_owning_it() -> None:
    own = stack.Holder(stack.POSTGRES_CONTAINER, "postgres:17", {stack.OWN_LABEL: "1"}, PG_ENV)
    d = stack.decide(PG, [own], port_busy=True)
    assert (d.action, d.container, d.owned) == ("reuse", stack.POSTGRES_CONTAINER, True)


def test_same_name_without_our_label_is_not_ours() -> None:
    impostor = stack.Holder(stack.POSTGRES_CONTAINER, "postgres:17", {}, PG_ENV)
    assert stack.decide(PG, [impostor], port_busy=True).action == "fail"


def test_find_holders_uses_published_port_and_inspect() -> None:
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[1] == "ps":
            assert f"publish={stack.POSTGRES_PORT}" in argv
            return "edu-it-postgres-1\n"
        return json.dumps(
            [
                {
                    "Name": "/edu-it-postgres-1",
                    "Config": {
                        "Image": "postgres:17",
                        "Labels": COMPOSE_PG.labels,
                        "Env": ["POSTGRES_USER=edu_it", "POSTGRES_PASSWORD=change-me", "PATH=/x"],
                    },
                    "State": {"Running": True},
                }
            ]
        )

    (holder,) = stack.find_holders(stack.POSTGRES_PORT, run)
    assert holder.name == "edu-it-postgres-1" and holder.image == "postgres:17"
    assert holder.env["POSTGRES_PASSWORD"] == "change-me"
    assert stack.find_holders(1, lambda argv: "") == []


def test_apply_decisions_drops_reused_start_steps_and_retargets_probes() -> None:
    plan = stack.build_plan({})
    reuse_pg = stack.decide(PG, [COMPOSE_PG], port_busy=True)
    start_rd = stack.decide(RD, [], port_busy=False)
    out = stack.apply_decisions(plan, {"postgres": reuse_pg, "redis": start_rd})
    names = [s.name for s in out]
    assert "postgres" not in names and "redis" in names
    ready = next(s for s in out if s.name == "postgres-ready")
    assert "edu-it-postgres-1" in ready.argv and stack.POSTGRES_CONTAINER not in ready.argv


def test_started_containers_carry_the_ownership_label() -> None:
    for name in ("postgres", "redis"):
        argv = next(s for s in stack.build_plan({}) if s.name == name).argv
        assert f"{stack.OWN_LABEL}=1" in argv


def test_down_removes_only_recorded_containers() -> None:
    assert [s.argv[-1] for s in stack.build_down_plan([])] == []
    only_redis = stack.build_down_plan([stack.REDIS_CONTAINER])
    assert [s.argv[-1] for s in only_redis] == [stack.REDIS_CONTAINER]


@pytest.mark.parametrize("bad", ["edu-it-postgres-1", "dme-db-1"])
def test_down_never_removes_unrecorded_names(bad: str) -> None:
    plan = stack.build_down_plan([stack.POSTGRES_CONTAINER])
    assert bad not in [s.argv[-1] for s in plan]
