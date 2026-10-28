"""Unit tests for the datastore-unavailability classifier (task 20, Req 27.2).

:func:`edu_it_infrastructure.datastore_errors.is_datastore_unavailable` is the
single place that knows which driver exceptions mean "PostgreSQL or Redis is
unavailable right now". The API boundary relies on it to turn a request-time
outage into a translated ``503`` Problem_Details rather than a generic ``500``.

These tests pin that contract: the connection/operational families of both
datastores are recognized (directly and when wrapped as an exception
``__cause__`` / ``__context__``), while ordinary programming / integrity /
logical faults and unrelated errors are **not**, so genuine bugs and 4xx
conditions still flow to the generic handlers.
"""

from __future__ import annotations

import redis.exceptions as redis_exceptions
from edu_it_infrastructure.datastore_errors import (
    DATASTORE_UNAVAILABLE_ERRORS,
    is_datastore_unavailable,
)
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    InterfaceError,
    OperationalError,
    ProgrammingError,
)


def _sqlalchemy_error(cls: type[Exception]) -> Exception:
    """Build a SQLAlchemy DBAPI-style error with the shape the drivers raise."""
    return cls("SELECT 1", {}, Exception("connection refused"))


def test_postgres_connection_failures_are_unavailable() -> None:
    """OperationalError / InterfaceError signal a PostgreSQL outage."""
    assert is_datastore_unavailable(_sqlalchemy_error(OperationalError))
    assert is_datastore_unavailable(_sqlalchemy_error(InterfaceError))


def test_invalidated_connection_dbapi_error_is_unavailable() -> None:
    """A DBAPIError flagged ``connection_invalidated`` is a lost connection."""
    err = DBAPIError("SELECT 1", {}, Exception("server closed the connection"))
    err.connection_invalidated = True
    assert is_datastore_unavailable(err)


def test_plain_dbapi_error_is_not_unavailable() -> None:
    """A DBAPIError that is not an invalidated connection is not an outage."""
    assert not is_datastore_unavailable(_sqlalchemy_error(DBAPIError))


def test_redis_connection_failures_are_unavailable() -> None:
    """Redis ConnectionError / TimeoutError signal a Redis outage."""
    assert is_datastore_unavailable(redis_exceptions.ConnectionError("Error 111 connecting"))
    assert is_datastore_unavailable(redis_exceptions.TimeoutError("timed out"))


def test_logical_driver_faults_are_not_unavailable() -> None:
    """A bad query or constraint violation is a bug / 4xx, not an outage."""
    assert not is_datastore_unavailable(_sqlalchemy_error(ProgrammingError))
    assert not is_datastore_unavailable(_sqlalchemy_error(IntegrityError))
    assert not is_datastore_unavailable(redis_exceptions.ResponseError("WRONGTYPE"))


def test_unrelated_errors_are_not_unavailable() -> None:
    """Plain application errors never classify as a datastore outage."""
    assert not is_datastore_unavailable(ValueError("bad value"))
    assert not is_datastore_unavailable(KeyError("missing"))


def test_wrapped_cause_is_detected() -> None:
    """An outage wrapped as ``__cause__`` (raise ... from) is still recognized."""
    outage = redis_exceptions.ConnectionError("Error 111 connecting")
    try:
        try:
            raise outage
        except redis_exceptions.ConnectionError as err:
            raise RuntimeError("session store unavailable") from err
    except RuntimeError as wrapper:
        assert is_datastore_unavailable(wrapper)


def test_wrapped_context_is_detected() -> None:
    """An outage surfacing as an implicit ``__context__`` is still recognized."""
    try:
        try:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))
        except OperationalError:
            raise RuntimeError("repository call failed") from None
    except RuntimeError as wrapper:
        # ``from None`` suppresses __cause__ but __context__ still chains; the
        # classifier must look past the wrapper to the real outage.
        wrapper.__context__ = OperationalError("SELECT 1", {}, Exception("connection refused"))
        assert is_datastore_unavailable(wrapper)


def test_matched_set_is_exposed_and_nonempty() -> None:
    """The matched exception set is exported for callers to register against."""
    assert DATASTORE_UNAVAILABLE_ERRORS
    assert OperationalError in DATASTORE_UNAVAILABLE_ERRORS
    assert redis_exceptions.ConnectionError in DATASTORE_UNAVAILABLE_ERRORS
