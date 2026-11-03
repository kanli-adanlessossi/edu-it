"""``hashlib.scrypt`` password hasher (no third-party dependency).

Stored format: ``scrypt$N$r$p$<salt_b64>$<hash_b64>``. The cost parameters and
salt travel with the hash, so :meth:`Scrypt_PasswordHasher.verify` reads them
from the stored string and a later cost increase does not invalidate old hashes.
Comparison uses :func:`hmac.compare_digest`.

A missing or malformed credential is verified against a dummy hash so the work
done is the same as for a wrong password.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets

from edu_it_domain.config import (
    PASSWORD_HASH_KEY_BYTES,
    PASSWORD_HASH_SALT_BYTES,
    PASSWORD_HASH_SCRYPT_N,
    PASSWORD_HASH_SCRYPT_P,
    PASSWORD_HASH_SCRYPT_R,
)

_SCHEME = "scrypt"
_SEPARATOR = "$"


def _maxmem(n: int, r: int) -> int:
    """Memory ceiling for scrypt: ``128 * N * r`` plus headroom."""
    return 128 * n * r + 1024 * 1024 * 2


def _password_bytes(raw: str) -> bytes:
    """Encode ``raw`` for scrypt.

    OpenSSL's scrypt gives the empty password and ``"\x00"`` the same key, so a password made
    of NUL bytes could sign in as an empty one. A password with a NUL byte is therefore replaced
    by a fixed-length digest of itself. Passwords without NUL keep their plain encoding, so
    every existing stored hash still verifies.
    """
    data = raw.encode("utf-8")
    if b"\x00" in data:
        return b"nul-sha256:" + hashlib.sha256(data).hexdigest().encode("ascii")
    return data


def _derive(raw: str, salt: bytes, n: int, r: int, p: int, key_len: int) -> bytes:
    return hashlib.scrypt(
        _password_bytes(raw),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=_maxmem(n, r),
        dklen=key_len,
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class Scrypt_PasswordHasher:
    """Hash and verify passwords with scrypt."""

    def __init__(self) -> None:
        # A real hash of a random secret, used to burn equal work for a
        # missing credential. Computed once with the current parameters.
        self._dummy_hash = self.hash(secrets.token_urlsafe(16))

    def hash(self, raw: str) -> str:
        """Return ``scrypt$N$r$p$salt$hash`` for ``raw`` with a fresh salt."""
        n = PASSWORD_HASH_SCRYPT_N
        r = PASSWORD_HASH_SCRYPT_R
        p = PASSWORD_HASH_SCRYPT_P
        salt = secrets.token_bytes(PASSWORD_HASH_SALT_BYTES)
        key = _derive(raw, salt, n, r, p, PASSWORD_HASH_KEY_BYTES)
        return _SEPARATOR.join((_SCHEME, str(n), str(r), str(p), _b64(salt), _b64(key)))

    def __call__(self, raw: str) -> str:
        """Allow use as the ``Callable[[str], str]`` the use cases inject."""
        return self.hash(raw)

    def verify(self, raw: str, stored: str | None) -> bool:
        """Return whether ``raw`` matches ``stored``.

        A missing (``None`` or empty) or malformed ``stored`` value triggers a
        dummy verification and returns ``False``.
        """
        if not stored:
            return self.dummy_verify(raw)
        parsed = self._parse(stored)
        if parsed is None:
            return self.dummy_verify(raw)
        n, r, p, salt, expected = parsed
        try:
            actual = _derive(raw, salt, n, r, p, len(expected))
        except (ValueError, MemoryError):
            return self.dummy_verify(raw)
        return hmac.compare_digest(actual, expected)

    def dummy_verify(self, raw: str) -> bool:
        """Do a full verification against a dummy hash; always ``False``."""
        parsed = self._parse(self._dummy_hash)
        assert parsed is not None
        n, r, p, salt, expected = parsed
        actual = _derive(raw, salt, n, r, p, len(expected))
        hmac.compare_digest(actual, expected)
        return False

    @staticmethod
    def _parse(stored: str) -> tuple[int, int, int, bytes, bytes] | None:
        parts = stored.split(_SEPARATOR)
        if len(parts) != 6 or parts[0] != _SCHEME:
            return None
        try:
            n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
            salt = base64.b64decode(parts[4], validate=True)
            expected = base64.b64decode(parts[5], validate=True)
        except (ValueError, binascii.Error):
            return None
        if n < 2 or n & (n - 1) or r < 1 or p < 1 or not salt or not expected:
            return None
        return n, r, p, salt, expected


__all__ = ["Scrypt_PasswordHasher"]
