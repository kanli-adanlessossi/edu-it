"""Unit tests for the pure parts of ``tools/kind_harness.py`` (kubernetes-deployment task 6.1).

Nothing here starts kind, Docker or kubectl.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "kind_harness.py"


def _load():
    spec = importlib.util.spec_from_file_location("edu_it_kind_harness", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["edu_it_kind_harness"] = module
    spec.loader.exec_module(module)
    return module


harness = _load()


def test_plan_order_matches_requirement() -> None:
    names = [s.name for s in harness.build_plan()]
    firsts = [
        names.index(n)
        for n in ("calico", "metrics-server", "ingress-nginx", "crds-external-secrets", "registry")
    ]
    assert firsts == sorted(firsts)
    assert names.index("crds-gateway-api") < names.index("registry")
    assert names.index("crds-servicemonitor") < names.index("registry")
    assert names.index("registry") < min(i for i, n in enumerate(names) if n.startswith("preload-"))


def test_crd_selection_and_unknown_set() -> None:
    names = [s.name for s in harness.build_plan(crds=["gateway-api"])]
    assert "crds-gateway-api" in names and "crds-external-secrets" not in names
    with pytest.raises(ValueError):
        harness.build_plan(crds=["nope"])


def test_every_lab_image_is_preloaded_into_the_cluster() -> None:
    steps = {s.name: s.argv for s in harness.build_plan()}
    for image in harness.LAB_IMAGES:
        assert steps[f"preload-{image}"] == (
            "kind",
            "load",
            "docker-image",
            image,
            "--name",
            harness.CLUSTER_NAME,
        )


def test_registry_listens_on_localhost_5001() -> None:
    run = next(s for s in harness.build_plan() if s.name == "registry")
    assert "127.0.0.1:5001:5000" in run.argv


def test_api_address_cidr() -> None:
    endpoints = {"subsets": [{"addresses": [{"ip": "172.18.0.2"}], "ports": [{"port": 6443}]}]}
    assert harness.api_address_cidr(endpoints) == ("172.18.0.2/32", 6443)
    with pytest.raises(ValueError):
        harness.api_address_cidr({"subsets": []})


def test_checked_in_kind_config_matches_domain_constant() -> None:
    errors = harness.check_kind_config(
        harness.KIND_CONFIG.read_text(encoding="utf-8"),
        harness.DOMAIN_CONFIG.read_text(encoding="utf-8"),
    )
    assert errors == []


def test_kind_config_topology_and_lab_pool() -> None:
    text = harness.KIND_CONFIG.read_text(encoding="utf-8")
    assert text.count("- role: control-plane") == 1
    assert text.count("- role: worker") == 2
    assert 'node-labels: "edu-it.io/pool=labs"' in text
    assert 'register-with-taints: "edu-it.io/labs=true:NoSchedule"' in text


def test_check_kind_config_reports_mismatch() -> None:
    domain = "SANDBOX_PID_LIMIT: Final[int] = 128\n"
    bad = "disableDefaultCNI: false\npodPidsLimit: 64\n"
    errors = harness.check_kind_config(bad, domain)
    assert len(errors) == 3
    assert any("singleProcessOOMKill" in e for e in errors)


def test_run_decodes_utf8_output_regardless_of_the_locale_codepage() -> None:
    """kubectl prints UTF-8 (for example the ellipsis); the Windows codepage is cp1252."""
    code = "import sys; sys.stdout.buffer.write('a\\u2026b \\u00e9 \\u8a9e\\n'.encode('utf-8'))"
    assert harness._run((sys.executable, "-c", code)).strip() == "a\u2026b \u00e9 \u8a9e"
    bad = "import sys; sys.stdout.buffer.write(b'x\\x81\\x8dy\\n')"  # undefined in cp1252 too
    assert harness._run((sys.executable, "-c", bad)).startswith("x")


def test_skip_removes_only_the_named_component() -> None:
    full = [s.name for s in harness.build_plan()]
    skipped = [s.name for s in harness.build_plan(skip=["ingress-nginx"])]
    assert [n for n in full if n not in skipped] == [
        "ingress-nginx",
        "ingress-nginx-workers",
        "ingress-nginx-ready",
    ]
    with pytest.raises(ValueError, match="cannot skip"):
        harness.build_plan(skip=["calico"])


def test_ingress_nginx_worker_count_is_pinned_before_the_readiness_wait() -> None:
    """A per-CPU worker count exceeds the cluster-wide podPidsLimit on many-core hosts."""

    plan = harness.build_plan()
    names = [s.name for s in plan]
    assert names.index("ingress-nginx") < names.index("ingress-nginx-workers")
    assert names.index("ingress-nginx-workers") < names.index("ingress-nginx-ready")
    argv = next(s for s in plan if s.name == "ingress-nginx-workers").argv
    assert argv[-1] == '{"data":{"worker-processes":"1"}}'


# --- kind test values override (task 14.1, Req 16.4, 16.11) ---

_CHART = harness.ROOT / "deploy" / "helm" / "edu-it"


def test_helm_values_args_only_with_short_ttl() -> None:
    assert harness.helm_values_args() == ()
    assert harness.helm_values_args(False) == ()
    assert harness.helm_values_args(True) == ("-f", str(harness.KIND_TEST_VALUES))
    assert harness.KIND_TEST_VALUES == _CHART / "ci" / "values-kind-test.yaml"


def test_values_args_command_prints_override_only_on_request(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert harness.main(["values-args"]) == 0
    assert capsys.readouterr().out.strip() == ""
    assert harness.main(["values-args", "--short-ttl"]) == 0
    assert str(harness.KIND_TEST_VALUES) in capsys.readouterr().out


def test_kind_override_sets_only_a_valid_short_ttl_and_no_production_file_has_it() -> None:
    yaml = pytest.importorskip("yaml")
    override = yaml.safe_load(harness.KIND_TEST_VALUES.read_text(encoding="utf-8"))
    assert set(override) == {"kindTest"}
    ttl = override["kindTest"]["sessionTtlSeconds"]
    assert isinstance(ttl, int) and 1 <= ttl < 3600
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((_CHART / "values.schema.json").read_text(encoding="utf-8"))
    jsonschema.validate(override["kindTest"], schema["properties"]["kindTest"])
    for name in ("values", "values-dev", "values-eks", "values-aks", "values-onprem"):
        data = yaml.safe_load((_CHART / f"{name}.yaml").read_text(encoding="utf-8")) or {}
        assert "kindTest" not in data, name
