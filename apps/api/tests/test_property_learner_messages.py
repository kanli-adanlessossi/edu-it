"""Property test: learner messages are translated and carry no raw detail.

Feature: kubernetes-deployment, Property 5: Learner messages are translated and carry
no raw detail

*For any* locale in French and English and any raw Kubernetes message text, the
learner-facing message for a capacity failure, a provisioning failure or an
infrastructure loss is the fixed translation for that locale and contains no part of
the raw text.

kubernetes-deployment task 8.6. The API catalog (``LAB_MESSAGES``) and the web
messages (``apps/web/src/messages``) are both checked; the message text is fixed, so
it cannot depend on the generated raw text.

**Validates: Requirements 6.8, 19.1, 22.2**
"""

from __future__ import annotations

import json
from pathlib import Path

from edu_it_api.errors.labs import (
    LAB_CAPACITY,
    LAB_MESSAGES,
    LAB_PROVISION_FAILED,
    LAB_SESSION_INTERRUPTED,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_LOCALES = ("fr", "en")
_MESSAGES_DIR = Path(__file__).resolve().parents[2] / "web" / "src" / "messages"

# Web key (under ``Labs.errors``) for each API message key.
_WEB_KEYS = {
    "labs.capacity": "capacity",
    "labs.session_interrupted": "sessionInterrupted",
    "labs.provision_failed": "provisionFailed",
}

_WEB = {
    loc: json.loads((_MESSAGES_DIR / f"{loc}.json").read_text(encoding="utf-8"))["Labs"]["errors"]
    for loc in _LOCALES
}

# Raw provider text: arbitrary text embedded in realistic Kubernetes output. The
# ``k8s-raw[...]`` wrapper keeps the raw string from occurring by chance in the prose.
_raw_text = st.builds(
    "k8s-raw[pods forbidden: exceeded quota {}: {}]".format,
    st.integers(1, 999),
    st.text(min_size=1, max_size=100),
)


def _texts(key: str, locale: str) -> list[str]:
    problem = LAB_MESSAGES[key]
    return [problem.title[locale], problem.detail[locale], _WEB[locale][_WEB_KEYS[key]]]


def test_catalog_covers_the_three_failures_in_both_locales() -> None:
    assert LAB_MESSAGES == {
        "labs.capacity": LAB_CAPACITY,
        "labs.session_interrupted": LAB_SESSION_INTERRUPTED,
        "labs.provision_failed": LAB_PROVISION_FAILED,
    }
    for key in LAB_MESSAGES:
        for locale in _LOCALES:
            assert all(t.strip() for t in _texts(key, locale)), (key, locale)
        # FR and EN are distinct translations.
        assert _texts(key, "fr") != _texts(key, "en")


@settings(max_examples=100, deadline=None)
@given(
    raw=_raw_text,
    locale=st.sampled_from(_LOCALES),
    key=st.sampled_from(sorted(LAB_MESSAGES)),
)
def test_learner_messages_are_fixed_and_free_of_raw_text(raw: str, locale: str, key: str) -> None:
    texts = _texts(key, locale)
    for text in texts:
        assert raw not in text
        assert "k8s-raw" not in text
        assert "forbidden" not in text.lower()
        assert "quota" not in text.lower()
