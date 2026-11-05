"""Tests for the image check tools (check_image_contents, image_refs, check_image_refs).

**Validates: Requirements 12.3, 12.4, 12.11**
"""

# Claims: 12.3, 12.4, 12.11

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from check_image_contents import (
    FORBIDDEN_BINARIES,
    check_image_contents,
    check_user,
    find_forbidden_binaries,
)
from check_image_contents import main as contents_main
from check_image_refs import check_image_refs
from check_image_refs import main as refs_main
from image_refs import compose_image_ref, is_pinned_digest, is_pinned_image_ref

DIGEST = "sha256:" + "a" * 64
FORBIDDEN = ["sh", "bash", "apk", "apt", "apt-get", "dnf", "yum", "pip"]


def _file(path: str) -> tuple[str, bool]:
    return (path, False)


# Claims: 12.3, 12.4, 12.11
def test_forbidden_set_is_exactly_the_required_binaries() -> None:
    assert FORBIDDEN_BINARIES == frozenset(FORBIDDEN)


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("binary", FORBIDDEN)
@pytest.mark.parametrize("directory", ["bin", "usr/bin", "sbin", "usr/sbin"])
def test_forbidden_binary_is_rejected(binary: str, directory: str) -> None:
    found = find_forbidden_binaries([[_file(f"{directory}/{binary}")]])
    assert found == [f"/{directory}/{binary}"]


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("binary", FORBIDDEN)
def test_image_without_forbidden_binary_is_accepted(binary: str) -> None:
    layers = [[_file("app/server"), _file(f"opt/{binary}"), _file(f"usr/bin/{binary}x")]]
    assert find_forbidden_binaries(layers) == []
    assert check_image_contents({"config": {"User": "10001"}}, layers) == []


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("binary", FORBIDDEN)
def test_whiteout_removes_forbidden_binary(binary: str) -> None:
    layers = [[_file(f"bin/{binary}")], [_file(f"bin/.wh.{binary}")]]
    assert find_forbidden_binaries(layers) == []


# Claims: 12.3, 12.4, 12.11
def test_opaque_whiteout_removes_directory_and_later_layer_readds() -> None:
    layers = [[_file("bin/sh"), _file("bin/bash")], [("bin/.wh..wh..opq", False)]]
    assert find_forbidden_binaries(layers) == []
    layers.append([_file("bin/sh")])
    assert find_forbidden_binaries(layers) == ["/bin/sh"]


# Claims: 12.3, 12.4, 12.11
def test_directory_entry_named_like_binary_is_ignored() -> None:
    assert find_forbidden_binaries([[("bin/sh", True)]]) == []


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("user", ["1", "10001", "65532:65532", "1000:0"])
def test_numeric_non_root_user_is_accepted(user: str) -> None:
    assert check_user(user) is None


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("user", [None, "", "  ", "root", "0", "0:0", "app", "1000:staff", 1000])
def test_missing_root_or_non_numeric_user_is_rejected(user: object) -> None:
    assert check_user(user) is not None


# Claims: 12.3, 12.4, 12.11
def test_check_image_contents_reports_user_and_binary() -> None:
    errors = check_image_contents({"config": {}}, [[_file("bin/sh")]])
    assert len(errors) == 2
    assert any("User" in e for e in errors)
    assert any("/bin/sh" in e for e in errors)


def _tar_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _docker_archive(path: Path, user: str, layer_files: list[str]) -> Path:
    layer = _tar_bytes({n: b"x" for n in layer_files})
    manifest = [{"Config": "cfg.json", "Layers": ["layer0.tar"]}]
    config = {"config": {"User": user}}
    path.write_bytes(
        _tar_bytes(
            {
                "manifest.json": json.dumps(manifest).encode(),
                "cfg.json": json.dumps(config).encode(),
                "layer0.tar": layer,
            }
        )
    )
    return path


# Claims: 12.3, 12.4, 12.11
def test_cli_accepts_clean_archive(tmp_path: Path) -> None:
    archive = _docker_archive(tmp_path / "ok.tar", "10001", ["app/server"])
    assert contents_main([str(archive)]) == 0


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("binary", FORBIDDEN)
def test_cli_rejects_archive_with_forbidden_binary(tmp_path: Path, binary: str) -> None:
    archive = _docker_archive(tmp_path / "bad.tar", "10001", [f"usr/bin/{binary}"])
    assert contents_main([str(archive)]) == 1


# Claims: 12.3, 12.4, 12.11
def test_cli_rejects_root_user_and_reports_usage_errors(tmp_path: Path) -> None:
    archive = _docker_archive(tmp_path / "root.tar", "0", ["app/server"])
    assert contents_main([str(archive)]) == 1
    assert contents_main([]) == 2
    assert contents_main([str(tmp_path / "missing.tar")]) == 2


# Claims: 12.3, 12.4, 12.11
def test_digest_only_reference_is_accepted() -> None:
    assert is_pinned_digest(DIGEST)
    assert is_pinned_image_ref(f"registry.example.com/edu-it/api@{DIGEST}")
    assert is_pinned_image_ref(compose_image_ref("edu-it/api", DIGEST))


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize(
    "ref",
    [
        "edu-it/api:latest",
        "edu-it/api:1.2.3",
        "edu-it/api",
        "registry.example.com:5000/edu-it/api:v1",
        f"edu-it/api:1.0@{DIGEST}x",
        "edu-it/api@sha256:" + "a" * 63,
        "edu-it/api@sha256:" + "A" * 64,
        "edu-it/api@sha1:" + "a" * 40,
        f"@{DIGEST}",
        "",
        None,
        42,
    ],
)
def test_tag_only_or_malformed_reference_is_rejected(ref: object) -> None:
    assert not is_pinned_image_ref(ref)


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("digest", ["latest", "sha256:abc", "sha256:" + "G" * 64, "", None])
def test_invalid_digest_is_rejected(digest: object) -> None:
    assert not is_pinned_digest(digest)


# Claims: 12.3, 12.4, 12.11
def test_values_with_pinned_images_are_accepted() -> None:
    values = {
        "images": {
            "api": {"repository": "registry.example.com/edu-it/api", "digest": DIGEST},
            "web": {"repository": "localhost:5000/edu-it/web", "digest": DIGEST},
        }
    }
    assert check_image_refs(values) == []


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize(
    "entry",
    [
        {"repository": "edu-it/api:latest", "digest": DIGEST},
        {"repository": f"edu-it/api@{DIGEST}", "digest": DIGEST},
        {"repository": "edu-it/api", "tag": "1.0"},
        {"repository": "edu-it/api", "digest": "latest"},
        {"repository": "edu-it/api"},
        {"digest": DIGEST},
        {"repository": "", "digest": DIGEST},
        "edu-it/api:1.0",
    ],
)
def test_tag_only_or_unpinned_image_entry_is_rejected(entry: object) -> None:
    assert check_image_refs({"images": {"api": entry}}) != []


# Claims: 12.3, 12.4, 12.11
@pytest.mark.parametrize("values", [{}, {"images": {}}, {"images": None}])
def test_missing_images_is_rejected(values: dict) -> None:
    assert check_image_refs(values) != []


# Claims: 12.3, 12.4, 12.11
def test_refs_cli_exit_codes(tmp_path: Path) -> None:
    good = tmp_path / "good.yaml"
    good.write_text(f"images:\n  api:\n    repository: edu-it/api\n    digest: {DIGEST}\n")
    bad = tmp_path / "bad.yaml"
    bad.write_text("images:\n  api:\n    repository: edu-it/api\n    tag: latest\n")
    assert refs_main([str(good)]) == 0
    assert refs_main([str(bad)]) == 1
    assert refs_main([]) == 2
    assert refs_main([str(tmp_path / "missing.yaml")]) == 2
