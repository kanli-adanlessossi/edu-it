"""The ``edu-it content`` CLI skeleton and the ``validate`` subcommand (Req 13).

This is the thin command-line shell around
:mod:`edu_it_content.validation`: it parses argv into a ``content`` command
group with a ``validate`` subcommand, loads authored content from a content
root, runs the ordered validation checks, and maps the outcome to a process exit
status and operator-facing output.

It follows the existing EDU-IT CLI pattern (``edu-it-migrate``,
``edu-it-grant-role``): an ``argparse`` parser with a ``main(argv) -> int``
entry point registered under ``[project.scripts]``. The script is named
``edu-it-content`` so the full command is ``edu-it-content validate`` /
``edu-it-content sync`` (the ``content`` group is modeled as subcommands so the
surface reads as the design's ``edu-it content validate`` / ``edu-it content
sync``).

The ``sync`` subcommand (Req 14, 26.1) performs the idempotent upsert keyed by
``stable_id`` described in :mod:`edu_it_content.sync`: it validates the content
root, then inserts new items, updates changed ones in place (bumping
``content_version``), leaves unchanged items untouched (a second run over
unchanged content is a no-op), archives items the authored set removed, and
never touches completion records. It runs against an injected
:class:`~edu_it_content.content_store.ContentStore`; the CLI uses the in-memory
store so ``sync`` is exercisable end-to-end against a fixture until the
PostgreSQL-backed store lands (task 20).

Usage::

    edu-it-content validate [--content-root DIR]
    edu-it-content sync [--content-root DIR]

Exit status:

- ``0`` — ``validate``: every item passed all six ordered checks (Req 13.1);
  ``sync``: the content root was valid and the upsert completed (Req 14).
- ``1`` — ``validate``: at least one check failed, each printed on stderr naming
  the failing rule and the offending item's ``stable_id`` (Req 13.1-13.6);
  ``sync``: the content was invalid, so nothing was synced (Req 14).
- ``2`` — the content root could not be loaded (missing directory or malformed
  ``items.json``), so the command could not begin.

When ``--content-root`` is omitted the command reads ``EDU_IT_CONTENT_ROOT`` from
the environment, falling back to a ``content`` directory under the current
working directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from edu_it_content.content_store import In_Memory_ContentStore
from edu_it_content.sync import Approval, sync_content
from edu_it_content.validation import (
    ContentLoadError,
    ValidationFailure,
    load_content,
    validate_content,
)

__all__ = ["main"]

#: Exit status when validation finds at least one failing check (Req 13.1).
_VALIDATION_FAILED_EXIT = 1

#: Exit status when the content root cannot be loaded at all (missing dir or
#: unparseable ``items.json``) — validation could not even start.
_LOAD_ERROR_EXIT = 2

#: Environment variable naming the content root when ``--content-root`` is
#: omitted; falls back to ``./content`` so a configured checkout needs no flag.
_CONTENT_ROOT_ENV = "EDU_IT_CONTENT_ROOT"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for ``edu-it content`` (Req 13).

    Parses ``argv`` into the ``content`` command group, dispatches the chosen
    subcommand, and returns its process exit status. Returns argparse's own
    exit (``2``) for an unknown/missing subcommand.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return _LOAD_ERROR_EXIT
    result: int = handler(args)
    return result


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``content`` command group with its ``validate`` and ``sync`` subcommands.

    ``validate`` runs the ordered content checks; ``sync`` performs the
    idempotent upsert (Req 14). Each action binds a ``handler`` so :func:`main`
    can dispatch without re-branching on the action name. Both take the same
    ``--content-root`` resolution.
    """
    parser = argparse.ArgumentParser(
        prog="edu-it content",
        description="EDU-IT content pipeline commands (Req 13, 14).",
    )
    subcommands = parser.add_subparsers(dest="command", metavar="command")

    validate = subcommands.add_parser(
        "validate",
        help="validate authored content against its schemas and the ordered checks",
        description=(
            "Run the ordered content checks — schema, bilingual completeness, "
            "reference resolution, prerequisite acyclicity, MDX allowlist, and "
            "AI-reviewer-present — exiting zero only when every item is valid."
        ),
    )
    _add_content_root_argument(validate)
    validate.set_defaults(handler=_run_validate)

    sync = subcommands.add_parser(
        "sync",
        help="idempotently upsert validated content by stable_id",
        description=(
            "Validate the content root, then upsert each item by its immutable "
            "stable_id — inserting new items, updating changed ones in place "
            "(bumping content_version), leaving unchanged items untouched, and "
            "archiving (never deleting) items the authored set removed. A second "
            "run over unchanged content makes no changes."
        ),
    )
    _add_content_root_argument(sync)
    sync.add_argument(
        "--approved-by",
        default=None,
        metavar="USER",
        help=(
            "stable id of the user who approved the seed content (Req 16.2). "
            "Records them as reviewed_by/reviewed_at and marks the synced content "
            "as the Golden_Set (Req 16.3). Omit when the content is not approved: "
            "no reviewer is recorded and no golden set is marked (Req 16.4)."
        ),
    )
    sync.add_argument(
        "--approved-at",
        default=None,
        metavar="ISO8601",
        help="approval timestamp (timezone-aware ISO 8601); defaults to now (UTC)",
    )
    sync.set_defaults(handler=_run_sync)

    return parser


def _add_content_root_argument(subparser: argparse.ArgumentParser) -> None:
    """Add the shared ``--content-root`` option to a subcommand parser."""
    subparser.add_argument(
        "--content-root",
        default=None,
        help=(
            "directory holding authored content (items.json and/or "
            "<track>/<type>.json files, plus an optional "
            f"mdx/ dir); defaults to the {_CONTENT_ROOT_ENV} environment "
            "variable or ./content"
        ),
    )


def _run_validate(args: argparse.Namespace) -> int:
    """Load the content root and run the ordered validation checks (Req 13).

    Resolves the content root from the flag, the environment, or ``./content``;
    loads the authored content; and runs every check. Prints each failure on
    stderr (rule + ``stable_id`` + detail) and exits non-zero on any failure, or
    prints an all-valid line and exits zero when clean (Req 13.1). A load error
    (missing root or malformed ``items.json``) exits ``2``.
    """
    content_root = _resolve_content_root(args.content_root)

    try:
        content = load_content(content_root)
    except ContentLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _LOAD_ERROR_EXIT

    failures = validate_content(content)
    if failures:
        _report_failures(failures)
        return _VALIDATION_FAILED_EXIT

    item_count = sum(len(items) for items in content.raw_items.values())
    print(f"content valid: {item_count} item(s) passed all checks in {content_root}")
    return 0


def _run_sync(args: argparse.Namespace) -> int:
    """Validate the content root and run the idempotent upsert (Req 14, 26.1).

    Resolves the content root like ``validate``; loads the authored content; and
    calls :func:`~edu_it_content.sync.sync_content` against an in-memory store.
    Sync validates first and refuses to write invalid content, so an invalid
    content root is reported on stderr and exits ``1`` without syncing anything
    (Req 14). A load error (missing root or malformed ``items.json``) exits
    ``2``. On success it prints the per-action counts and exits ``0``; an
    unchanged re-run reports all items as unchanged (the no-op path, Req 14.2).
    """
    content_root = _resolve_content_root(args.content_root)

    try:
        content = load_content(content_root)
    except ContentLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _LOAD_ERROR_EXIT

    # The CLI wires sync to the in-memory store so the command is runnable
    # end-to-end against a fixture now. Because the store is per-process, the
    # idempotent no-op / update-in-place / archive behavior is observed within a
    # single run (and is covered by the sync tests against a shared store); the
    # persistent PostgreSQL-backed store lands in task 20.
    try:
        approval = _parse_approval(args.approved_by, args.approved_at)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _LOAD_ERROR_EXIT

    store = In_Memory_ContentStore()
    try:
        report = sync_content(store, content, approval=approval)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return _VALIDATION_FAILED_EXIT

    print(f"content synced: {report.summary()} in {content_root}")
    if approval is not None:
        golden_count = len(store.golden_ids())
        print(f"approved by {approval.reviewer}: {golden_count} item(s) in the Golden_Set")
    return 0


def _parse_approval(approved_by: str | None, approved_at: str | None) -> Approval | None:
    """Build an :class:`Approval` from the CLI options, or ``None`` when not approved.

    The approver is never guessed: no ``--approved-by`` means no approval, so no
    reviewer is recorded and no golden set is marked (Req 16.4).
    """
    if approved_by is None:
        if approved_at is not None:
            raise ValueError("--approved-at requires --approved-by")
        return None
    when = datetime.now(UTC)
    if approved_at is not None:
        try:
            when = datetime.fromisoformat(approved_at)
        except ValueError as exc:
            raise ValueError(f"--approved-at is not a valid ISO 8601 timestamp: {exc}") from exc
        if when.tzinfo is None:
            raise ValueError("--approved-at must include a timezone offset")
    return Approval(reviewer=approved_by, reviewed_at=when)


def _resolve_content_root(flag_value: str | None) -> Path:
    """Resolve the content root from the flag, the environment, or ``./content``."""
    if flag_value is not None:
        return Path(flag_value)
    env_value = os.environ.get(_CONTENT_ROOT_ENV)
    if env_value:
        return Path(env_value)
    return Path.cwd() / "content"


def _report_failures(failures: list[ValidationFailure]) -> None:
    """Print each validation failure on stderr, one per line (Req 13.1-13.6).

    Every line names the failing rule and the offending item's ``stable_id`` via
    :meth:`ValidationFailure.message`, followed by a count so CI output makes the
    number of problems obvious.
    """
    for failure in failures:
        print(f"error: {failure.message()}", file=sys.stderr)
    print(f"error: content invalid: {len(failures)} check failure(s)", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - module entry point
    raise SystemExit(main())
