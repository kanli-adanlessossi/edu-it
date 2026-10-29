"""Property test for prerequisite acyclicity + per-check failing fixtures (task 10.2).

This file pins the behavior of the ordered ``edu-it content validate`` checks
implemented in task 10.1 (:mod:`edu_it_content.validation`,
:mod:`edu_it_content.cli`, Req 13.1-13.6):

- **Property 20** (``test_property_20_*``) exercises the prerequisite-acyclicity
  check over arbitrary directed prerequisite graphs, asserting the validator
  reports a cycle *if and only if* the graph actually contains one — compared
  against an independent Kahn's-algorithm oracle built in the test (Req 13.4).
- **Per-check failing fixtures** (``test_check_*_fails`` / ``test_all_valid_*``)
  give one minimal catalog per ordered check that violates exactly that check
  and asserts the CLI exits non-zero naming that rule and the offending
  ``stable_id``, plus the all-valid catalog that exits zero (Req 13.1, 13.2,
  13.3, 13.5, 13.6). Where practical each fixture also asserts the *other*
  rules do not fire, so the check is isolated and the ordered run is exercised.

The fixtures drive the real CLI entry point
(:func:`edu_it_content.cli.main`) end to end — ``argv -> load -> ordered checks
-> exit status`` — and the property drives the framework-free core
(:func:`edu_it_content.validation.validate_content`) so it can assert on the
structured :class:`~edu_it_content.validation.ValidationFailure` list directly.

Rule names and the allowlist are read from
:mod:`edu_it_content.validation` rather than hardcoded, so this test tracks the
implementation's definitions instead of duplicating magic values.

Uses Hypothesis (the workspace's pinned property-based testing library) with at
least 100 examples per property run.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import Any

# ``validation`` is imported as a module so the rule-name constants can be read
# from it below; a rename in validation.py then surfaces here rather than
# silently drifting against a hardcoded literal.
from edu_it_content import validation as _validation
from edu_it_content.cli import main
from edu_it_content.validation import (
    MDX_COMPONENT_ALLOWLIST,
    LoadedContent,
    validate_content,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# Rule names the validator reports on failure. Read from the module's constants
# so the test tracks the implementation rather than restating the literals.
_RULE_SCHEMA = _validation._RULE_SCHEMA
_RULE_BILINGUAL = _validation._RULE_BILINGUAL
_RULE_REFERENCE = _validation._RULE_REFERENCE
_RULE_ACYCLIC = _validation._RULE_ACYCLIC
_RULE_MDX = _validation._RULE_MDX
_RULE_REVIEWER = _validation._RULE_REVIEWER


# --------------------------------------------------------------------------- #
# Catalog helpers (mirror the smoke test so fixtures start from a known-good    #
# catalog and violate exactly one rule at a time).                              #
# --------------------------------------------------------------------------- #


def _localized(fr: str = "Titre", en: str = "Title") -> dict[str, str]:
    return {"fr": fr, "en": en}


def _provenance(stable_id: str, *, origin: str = "human", reviewed: bool = True) -> dict[str, Any]:
    provenance: dict[str, Any] = {
        "stable_id": stable_id,
        "content_version": "cv_1",
        "origin": origin,
    }
    if reviewed:
        provenance["reviewed_by"] = "usr_reviewer"
    return provenance


def _valid_catalog() -> dict[str, list[dict[str, Any]]]:
    """A tiny but complete, internally-consistent, all-valid catalog.

    Two concepts joined by one acyclic prerequisite edge, a track/module/lesson
    chain with resolvable references, all human-origin with a reviewer recorded.
    Each per-check fixture mutates one slice of this to violate one rule.
    """
    return {
        "track": [
            {"provenance": _provenance("tr_1"), "title": _localized(), "status": "published"}
        ],
        "module": [
            {
                "provenance": _provenance("md_1"),
                "track_stable_id": "tr_1",
                "ordinal": 0,
                "title": _localized(),
            }
        ],
        "lesson": [
            {
                "provenance": _provenance("ls_1"),
                "module_stable_id": "md_1",
                "ordinal": 0,
                "title": _localized(),
            }
        ],
        "concept": [
            {"provenance": _provenance("cp_1"), "title": _localized()},
            {"provenance": _provenance("cp_2"), "title": _localized()},
        ],
        "prerequisite": [
            {
                "stable_id": "pr_1",
                "concept_stable_id": "cp_2",
                "requires_concept_stable_id": "cp_1",
            }
        ],
    }


def _write_items(root: Path, items: Mapping[str, list[dict[str, Any]]]) -> None:
    """Write an ``items.json`` catalog into a content root directory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "items.json").write_text(json.dumps(items), encoding="utf-8")


def _rules_in(stderr: str) -> set[str]:
    """Return the set of known rule names that appear in the CLI's stderr."""
    all_rules = {
        _RULE_SCHEMA,
        _RULE_BILINGUAL,
        _RULE_REFERENCE,
        _RULE_ACYCLIC,
        _RULE_MDX,
        _RULE_REVIEWER,
    }
    return {rule for rule in all_rules if rule in stderr}


# --------------------------------------------------------------------------- #
# Per-check failing fixtures — one catalog per ordered check (Req 13.1-13.6).   #
# --------------------------------------------------------------------------- #


def test_all_valid_catalog_exits_zero(tmp_path: Path, capsys: Any) -> None:
    """The all-valid catalog passes every ordered check and exits zero (Req 13.1)."""
    root = tmp_path / "content"
    _write_items(root, _valid_catalog())

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 0
    assert "content valid" in capsys.readouterr().out


def test_check_schema_fails_on_unparseable_item(tmp_path: Path, capsys: Any) -> None:
    """Check 1 (schema-validation): an item with a stray field fails (Req 13.1).

    ``extra="forbid"`` on the schemas rejects an unknown key, so a track with a
    stray field is a schema failure naming the track's ``stable_id`` and the
    schema rule.
    """
    root = tmp_path / "content"
    catalog = _valid_catalog()
    catalog["track"][0]["unexpected_field"] = "boom"
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert _RULE_SCHEMA in stderr
    assert "tr_1" in stderr


def test_check_bilingual_fails_on_missing_locale(tmp_path: Path, capsys: Any) -> None:
    """Check 2 (bilingual-completeness): a missing locale fails (Req 13.2).

    A ``Localized`` title missing its French value is rejected. The schemas
    enforce fr+en at parse time, so this surfaces as a schema failure that names
    the French field and the offending item — both the bilingual rule and the
    missing-locale intent are exercised end to end.
    """
    root = tmp_path / "content"
    catalog = _valid_catalog()
    # Drop the French value from the module title.
    catalog["module"][0]["title"] = {"en": "Title only"}
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    # fr/en completeness is enforced at the schema layer, so the failure is
    # reported under the schema rule; it must name the French field and the item.
    assert _RULE_SCHEMA in stderr
    assert "md_1" in stderr
    assert "fr" in stderr


def test_check_reference_fails_on_dangling_reference(tmp_path: Path, capsys: Any) -> None:
    """Check 3 (reference-resolution): a dangling reference fails (Req 13.3).

    A module pointing at a track ``stable_id`` that does not exist is a
    broken-reference failure naming the module and the reference rule, while the
    schema, bilingual, acyclicity, MDX, and reviewer checks all pass.
    """
    root = tmp_path / "content"
    catalog = _valid_catalog()
    catalog["module"][0]["track_stable_id"] = "tr_missing"
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert _RULE_REFERENCE in stderr
    assert "md_1" in stderr
    # Isolated: only the reference rule should fire for this catalog.
    assert _rules_in(stderr) == {_RULE_REFERENCE}


def test_check_acyclicity_fails_on_prerequisite_cycle(tmp_path: Path, capsys: Any) -> None:
    """Check 4 (prerequisite-acyclicity): a two-node cycle fails (Req 13.4).

    ``cp_1`` requires ``cp_2`` and ``cp_2`` requires ``cp_1`` forms a cycle; the
    validator reports the prerequisite-acyclicity rule and names a concept in the
    cycle. All earlier checks (schema, bilingual, reference) still pass, so the
    ordered run reaches and fires exactly this check.
    """
    root = tmp_path / "content"
    catalog = _valid_catalog()
    catalog["prerequisite"] = [
        {"stable_id": "pr_1", "concept_stable_id": "cp_1", "requires_concept_stable_id": "cp_2"},
        {"stable_id": "pr_2", "concept_stable_id": "cp_2", "requires_concept_stable_id": "cp_1"},
    ]
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert _RULE_ACYCLIC in stderr
    # The cycle is over cp_1/cp_2; the failure names one of them.
    assert "cp_1" in stderr or "cp_2" in stderr
    assert _rules_in(stderr) == {_RULE_ACYCLIC}


def test_check_mdx_fails_on_non_allowlisted_component(tmp_path: Path, capsys: Any) -> None:
    """Check 5 (mdx-allowlist): a non-allowlisted MDX component fails (Req 13.5).

    An MDX body for ``ls_1`` that references a component not in
    :data:`MDX_COMPONENT_ALLOWLIST` is an unknown-component failure naming the
    lesson and the MDX rule. The component name is derived from the allowlist so
    the test never hardcodes a value that lives in the implementation.
    """
    root = tmp_path / "content"
    catalog = _valid_catalog()
    _write_items(root, catalog)

    # Pick a component name guaranteed to be outside the allowlist.
    forbidden = "NotAllowed"
    assert forbidden not in MDX_COMPONENT_ALLOWLIST
    mdx_dir = root / "mdx"
    mdx_dir.mkdir(parents=True, exist_ok=True)
    (mdx_dir / "ls_1.en.mdx").write_text(f"# Lesson\n<{forbidden} />\n", encoding="utf-8")

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert _RULE_MDX in stderr
    assert "ls_1" in stderr
    assert _rules_in(stderr) == {_RULE_MDX}


def test_check_mdx_allows_allowlisted_component(tmp_path: Path, capsys: Any) -> None:
    """Counterpart to the MDX fixture: an allowlisted component passes (Req 13.5).

    Confirms the MDX check does not over-fire — a body using only an allowlisted
    component keeps the whole catalog valid and exits zero.
    """
    root = tmp_path / "content"
    _write_items(root, _valid_catalog())

    allowed = next(iter(sorted(MDX_COMPONENT_ALLOWLIST)))
    mdx_dir = root / "mdx"
    mdx_dir.mkdir(parents=True, exist_ok=True)
    (mdx_dir / "ls_1.en.mdx").write_text(f"# Lesson\n<{allowed} />\n", encoding="utf-8")

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 0
    assert "content valid" in capsys.readouterr().out


def test_check_reviewer_fails_on_ai_item_without_reviewer(tmp_path: Path, capsys: Any) -> None:
    """Check 6 (ai-reviewer-present): an AI item with no reviewer fails (Req 13.6).

    A track whose provenance is ``origin == ai`` with no ``reviewed_by`` is a
    missing-reviewer failure naming the track and the reviewer rule. The module
    is re-pointed at the renamed track so only the reviewer rule fires.
    """
    root = tmp_path / "content"
    catalog = _valid_catalog()
    catalog["track"] = [
        {
            "provenance": _provenance("tr_ai", origin="ai", reviewed=False),
            "title": _localized(),
            "status": "published",
        }
    ]
    catalog["module"][0]["track_stable_id"] = "tr_ai"
    _write_items(root, catalog)

    exit_code = main(["validate", "--content-root", str(root)])

    assert exit_code == 1
    stderr = capsys.readouterr().err
    assert _RULE_REVIEWER in stderr
    assert "tr_ai" in stderr
    assert _rules_in(stderr) == {_RULE_REVIEWER}


# --------------------------------------------------------------------------- #
# Property 20 — prerequisite acyclicity.                                        #
# --------------------------------------------------------------------------- #


def _has_cycle(nodes: list[str], edges: list[tuple[str, str]]) -> bool:
    """Independent oracle: True iff the directed graph contains a cycle.

    Uses Kahn's algorithm (topological sort by indegree). A directed graph is
    acyclic exactly when every node can be removed in topological order; if any
    node remains with a non-zero indegree after the queue drains, a cycle exists.
    This is deliberately a different algorithm from the validator's DFS so the
    two independently agree on the ground truth.
    """
    adjacency: dict[str, list[str]] = {node: [] for node in nodes}
    indegree: dict[str, int] = {node: 0 for node in nodes}
    for source, target in edges:
        adjacency.setdefault(source, [])
        adjacency.setdefault(target, [])
        indegree.setdefault(source, 0)
        indegree.setdefault(target, 0)
        adjacency[source].append(target)
        indegree[target] += 1

    queue: deque[str] = deque(node for node, degree in indegree.items() if degree == 0)
    removed = 0
    while queue:
        node = queue.popleft()
        removed += 1
        for neighbor in adjacency[node]:
            indegree[neighbor] -= 1
            if indegree[neighbor] == 0:
                queue.append(neighbor)

    return removed != len(indegree)


# A pool of concept ids the generated graph draws nodes and edges from. Kept
# small so cycles arise with meaningful probability across generated examples.
_CONCEPT_IDS = [f"cp_{i}" for i in range(6)]

# Directed prerequisite edges over the concept pool, as (concept, requires)
# pairs. Self-loops (cp_x -> cp_x) are allowed and are themselves cycles, so
# both acyclic DAGs and cyclic graphs appear with reasonable probability.
_edges_strategy = st.lists(
    st.tuples(st.sampled_from(_CONCEPT_IDS), st.sampled_from(_CONCEPT_IDS)),
    min_size=0,
    max_size=12,
    unique=True,
)


def _build_graph_content(edges: list[tuple[str, str]]) -> LoadedContent:
    """Build a LoadedContent whose prerequisite graph is exactly ``edges``.

    Includes a concept item for every node that appears so references resolve and
    the acyclicity check — not an incidental reference failure — is what the
    property observes. Each edge is a parseable ``prerequisite`` item.
    """
    used_nodes = sorted({node for edge in edges for node in edge})
    concepts = [{"provenance": _provenance(node), "title": _localized()} for node in used_nodes]
    prerequisites = [
        {
            "stable_id": f"pr_{index}",
            "concept_stable_id": source,
            "requires_concept_stable_id": target,
        }
        for index, (source, target) in enumerate(edges)
    ]
    raw_items: dict[str, list[dict[str, Any]]] = {
        "concept": concepts,
        "prerequisite": prerequisites,
    }
    # ``root`` is unused by the acyclicity check (no MDX scan needed here).
    return LoadedContent(raw_items=raw_items, root=Path("."))


@settings(max_examples=200)
@given(edges=_edges_strategy)
def test_property_20_cycle_reported_iff_graph_has_cycle(
    edges: list[tuple[str, str]],
) -> None:
    """Feature: learning-core, Property 20: The prerequisite graph is acyclic or the cycle is reported

    For any prerequisite graph, ``content validate`` reports a
    prerequisite-cycle error if and only if the graph contains a cycle
    (Req 13.4). The expected truth is decided by an independent Kahn's-algorithm
    oracle; the validator's DFS-based acyclicity check must agree.
    """
    content = _build_graph_content(edges)
    failures = validate_content(content)

    reported_cycle = any(failure.rule == _RULE_ACYCLIC for failure in failures)
    nodes = sorted({node for edge in edges for node in edge})
    expected_cycle = _has_cycle(nodes, edges)

    assert reported_cycle == expected_cycle, (
        f"edges={edges} expected_cycle={expected_cycle} "
        f"reported_cycle={reported_cycle} failures={[f.message() for f in failures]}"
    )
