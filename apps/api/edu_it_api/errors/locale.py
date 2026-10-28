"""Request-locale resolution for learner-facing error text (Req 19.3, 19.4).

A learner-facing ``detail`` is rendered in the request Locale, falling back to a
configured default Locale when the request Locale is absent or unsupported. The
platform supports exactly ``fr`` and ``en`` and defaults to ``en`` (matching the
infrastructure SMTP mailer's FR/EN template surface and the ``fr``/``en`` web
route trees), per ``.kiro/steering/api-conventions.md``.

The resolver is a pure function so the locale-fallback property (task 8.5) can
exercise it directly. It accepts the two ways a request carries a locale in this
phase — an explicit locale hint (the ``/fr`` or ``/en`` route segment, or a
locale query/header the web app forwards) and the standard ``Accept-Language``
request header — and returns a supported locale, never raising.
"""

from __future__ import annotations

from edu_it_api.errors.catalog import DEFAULT_LOCALE, SUPPORTED_LOCALES

__all__ = ["resolve_locale"]


def _normalize(candidate: str | None) -> str | None:
    """Return the base language subtag of ``candidate`` when it is supported.

    ``fr-CA`` / ``EN-us`` style values are reduced to their primary subtag and
    lower-cased; anything that is not one of the supported locales yields
    ``None`` so the caller can continue the fallback chain.
    """
    if not candidate:
        return None
    primary = candidate.strip().split("-", 1)[0].lower()
    return primary if primary in SUPPORTED_LOCALES else None


def _from_accept_language(header: str | None) -> str | None:
    """Return the first supported locale named in an ``Accept-Language`` header.

    Quality weights (``;q=``) are ignored for ordering — the header lists
    preferences most-preferred-first — and each entry is reduced to its primary
    subtag. The first entry that is a supported locale wins; an absent, empty, or
    fully unsupported header yields ``None``.
    """
    if not header:
        return None
    for part in header.split(","):
        token = part.split(";", 1)[0]
        matched = _normalize(token)
        if matched is not None:
            return matched
    return None


def resolve_locale(
    *,
    explicit: str | None = None,
    accept_language: str | None = None,
) -> str:
    """Resolve the request locale, falling back to the default (Req 19.3, 19.4).

    Resolution order:

    1. ``explicit`` — a locale the request states directly (the ``/fr`` or
       ``/en`` route segment, or a locale hint the web app forwards). This is the
       request Locale proper and takes precedence.
    2. ``accept_language`` — the standard ``Accept-Language`` header.
    3. :data:`~edu_it_api.errors.catalog.DEFAULT_LOCALE` — used when the request
       Locale is absent or unsupported (Req 19.4).

    The returned value is always one of
    :data:`~edu_it_api.errors.catalog.SUPPORTED_LOCALES`.
    """
    from_explicit = _normalize(explicit)
    if from_explicit is not None:
        return from_explicit
    from_header = _from_accept_language(accept_language)
    if from_header is not None:
        return from_header
    return DEFAULT_LOCALE
