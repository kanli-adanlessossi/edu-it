"""Smoke tests for the reset, pending OAuth, and OAuth link adapters (task 15.2).

Full contract coverage is task 15.3. Validates: Requirements 7.6, 7.8, 7.11, 7.12
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edu_it_domain.ports import UserRecord
from edu_it_domain.ports.oauth_link import OAuthLink
from edu_it_domain.ports.pending_oauth_link import PendingOAuthLink
from edu_it_domain.ports.reset_link import ResetLink
from edu_it_infrastructure.persistence import Postgres_UserRepository, metadata
from edu_it_infrastructure.persistence.account_repositories import (
    Postgres_OAuthLinkRepository,
    Postgres_PendingOAuthLinkRepository,
    Postgres_ResetLinkRepository,
    token_digest,
)
from sqlalchemy import create_engine, select
from sqlalchemy.pool import StaticPool

_T0 = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def engine():  # type: ignore[no-untyped-def]
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    metadata.create_all(eng)
    Postgres_UserRepository(eng).add(
        UserRecord(
            stable_id="u-1", email="a@example.test", locale="en", status="active", display_name="A"
        )
    )
    yield eng
    eng.dispose()


def test_reset_link_digest_only_and_single_consume(engine) -> None:  # type: ignore[no-untyped-def]
    repo = Postgres_ResetLinkRepository(engine)
    link = ResetLink("tok-r", "u-1", _T0, _T0 + timedelta(hours=1))
    repo.add(link)
    with pytest.raises(ValueError):
        repo.add(link)
    assert repo.get("tok-r") == link
    assert repo.get("nope") is None
    with engine.connect() as c:
        stored = c.execute(select(metadata.tables["reset_link"].c.token_digest)).scalar()
    assert stored == token_digest("tok-r") != "tok-r"
    assert repo.mark_consumed("tok-r", consumed_at=_T0) is True
    assert repo.mark_consumed("tok-r", consumed_at=_T0) is False
    assert repo.mark_consumed("nope", consumed_at=_T0) is False
    got = repo.get("tok-r")
    assert got is not None and got.consumed_at == _T0


def test_pending_oauth_link_roundtrip_and_single_consume(engine) -> None:  # type: ignore[no-untyped-def]
    repo = Postgres_PendingOAuthLinkRepository(engine)
    pending = PendingOAuthLink(
        "tok-p", "github", "123", "u-1", "a@example.test", _T0, _T0 + timedelta(minutes=10)
    )
    repo.add(pending)
    with pytest.raises(ValueError):
        repo.add(pending)
    assert repo.get("tok-p") == pending
    assert repo.mark_consumed("tok-p", consumed_at=_T0) is True
    assert repo.mark_consumed("tok-p", consumed_at=_T0) is False


def test_oauth_link_add_get_and_duplicate(engine) -> None:  # type: ignore[no-untyped-def]
    repo = Postgres_OAuthLinkRepository(engine)
    link = OAuthLink("github", "123", "u-1")
    assert repo.get("github", "123") is None
    repo.add(link)
    assert repo.get("github", "123") == link
    with pytest.raises(ValueError):
        repo.add(link)
