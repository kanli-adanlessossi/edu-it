"""Tests for tools/verify_image_signatures.py (cosign is mocked).

**Validates: Requirements 12.14, 15.8**
"""

# Claims: 12.14, 15.8

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest
from verify_image_signatures import (
    SKIP_NOTE,
    collect_image_refs,
    main,
    verify_local,
    verify_signatures,
)

WEB = "sha256:" + "a" * 64
API = "sha256:" + "b" * 64
IDENTITY = "https://github.com/edu-it/.*"
ISSUER = "https://token.actions.githubusercontent.com"


def _values(repo: str = "ghcr.io/edu-it", web: str = WEB, api: str = API) -> str:
    return (
        "images:\n"
        f"  web:\n    repository: {repo}/edu-it-web\n    digest: {web}\n"
        f"  api:\n    repository: {repo}/edu-it-api\n    digest: {api}\n"
    )


class FakeCosign:
    """Records argv and fails for refs listed in ``bad`` (ref -> (code, message))."""

    def __init__(self, bad: dict[str, tuple[int, str]] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.bad = bad or {}

    def __call__(self, argv: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(argv))
        return self.bad.get(argv[-1], (0, ""))


# Claims: 12.14
def test_valid_signatures_pass_and_every_digest_is_verified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "values.yaml"
    f.write_text(_values(), encoding="utf-8")
    cosign = FakeCosign()
    code = main(
        ["--certificate-identity-regexp", IDENTITY, str(f)],
        runner=cosign,
    )
    assert code == 0
    assert [c[-1] for c in cosign.calls] == [
        f"ghcr.io/edu-it/edu-it-web@{WEB}",
        f"ghcr.io/edu-it/edu-it-api@{API}",
    ]
    for call in cosign.calls:
        assert call[:2] == ["cosign", "verify"]
        assert call[2:4] == ["--certificate-identity-regexp", IDENTITY]
        assert call[4:6] == ["--certificate-oidc-issuer", ISSUER]
    assert "verified 2 image(s)" in capsys.readouterr().out


# Claims: 12.14
def test_missing_signature_fails_and_names_the_image(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "values.yaml"
    f.write_text(_values(), encoding="utf-8")
    ref = f"ghcr.io/edu-it/edu-it-api@{API}"
    cosign = FakeCosign({ref: (1, "Error: no signatures found")})
    code = main(["--certificate-identity-regexp", IDENTITY, str(f)], runner=cosign)
    out = capsys.readouterr().out
    assert code == 1
    assert "images.api" in out and ref in out and "no signatures found" in out
    assert "images.web" not in out


# Claims: 12.14
def test_invalid_signature_fails() -> None:
    refs = {"web": f"ghcr.io/edu-it/edu-it-web@{WEB}"}
    cosign = FakeCosign({refs["web"]: (1, "Error: none of the expected identities matched")})
    errors = verify_signatures(refs, identity_regexp=IDENTITY, oidc_issuer=ISSUER, runner=cosign)
    assert len(errors) == 1
    assert "signature missing or invalid" in errors[0]
    assert "expected identities" in errors[0]


# Claims: 12.14
def test_missing_cosign_binary_fails_with_message() -> None:
    refs = {"web": f"ghcr.io/edu-it/edu-it-web@{WEB}"}
    cosign = FakeCosign({refs["web"]: (127, "cosign executable not found")})
    errors = verify_signatures(refs, identity_regexp=IDENTITY, oidc_issuer=ISSUER, runner=cosign)
    assert errors and "cosign executable not found" in errors[0]


# Claims: 12.14
def test_unpinned_digest_is_rejected_without_running_cosign(tmp_path: Path) -> None:
    f = tmp_path / "values.yaml"
    f.write_text(_values(web="latest"), encoding="utf-8")
    cosign = FakeCosign()
    code = main(["--certificate-identity-regexp", IDENTITY, str(f)], runner=cosign)
    assert code == 1
    assert cosign.calls == []


# Claims: 15.8
def test_pull_request_mode_verifies_local_digests_and_records_skip_note(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "values.yaml"
    f.write_text(_values(repo="localhost:5001"), encoding="utf-8")
    cosign = FakeCosign()
    code = main(["--pull-request", str(f)], runner=cosign)
    assert code == 0
    assert cosign.calls == []
    assert SKIP_NOTE in capsys.readouterr().out
    assert "skipped" in SKIP_NOTE


# Claims: 15.8
def test_pull_request_mode_rejects_non_local_registry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    f = tmp_path / "values.yaml"
    f.write_text(_values(), encoding="utf-8")
    cosign = FakeCosign()
    code = main(["--pull-request", str(f)], runner=cosign)
    assert code == 1
    assert cosign.calls == []
    assert "not in the local registry" in capsys.readouterr().out


# Claims: 15.8
def test_verify_local_accepts_localhost_only() -> None:
    assert verify_local({"a": f"localhost:5001/x@{WEB}"}) == []
    assert verify_local({"a": f"ghcr.io/x@{WEB}"}) != []


# Claims: 12.14
def test_collect_image_refs_reports_missing_images() -> None:
    assert collect_image_refs({})[1] == ["images is missing or empty"]


# Claims: 12.14
def test_usage_errors_exit_2(tmp_path: Path) -> None:
    f = tmp_path / "values.yaml"
    f.write_text(_values(), encoding="utf-8")
    assert main([str(f)], runner=FakeCosign()) == 2  # identity required outside PR mode
    assert main(["--pull-request", str(tmp_path / "missing.yaml")], runner=FakeCosign()) == 2
