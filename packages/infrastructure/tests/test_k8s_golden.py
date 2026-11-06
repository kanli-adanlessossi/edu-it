"""Golden manifest test for the Lab_Pod builder (layout B of ADR 0009).

The expected manifest lives in ``tests/golden/lab_pod_layout_b.json``.

Regenerate it after an intended change to the builder, then review the diff:

    $env:UPDATE_GOLDEN = "1"
    .venv\\Scripts\\python -m pytest packages/infrastructure/tests/test_k8s_golden.py

(bash: ``UPDATE_GOLDEN=1 .venv/bin/python -m pytest ...``). Without the variable the
test only compares and never writes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration, build_lab_pod

GOLDEN = Path(__file__).parent / "golden" / "lab_pod_layout_b.json"


@dataclass(frozen=True)
class _Lab:
    image: str = "registry.example/lab@sha256:abc"


def _render() -> str:
    settings = K8sSettings(
        namespace="labs",
        runtime_class_name="gvisor",
        runtime_kind="gvisor",
        node_selector={"edu-it/pool": "labs"},
        tolerations=(Toleration("edu-it/lab", "Equal", "true", "NoSchedule"),),
        node_loss_toleration_seconds=30,
        grader_gid=1001,
    )
    pod = build_lab_pod(build_sandbox_spec(_Lab(), "golden-session"), settings)
    return json.dumps(pod, indent=2, sort_keys=True) + "\n"


# Claims: 2.2, 2.6
def test_lab_pod_matches_golden_manifest() -> None:
    rendered = _render()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(rendered, encoding="utf-8", newline="\n")
    assert GOLDEN.exists(), "golden file missing; run with UPDATE_GOLDEN=1 to create it"
    expected = GOLDEN.read_text(encoding="utf-8")
    assert json.loads(rendered) == json.loads(expected)
    assert rendered == expected
