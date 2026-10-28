"""Property-based test for locale-aware detail with default fallback (task 8.5).

Feature: foundations, Property 30: Learner-facing detail follows the request locale with default fallback

Property 30 (design.md): *For any* request carrying a learner-facing ``detail``,
the ``detail`` is rendered in the request locale when it is a supported locale
(``fr`` or ``en``) and in the configured default locale when the request locale
is absent or unsupported.

Validates: Requirements 19.3, 19.4

The property is exercised through the real rendering path with no running
server: the request locale is resolved by
:func:`edu_it_api.errors.locale.resolve_locale` (which accepts the explicit
``/fr``-``/en`` hint and the ``Accept-Language`` header) and the resulting
learner-facing ``detail`` is produced by :func:`edu_it_api.errors.problem.build_problem`,
whose ``detail`` comes from the FR/EN catalog via
:meth:`~edu_it_api.errors.catalog.ProblemType.detail_for`.

Uses Hypothesis, the pinned Python property-based testing library
(docs/adr/0001-versions.md), with at least 100 iterations.
"""

from __future__ import annotations

from http import HTTPStatus

from edu_it_api.errors import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    build_problem,
    problem_type_for_status,
    resolve_locale,
)
from hypothesis import given, settings
from hypothesis import strategies as st

# Statuses that map to a catalogued problem type, so each carries a defined FR
# and EN ``detail`` template to render.
_CATALOGUED_STATUSES = sorted(
    {
        int(HTTPStatus.BAD_REQUEST),
        int(HTTPStatus.UNAUTHORIZED),
        int(HTTPStatus.FORBIDDEN),
        int(HTTPStatus.NOT_FOUND),
        int(HTTPStatus.METHOD_NOT_ALLOWED),
        int(HTTPStatus.CONFLICT),
        int(HTTPStatus.UNPROCESSABLE_ENTITY),
        int(HTTPStatus.TOO_MANY_REQUESTS),
        int(HTTPStatus.INTERNAL_SERVER_ERROR),
        int(HTTPStatus.SERVICE_UNAVAILABLE),
    }
)

# A locale slot (explicit hint or Accept-Language header) is one of: absent
# (``None``), a supported locale optionally region-tagged and in mixed case, or
# an unsupported language tag. Region tags and case variants must still resolve
# to their supported primary subtag (fr-CA -> fr), and unsupported tags must be
# treated as absent so the fallback chain continues.
_SUPPORTED = sorted(SUPPORTED_LOCALES)
_UNSUPPORTED = ["de", "es", "it", "pt", "nl", "zz", "", "  ", "english", "x-fr"]


def _supported_variants() -> st.SearchStrategy[str]:
    base = st.sampled_from(_SUPPORTED)
    region = st.sampled_from(["", "-CA", "-US", "-FR", "-GB"])
    casing = st.sampled_from(["lower", "upper", "title"])

    def _render(values: tuple[str, str, str]) -> str:
        primary, suffix, case = values
        tag = primary + suffix
        if case == "upper":
            return tag.upper()
        if case == "title":
            return tag.title()
        return tag

    return st.tuples(base, region, casing).map(_render)


# A locale slot value together with the supported primary subtag it must resolve
# to (``None`` when the value is absent/unsupported and so contributes nothing).
_locale_slot = st.one_of(
    st.none().map(lambda _: (None, None)),
    _supported_variants().map(lambda tag: (tag, tag.strip().split("-", 1)[0].lower())),
    st.sampled_from(_UNSUPPORTED).map(lambda tag: (tag, None)),
)


def _expected_locale(explicit_resolves_to: str | None, header_resolves_to: str | None) -> str:
    """The locale resolution contract: explicit hint, then header, then default."""
    if explicit_resolves_to is not None:
        return explicit_resolves_to
    if header_resolves_to is not None:
        return header_resolves_to
    return DEFAULT_LOCALE


@given(
    status=st.sampled_from(_CATALOGUED_STATUSES),
    explicit_slot=_locale_slot,
    header_slot=_locale_slot,
)
@settings(max_examples=200, deadline=None)
def test_property_30_detail_follows_request_locale_with_default_fallback(
    status: int,
    explicit_slot: tuple[str | None, str | None],
    header_slot: tuple[str | None, str | None],
) -> None:
    """Feature: foundations, Property 30: Learner-facing detail follows the request locale with default fallback.

    For any status carrying a learner-facing ``detail``, the rendered ``detail``
    follows the request locale when it is a supported locale (``fr``/``en``) and
    falls back to the configured default locale when the request locale is absent
    or unsupported.
    """
    explicit_value, explicit_resolves_to = explicit_slot
    header_value, header_resolves_to = header_slot

    expected_locale = _expected_locale(explicit_resolves_to, header_resolves_to)
    # The resolved locale is always a supported locale (fr/en).
    assert expected_locale in SUPPORTED_LOCALES

    resolved = resolve_locale(explicit=explicit_value, accept_language=header_value)
    assert resolved == expected_locale

    # The learner-facing detail rendered for this request equals the catalog's
    # detail for the expected locale — the request locale when supported, the
    # default locale otherwise.
    rendered = build_problem(status=status, locale=resolved).detail
    problem_type = problem_type_for_status(status)
    assert rendered == problem_type.detail_for(expected_locale)

    # Concretely: a supported request locale yields that locale's text, and an
    # absent/unsupported request locale yields the default locale's text.
    if explicit_resolves_to is None and header_resolves_to is None:
        assert rendered == problem_type.detail_for(DEFAULT_LOCALE)
    else:
        assert rendered == problem_type.detail_for(expected_locale)

    # A supported request locale that differs from the default must actually
    # change the rendered text (the two catalog templates differ), proving the
    # detail follows the request locale rather than always defaulting.
    non_default = next(loc for loc in SUPPORTED_LOCALES if loc != DEFAULT_LOCALE)
    if expected_locale == non_default:
        assert rendered != problem_type.detail_for(DEFAULT_LOCALE)
