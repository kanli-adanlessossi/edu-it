"""Property test for secret-refresh last-good-value semantics (task 8.3, Req 23.6)."""

from __future__ import annotations

from edu_it_infrastructure.secrets import SecretHolder
from hypothesis import given, settings
from hypothesis import strategies as st


class _ScriptedSecretSource:
    """A ``SecretSource`` whose ``read`` succeeds or fails according to a fixed script.

    Each entry in ``outcomes`` is either the value to return (success) or ``None`` to raise
    (failure), consumed one per call to ``read``.
    """

    def __init__(self, outcomes: list[str | None]) -> None:
        self._outcomes = list(outcomes)
        self._index = 0

    def read(self, key: str) -> str:
        outcome = self._outcomes[self._index]
        self._index += 1
        if outcome is None:
            raise OSError("secret unavailable")
        return outcome


_outcomes = st.lists(
    st.one_of(st.none(), st.text(min_size=1, max_size=10)),
    min_size=0,
    max_size=30,
)


# Feature: kubernetes-deployment, Property 23: Secret refresh keeps the last good value
# Claims: 23.6
# **Validates: Requirements 23.6**
@settings(max_examples=200, deadline=None)
@given(outcomes=_outcomes)
def test_refresh_keeps_last_good_value_and_never_raises(outcomes: list[str | None]) -> None:
    source = _ScriptedSecretSource(outcomes)
    holder = SecretHolder(source, "any/key")

    last_good: str | None = None
    for outcome in outcomes:
        result = holder.refresh()  # must never raise
        if outcome is None:
            assert result is False
        else:
            assert result is True
            last_good = outcome
        assert holder.value == last_good
