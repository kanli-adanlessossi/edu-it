"""Property 15: Message limits (task 10.3).

Feature: hands-on-labs, Property 15: Message limits

**Validates: Requirements 12.9**

All numbers come from the domain config. The limiter is the unit the terminal
gateway's reader delegates to (``verdict.value`` is the close code).
"""

from __future__ import annotations

from datetime import timedelta

from edu_it_domain import config
from edu_it_domain.labs.limits import MessageLimiter, MessageVerdict
from edu_it_infrastructure.fakes import Test_Clock
from hypothesis import given, settings
from hypothesis import strategies as st

MAX_BYTES = config.TERMINAL_MAX_MESSAGE_BYTES
RATE = config.TERMINAL_MAX_MESSAGES_PER_SECOND

_SETTINGS = settings(max_examples=200, deadline=None)

# (size in bytes, gap in milliseconds since the previous message)
_sizes = st.one_of(
    st.integers(0, MAX_BYTES),
    st.integers(MAX_BYTES + 1, MAX_BYTES * 4),
    st.sampled_from([0, MAX_BYTES - 1, MAX_BYTES, MAX_BYTES + 1]),
)
_messages = st.lists(st.tuples(_sizes, st.integers(0, 3)), min_size=1, max_size=3 * RATE)


def _run(messages: list[tuple[int, int]]) -> list[tuple[timedelta, int, MessageVerdict]]:
    clock = Test_Clock()
    limiter = MessageLimiter(clock)
    start = clock.now()
    out = []
    for size, gap_ms in messages:
        clock.advance(timedelta(milliseconds=gap_ms))
        out.append((clock.now() - start, size, limiter.check(size)))
    return out


@_SETTINGS
@given(_messages)
def test_oversized_message_is_never_accepted(messages: list[tuple[int, int]]) -> None:
    """Feature: hands-on-labs, Property 15: Message limits (size)."""
    for _, size, verdict in _run(messages):
        if size > MAX_BYTES:
            assert verdict is MessageVerdict.TOO_BIG
            assert verdict.value == 1009
        else:
            assert verdict is not MessageVerdict.TOO_BIG


@_SETTINGS
@given(_messages)
def test_accepted_messages_stay_within_token_bucket_envelope(
    messages: list[tuple[int, int]],
) -> None:
    """Feature: hands-on-labs, Property 15: Message limits (rate envelope).

    Over any span of ``w`` seconds the limiter accepts at most the bucket
    capacity plus the refill (``RATE + RATE * w``), and a rejection is always
    1008 (never an accepted message).
    """
    results = _run(messages)
    accepted = [t for t, _, v in results if v is MessageVerdict.OK]
    for _, _, verdict in results:
        assert verdict in (MessageVerdict.OK, MessageVerdict.TOO_BIG, MessageVerdict.RATE_EXCEEDED)
    for i, start in enumerate(accepted):
        for j in range(i, len(accepted)):
            span = (accepted[j] - start).total_seconds()
            assert (j - i + 1) <= RATE + RATE * span + 1e-6
