"""Tests for the scrypt password hasher (api-route-wiring 7.1, 7.2)."""

from __future__ import annotations

from edu_it_domain.config import (
    PASSWORD_HASH_SCRYPT_N,
    PASSWORD_HASH_SCRYPT_P,
    PASSWORD_HASH_SCRYPT_R,
)
from edu_it_infrastructure.password_hasher import Scrypt_PasswordHasher
from hypothesis import given, settings
from hypothesis import strategies as st

_HASHER = Scrypt_PasswordHasher()


def test_hash_format_embeds_parameters_and_salt() -> None:
    parts = _HASHER.hash("Correct-Horse-9").split("$")
    assert parts[:4] == [
        "scrypt",
        str(PASSWORD_HASH_SCRYPT_N),
        str(PASSWORD_HASH_SCRYPT_R),
        str(PASSWORD_HASH_SCRYPT_P),
    ]
    assert len(parts) == 6


def test_salt_makes_hashes_differ() -> None:
    assert _HASHER.hash("same") != _HASHER.hash("same")


def test_verify_right_wrong_and_missing() -> None:
    stored = _HASHER.hash("secret-pw")
    assert _HASHER.verify("secret-pw", stored) is True
    assert _HASHER.verify("other-pw", stored) is False
    assert _HASHER.verify("secret-pw", None) is False
    assert _HASHER.verify("secret-pw", "garbage") is False


def test_verify_uses_stored_parameters() -> None:
    import base64
    import hashlib

    salt = b"0123456789abcdef"
    key = hashlib.scrypt(b"pw", salt=salt, n=2**10, r=8, p=1, dklen=32)
    b64 = base64.b64encode
    stored = f"scrypt$1024$8$1${b64(salt).decode()}${b64(key).decode()}"
    assert _HASHER.verify("pw", stored) is True


# Feature: api-route-wiring, Property 1: For any password, verifying it against its own stored
# hash returns true; for any different password it returns false; hashing the same password
# twice yields two different stored strings; and verifying any password for a user with no
# credential returns false.
# **Validates: Requirements 7.1, 7.2**
@settings(max_examples=100, deadline=None)
@given(st.text(max_size=40), st.text(max_size=40))
def test_password_hash_round_trip_property(a: str, b: str) -> None:
    stored = _HASHER.hash(a)
    other = _HASHER.hash(a)

    # Own hash verifies; a different password does not.
    assert _HASHER.verify(a, stored) is True
    assert _HASHER.verify(b, stored) is (a == b)

    # Salted: hashing twice differs, yet both verify.
    assert stored != other
    assert _HASHER.verify(a, other) is True

    # Stored string honors configured parameters and never contains the raw password.
    parts = stored.split("$")
    assert parts[:4] == [
        "scrypt",
        str(PASSWORD_HASH_SCRYPT_N),
        str(PASSWORD_HASH_SCRYPT_R),
        str(PASSWORD_HASH_SCRYPT_P),
    ]
    if len(a) >= 8:  # shorter strings can match base64 text by chance
        assert a not in stored

    # No credential: always false.
    assert _HASHER.verify(a, None) is False
