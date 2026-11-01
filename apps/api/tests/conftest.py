"""Fixtures for the mounted-app tests; the harness lives in ``served_harness.py``."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest


def load_harness() -> ModuleType:
    """Import ``served_harness.py`` once (importlib import mode has no sibling imports)."""
    name = "served_harness"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def served() -> object:
    """A fresh Served_App over the fakes for one test."""
    return load_harness().Served()


@pytest.fixture(scope="module")
def shared_served() -> Iterator[object]:
    """One Served_App per module, for Hypothesis tests that use fresh users per example."""
    yield load_harness().Served()
