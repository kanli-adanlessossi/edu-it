"""Live HTTPS transport (ai-content Req 1.1, 1.7, 1.8, 2.2).

A :class:`ModelTransport` over the standard-library HTTP client, so no new
dependency enters ADR 0001. It owns the mechanics only: connection, TLS,
timeouts, the EU endpoint host binding, redirect policy, cost from configured
prices, and record mode. The wire format belongs to a :class:`VendorAdapter`
obtained from the registry; this module invents none.

Host binding. The endpoint host must be in ``allowed_hosts`` at construction.
A redirect is followed only to another allowed host. A redirect to any other
host is not followed and no request is sent there: the transport reports that
host as ``origin_host`` with no credential, which the engine rejects as a
binding failure. The reported ``credential_id`` is the fingerprint of the key
this transport handed to the adapter.

Credential. The key comes from a :class:`CredentialSource`, read on first use.
After an HTTP 401 or 403 it is re-read once and that attempt is retried once; a
second 401 or 403 is ``ProviderUnavailableError``.

Status class. Any other non-2xx reply is handed to the adapter, which classifies it: a
``VendorProtocolError`` (configuration fault, HTTP 400 or 404) reaches the caller unchanged and
is not retried; ``ProviderUnavailableError`` stays an outage. The fingerprint follows the
key in use, so a rotation is not a binding failure.

Record mode. Every exchange is written through :class:`FixtureStore` after its
privacy scan. Attempt 1 of a request starts a fresh fixture, so a re-recorded
key never keeps stale attempts from an earlier run.
"""

from __future__ import annotations

import http.client
import ssl
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from edu_it_domain.ai.fixtures import Fixture, FixtureAttempt, fixture_key
from edu_it_domain.ports.llm_provider import ProviderBindingError, ProviderUnavailableError

from edu_it_infrastructure.llm.credentials import (
    CredentialSource,
    CredentialUnavailableError,
    credential_fingerprint,
)
from edu_it_infrastructure.llm.fixture_store import FixtureStore
from edu_it_infrastructure.llm.registry import (
    HttpRequest,
    VendorAdapter,
    VendorProtocolError,
)
from edu_it_infrastructure.llm.transport import (
    ModelTransport,
    RawCompletion,
    TransportRequest,
    TransportTimeoutError,
)

__all__ = ["LiveTransport", "ModelPrice", "credential_fingerprint"]

_MAX_REDIRECTS = 3
_MAX_BODY_BYTES = 8 * 1024 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_AUTH_REFUSED = frozenset({401, 403})
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """Price of one model in cents per million tokens."""

    input_cents_per_mtok: int
    output_cents_per_mtok: int

    def cost_cents(self, input_tokens: int, output_tokens: int) -> int:
        """Cost of one call, rounded up to a whole cent."""
        micro = (
            input_tokens * self.input_cents_per_mtok + output_tokens * self.output_cents_per_mtok
        )
        return -(-micro // 1_000_000)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").casefold()


class LiveTransport(ModelTransport):
    """One model call per :meth:`complete`, over HTTPS, through a vendor adapter."""

    def __init__(
        self,
        *,
        adapter: VendorAdapter,
        endpoint: str,
        credentials: CredentialSource,
        allowed_hosts: Collection[str],
        prices: Mapping[str, ModelPrice],
        record_to: FixtureStore | None = None,
        allow_plain_http_loopback: bool = False,
    ) -> None:
        """``allow_plain_http_loopback`` exists for loopback stub servers in tests only."""
        self._allowed = frozenset(h.casefold() for h in allowed_hosts)
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").casefold()
        if host not in self._allowed:
            raise ProviderBindingError(f"endpoint host {host!r} is not an allowed provider host")
        plain_ok = allow_plain_http_loopback and host in _LOOPBACK_HOSTS
        if parts.scheme != "https" and not (parts.scheme == "http" and plain_ok):
            raise ProviderBindingError("the live provider endpoint must use https")
        self._adapter = adapter
        self._endpoint = endpoint
        self._credentials = credentials
        self._prices = dict(prices)
        self._store = record_to
        self._plain_ok = plain_ok

    @property
    def credential_id(self) -> str | None:
        """Fingerprint of the key currently in use; ``None`` before the first read."""
        return self._credentials.fingerprint

    def complete(self, request: TransportRequest) -> RawCompletion:
        price = self._prices.get(request.model)
        if price is None:  # a zero cost would silently defeat the budgets
            raise ProviderUnavailableError(f"no price configured for model {request.model!r}")
        api_key = self._read_key(refresh=False)
        outcome = self._exchange(request, api_key)
        if isinstance(outcome, RawCompletion):
            return outcome
        if outcome[0] in _AUTH_REFUSED:
            # The key may have been rotated: re-read it once and retry this attempt once.
            api_key = self._read_key(refresh=True)
            outcome = self._exchange(request, api_key)
            if isinstance(outcome, RawCompletion):
                return outcome
            if outcome[0] in _AUTH_REFUSED:
                raise ProviderUnavailableError(
                    f"live provider answered HTTP {outcome[0]} after re-reading the credential"
                )
        status, body, url = outcome
        credential_id = credential_fingerprint(api_key)

        if not 200 <= status < 300:
            # The adapter classifies: ``VendorProtocolError`` is a configuration fault (400,
            # 404) and propagates so the engine does not retry it; ``ProviderUnavailableError``
            # propagates as is. A non-2xx reply is never read as a success.
            self._adapter.parse_response(status, body)
            raise ProviderUnavailableError(f"live provider answered HTTP {status}")
        try:
            reply = self._adapter.parse_response(status, body)
        except VendorProtocolError as err:
            raise ProviderUnavailableError(f"unreadable live provider response: {err}") from err

        completion = RawCompletion(
            text=reply.text,
            model=reply.model,
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            cost_cents=price.cost_cents(reply.input_tokens, reply.output_tokens),
            origin_host=_host(url),
            credential_id=credential_id,
        )
        self._record(request, completion)
        return completion

    def _read_key(self, *, refresh: bool) -> str:
        try:
            key = self._credentials.refresh() if refresh else self._credentials.current()
        except CredentialUnavailableError as err:
            raise ProviderUnavailableError(f"live provider credential unavailable: {err}") from err
        if not key:
            raise ProviderUnavailableError("live provider credential is empty")
        return key

    def _exchange(
        self, request: TransportRequest, api_key: str
    ) -> RawCompletion | tuple[int, bytes, str]:
        """Send one request, following allowed redirects.

        Returns ``(status, body, final_url)``, or a credential-less completion
        when a redirect leaves the allowed hosts (the engine rejects that).
        """
        http_request = self._adapter.build_request(
            request, endpoint=self._endpoint, api_key=api_key
        )
        url = http_request.url
        for _ in range(_MAX_REDIRECTS + 1):
            host = _host(url)
            if host not in self._allowed:
                return RawCompletion("", request.model, 0, 0, 0, origin_host=host)
            status, headers, body = self._send(http_request, url, request)
            if status in _REDIRECT_STATUSES and (location := headers.get("location")):
                url = urljoin(url, location)
                continue
            return status, body, url
        raise ProviderUnavailableError("too many redirects from the live provider")

    def _send(
        self, http_request: HttpRequest, url: str, request: TransportRequest
    ) -> tuple[int, dict[str, str], bytes]:
        parts = urlsplit(url)
        timeout = request.timeout.total_seconds()
        connection: http.client.HTTPConnection
        if parts.scheme == "https":
            connection = http.client.HTTPSConnection(
                parts.hostname or "",
                parts.port,
                timeout=timeout,
                context=ssl.create_default_context(),
            )
        elif parts.scheme == "http" and self._plain_ok:
            connection = http.client.HTTPConnection(
                parts.hostname or "", parts.port, timeout=timeout
            )
        else:
            raise ProviderBindingError("refusing a non-https redirect target")
        target = parts.path or "/"
        if parts.query:
            target += "?" + parts.query
        try:
            connection.request(
                http_request.method,
                target,
                body=http_request.body,
                headers=dict(http_request.headers),
            )
            response = connection.getresponse()
            body = response.read(_MAX_BODY_BYTES + 1)
            headers = {k.casefold(): v for k, v in response.getheaders()}
            status = response.status
        except TimeoutError as err:
            raise TransportTimeoutError(str(err)) from err
        except (OSError, http.client.HTTPException) as err:
            raise ProviderUnavailableError(f"live provider unreachable: {err}") from err
        finally:
            connection.close()
        if len(body) > _MAX_BODY_BYTES:
            raise ProviderUnavailableError("live provider response is too large")
        return status, headers, body

    def _record(self, request: TransportRequest, completion: RawCompletion) -> None:
        if self._store is None:
            return
        attempt = FixtureAttempt(
            text=completion.text,
            model=completion.model,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
        )
        if request.attempt > 1:
            self._store.record_attempt(request.prompt, request.input, attempt)
            return
        self._store.write(
            Fixture(
                key=fixture_key(request.prompt, request.input),
                prompt_id=request.prompt.prompt_id,
                prompt_version=request.prompt.prompt_version,
                request={"kind": request.input.kind, "fields": dict(request.input.fields)},
                attempts=(attempt,),
            )
        )
