"""Unit tests for fake substitutability and clock-wiring absence (task 5.4).

These assertions formalize what the per-fake tests exercise by example:

- Req 5.2: every Fake_Implementation implements *every* method of its domain
  port and is substitutable for the production implementation without changing
  domain code. We verify this two ways: structurally via ``isinstance`` against
  each ``runtime_checkable`` port Protocol, and by name — every public method the
  port declares is present and callable on the fake with a matching signature.
- Req 5.3: the development Mailer (``Console_Mailer``) retains the mail it sends
  in a retrievable record for test inspection.
- Req 5.4: dev/test use the ``LLMProvider`` fake and make no network call — the
  fake completes without touching a socket.
- Req 6.3: no production composition root exposes an interface that sets or
  advances the Clock. The production composition root now exists (task 7), so we
  inspect it directly: :func:`build_ports` for the ``production`` environment
  wires the read-only ``System_Clock`` (no ``set`` / ``advance``) and never the
  controllable ``Test_Clock``. We also keep the surrounding guards — the domain
  ``Clock`` port exposes no mutating method, the only set/advance surface lives
  under ``edu_it_infrastructure.fakes``, and no production app module imports or
  re-exports the ``Test_Clock``.
"""

from __future__ import annotations

import ast
import inspect
import io
import socket
from pathlib import Path
from typing import Protocol, get_type_hints

import pytest
from edu_it_domain.ports import (
    Clock,
    LLMProvider,
    Mailer,
    ObjectStorage,
    OutboundMail,
    TaskQueue,
)
from edu_it_domain.ports.attempts import AttemptRepository
from edu_it_domain.ports.catalog import CatalogRepository
from edu_it_domain.ports.enrollment import EnrollmentRepository
from edu_it_domain.ports.lab_check import LabCheck
from edu_it_domain.ports.mastery import MasteryRepository
from edu_it_domain.ports.progress import ProgressRepository
from edu_it_domain.ports.project_reviewer import ProjectReviewer
from edu_it_domain.ports.review import ReviewRepository
from edu_it_infrastructure.fakes import (
    Console_Mailer,
    Fake_LabCheck,
    Fake_LLMProvider,
    Fake_ProjectReviewer,
    In_Memory_AttemptRepository,
    In_Memory_CatalogRepository,
    In_Memory_EnrollmentRepository,
    In_Memory_MasteryRepository,
    In_Memory_ObjectStorage,
    In_Memory_ProgressRepository,
    In_Memory_ReviewRepository,
    In_Memory_TaskQueue,
    Test_Clock,
)

# Each domain port paired with the fake that must be substitutable for it (Req 5.2).
PORT_FAKE_PAIRS: tuple[tuple[type, object], ...] = (
    (Clock, Test_Clock()),
    (Mailer, Console_Mailer()),
    (ObjectStorage, In_Memory_ObjectStorage()),
    (TaskQueue, In_Memory_TaskQueue()),
    (LLMProvider, Fake_LLMProvider()),
    # Learning extension points and repositories (task 1.4).
    (ProjectReviewer, Fake_ProjectReviewer()),
    (LabCheck, Fake_LabCheck()),
    (CatalogRepository, In_Memory_CatalogRepository()),
    (EnrollmentRepository, In_Memory_EnrollmentRepository()),
    (ProgressRepository, In_Memory_ProgressRepository()),
    (MasteryRepository, In_Memory_MasteryRepository()),
    (ReviewRepository, In_Memory_ReviewRepository()),
    (AttemptRepository, In_Memory_AttemptRepository()),
)


def _port_method_names(port: type) -> tuple[str, ...]:
    """Return the names of the methods a port Protocol declares.

    These are the public callables defined on the Protocol itself (excluding the
    dunder/typing machinery), i.e. exactly the methods a fake must implement to be
    substitutable without changing domain code (Req 5.2).
    """
    members = getattr(port, "__protocol_attrs__", None)
    if members is None:
        # Fall back to the Protocol's own annotations/callables when the private
        # ``__protocol_attrs__`` set is unavailable across typing versions.
        members = {
            name
            for name, value in vars(port).items()
            if callable(value) and not name.startswith("_")
        }
    return tuple(sorted(name for name in members if not name.startswith("_")))


# --- Req 5.2: substitutability (structural + by-method) ----------------------


@pytest.mark.parametrize(
    ("port", "fake"),
    PORT_FAKE_PAIRS,
    ids=[port.__name__ for port, _ in PORT_FAKE_PAIRS],
)
def test_fake_is_structurally_substitutable_for_its_port(port: type, fake: object) -> None:
    # Substitutable for the production implementation without changing domain
    # code: the fake satisfies the runtime_checkable port Protocol (Req 5.2).
    assert isinstance(fake, port)


@pytest.mark.parametrize(
    ("port", "fake"),
    PORT_FAKE_PAIRS,
    ids=[port.__name__ for port, _ in PORT_FAKE_PAIRS],
)
def test_fake_implements_every_port_method(port: type, fake: object) -> None:
    # Req 5.2: each fake implements *every* method the port declares, and each is
    # a bound callable on the fake (not merely inherited as the Protocol stub).
    method_names = _port_method_names(port)
    assert method_names, f"{port.__name__} declares no methods to check"
    for name in method_names:
        assert hasattr(fake, name), f"{type(fake).__name__} is missing port method {name!r}"
        assert callable(getattr(fake, name)), f"{type(fake).__name__}.{name} is not callable"


@pytest.mark.parametrize(
    ("port", "fake"),
    PORT_FAKE_PAIRS,
    ids=[port.__name__ for port, _ in PORT_FAKE_PAIRS],
)
def test_fake_method_signatures_match_the_port(port: type, fake: object) -> None:
    # Req 5.2: a fake is substitutable only if each method accepts the same
    # parameters the port declares. Compare the parameter names of every port
    # method against the fake's implementation.
    # Compare unbound signatures from each class so ``self`` is present on both
    # sides; a bound instance method would drop ``self`` and skew the comparison.
    fake_type = type(fake)
    for name in _port_method_names(port):
        port_params = list(inspect.signature(getattr(port, name)).parameters)
        fake_params = list(inspect.signature(getattr(fake_type, name)).parameters)
        assert fake_params == port_params, (
            f"{fake_type.__name__}.{name}{tuple(fake_params)} does not match "
            f"{port.__name__}.{name}{tuple(port_params)}"
        )


# --- Req 5.3: the dev Mailer retains sent mail -------------------------------


def test_dev_mailer_retains_sent_mail_in_a_retrievable_record() -> None:
    # Req 5.3: the development Mailer retains each sent mail for test inspection.
    mailer = Console_Mailer(stream=io.StringIO())
    first = OutboundMail(to="a@example.com", subject="Verify", body="Hello")
    second = OutboundMail(to="b@example.com", subject="Reset", body="World")
    mailer.send(first)
    mailer.send(second)
    # The record is retrievable and holds exactly what was sent, in order.
    assert mailer.sent == (first, second)
    assert mailer.last() is second


# --- Req 5.4: the LLMProvider fake makes no network call ----------------------


def test_llm_provider_fake_makes_no_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    # Req 5.4: dev/test use the LLMProvider fake and make no network call. Trip a
    # guard if the fake opens a socket or connects while completing.
    def _forbid_socket(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Fake_LLMProvider must not open a network socket (Req 5.4)")

    def _forbid_connect(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Fake_LLMProvider must not make a network connection (Req 5.4)")

    monkeypatch.setattr(socket, "socket", _forbid_socket)
    monkeypatch.setattr(socket, "create_connection", _forbid_connect)

    provider = Fake_LLMProvider()
    result = provider.complete("generate a lesson", model="fake-model")

    # The call completed locally and produced usable text with provenance.
    assert result.text
    assert result.model == "fake-model"
    assert provider.calls == (("generate a lesson", "fake-model"),)


# --- Req 6.3: no production composition root sets/advances the clock ----------

_APPS_DIR = Path(__file__).resolve().parents[3] / "apps"


def _production_python_files() -> list[Path]:
    """Every production Python module under the app packages (excluding tests)."""
    files: list[Path] = []
    for app in ("api", "worker"):
        pkg_root = _APPS_DIR / app
        for path in pkg_root.rglob("*.py"):
            parts = set(path.parts)
            if "tests" in parts or "__pycache__" in parts:
                continue
            files.append(path)
    return files


def test_domain_clock_port_exposes_no_clock_setting_method() -> None:
    # Req 6.3: the production-facing Clock contract carries no set/advance surface,
    # so wiring the production Clock cannot expose a clock-setting interface. The
    # mutating surface lives only on the Test_Clock fake.
    port_methods = _port_method_names(Clock)
    assert "now" in port_methods
    assert "set" not in port_methods
    assert "advance" not in port_methods


def test_test_clock_is_the_only_set_advance_surface() -> None:
    # The set/advance-capable interface is the Test_Clock fake, confined to the
    # infrastructure fakes package (not the domain port, not a production app).
    assert callable(Test_Clock().set)
    assert callable(Test_Clock().advance)
    assert Test_Clock.__module__ == "edu_it_infrastructure.fakes.clock"


def test_no_production_app_module_imports_the_test_clock() -> None:
    # Req 6.3 (assumption documented above): with no production composition root
    # yet, assert the set/advance-capable Test_Clock is never imported or
    # re-exported by a production app package, so no production code path can wire
    # a clock-setting interface.
    offenders: list[str] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported = {alias.name for alias in node.names}
                if "Test_Clock" in imported or node.module.endswith("fakes.clock"):
                    offenders.append(str(path))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.endswith("fakes.clock"):
                        offenders.append(str(path))
    assert not offenders, (
        "production app modules must not import the set/advance-capable Test_Clock "
        f"(Req 6.3): {offenders}"
    )


def test_production_composition_root_wires_the_read_only_system_clock() -> None:
    # The production composition root now exists (task 7), so the Req 6.3 guard is
    # the stronger direct inspection the original stub promised: build the
    # production Ports and assert the wired Clock is the read-only System_Clock,
    # which exposes no set/advance surface, and is never the controllable
    # Test_Clock. (App boundary modules such as app.py/health.py are HTTP wiring,
    # not clock wiring; the clock-import guard above covers them.)
    from edu_it_infrastructure.clock import System_Clock
    from edu_it_infrastructure.composition import (
        CompositionSettings,
        Environment,
        build_ports,
    )

    production = build_ports(CompositionSettings(environment=Environment.PRODUCTION))
    assert isinstance(production.clock, System_Clock)
    assert not isinstance(production.clock, Test_Clock)
    assert not hasattr(production.clock, "set")
    assert not hasattr(production.clock, "advance")


def test_clock_type_hints_resolve_for_substitutability() -> None:
    # Sanity: the Clock Protocol's own annotations resolve, so the isinstance
    # substitutability checks above are exercising a well-formed Protocol.
    assert issubclass(Clock, Protocol)  # type: ignore[arg-type]
    hints = get_type_hints(Clock.now)
    assert "return" in hints
