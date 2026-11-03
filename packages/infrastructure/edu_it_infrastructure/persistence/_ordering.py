"""Ordering helpers shared by the SQL repositories."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.sql.elements import ColumnElement

__all__ = ["code_point_order"]


def code_point_order(column: ColumnElement[str], engine: Engine) -> ColumnElement[str]:
    """``column`` ordered/compared in code-point order, as Python compares ``str``.

    The ports and in-memory adapters order text ids with Python's ``<`` / ``sorted``
    (code-point order). PostgreSQL's default collation is locale-aware (en_US: ``B``
    sorts after ``a``, ``-`` and ``_`` are ignored), so on PostgreSQL the bytewise
    ``"C"`` collation is used, which for UTF-8 is code-point order. SQLite's default
    ``BINARY`` collation already is, so other dialects get the column unchanged.
    Use it for both ``ORDER BY`` and keyset comparisons so they agree.
    """
    return column.collate("C") if engine.dialect.name == "postgresql" else column
