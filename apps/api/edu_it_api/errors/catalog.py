"""Problem-type metadata and the FR/EN learner-facing message catalog (Req 19).

Each problem type carries the stable members RFC 9457 requires — a ``type`` URI
reference and a ``title`` that does not change from occurrence to occurrence
(aside from localization) — plus the HTTP ``status`` it maps to and the FR/EN
``detail`` templates rendered in the request Locale (Req 19.1, 19.3). ``title``
is stable per problem type; occurrence-specific information goes in ``detail``
(``docs/API-CONVENTIONS.md`` Section 1).

Keeping the catalog as plain data (no framework import) lets the rendering logic
in :mod:`edu_it_api.errors.problem` and the locale resolver in
:mod:`edu_it_api.errors.locale` be exercised by the property tests without a
running server. Every ``detail`` string is defined for both supported locales so
a lookup in either locale always resolves without leaking an untranslated key.
"""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import Final

__all__ = [
    "CSRF",
    "DEFAULT_LOCALE",
    "SUPPORTED_LOCALES",
    "ProblemType",
    "problem_type_for_status",
]

#: The platform's two supported locales (``.kiro/steering/api-conventions.md``
#: and the ``fr``/``en`` web route trees). A request locale outside this set is
#: treated as absent and falls back to :data:`DEFAULT_LOCALE` (Req 19.4).
SUPPORTED_LOCALES: Final[frozenset[str]] = frozenset({"fr", "en"})

#: The configured default Locale used when the request Locale is absent or
#: unsupported (Req 19.4). ``en`` matches the SMTP mailer's default template
#: locale so error text and transactional mail share one default.
DEFAULT_LOCALE: Final[str] = "en"

#: Base URI for problem ``type`` references. Dereferencing a concrete type URI
#: SHOULD provide human-readable documentation for the problem type
#: (``docs/API-CONVENTIONS.md`` Section 1). ``about:blank`` is used when no
#: specific type applies.
_PROBLEM_TYPE_BASE: Final[str] = "https://api.edu-it.example/problems/"


@dataclass(frozen=True, slots=True)
class ProblemType:
    """Stable metadata for one RFC 9457 problem type.

    ``type`` and ``title`` do not change from occurrence to occurrence (aside
    from the locale-specific ``detail``); ``status`` is the HTTP status the type
    maps to, and ``detail`` holds the FR/EN learner-facing templates. ``title``
    is intentionally rendered in the request locale too, so the whole
    learner-facing surface follows the request Locale.
    """

    slug: str
    """Short, stable identifier used to build the ``type`` URI and look up text."""

    status: int
    """The HTTP status code this problem type maps to; the body's ``status``
    member always equals this (Req 19.2)."""

    title: dict[str, str]
    """Per-locale short, stable summary of the problem type."""

    detail: dict[str, str]
    """Per-locale human-readable, actionable explanation for this occurrence."""

    @property
    def type_uri(self) -> str:
        """Return the ``type`` URI reference for this problem type.

        ``about:blank`` is preserved verbatim (RFC 9457's sentinel for "no
        specific type"); every other slug is namespaced under the problem-type
        base URI.
        """
        if self.slug == "about:blank":
            return "about:blank"
        return f"{_PROBLEM_TYPE_BASE}{self.slug}"

    def title_for(self, locale: str) -> str:
        """Return the ``title`` in ``locale``, falling back to the default."""
        return self.title.get(locale, self.title[DEFAULT_LOCALE])

    def detail_for(self, locale: str) -> str:
        """Return the ``detail`` in ``locale``, falling back to the default."""
        return self.detail.get(locale, self.detail[DEFAULT_LOCALE])


def _generic(
    status: HTTPStatus, slug: str, title_fr: str, title_en: str, detail_fr: str, detail_en: str
) -> ProblemType:
    return ProblemType(
        slug=slug,
        status=int(status),
        title={"fr": title_fr, "en": title_en},
        detail={"fr": detail_fr, "en": detail_en},
    )


#: The CSRF problem type (Req 11.2). Returned with HTTP ``403 Forbidden`` when an
#: unsafe mutation arrives with a missing, malformed, or invalid CSRF_Token. The
#: ``detail`` is learner-facing and actionable (reload and retry) and carries no
#: diagnostic about why the token failed, so an attacker learns nothing.
CSRF: Final[ProblemType] = _generic(
    HTTPStatus.FORBIDDEN,
    "csrf-token-invalid",
    "Jeton de sécurité manquant ou invalide",
    "Missing or invalid security token",
    "Votre session de sécurité a expiré. Rechargez la page et réessayez.",
    "Your security session has expired. Reload the page and try again.",
)

#: The validation problem type (Req 7.8, 19.5). Its ``fields`` extension member
#: carries one entry per rejected field; see :mod:`edu_it_api.errors.problem`.
VALIDATION: Final[ProblemType] = _generic(
    HTTPStatus.UNPROCESSABLE_ENTITY,
    "validation-error",
    "Requête invalide",
    "Invalid request",
    "Certains champs sont invalides. Corrigez-les et réessayez.",
    "Some fields are invalid. Correct them and try again.",
)

# A small registry of generic, status-keyed problem types. Feature-specific
# problem types (unverified email, invalid credentials, rate limiting, ...) are
# added by the account tasks that raise them; this foundation supplies the
# generic fallbacks every status needs so no non-2xx response is ever untyped.
_GENERIC_BY_STATUS: Final[dict[int, ProblemType]] = {
    int(HTTPStatus.BAD_REQUEST): _generic(
        HTTPStatus.BAD_REQUEST,
        "bad-request",
        "Requête incorrecte",
        "Bad request",
        "La requête est incorrecte. Vérifiez les informations envoyées.",
        "The request is malformed. Check the information you sent.",
    ),
    int(HTTPStatus.UNAUTHORIZED): _generic(
        HTTPStatus.UNAUTHORIZED,
        "unauthenticated",
        "Authentification requise",
        "Authentication required",
        "Vous devez vous connecter pour effectuer cette action.",
        "You need to sign in to perform this action.",
    ),
    int(HTTPStatus.FORBIDDEN): _generic(
        HTTPStatus.FORBIDDEN,
        "forbidden",
        "Accès refusé",
        "Access denied",
        "Vous n'êtes pas autorisé à effectuer cette action.",
        "You are not allowed to perform this action.",
    ),
    int(HTTPStatus.NOT_FOUND): _generic(
        HTTPStatus.NOT_FOUND,
        "not-found",
        "Ressource introuvable",
        "Not found",
        "La ressource demandée est introuvable.",
        "The requested resource could not be found.",
    ),
    int(HTTPStatus.METHOD_NOT_ALLOWED): _generic(
        HTTPStatus.METHOD_NOT_ALLOWED,
        "method-not-allowed",
        "Méthode non autorisée",
        "Method not allowed",
        "Cette méthode n'est pas autorisée pour cette ressource.",
        "This method is not allowed for this resource.",
    ),
    int(HTTPStatus.CONFLICT): _generic(
        HTTPStatus.CONFLICT,
        "conflict",
        "Conflit",
        "Conflict",
        "La requête entre en conflit avec l'état actuel de la ressource.",
        "The request conflicts with the current state of the resource.",
    ),
    int(HTTPStatus.UNPROCESSABLE_ENTITY): VALIDATION,
    int(HTTPStatus.TOO_MANY_REQUESTS): _generic(
        HTTPStatus.TOO_MANY_REQUESTS,
        "too-many-requests",
        "Trop de requêtes",
        "Too many requests",
        "Trop de tentatives. Patientez avant de réessayer.",
        "Too many attempts. Please wait before trying again.",
    ),
    int(HTTPStatus.INTERNAL_SERVER_ERROR): _generic(
        HTTPStatus.INTERNAL_SERVER_ERROR,
        "internal-error",
        "Erreur du serveur",
        "Server error",
        "Une erreur interne est survenue. Réessayez plus tard.",
        "An internal error occurred. Please try again later.",
    ),
    int(HTTPStatus.SERVICE_UNAVAILABLE): _generic(
        HTTPStatus.SERVICE_UNAVAILABLE,
        "service-unavailable",
        "Service indisponible",
        "Service unavailable",
        "Le service est momentanément indisponible. Réessayez plus tard.",
        "The service is temporarily unavailable. Please try again later.",
    ),
}

#: Generic fallback used for any status with no more specific generic entry. Its
#: ``type`` is ``about:blank`` per RFC 9457 (no specific type applies), while the
#: body's ``status`` is still set to the response's real status code.
_FALLBACK: Final[ProblemType] = ProblemType(
    slug="about:blank",
    status=int(HTTPStatus.INTERNAL_SERVER_ERROR),
    title={"fr": "Erreur", "en": "Error"},
    detail={
        "fr": "Une erreur est survenue lors du traitement de la requête.",
        "en": "An error occurred while processing the request.",
    },
)


def problem_type_for_status(status: int) -> ProblemType:
    """Return a generic :class:`ProblemType` for ``status`` (Req 19.1, 19.2).

    Every non-2xx response maps to a problem type carrying stable ``type`` and
    ``title`` members. A status without a specific generic entry falls back to an
    ``about:blank`` type whose rendered ``status`` is overridden to the real
    status by the caller, so the body's ``status`` always equals the response's
    HTTP status code.
    """
    specific = _GENERIC_BY_STATUS.get(status)
    if specific is not None:
        return specific
    # No specific generic type: reuse the about:blank fallback but carry the real
    # status so the serialized document's ``status`` still equals it.
    return ProblemType(
        slug="about:blank",
        status=status,
        title=_FALLBACK.title,
        detail=_FALLBACK.detail,
    )
