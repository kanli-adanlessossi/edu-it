"""Tests for the Generator's content writer.

Validates: Requirements 5.8, 5.10, 5.5
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from edu_it_content.schemas import Lesson, Origin
from edu_it_infrastructure.fakes.clock import Test_Clock
from edu_it_infrastructure.fakes.idempotency import Sequential_IdGenerator
from edu_it_worker.ai.content_writer import (
    ContentWriter,
    GenerationInfo,
    IncompleteUnitError,
    canonical_json,
    content_version,
    slugify,
)
from hypothesis import given
from hypothesis import strategies as st

INFO = GenerationInfo(model="m-1", prompt_id="lesson", prompt_version="1")
TRACK = "networking-basics"


def _writer() -> ContentWriter:
    return ContentWriter(
        Test_Clock(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)), Sequential_IdGenerator("id")
    )


def _lesson(writer: ContentWriter, title: str, taken: tuple[str, ...] = ()) -> dict[str, object]:
    sid = writer.stable_id("lesson", title, taken)
    item = {
        "module_stable_id": "module.m",
        "ordinal": 0,
        "title": {"en": title, "fr": "Titre"},
    }
    return writer.stamp(item, stable_id=sid, info=INFO, bodies={"fr": "f", "en": "e"})


def test_stamp_writes_unreviewed_ai_provenance_that_the_schema_accepts() -> None:
    item = _lesson(_writer(), "Reading IP Addresses")
    prov = item["provenance"]
    assert isinstance(prov, dict)
    assert prov["stable_id"] == "lesson.reading-ip-addresses"
    assert prov["origin"] == "ai"
    assert "reviewed_by" not in prov
    assert "reviewed_at" not in prov
    assert prov["generation_date"] == "2026-01-02T03:04:05Z"
    assert str(prov["content_version"]).startswith("sha256:")
    assert len(str(prov["content_version"])) == len("sha256:") + 64
    parsed = Lesson.model_validate(item)
    assert parsed.provenance.origin is Origin.AI
    assert parsed.provenance.reviewed_by is None


def test_content_version_changes_with_content_and_ignores_old_provenance() -> None:
    writer = _writer()
    a = writer.stamp({"title": "x"}, stable_id="lesson.a", info=INFO)
    b = writer.stamp({"title": "y"}, stable_id="lesson.a", info=INFO)
    again = writer.stamp(a, stable_id="lesson.a", info=INFO)
    assert a["provenance"]["content_version"] != b["provenance"]["content_version"]
    assert again["provenance"]["content_version"] == a["provenance"]["content_version"]
    with_body = writer.stamp({"title": "x"}, stable_id="lesson.a", info=INFO, bodies={"en": "z"})
    assert with_body["provenance"]["content_version"] != a["provenance"]["content_version"]


def test_stable_id_slug_rule_and_id_generator_for_collisions_and_empty_slugs() -> None:
    writer = _writer()
    assert writer.stable_id("lesson", "Les adresses réseau") == "lesson.les-adresses-reseau"
    assert writer.stable_id("lesson", "!!!") == "lesson.id-1"
    assert writer.stable_id("lesson", "DNS", taken=("lesson.dns",)) == "lesson.dns-id-2"
    with pytest.raises(ValueError):
        writer.stable_id("Bad Kind", "x")


def test_render_writes_fr_and_en_mdx_and_sorted_json_together() -> None:
    writer = _writer()
    b = _lesson(writer, "Beta")
    a = _lesson(writer, "Alpha")
    files = writer.render(
        TRACK,
        lists={"lessons.json": [b, a]},
        bodies={"lesson.alpha": {"fr": "Bonjour\r\n", "en": "Hello"}},
    )
    base = f"content/{TRACK}"
    assert set(files) == {
        f"{base}/lessons.json",
        f"{base}/mdx/lesson.alpha.fr.mdx",
        f"{base}/mdx/lesson.alpha.en.mdx",
    }
    assert files[f"{base}/mdx/lesson.alpha.fr.mdx"] == b"Bonjour\n"
    text = files[f"{base}/lessons.json"].decode()
    assert text == canonical_json(json.loads(text))
    assert [i["provenance"]["stable_id"] for i in json.loads(text)] == [
        "lesson.alpha",
        "lesson.beta",
    ]


def test_render_is_deterministic_and_merges_by_stable_id() -> None:
    writer = _writer()
    old = _lesson(writer, "Alpha")
    path = f"content/{TRACK}/lessons.json"
    existing = {path: canonical_json([old])}
    new = {**old, "ordinal": 3}
    one = writer.render(TRACK, lists={"lessons.json": [new]}, existing=existing)
    two = writer.render(TRACK, lists={"lessons.json": [new]}, existing=existing)
    assert one == two
    merged = json.loads(one[path])
    assert len(merged) == 1
    assert merged[0]["ordinal"] == 3


def test_missing_locale_discards_the_whole_unit() -> None:
    writer = _writer()
    for bodies in ({"lesson.a": {"en": "only"}}, {"lesson.a": {"fr": "x", "en": "  "}}):
        with pytest.raises(IncompleteUnitError):
            writer.render(TRACK, lists={"lessons.json": []}, bodies=bodies)


def test_documents_are_written_as_single_objects() -> None:
    files = _writer().render(TRACK, documents={"brief.json": {"title": {"en": "T", "fr": "T"}}})
    assert json.loads(files[f"content/{TRACK}/brief.json"]) == {"title": {"en": "T", "fr": "T"}}


_json = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(max_size=8),
    lambda c: st.lists(c, max_size=3) | st.dictionaries(st.text(max_size=5), c, max_size=3),
    max_leaves=8,
)


@given(_json)
def test_canonical_json_is_stable_under_key_order_and_round_trips(value: object) -> None:
    text = canonical_json(value)
    assert text.endswith("\n")
    assert json.loads(text) == value
    assert canonical_json(json.loads(text)) == text
    assert content_version(value) == content_version(json.loads(text))


@given(st.text(max_size=30))
def test_slugify_output_is_url_safe(text: str) -> None:
    slug = slugify(text)
    assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in slug)
    assert not slug.startswith("-")
    assert not slug.endswith("-")
