#!/usr/bin/env python3
"""Image signature verification for the CI deploy and upgrade steps (Req 12.14, 15.8).

Runs ``cosign verify`` on every ``repository@digest`` taken from ``images`` in the Chart
values and fails on a missing or invalid signature, so nothing unsigned is installed.

Pull-request and other non-release runs cannot sign (Req 15.8). With ``--pull-request`` the
tool only checks that every image is a pinned digest in the local registry, runs no
``cosign`` command and prints a note that signature verification was skipped.

Usage::

    python tools/verify_image_signatures.py \\
        --certificate-identity-regexp REGEXP --certificate-oidc-issuer URL FILE [FILE ...]
    python tools/verify_image_signatures.py --pull-request FILE [FILE ...]

Exit codes: 0 all verified (or skipped in pull-request mode), 1 verification failure,
2 usage or read error.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

try:  # run as a script (tools/ on sys.path) or imported as a module
    from image_refs import compose_image_ref, is_pinned_digest
except ImportError:  # pragma: no cover
    from tools.image_refs import compose_image_ref, is_pinned_digest

__all__ = ["SKIP_NOTE", "collect_image_refs", "main", "verify_local", "verify_signatures"]

#: Recorded in the run output for pull-request runs (Req 15.8).
SKIP_NOTE = "NOTE: signature verification was skipped (pull-request run: images are not signed)"

#: Default OIDC issuer of GitHub Actions keyless signing.
DEFAULT_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

LOCAL_REGISTRY_PREFIXES = ("localhost:", "127.0.0.1:")

COSIGN_TIMEOUT_SECONDS = 120

#: ``runner(argv) -> (returncode, output)``; replaced by a fake in tests.
Runner = Callable[[Sequence[str]], tuple[int, str]]


def collect_image_refs(values: Mapping[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Return ``({name: repository@digest}, errors)`` for ``values['images']``."""
    images = values.get("images")
    if not isinstance(images, Mapping) or not images:
        return {}, ["images is missing or empty"]
    refs: dict[str, str] = {}
    errors: list[str] = []
    for name, entry in images.items():
        repository = entry.get("repository") if isinstance(entry, Mapping) else None
        digest = entry.get("digest") if isinstance(entry, Mapping) else None
        if not isinstance(repository, str) or not repository.strip():
            errors.append(f"images.{name}.repository is missing or empty")
        elif not is_pinned_digest(digest):
            errors.append(f"images.{name}.digest {digest!r} is not a pinned sha256 digest")
        else:
            refs[str(name)] = compose_image_ref(repository, digest)
    return refs, errors


def _run_cosign(argv: Sequence[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            timeout=COSIGN_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        return 127, "cosign executable not found"
    except subprocess.TimeoutExpired:
        return 124, f"cosign did not finish within {COSIGN_TIMEOUT_SECONDS} s"
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def verify_signatures(
    refs: Mapping[str, str],
    *,
    identity_regexp: str,
    oidc_issuer: str,
    runner: Runner = _run_cosign,
) -> list[str]:
    """Run ``cosign verify`` for every ref; return one error per missing or invalid signature."""
    errors: list[str] = []
    for name, ref in refs.items():
        argv = [
            "cosign",
            "verify",
            "--certificate-identity-regexp",
            identity_regexp,
            "--certificate-oidc-issuer",
            oidc_issuer,
            ref,
        ]
        code, output = runner(argv)
        if code != 0:
            detail = output.splitlines()[-1] if output else f"exit code {code}"
            errors.append(f"images.{name}: signature missing or invalid for {ref}: {detail}")
    return errors


def verify_local(refs: Mapping[str, str]) -> list[str]:
    """Pull-request mode: every ref must be a digest pinned in the local registry."""
    return [
        f"images.{name}: {ref} is not in the local registry"
        for name, ref in refs.items()
        if not ref.startswith(LOCAL_REGISTRY_PREFIXES)
    ]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Verify image signatures with cosign.")
    parser.add_argument("files", nargs="+", metavar="FILE", help="Chart values files")
    parser.add_argument(
        "--pull-request",
        action="store_true",
        help="verify local-registry digests only and record that verification was skipped",
    )
    parser.add_argument("--certificate-identity-regexp", help="signer identity regexp")
    parser.add_argument("--certificate-oidc-issuer", default=DEFAULT_OIDC_ISSUER)
    return parser


def main(argv: list[str] | None = None, runner: Runner = _run_cosign) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    except SystemExit:
        return 2
    if not args.pull_request and not args.certificate_identity_regexp:
        print("error: --certificate-identity-regexp is required", file=sys.stderr)
        return 2
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment problem
        print(f"error: {exc}", file=sys.stderr)
        return 2

    failed = False
    for name in args.files:
        try:
            values = yaml.safe_load(Path(name).read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            print(f"error: {name}: {exc}", file=sys.stderr)
            return 2
        refs, errors = collect_image_refs(values)
        if not errors:
            if args.pull_request:
                errors = verify_local(refs)
            else:
                errors = verify_signatures(
                    refs,
                    identity_regexp=args.certificate_identity_regexp,
                    oidc_issuer=args.certificate_oidc_issuer,
                    runner=runner,
                )
        for message in errors:
            failed = True
            print(f"{name}: {message}")
        if not errors and not args.pull_request:
            print(f"{name}: verified {len(refs)} image(s)")
    if args.pull_request:
        print(SKIP_NOTE)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
