"""Image references use a sha256 digest (kubernetes-deployment Req 7.11, 7.12, 12.11)."""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st
from check_image_refs import check_image_refs
from image_refs import compose_image_ref, is_pinned_digest, is_pinned_image_ref

_HEX = "0123456789abcdef"
_digest_hex = st.text(alphabet=_HEX, min_size=64, max_size=64)
_repo = st.from_regex(r"[a-z0-9]+([./-][a-z0-9]+){0,3}(:[0-9]{2,5}/[a-z0-9]+)?", fullmatch=True)
_REFERENCE = re.compile(r"[^\s@]+@sha256:[a-f0-9]{64}")


# Feature: kubernetes-deployment, Property 25: Image references use a sha256 digest
# Claims: 7.11, 7.12, 12.11
@given(st.text())
def test_accepts_iff_repository_at_sha256_64_lowercase_hex(ref: str) -> None:
    assert is_pinned_image_ref(ref) == (_REFERENCE.fullmatch(ref) is not None)


# Claims: 7.11, 7.12, 12.11
@given(_repo, _digest_hex)
def test_pinned_reference_accepted(repo: str, hex_: str) -> None:
    assert is_pinned_image_ref(f"{repo}@sha256:{hex_}")
    assert is_pinned_digest(f"sha256:{hex_}")


# Claims: 7.11, 7.12, 12.11
@given(_repo, st.from_regex(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,20}", fullmatch=True))
def test_tag_only_reference_rejected(repo: str, tag: str) -> None:
    assert not is_pinned_image_ref(f"{repo}:{tag}")
    assert not is_pinned_image_ref(repo)


# Claims: 7.11, 7.12, 12.11
@given(_repo, _digest_hex, st.sampled_from(["upper", "short", "long", "algo"]))
def test_malformed_digest_rejected(repo: str, hex_: str, kind: str) -> None:
    bad = {
        "upper": "sha256:" + hex_[:-1] + "A",
        "short": "sha256:" + hex_[:-1],
        "long": "sha256:" + hex_ + "0",
        "algo": "sha512:" + hex_,
    }[kind]
    assert not is_pinned_image_ref(f"{repo}@{bad}")
    assert not is_pinned_digest(bad)


# Claims: 7.11, 7.12, 12.11
@given(st.dictionaries(st.from_regex(r"[a-z]{1,8}", fullmatch=True), st.tuples(_repo, _digest_hex), min_size=1))
def test_check_image_refs_accepts_pinned_values(entries: dict) -> None:
    values = {"images": {n: {"repository": r, "digest": f"sha256:{h}"} for n, (r, h) in entries.items()}}
    assert check_image_refs(values) == []
    for entry in values["images"].values():
        assert is_pinned_image_ref(compose_image_ref(entry["repository"], entry["digest"]))


# Claims: 7.11, 7.12, 12.11
@given(_repo, st.from_regex(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,20}", fullmatch=True))
def test_check_image_refs_rejects_tag_only(repo: str, tag: str) -> None:
    assert check_image_refs({"images": {"api": {"repository": f"{repo}:{tag}"}}})
    assert check_image_refs({"images": {"api": {"repository": repo, "digest": tag}}})
