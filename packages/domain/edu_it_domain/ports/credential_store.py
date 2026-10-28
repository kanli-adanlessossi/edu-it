"""The password ``CredentialStore`` port (Req 5.1, 8.1, 8.4).

Sign-in has to answer one question the identity records cannot: *do the
submitted credentials match the stored secret for this account?* The data model
deliberately keeps no password column on the ``user`` row (D3 Section 4): the
raw password never enters the domain, and the stored secret is a hashed
credential that lives behind this port, keyed by the owning user's immutable
``stable_id``. Keeping it behind a port means the application layer verifies a
password without ever seeing a hash, a salt, or a hashing algorithm — exactly
the separation the sign-up use case already assumes when it injects a
``password_hasher`` and keeps the raw secret out of the ``UserRecord``.

The surface is the two operations the account system needs: **set** (or replace)
a user's stored credential from an already-hashed secret, and **verify** a raw
password submission against the stored secret. Verification is the security
boundary: it returns a plain boolean and never discloses *why* a check failed
(no stored credential vs. a wrong password), so the sign-in flow can keep its
"invalid credentials" response non-disclosing (Req 8.4).

This is a framework-free :class:`typing.Protocol` port like the others; the
infrastructure layer supplies the concrete implementation and, for tests, an
in-memory one. The port carries no cryptography: how a raw password is checked
against the stored secret is the implementation's concern, mirroring how the
``password_hasher`` injected into sign-up owns the hashing.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["CredentialStore"]


@runtime_checkable
class CredentialStore(Protocol):
    """Port for storing and verifying a user's password credential (Req 8)."""

    def set_password(self, user_stable_id: str, hashed_secret: str) -> None:
        """Store (or replace) the hashed credential for ``user_stable_id``.

        ``hashed_secret`` is an already-hashed secret — the raw password never
        reaches this port — so the store never performs hashing itself. Called
        when an account first sets a password and again whenever the password
        changes (e.g. a completed reset), replacing any existing credential.
        """
        ...

    def verify_password(self, user_stable_id: str, password: str) -> bool:
        """Return whether ``password`` matches the stored credential.

        ``True`` only when a credential is stored for ``user_stable_id`` and the
        raw ``password`` matches it. Returns ``False`` for a wrong password *and*
        for a user with no stored credential, drawing no distinction between the
        two, so a caller cannot tell "no such credential" from "wrong password"
        and the sign-in response stays non-disclosing (Req 8.4).
        """
        ...
