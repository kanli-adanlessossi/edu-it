"""Property test for logs and metric labels (kubernetes-deployment task 10.4).

Property 20: Logs and metrics exclude personal data.

For any log record, the formatted output is a single-line JSON object that contains no secret
value and no terminal data; for any Lab_Metric, the label names are a subset of the allowed set
(``reason``, ``result``, ``provider``).

**Validates: Requirements 13.8, 13.11, 13.12**
"""

from __future__ import annotations

import json
import logging

from edu_it_infrastructure.telemetry import (
    JsonLogFormatter,
    PrometheusLabMetrics,
    RedactionFilter,
)
from hypothesis import given, settings
from hypothesis import strategies as st
from prometheus_client import CollectorRegistry

_ALLOWED_LABELS = frozenset({"reason", "result", "provider"})

_ALNUM = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# Distinctive values so a leak is never a coincidental substring of fixed text.
_secret_values = st.text(alphabet=_ALNUM, min_size=10, max_size=24).map(lambda s: "Zq" + s)
_terminal_values = st.text(alphabet=_ALNUM + " /-_", min_size=10, max_size=40).map(
    lambda s: "TTY" + s
)
_emails = st.builds(
    lambda local, domain: f"{local}@{domain}.org",
    st.text(alphabet=_ALNUM, min_size=3, max_size=10),
    st.text(alphabet="abcdefghij", min_size=3, max_size=8),
)

_secret_keys = st.sampled_from(
    ["password", "api_token", "client_secret", "Authorization", "session_cookie", "private_key"]
)
_terminal_keys = st.sampled_from(["terminal_data", "terminal_input", "stdin", "stdout", "pty_out"])
_personal_keys = st.sampled_from(["email", "learner_id", "user", "username", "ip_address"])
_assignment_names = st.sampled_from(["password", "token", "secret", "api_key", "authorization"])


def _emit(record: logging.LogRecord, *, with_filter: bool) -> str:
    if with_filter:
        RedactionFilter().filter(record)
    return JsonLogFormatter().format(record)


def _record(message: str, **extra: object) -> logging.LogRecord:
    return logging.makeLogRecord(
        {
            "name": "edu_it.prop",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "msg": message,
            **extra,
        }
    )


# Feature: kubernetes-deployment, Property 20: Logs and metrics exclude personal data
@settings(max_examples=100, deadline=None)
@given(
    secret=_secret_values,
    terminal=_terminal_values,
    email=_emails,
    secret_key=_secret_keys,
    terminal_key=_terminal_keys,
    personal_key=_personal_keys,
    assignment=_assignment_names,
    nested=st.booleans(),
    with_filter=st.booleans(),
    prefix=st.text(alphabet=_ALNUM + " ", max_size=20),
)
def test_property_20_log_output_is_single_line_json_without_secrets_or_terminal_data(
    secret: str,
    terminal: str,
    email: str,
    secret_key: str,
    terminal_key: str,
    personal_key: str,
    assignment: str,
    nested: bool,
    with_filter: bool,
    prefix: str,
) -> None:
    message = f"{prefix}\n{assignment}={secret} Bearer {secret}x contact {email}"
    fields: dict[str, object] = {
        secret_key: secret,
        terminal_key: terminal,
        personal_key: email,
        "http_status": 200,
    }
    extra = {"context": fields} if nested else fields
    line = _emit(_record(message, **extra), with_filter=with_filter)

    assert "\n" not in line and "\r" not in line
    payload = json.loads(line)
    assert isinstance(payload, dict)

    assert secret not in line
    assert terminal not in line
    assert email not in line
    # Non-sensitive structured data survives.
    status = payload["context"]["http_status"] if nested else payload["http_status"]
    assert status == 200


# Feature: kubernetes-deployment, Property 20: Logs and metrics exclude personal data
@settings(max_examples=100, deadline=None)
@given(secret=_secret_values, email=_emails, with_filter=st.booleans())
def test_property_20_exception_and_args_do_not_leak(
    secret: str, email: str, with_filter: bool
) -> None:
    try:
        raise ValueError(f"token={secret} for {email}")
    except ValueError:
        import sys

        record = logging.makeLogRecord(
            {
                "name": "edu_it.prop",
                "levelname": "INFO",
                "levelno": logging.INFO,
                "msg": "user %s failed",
                "args": (email,),
                "exc_info": sys.exc_info(),
            }
        )
    line = _emit(record, with_filter=with_filter)

    assert "\n" not in line
    json.loads(line)
    assert secret not in line
    assert email not in line


_label_text = st.text(max_size=20)


# Feature: kubernetes-deployment, Property 20: Logs and metrics exclude personal data
@settings(max_examples=100, deadline=None)
@given(
    calls=st.lists(
        st.tuples(
            st.sampled_from(
                [
                    "sessions",
                    "provision",
                    "failure",
                    "grading",
                    "reaper",
                    "quota",
                    "loss",
                ]
            ),
            _label_text,
            _label_text,
            st.floats(min_value=0, max_value=1000, allow_nan=False),
        ),
        max_size=15,
    )
)
def test_property_20_metric_label_names_are_within_the_allowed_set(
    calls: list[tuple[str, str, str, float]],
) -> None:
    registry = CollectorRegistry()
    metrics = PrometheusLabMetrics(registry)
    for kind, a, b, seconds in calls:
        if kind == "sessions":
            metrics.set_active_sessions(int(seconds))
        elif kind == "provision":
            metrics.observe_provision_duration(seconds, result=a, provider=b)
        elif kind == "failure":
            metrics.inc_provision_failure(reason=a)
        elif kind == "grading":
            metrics.observe_grading_duration(seconds, result=a)
        elif kind == "reaper":
            metrics.inc_reaper_deletion(reason=a)
        elif kind == "quota":
            metrics.inc_quota_rejection()
        else:
            metrics.inc_infrastructure_loss()

    for family in registry.collect():
        for sample in family.samples:
            # ``le`` is the Prometheus-defined histogram bucket bound, not a data label.
            names = set(sample.labels) - {"le"}
            assert names <= _ALLOWED_LABELS, (sample.name, names)
