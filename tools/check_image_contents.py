#!/usr/bin/env python3
"""Image content check (kubernetes-deployment, Req 12.3, 12.4).

An image is accepted only if:

* its layers contain no shell or package-manager binary (``sh``, ``bash``, ``apk``, ``apt``,
  ``apt-get``, ``dnf``, ``yum``, ``pip``) in an executable directory (``bin`` or ``sbin``),
  after applying whiteouts layer by layer (Req 12.3),
* its image configuration declares a numeric user ID greater than 0 (Req 12.4).

Input is an image archive (``docker save`` or OCI layout tarball), for example produced with
``docker save IMAGE -o image.tar`` or ``skopeo copy ... oci-archive:image.tar``.

Usage::

    python tools/check_image_contents.py ARCHIVE [ARCHIVE ...]

Exit codes: 0 accepted, 1 violations, 2 usage or read error.
"""

from __future__ import annotations

import io
import json
import posixpath
import sys
import tarfile
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

__all__ = [
    "FORBIDDEN_BINARIES",
    "check_image_contents",
    "check_user",
    "find_forbidden_binaries",
    "main",
]

FORBIDDEN_BINARIES: frozenset[str] = frozenset(
    {"sh", "bash", "apk", "apt", "apt-get", "dnf", "yum", "pip"}
)
_BIN_DIRS = frozenset({"bin", "sbin"})
_WHITEOUT = ".wh."
_OPAQUE = ".wh..wh..opq"


def _normalize(name: str) -> str:
    return posixpath.normpath("/" + name.removeprefix("./").lstrip("/"))


def find_forbidden_binaries(layers: Iterable[Iterable[tuple[str, bool]]]) -> list[str]:
    """Return the forbidden binaries present in the final filesystem of ``layers``.

    Each layer is an iterable of ``(path, is_directory)`` entries, in image order. Whiteout
    entries (``.wh.<name>``, ``.wh..wh..opq``) remove files from earlier layers.
    """
    files: set[str] = set()
    for layer in layers:
        for raw, is_dir in layer:
            path = _normalize(raw)
            parent, base = posixpath.split(path)
            if base == _OPAQUE:
                prefix = parent.rstrip("/") + "/"
                files = {f for f in files if not f.startswith(prefix)}
            elif base.startswith(_WHITEOUT):
                target = posixpath.join(parent, base[len(_WHITEOUT) :])
                prefix = target + "/"
                files = {f for f in files if f != target and not f.startswith(prefix)}
            elif not is_dir:
                files.add(path)
    found = []
    for path in sorted(files):
        parent, base = posixpath.split(path)
        if base in FORBIDDEN_BINARIES and posixpath.basename(parent) in _BIN_DIRS:
            found.append(path)
    return found


def check_user(user: object) -> str | None:
    """Return a violation message unless ``user`` is a numeric UID > 0 (optionally ``uid:gid``)."""
    if not isinstance(user, str) or not user.strip():
        return "image config declares no User; a numeric non-root user is required"
    uid, _, gid = user.partition(":")
    if not uid.isascii() or not uid.isdigit():
        return f"image config User {user!r} is not numeric"
    if int(uid) <= 0:
        return f"image config User {user!r} runs as root (UID must be greater than 0)"
    if gid and (not gid.isascii() or not gid.isdigit()):
        return f"image config User {user!r} has a non-numeric group"
    return None


def check_image_contents(
    config: Mapping[str, Any], layers: Sequence[Iterable[tuple[str, bool]]]
) -> list[str]:
    """Return the violations for an image config and its layer listing."""
    errors: list[str] = []
    cfg = config.get("config")
    user = cfg.get("User") if isinstance(cfg, Mapping) else None
    problem = check_user(user)
    if problem:
        errors.append(problem)
    for path in find_forbidden_binaries(layers):
        errors.append(f"forbidden binary in image: {path}")
    return errors


def _read_member(tar: tarfile.TarFile, name: str) -> bytes:
    member = tar.getmember(name.lstrip("/"))
    handle = tar.extractfile(member)
    if handle is None:
        raise ValueError(f"{name} is not a regular file in the archive")
    return handle.read()


def _blob(digest: str) -> str:
    algo, _, value = digest.partition(":")
    return f"blobs/{algo}/{value}"


def _layer_entries(data: bytes) -> list[tuple[str, bool]]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as layer:
        return [(m.name, m.isdir()) for m in layer.getmembers()]


def load_archive(path: str) -> tuple[dict[str, Any], list[list[tuple[str, bool]]]]:
    """Read the image config and the ordered layer listings from an image archive."""
    with tarfile.open(path, "r:*") as tar:
        names = set(tar.getnames())
        if "manifest.json" in names:  # docker save format
            manifest = json.loads(_read_member(tar, "manifest.json"))[0]
            config = json.loads(_read_member(tar, manifest["Config"]))
            layer_names = manifest["Layers"]
        elif "index.json" in names:  # OCI layout
            index = json.loads(_read_member(tar, "index.json"))
            manifest = json.loads(_read_member(tar, _blob(index["manifests"][0]["digest"])))
            config = json.loads(_read_member(tar, _blob(manifest["config"]["digest"])))
            layer_names = [_blob(layer["digest"]) for layer in manifest["layers"]]
        else:
            raise ValueError("not a docker save or OCI layout archive")
        layers = [_layer_entries(_read_member(tar, name)) for name in layer_names]
    return config, layers


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    failed = False
    for name in args:
        try:
            config, layers = load_archive(name)
        except (OSError, KeyError, IndexError, ValueError, tarfile.TarError) as exc:
            print(f"error: {name}: {exc}", file=sys.stderr)
            return 2
        for message in check_image_contents(config, layers):
            failed = True
            print(f"{name}: {message}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
