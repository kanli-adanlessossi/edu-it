"""Property test: Lab_Pod identity is one per session.

Feature: kubernetes-deployment, Property 3: Pod identity is one per session
**Validates: Requirements 1.4, 1.6, 20.5**
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from edu_it_domain.labs.sandbox_spec import build_sandbox_spec
from edu_it_infrastructure.sandbox.k8s_pod_spec import K8sSettings, Toleration, build_lab_pod
from hypothesis import given
from hypothesis import settings as hsettings
from hypothesis import strategies as st

_NAME_RE = re.compile(r"^lab-[0-9a-f]{20}$")


@dataclass(frozen=True)
class _Lab:
    image: str = "registry.example/lab@sha256:abc"


_session_ids = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=64
)
_label_text = st.text(alphabet="abcdefghij-", min_size=1, max_size=12)


@st.composite
def _k8s_settings(draw: st.DrawFn) -> K8sSettings:
    kind = draw(st.sampled_from(["gvisor", "kata", "runc"]))
    runtime_class = None if kind == "runc" else draw(_label_text)
    return K8sSettings(
        namespace=draw(_label_text),
        runtime_class_name=runtime_class,
        runtime_kind=kind,  # type: ignore[arg-type]
        node_selector=draw(st.dictionaries(_label_text, _label_text, max_size=3)),
        tolerations=draw(
            st.lists(
                st.builds(
                    Toleration,
                    key=_label_text,
                    operator=st.just("Equal"),
                    value=_label_text,
                    effect=st.just("NoSchedule"),
                ),
                max_size=3,
            ).map(tuple)
        ),
        node_loss_toleration_seconds=draw(st.integers(min_value=0, max_value=3600)),
        grader_gid=draw(st.integers(min_value=1, max_value=65535)),
        priority_class_name=draw(st.one_of(st.none(), _label_text)),
    )


def _pod(session_id: str, settings: K8sSettings) -> dict:
    return build_lab_pod(build_sandbox_spec(_Lab(), session_id), settings)


# Claims: 1.4, 1.6, 20.5
@hsettings(max_examples=100, deadline=None)
@given(session_id=_session_ids, first=_k8s_settings(), second=_k8s_settings())
def test_same_session_gives_same_name_and_labels_across_settings(
    session_id: str, first: K8sSettings, second: K8sSettings
) -> None:
    a = _pod(session_id, first)["metadata"]
    b = _pod(session_id, second)["metadata"]
    assert a["name"] == b["name"]
    assert a["labels"] == b["labels"]
    # Repeating the same call is deterministic too.
    again = _pod(session_id, first)["metadata"]
    assert again["name"] == a["name"]
    assert again["labels"] == a["labels"]


# Claims: 1.4, 1.6, 20.5
@hsettings(max_examples=100, deadline=None)
@given(a=_session_ids, b=_session_ids, settings=_k8s_settings())
def test_different_sessions_give_different_names(a: str, b: str, settings: K8sSettings) -> None:
    name_a = _pod(a, settings)["metadata"]["name"]
    name_b = _pod(b, settings)["metadata"]["name"]
    assert (name_a == name_b) == (a == b)


# Claims: 1.4, 1.6, 20.5
@hsettings(max_examples=100, deadline=None)
@given(session_id=_session_ids, settings=_k8s_settings())
def test_name_format_and_labels_carry_managed_and_session(
    session_id: str, settings: K8sSettings
) -> None:
    meta = _pod(session_id, settings)["metadata"]
    assert _NAME_RE.fullmatch(meta["name"])
    assert meta["labels"]["edu-it.managed"] == "1"
    assert meta["labels"]["edu-it.session"] == session_id
