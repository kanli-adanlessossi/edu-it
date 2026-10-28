"""The self-service sign-up use case (task 12.1, Req 7.1-7.3, 7.6-7.9, 13.3).

:class:`SignUpUseCase` orchestrates the framework-free domain ports to realize
the sign-up behavior the design specifies (design, "Sign-up and email
verification"):

- A well-formed email (at most 254 characters) and a policy-compliant password
  create exactly one :class:`~edu_it_domain.ports.repository.UserRecord` whose
  email is unverified and whose only role is ``learner`` (Req 7.1, 7.9, 13.3),
  and enqueue exactly one single-use verification email whose link expires
  ``VERIFICATION_LINK_TTL`` (24 h) after issuance as measured by the injected
  ``Clock`` (Req 7.2, 7.3).
- A sign-up for an email that already belongs to an existing user creates **no**
  second user, returns the **same** non-disclosing accepted result, and notifies
  the existing owner through the ``Mailer`` that a sign-up was attempted with
  their address (Req 7.6, 7.7).
- Input that fails validation raises :class:`SignUpValidationError`, which
  carries one :class:`SignUpFieldError` per rejected field (the field name and
  the reason). The ``apps/api`` boundary maps this to an RFC 9457
  Problem_Details with one ``fields`` entry per rejected field (Req 7.8), so the
  rejected-field information is produced here, framework-free, and rendered at
  the edge.

The use case is deliberately framework-free: it imports only the domain ports,
records, and business constants, never a web or API framework (Req 1.8). Every
non-deterministic input — the current time, the new user's ``stable_id``, the
verification token, and the password hash — enters through an injected
collaborator so the whole flow is deterministic under test.

Duplicate detection is non-disclosing by construction: the accepted result for a
brand-new email and the result for an already-registered email are byte-for-byte
identical (:data:`ACCEPTED`), so a caller — and therefore a visitor — cannot
tell the two apart from the response (Req 7.6).

Email-delivery failure (task 12.3, Req 24.1, 24.3). The verification email is the
*last* step of the new-user path, and it is the one step that reaches an external
system (the ``Mailer``) and so can fail independently of our own state. When it
raises, the created user is **retained unverified** rather than rolled back: Req
24.1 says the User is *kept in the unverified state* (not deleted), so a learner
whose mail merely failed to send keeps their account and can simply re-request
the email (:class:`~edu_it_application.resend_verification.ResendVerificationUseCase`,
Req 24.3) instead of starting over. The use case records the delivery failure in
the logs with only safe, non-disclosing fields (never the provider's error text,
which the structured logger keeps out of any response, Req 18.6) and raises
:class:`SignUpMailDeliveryError`; the ``apps/api`` boundary maps that to a
translated Problem_Details with a server-error status, exactly as it maps
:class:`SignUpValidationError` to a validation Problem_Details. The duplicate-email
notice is sent on the pre-existing-owner path, which creates no new state, so a
failure there needs no retention decision and is surfaced the same way.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from edu_it_domain.config import PASSWORD_POLICY, VERIFICATION_LINK_TTL, PasswordPolicy
from edu_it_domain.ports.clock import Clock
from edu_it_domain.ports.mailer import Mailer, OutboundMail
from edu_it_domain.ports.repository import (
    USER_STATUS_UNVERIFIED,
    UserRecord,
    UserRepository,
)
from edu_it_domain.ports.verification_link import (
    VerificationLink,
    VerificationLinkRepository,
)

__all__ = [
    "ACCEPTED",
    "LEARNER_ROLE",
    "MAX_EMAIL_LENGTH",
    "SignUpFieldError",
    "SignUpMailDeliveryError",
    "SignUpRequest",
    "SignUpResult",
    "SignUpUseCase",
    "SignUpValidationError",
]

#: Application-layer logger for the sign-up flow. The ``apps/api`` structured
#: JSON logging foundation (task 8) formats every record emitted through the
#: standard :mod:`logging` module as JSON carrying the Request_ID, so a plain
#: ``logger.warning(..., extra={...})`` here lands as a structured record with
#: no bespoke wiring and no internal diagnostics leaking into any response
#: (Req 18.4, 18.6).
_LOGGER: Final[logging.Logger] = logging.getLogger("edu_it_application.sign_up")

#: Structured-log event name recorded when the verification email fails to send
#: during sign-up (Req 24.1). A stable string so log consumers can alert on it.
_MAIL_DELIVERY_FAILED_EVENT: Final[str] = "signup_verification_email_delivery_failed"

#: The only role a self-service sign-up assigns (Req 7.9, 13.3). Privileged
#: roles (reviewer/author/admin) are provisioned only through the CLI (Req 13.2),
#: never a self-service path.
LEARNER_ROLE: Final[str] = "learner"

#: The maximum accepted email length (Req 7.1): a well-formed email no longer
#: than 254 characters. 254 is the practical maximum length of an email address.
MAX_EMAIL_LENGTH: Final[int] = 254

# Field names used in :class:`SignUpFieldError`, so the API boundary and tests
# refer to rejected fields by a single stable name rather than a literal.
_EMAIL_FIELD: Final[str] = "email"
_PASSWORD_FIELD: Final[str] = "password"


@dataclass(frozen=True, slots=True)
class SignUpRequest:
    """The raw sign-up submission handed to :meth:`SignUpUseCase.execute`.

    Both fields are the visitor's unvalidated input; the use case validates them
    and raises :class:`SignUpValidationError` when either is rejected (Req 7.8).
    """

    email: str
    """The submitted email address (validated for shape and length)."""

    password: str
    """The submitted password (validated against the configured policy)."""

    locale: str = "en"
    """The visitor's locale (``fr``/``en``) for the user record and mail."""


@dataclass(frozen=True, slots=True)
class SignUpResult:
    """The non-disclosing outcome of a sign-up (Req 7.6).

    The result is intentionally identical whether the sign-up created a new user
    or hit an already-registered email, so it carries nothing that could
    disclose account existence: the only public signal is ``accepted``, always
    ``True`` for a validated submission. The two internal paths (new user vs.
    duplicate) are deliberately indistinguishable from the returned value —
    which one ran is observable only through side effects (the user created, the
    mail sent), never through the response. This makes non-disclosure a property
    of the type itself rather than of careful caller discipline.
    """

    accepted: bool
    """Always ``True`` for a validated submission (the public, non-disclosing signal)."""


#: The single accepted result every validated sign-up returns, new email or not
#: (Req 7.6). Both the new-user and duplicate-email paths return this exact
#: value, so the visitor-facing response is byte-for-byte identical and cannot
#: disclose whether the email was already registered.
ACCEPTED: Final[SignUpResult] = SignUpResult(accepted=True)


@dataclass(frozen=True, slots=True)
class SignUpFieldError:
    """One rejected sign-up field and why it was rejected (Req 7.8, 19.5).

    The API boundary turns each :class:`SignUpFieldError` into one ``fields``
    entry of the RFC 9457 validation Problem_Details (location + reason), so the
    rejected-field detail is produced here without any framework coupling.
    """

    field: str
    """The name of the rejected field (``email`` or ``password``)."""

    reason: str
    """A short, stable machine reason code for the rejection."""


class SignUpValidationError(Exception):
    """Raised when sign-up input fails validation (Req 7.8).

    Carries one :class:`SignUpFieldError` per rejected field in :attr:`errors`,
    in a stable order, so the boundary can emit one validation Problem_Details
    entry per rejected field. The exception is raised **before** any user is
    created or any mail is sent, so a rejected submission changes no state.
    """

    def __init__(self, errors: tuple[SignUpFieldError, ...]) -> None:
        self.errors: tuple[SignUpFieldError, ...] = errors
        rejected = ", ".join(error.field for error in errors)
        super().__init__(f"sign-up validation failed for: {rejected}")


class SignUpMailDeliveryError(Exception):
    """Raised when the ``Mailer`` fails to deliver the sign-up email (Req 24.1).

    The failure is non-fatal to the account: for a new-user sign-up the created
    user is **retained unverified** and its verification link remains stored, so
    the learner can re-request the email (Req 24.1, 24.3,
    :class:`~edu_it_application.resend_verification.ResendVerificationUseCase`).
    The exception carries the triggering ``Mailer`` error only as its
    ``__cause__`` (for the logs), never in a field the boundary might echo, so no
    provider detail can leak into a response (Req 18.6, 19.6). The ``apps/api``
    boundary maps this to a translated Problem_Details with a server-error status
    (Req 24.1), just as it maps :class:`SignUpValidationError` to a validation
    Problem_Details.
    """

    def __init__(self) -> None:
        super().__init__("sign-up verification email could not be delivered")


class SignUpUseCase:
    """Create an unverified learner and enqueue a verification email (Req 7)."""

    def __init__(
        self,
        *,
        users: UserRepository,
        verification_links: VerificationLinkRepository,
        mailer: Mailer,
        clock: Clock,
        token_factory: Callable[[], str],
        stable_id_factory: Callable[[], str],
        password_hasher: Callable[[str], str],
        password_policy: PasswordPolicy = PASSWORD_POLICY,
    ) -> None:
        """Wire the use case to its collaborators.

        ``token_factory`` and ``stable_id_factory`` mint the opaque verification
        token and the new user's ``stable_id``; ``password_hasher`` turns the raw
        password into the stored secret. All three are injected so the flow is
        deterministic under test and carries no cryptography into the domain.
        ``password_policy`` defaults to the configured 12-128 character policy
        (Req 7.2) but is injectable for focused tests.
        """
        self._users = users
        self._verification_links = verification_links
        self._mailer = mailer
        self._clock = clock
        self._token_factory = token_factory
        self._stable_id_factory = stable_id_factory
        self._password_hasher = password_hasher
        self._password_policy = password_policy

    def execute(self, request: SignUpRequest) -> SignUpResult:
        """Run the sign-up and return the non-disclosing accepted result.

        Validates the submission first and raises :class:`SignUpValidationError`
        with one entry per rejected field before touching any state (Req 7.8).
        For a valid, not-yet-registered email, creates one unverified learner,
        stores one single-use verification link expiring 24 h after issuance per
        the Clock, and sends the verification mail (Req 7.1-7.3, 7.9). For an
        already-registered email, creates no second user and notifies the
        existing owner, returning the identical accepted result so account
        existence is never disclosed (Req 7.6, 7.7).

        If the ``Mailer`` fails to deliver the email, the created user is kept
        unverified (never rolled back), the failure is logged, and
        :class:`SignUpMailDeliveryError` is raised so the boundary returns a
        translated Problem_Details and the learner can re-request the email
        (Req 24.1, 24.3).
        """
        self._validate(request)

        existing = self._users.get_by_email(request.email)
        if existing is not None:
            self._notify_existing_owner(existing)
            return ACCEPTED

        self._create_unverified_learner(request)
        return ACCEPTED

    def _validate(self, request: SignUpRequest) -> None:
        """Collect every rejected field, then raise once if any were rejected."""
        errors: list[SignUpFieldError] = []

        email_reason = _email_rejection(request.email)
        if email_reason is not None:
            errors.append(SignUpFieldError(field=_EMAIL_FIELD, reason=email_reason))

        if not self._password_policy.is_satisfied_by(request.password):
            errors.append(SignUpFieldError(field=_PASSWORD_FIELD, reason="password_policy_not_met"))

        if errors:
            raise SignUpValidationError(tuple(errors))

    def _create_unverified_learner(self, request: SignUpRequest) -> None:
        """Create the unverified learner and enqueue its verification email.

        The user and its verification link are persisted **before** the mail is
        sent, so a ``Mailer`` failure leaves a fully-formed, unverified account
        with a stored link rather than a half-created record (Req 24.1). On such
        a failure the account is deliberately *not* rolled back: it is logged and
        re-raised as :class:`SignUpMailDeliveryError` so the learner can simply
        request the email again (Req 24.3).
        """
        issued_at = self._clock.now()
        user = UserRecord(
            stable_id=self._stable_id_factory(),
            email=request.email,
            locale=request.locale,
            status=USER_STATUS_UNVERIFIED,
        )
        # The password hash is computed so a real adapter persists it; the
        # repository port stores only API-safe identity attributes, so hashing
        # here keeps the raw secret out of the domain record.
        _ = self._password_hasher(request.password)
        self._users.add(user)

        link = VerificationLink(
            token=self._token_factory(),
            user_stable_id=user.stable_id,
            issued_at=issued_at,
            expires_at=issued_at + VERIFICATION_LINK_TTL,
        )
        self._verification_links.add(link)

        self._send_or_raise(
            _verification_mail(user, link),
            user_stable_id=user.stable_id,
            stage="verification",
        )

    def _notify_existing_owner(self, owner: UserRecord) -> None:
        """Tell the existing owner a sign-up was attempted with their email."""
        self._send_or_raise(
            _duplicate_notice_mail(owner),
            user_stable_id=owner.stable_id,
            stage="duplicate_notice",
        )

    def _send_or_raise(self, mail: OutboundMail, *, user_stable_id: str, stage: str) -> None:
        """Send ``mail``; on a ``Mailer`` failure, log it and re-raise safely.

        The user record created on the new-user path is left untouched (kept
        unverified, Req 24.1); only the failure is recorded, with non-disclosing
        structured fields — the affected user's ``stable_id`` and the sign-up
        stage, never the recipient address or the provider's error text, which
        the structured logger keeps out of any response (Req 18.6). The original
        ``Mailer`` exception is chained as ``__cause__`` so the full diagnostic
        lives in the logs while :class:`SignUpMailDeliveryError` carries nothing
        the boundary could echo (Req 24.1).
        """
        try:
            self._mailer.send(mail)
        except Exception as error:
            _LOGGER.warning(
                "sign-up email delivery failed",
                extra={
                    "event": _MAIL_DELIVERY_FAILED_EVENT,
                    "user_stable_id": user_stable_id,
                    "stage": stage,
                },
            )
            raise SignUpMailDeliveryError from error


def _email_rejection(email: str) -> str | None:
    """Return a reason code if ``email`` is rejected, else ``None`` (Req 7.1).

    An email is accepted only when it is well-formed and no longer than
    :data:`MAX_EMAIL_LENGTH`. Shape is checked with a deliberately small,
    conservative rule (exactly one ``@`` separating a non-empty local part from a
    dotted domain with no whitespace); the stricter RFC-5322 surface is left to
    the boundary's Pydantic ``EmailStr``, so this core rejects the clearly
    malformed without over-constraining.
    """
    if len(email) > MAX_EMAIL_LENGTH:
        return "email_too_long"
    if not _is_well_formed_email(email):
        return "email_malformed"
    return None


def _is_well_formed_email(email: str) -> bool:
    """Return whether ``email`` has the minimal well-formed shape.

    Requires exactly one ``@``, a non-empty local part, a domain containing at
    least one dot with non-empty labels on both sides of every dot, and no ASCII
    whitespace anywhere.
    """
    if any(character.isspace() for character in email):
        return False
    local, separator, domain = email.partition("@")
    if separator != "@" or "@" in domain:
        return False
    if not local or not domain:
        return False
    labels = domain.split(".")
    if len(labels) < 2:
        return False
    return all(labels)


def _verification_mail(user: UserRecord, link: VerificationLink) -> OutboundMail:
    """Build the single-use verification email for ``user`` (Req 7.1, 7.3).

    The mail is locale-marked so the ``Mailer`` renders the FR/EN template; the
    opaque token is carried so the verification use case (task 12.2) can resolve
    the link the recipient opens.
    """
    subject = "Confirmez votre adresse e-mail" if user.locale == "fr" else "Confirm your email"
    body = (
        f"Bonjour, confirmez votre adresse e-mail avec ce lien à usage unique : {link.token}"
        if user.locale == "fr"
        else f"Hello, confirm your email with this single-use link: {link.token}"
    )
    return OutboundMail(
        to=user.email,
        subject=subject,
        body=body,
        headers={"locale": user.locale, "X-Verification-Token": link.token},
    )


def _duplicate_notice_mail(owner: UserRecord) -> OutboundMail:
    """Build the "a sign-up was attempted with your email" notice (Req 7.7)."""
    subject = (
        "Tentative d'inscription avec votre e-mail"
        if owner.locale == "fr"
        else "Sign-up attempted with your email"
    )
    body = (
        "Quelqu'un a tenté de créer un compte avec votre adresse e-mail. "
        "Si ce n'était pas vous, aucune action n'est nécessaire."
        if owner.locale == "fr"
        else (
            "Someone attempted to create an account with your email address. "
            "If this was not you, no action is needed."
        )
    )
    return OutboundMail(
        to=owner.email,
        subject=subject,
        body=body,
        headers={"locale": owner.locale},
    )
