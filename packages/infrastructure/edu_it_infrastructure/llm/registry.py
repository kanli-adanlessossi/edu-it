"""Vendor adapter contract and registry (ai-content Req 1.7, 1.10).

The generic live transport (:mod:`edu_it_infrastructure.llm.live`) owns the
HTTPS mechanics, the host binding and record mode. It knows no wire format: it
asks a registered :class:`VendorAdapter` to build the HTTP request and to parse
the HTTP response.

A vendor module under ``llm/vendors/`` registers itself by name at import time
and edits no existing file::

    @register_vendor("example")
    def _factory() -> VendorAdapter: ...

:func:`load_vendors` imports every module in the ``vendors`` package, so a new
module is discovered without a central list.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from edu_it_infrastructure.llm.transport import TransportRequest

__all__ = [
    "HttpRequest",
    "UnknownVendorError",
    "VendorAdapter",
    "VendorFactory",
    "VendorProtocolError",
    "VendorReply",
    "get_vendor",
    "load_vendors",
    "register_vendor",
    "registered_vendors",
]


class UnknownVendorError(LookupError):
    """No vendor adapter is registered under the requested name."""


class VendorProtocolError(ValueError):
    """The vendor response could not be understood (malformed envelope)."""


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """One HTTP request, as built by a vendor adapter."""

    url: str
    body: bytes
    headers: Mapping[str, str] = field(default_factory=dict)
    method: str = "POST"


@dataclass(frozen=True, slots=True)
class VendorReply:
    """Model text and token usage extracted from a vendor response."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int


@runtime_checkable
class VendorAdapter(Protocol):
    """Maps a :class:`TransportRequest` to HTTP and an HTTP body to a reply."""

    def build_request(
        self, request: TransportRequest, *, endpoint: str, api_key: str
    ) -> HttpRequest:
        """Build the HTTP request. ``api_key`` is the only credential to send."""
        ...

    def parse_response(self, status: int, body: bytes) -> VendorReply:
        """Parse a 2xx response body; raise :class:`VendorProtocolError` when malformed."""
        ...


VendorFactory = Callable[[], VendorAdapter]

_REGISTRY: dict[str, VendorFactory] = {}


def register_vendor(name: str) -> Callable[[VendorFactory], VendorFactory]:
    """Decorator registering ``factory`` under ``name``; a duplicate name is an error."""

    def decorate(factory: VendorFactory) -> VendorFactory:
        if not name or name != name.strip().lower():
            raise ValueError("vendor name must be non-empty, lower-case and trimmed")
        if name in _REGISTRY:
            raise ValueError(f"vendor {name!r} is already registered")
        _REGISTRY[name] = factory
        return factory

    return decorate


def registered_vendors() -> tuple[str, ...]:
    """Names of every registered vendor, sorted."""
    return tuple(sorted(_REGISTRY))


def get_vendor(name: str) -> VendorAdapter:
    """Return a new adapter for ``name``, discovering vendor modules first."""
    if name not in _REGISTRY:
        load_vendors()
    try:
        factory = _REGISTRY[name]
    except KeyError:
        known = ", ".join(registered_vendors()) or "none"
        raise UnknownVendorError(f"unknown LLM vendor {name!r} (registered: {known})") from None
    return factory()


def load_vendors() -> None:
    """Import every module under ``edu_it_infrastructure.llm.vendors`` (idempotent)."""
    package = importlib.import_module("edu_it_infrastructure.llm.vendors")
    for info in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package.__name__}.{info.name}")
