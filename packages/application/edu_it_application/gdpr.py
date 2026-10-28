"""The GDPR export and account-deletion use cases (task 19.1, Req 14.1-14.4).

These two framework-free use cases realize the design's GDPR behavior (design,
"GDPR export, deletion, and placeholder pages"), built on the ports from prior
tasks plus the :class:`~edu_it_domain.ports.personal_data.PersonalDataGateway`:

- :class:`ExportUserDataUseCase` produces a downloadable export for a signed-in
  User. It reads the User's rows across **every** personal-data table through the
  gateway (tables with no rows represented as empty, so the export covers exactly
  the personal-data tables in ``docs/DATA-MODEL.md`` Section 13 regardless of the
  data present, Req 14.1), serializes them to a JSON document, and writes that
  document to the ``ObjectStorage`` port under a per-User key. The stored key is
  returned so the boundary can hand the learner a download; the export artifact
  itself lives in object storage (design: "produced via the ``ObjectStorage``
  port").

- :class:`DeleteAccountUseCase` deletes a signed-in User's account after the
  distinct confirmation step required by Req 14.2. It removes the User's personal
  data across **every** personal-data table in a single atomic gateway operation
  — if removal from any table fails the gateway leaves every table unchanged and
  raises, so partial deletion can never persist (Req 14.2, 14.4) — and revokes
  **all** of the User's Sessions through both the durable
  :class:`~edu_it_domain.ports.repository.SessionRepository` and the ephemeral
  :class:`~edu_it_domain.ports.session_store.SessionStore` (Req 14.3). Sessions
  are revoked **before** the data delete so a success leaves no Session able to
  authenticate; because revocation is idempotent and the data delete is the step
  that can fail atomically, a failed delete raises
  :class:`AccountDeletionError` with all personal-data tables unchanged, and the
  caller returns an error indication that the deletion did not complete (Req
  14.4). A deletion attempt that is **not** confirmed removes nothing and revokes
  nothing (Req 14.2).

Like the other account use cases this is deliberately framework-free: it imports
only the domain ports and records, never a web or API framework (Req 1.8). The
non-deterministic inputs — the current time used for Session revocation and the
export's storage key — enter through injected collaborators so the flow is
deterministic under test.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.object_storage import ObjectStorage
from edu_it_domain.ports.personal_data import PersonalDataGateway
from edu_it_domain.ports.repository import SessionRepository
from edu_it_domain.ports.session_store import SessionStore

__all__ = [
    "AccountDeletionError",
    "DeleteAccountRequest",
    "DeleteAccountResult",
    "DeleteAccountUseCase",
    "ExportUserDataRequest",
    "ExportUserDataResult",
    "ExportUserDataUseCase",
]

#: Application-layer logger for the GDPR flow. Records emitted through the
#: standard :mod:`logging` module are formatted as structured JSON carrying the
#: Request_ID by the ``apps/api`` logging foundation (task 8), with no internal
#: diagnostics reaching any response (Req 18.4, 18.6).
_LOGGER: Final[logging.Logger] = logging.getLogger("edu_it_application.gdpr")

#: Structured-log event recorded when the atomic data delete fails (Req 14.4).
_DELETION_FAILED_EVENT: Final[str] = "account_deletion_failed"

#: The MIME type of the stored export document.
_EXPORT_CONTENT_TYPE: Final[str] = "application/json"


# --- Data export (Req 14.1) --------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExportUserDataRequest:
    """A request to export the signed-in User's personal data (Req 14.1)."""

    user_stable_id: str
    """The ``stable_id`` of the signed-in User whose data is exported."""


@dataclass(frozen=True, slots=True)
class ExportUserDataResult:
    """The outcome of a data export (Req 14.1).

    Carries the ``ObjectStorage`` key under which the export document was written
    and its byte size, so the boundary can produce the download without the
    export artifact ever passing through the domain as a value.
    """

    storage_key: str
    """The ``ObjectStorage`` key the export document was stored under."""

    size_bytes: int
    """The size of the stored export document in bytes."""


class ExportUserDataUseCase:
    """Produce a downloadable personal-data export via ``ObjectStorage`` (Req 14.1)."""

    def __init__(
        self,
        *,
        personal_data: PersonalDataGateway,
        object_storage: ObjectStorage,
        key_factory: Callable[[str], str] | None = None,
    ) -> None:
        """Wire the use case to its collaborators.

        ``key_factory`` maps a User's ``stable_id`` to the ``ObjectStorage`` key
        the export is written under; it defaults to a stable per-User key so a
        re-export overwrites the previous one. It is injectable so a deployment
        can namespace or time-stamp export keys without changing the use case.
        """
        self._personal_data = personal_data
        self._object_storage = object_storage
        self._key_factory = key_factory or _default_export_key

    def execute(self, request: ExportUserDataRequest) -> ExportUserDataResult:
        """Export the User's data to ``ObjectStorage`` and return the stored key.

        Reads the User's rows across every personal-data table through the
        gateway — tables with no rows present as empty lists, so the export
        covers exactly the personal-data tables in D3 Section 13 regardless of
        the data present (Req 14.1) — serializes them to a JSON document, and
        writes the document to object storage under the User's export key. The
        document is a single JSON object keyed by table name so a table with no
        rows is visibly present as an empty array.
        """
        export = self._personal_data.export_user_data(request.user_stable_id)
        # ``sort_keys`` makes the document deterministic for a given dataset, and
        # ``default=str`` renders datetimes and any other non-JSON-native scalars
        # as strings so arbitrary column values serialize without special-casing.
        document = json.dumps(
            {table: list(rows) for table, rows in export.items()},
            sort_keys=True,
            default=str,
        ).encode("utf-8")
        key = self._key_factory(request.user_stable_id)
        self._object_storage.put(
            key,
            document,
            content_type=_EXPORT_CONTENT_TYPE,
            metadata={"user_stable_id": request.user_stable_id},
        )
        return ExportUserDataResult(storage_key=key, size_bytes=len(document))


# --- Account deletion (Req 14.2, 14.3, 14.4) ---------------------------------


class AccountDeletionError(Exception):
    """Raised when the atomic personal-data delete fails (Req 14.4).

    The gateway runs the delete in a single transaction, so a failure leaves
    every personal-data table unchanged; this error signals that the deletion did
    not complete and the boundary must return an error indication (Req 14.4). The
    triggering error is carried only as ``__cause__`` (for the logs), never in a
    field the boundary might echo, so no internal detail leaks into a response
    (Req 18.6, 19.6).
    """

    def __init__(self) -> None:
        super().__init__("account deletion did not complete")


@dataclass(frozen=True, slots=True)
class DeleteAccountRequest:
    """A request to delete the signed-in User's account (Req 14.2).

    ``confirmed`` records the distinct confirmation step Req 14.2 requires: a
    request that is not confirmed removes nothing and revokes nothing.
    """

    user_stable_id: str
    """The ``stable_id`` of the signed-in User whose account is being deleted."""

    confirmed: bool
    """Whether the User completed the distinct deletion-confirmation step (Req 14.2)."""


@dataclass(frozen=True, slots=True)
class DeleteAccountResult:
    """The outcome of an account-deletion attempt (Req 14.2, 14.3).

    ``deleted`` is ``True`` only when a confirmed deletion completed: the User's
    personal data was removed across every personal-data table and all Sessions
    revoked. For an unconfirmed request ``deleted`` is ``False`` and
    ``rows_removed`` and ``sessions_revoked`` are zero, since nothing changed.
    """

    deleted: bool
    """Whether a confirmed deletion completed and removed the User's data."""

    rows_removed: int = 0
    """The number of personal-data rows removed (0 when unconfirmed)."""

    sessions_revoked: int = 0
    """The number of durable Sessions revoked (0 when unconfirmed)."""


#: The single result every unconfirmed deletion request returns (Req 14.2).
#: Nothing was removed or revoked, so all counts are zero.
_NOT_CONFIRMED_RESULT: Final[DeleteAccountResult] = DeleteAccountResult(deleted=False)


class DeleteAccountUseCase:
    """Atomically delete a User's personal data and revoke all Sessions (Req 14.2-14.4)."""

    def __init__(
        self,
        *,
        personal_data: PersonalDataGateway,
        sessions: SessionRepository,
        session_store: SessionStore,
        clock: Clock,
    ) -> None:
        """Wire the use case to its collaborators.

        ``clock`` supplies the revocation instant for the durable Session
        records, so the flow is deterministic under test.
        """
        self._personal_data = personal_data
        self._sessions = sessions
        self._session_store = session_store
        self._clock = clock

    def execute(self, request: DeleteAccountRequest) -> DeleteAccountResult:
        """Delete the confirmed User's account, or do nothing when unconfirmed.

        An unconfirmed request removes and revokes nothing and returns
        :data:`_NOT_CONFIRMED_RESULT` (Req 14.2). A confirmed request revokes all
        of the User's Sessions through both the durable system of record and the
        ephemeral store (Req 14.3), then removes the User's personal data across
        every personal-data table in a single atomic gateway operation (Req 14.2).
        If the gateway's delete fails it leaves every personal-data table
        unchanged and raises, which this use case logs and re-raises as
        :class:`AccountDeletionError` so the boundary returns an error indication
        that the deletion did not complete (Req 14.4).
        """
        if not request.confirmed:
            return _NOT_CONFIRMED_RESULT

        user_stable_id = request.user_stable_id

        # Revoke every Session first through the durable system of record (ADR
        # 0004) and the ephemeral fast-path store, so a completed deletion leaves
        # no Session able to authenticate (Req 14.3). Revocation is idempotent, so
        # revoking before the atomic data delete is safe even though the delete is
        # the step that can fail.
        sessions_revoked = self._sessions.revoke_all_for_user(
            user_stable_id, revoked_at=self._clock.now()
        )
        self._session_store.revoke_all_for_user(user_stable_id)

        try:
            rows_removed = self._personal_data.delete_user_data(user_stable_id)
        except Exception as error:
            _LOGGER.error(
                "account deletion failed; personal-data tables left unchanged",
                extra={
                    "event": _DELETION_FAILED_EVENT,
                    "user_stable_id": user_stable_id,
                },
            )
            raise AccountDeletionError from error

        return DeleteAccountResult(
            deleted=True,
            rows_removed=rows_removed,
            sessions_revoked=sessions_revoked,
        )


def _default_export_key(user_stable_id: str) -> str:
    """Return the default ``ObjectStorage`` key for a User's export document.

    A stable per-User key so a re-export overwrites the previous document rather
    than accumulating artifacts.
    """
    return f"exports/{user_stable_id}.json"
