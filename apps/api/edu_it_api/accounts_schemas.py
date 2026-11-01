"""Pydantic v2 request/response models for the account routes (api-route-wiring Req 3, 4).

Request models forbid unknown fields and type ``email`` and ``password`` as plain
``str`` with no length, pattern, or format constraint: the use case is the single
validator of email shape, email length, and password policy (Req 3.10), so an
over-long email reaches ``SignUpUseCase`` and comes back as ``SignUpValidationError``.
Response models are frozen and never carry the Session reference, a password, or a
password hash (Req 4.8).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = [
    "AcceptedResponse",
    "AccountDeletionBody",
    "CsrfResponse",
    "ExportResponse",
    "PasswordResetBody",
    "PasswordResetCompleteBody",
    "ResendVerificationBody",
    "SessionResponse",
    "SignInBody",
    "SignInResponse",
    "SignUpBody",
    "VerifyEmailBody",
    "VerifyEmailResponse",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class SignUpBody(_Strict):
    """Sign-up submission; validation belongs to the use case."""

    email: str
    password: str


class VerifyEmailBody(_Strict):
    """Verification submission carrying the opaque link token."""

    token: str


class ResendVerificationBody(_Strict):
    """Resend request naming the email to verify."""

    email: str


class SignInBody(_Strict):
    """Sign-in submission."""

    email: str
    password: str


class PasswordResetBody(_Strict):
    """Reset request naming the email; the use case decides what happens."""

    email: str


class PasswordResetCompleteBody(_Strict):
    """Reset completion: the opaque link token and the new password (policy in use case)."""

    token: str
    new_password: str


class AccountDeletionBody(_Strict):
    """Deletion request; the account is deleted only when ``confirm`` is ``true``."""

    confirm: bool


class ExportResponse(_Frozen):
    """Where to download the export, and its size."""

    download_path: str
    size_bytes: int


class AcceptedResponse(_Frozen):
    """Identical body for registered, unregistered, and already-verified emails."""

    accepted: bool = True


class VerifyEmailResponse(_Frozen):
    """Body returned when a verification link is consumed."""

    verified: bool = True


class SignInResponse(_Frozen):
    """Locale-free post-sign-in destination; never the session reference."""

    redirect_to: str


class SessionResponse(_Frozen):
    """The authenticated user's stable identifier."""

    user_id: str


class CsrfResponse(_Frozen):
    """The CSRF_Token, also set as the ``csrf_token`` cookie."""

    token: str
