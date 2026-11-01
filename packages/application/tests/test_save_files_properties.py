"""Property test for learner file caps and path safety (hands-on-labs task 6.7).

Feature: hands-on-labs, Property 21: Learner file caps and path safety

Drives the real ``SaveFilesUseCase`` over ``Fake_SandboxProvider`` and in-memory
repositories.
"""

from __future__ import annotations

import pytest
from edu_it_application.labs import LabFilesRejectedError, SaveFilesUseCase
from edu_it_domain import config
from edu_it_domain.labs.models import LabSession, SandboxSpec, SessionStatus
from edu_it_domain.ports.lab_catalog import LabDefinition, LabTaskDefinition
from edu_it_infrastructure.fakes import (
    Fake_LabCatalog,
    Fake_SandboxProvider,
    In_Memory_GradingRunRepository,
    In_Memory_LabSessionRepository,
    Test_Clock,
)
from hypothesis import given, settings
from hypothesis import strategies as st

_MAX_COUNT = config.LAB_FILE_MAX_COUNT
_MAX_BYTES = config.LAB_FILE_MAX_BYTES

# More allowed names than the count cap, so the count rule is reachable alone.
_ALLOWED = tuple(f"f{i}.py" for i in range(_MAX_COUNT + 3))
_LAB = LabDefinition(
    lab_id="lab1",
    kind="code",
    image="img",
    concept_ids=(),
    tasks=(LabTaskDefinition("t1", required=True, hint_count=0),),
    checks=(),
    learner_files=_ALLOWED,
)

_BAD_NAMES = (
    "other.py",
    "../f0.py",
    "..",
    ".",
    "",
    "sub/f0.py",
    "/etc/passwd",
    "sub\\f0.py",
    "f0.py\x00",
    "F0.PY",
)
_NAMES = st.one_of(st.sampled_from(_ALLOWED), st.sampled_from(_BAD_NAMES))
_SIZES = st.sampled_from([0, 1, 100, _MAX_BYTES - 1, _MAX_BYTES, _MAX_BYTES + 1])
_files = st.dictionaries(
    _NAMES, _SIZES, max_size=_MAX_COUNT + 3
)  # name -> size; content is built from the size


def _is_safe_allowed(name: str) -> bool:
    flat = name not in {"", ".", ".."} and not any(c in name for c in "/\\\x00")
    return flat and name in _ALLOWED


def _expect_accept(sizes: dict[str, int]) -> bool:
    return (
        len(sizes) <= _MAX_COUNT
        and all(size <= _MAX_BYTES for size in sizes.values())
        and all(_is_safe_allowed(name) for name in sizes)
    )


def _setup() -> tuple[SaveFilesUseCase, Fake_SandboxProvider, In_Memory_LabSessionRepository]:
    clock = Test_Clock()
    sessions = In_Memory_LabSessionRepository(In_Memory_GradingRunRepository())
    sandbox = Fake_SandboxProvider(clock)
    handle = sandbox.provision(
        SandboxSpec("s1", "img", "learner", False, 1, 1, 1, 1, True, True, True, (), ())
    )
    now = clock.now()
    sessions.add_if_no_active(
        LabSession(
            stable_id="s1",
            user_id="u1",
            lab_id="lab1",
            status=SessionStatus.READY,
            created_at=now,
            ttl_at=now + config.LAB_SESSION_TTL,
            sandbox_ref=handle.sandbox_id,
        )
    )
    use_case = SaveFilesUseCase(
        sessions=sessions,
        catalog=Fake_LabCatalog([_LAB]),
        sandbox=sandbox,
        clock=clock,
        provider_name="fake",
    )
    return use_case, sandbox, sessions


# Feature: hands-on-labs, Property 21: Learner file caps and path safety
# **Validates: Requirements 14.2, 14.6, 14.7**
@settings(max_examples=200, deadline=None)
@given(sizes=_files)
def test_property_21_learner_file_caps_and_path_safety(sizes: dict[str, int]) -> None:
    """Feature: hands-on-labs, Property 21: Learner file caps and path safety.

    **Validates: Requirements 14.2, 14.6, 14.7**
    """
    use_case, sandbox, sessions = _setup()
    files = {name: bytes(size) for name, size in sizes.items()}

    if _expect_accept(sizes):
        assert use_case.execute("u1", "s1", files) == len(files)
        stored = {
            path: data for box in sandbox._sandboxes.values() for path, data in box.files.items()
        }
        assert stored == {f"/workspace/{n}": d for n, d in files.items()}
    else:
        with pytest.raises(LabFilesRejectedError):
            use_case.execute("u1", "s1", files)
        assert "copy_in" not in sandbox.calls
        assert all(not box.files for box in sandbox._sandboxes.values())
        assert sessions.get("s1").last_activity_at is None  # type: ignore[union-attr]
