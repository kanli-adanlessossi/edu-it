"""Document check: ADR 0009 records the Pod_Layout spike result (kubernetes-deployment, Req 16.12).

**Validates: Requirements 16.12**
"""

# Claims: 16.12

from __future__ import annotations

import re
from pathlib import Path

ADR = Path(__file__).resolve().parents[2] / "docs" / "adr" / "0009-kubernetes-sandbox-provider.md"


def _spike_section() -> str:
    text = ADR.read_text(encoding="utf-8")
    match = re.search(r"^### Spike result\n(?P<body>.*?)(?=^#{1,3} |\Z)", text, re.M | re.S)
    assert match, "ADR 0009 has no 'Spike result' section"
    return match.group("body")


# Claims: 16.12
def test_adr_0009_records_a_run_spike_with_the_chosen_and_rejected_layouts() -> None:
    body = _spike_section()
    assert "Status: RUN" in body
    assert re.search(r"Chosen layout \| \*\*B\*\*", body)
    assert "Rejected layouts" in body
    assert "Evidence" in body


# Claims: 16.12
def test_chosen_layout_has_no_shared_process_namespace_and_freezes_through_exec() -> None:
    chosen = next(
        line for line in _spike_section().splitlines() if line.startswith("| Chosen layout")
    )
    assert "`shareProcessNamespace: false`" in chosen
    assert "freeze through `exec` in `learner`" in chosen
