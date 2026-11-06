#!/usr/bin/env python3
"""LLM region check (kubernetes-deployment, Req 10.6).

EU data residency (`docs/adr/0007-llm-provider-and-data-residency.md`, Addendum 1) is
satisfied only by a region in the EU list recorded in that ADR (the regions the
`eu.anthropic.claude-sonnet-4-6` geographic inference profile can route to). A Values_File
that sets the dev Target is exempt, because the dev flag allows any region.

Usage::

    python tools/check_llm_region.py REGION [--dev]

Exit codes: 0 accepted, 1 rejected, 2 usage error.
"""

from __future__ import annotations

import argparse
import sys

__all__ = ["EU_LLM_REGIONS", "check_llm_region", "main"]

#: EU regions allowed by ADR 0007, Addendum 1 (destination regions of the `eu.`
#: geographic inference profile for `eu.anthropic.claude-sonnet-4-6`).
EU_LLM_REGIONS: frozenset[str] = frozenset(
    {
        "eu-central-1",
        "eu-north-1",
        "eu-south-1",
        "eu-south-2",
        "eu-west-1",
        "eu-west-3",
    }
)


def check_llm_region(region: str, dev: bool) -> str | None:
    """Return a rejection message naming ``region``, or ``None`` if it is accepted.

    Pure: accepts ``region`` if and only if it is in `EU_LLM_REGIONS` or ``dev`` is set.
    """
    if dev or region in EU_LLM_REGIONS:
        return None
    return (
        f"llm.region {region!r} is not an EU region allowed by "
        "docs/adr/0007-llm-provider-and-data-residency.md"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("region", help="configured LLM provider region")
    parser.add_argument(
        "--dev", action="store_true", help="dev Target flag (any region is allowed)"
    )
    args = parser.parse_args(argv)

    message = check_llm_region(args.region, args.dev)
    if message:
        print(f"llm-region: FAIL - {message}", file=sys.stderr)
        return 1
    print(f"llm-region: OK - {args.region!r} accepted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
