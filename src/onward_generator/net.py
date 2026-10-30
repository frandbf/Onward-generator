"""The one HTTPS client used by every explicit download.

Each call names the hosts it may reach, caps the response size, and maps
HTTP and network failures to the caller's own error type. TLS always uses
the certifi CA bundle, so verification does not depend on the system store.
Error messages carry the URL without its query string, so signed download
tokens never reach a log or a review file. The start of an HTTP error body
is appended only when the caller asks for it with ``error_body=True``: an
Overpass or Catastro error page explains the failure, but a raw body from
another service must not land in user output or a review file.
"""

from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, Protocol
from urllib.parse import urlparse

import certifi

CHUNK_BYTES = 256 * 1024
MAX_RETRY_DELAY_S = 90.0


class Response(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...

    def __enter__(self) -> Response: ...

    def __exit__(self, *exc: object) -> Any: ...


Opener = Callable[[urllib.request.Request, float], Response]
"""Opens a request with a timeout. Tests pass a fake; the default uses certifi TLS."""


def tls_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _default_opener(request: urllib.request.Request, timeout: float) -> Response:
    return urllib.request.urlopen(request, timeout=timeout, context=tls_context())  # noqa: S310 - https only, host checked


def redact(url: str) -> str:
    """``scheme://host/path`` without the query or fragment."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _read_capped(response: Response, max_bytes: int, url: str, error: type[Exception]) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        block = response.read(CHUNK_BYTES)
        if not block:
            return b"".join(chunks)
        total += len(block)
        if total > max_bytes:
            raise error(f"{redact(url)}: larger than {max_bytes} bytes")
        chunks.append(block)


def fetch_bytes(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    max_bytes: int,
    timeout: float,
    headers: Mapping[str, str],
    data: bytes | None = None,
    retry_429: int = 0,
    error: type[Exception] = OSError,
    retry_delay_s: float = 8.0,
    error_body: bool = False,
    opener: Opener = _default_opener,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    """GET ``url``, or POST ``data``, and return at most ``max_bytes`` bytes.

    Only ``https`` URLs on ``allowed_hosts`` are requested. HTTP 429 is
    retried ``retry_429`` times, doubling the wait from ``retry_delay_s`` up
    to 90 s. Every failure raises ``error`` with the cause chained. With
    ``error_body`` the message of an HTTP error ends with up to 400 bytes
    of the response body; without it the message names only status and URL.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise error(f"refusing to fetch host {parsed.hostname!r} over {parsed.scheme or 'no scheme'}")
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    safe = redact(url)
    delay = retry_delay_s
    retries = 0
    while True:
        request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST" if data is not None else "GET")  # pragma: no mutate: only dropping the method survives, urllib infers the same one from data; test_net_callers pins the rest
        try:
            with opener(request, timeout) as response:
                return _read_capped(response, max_bytes, url, error)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and retries < retry_429:
                retries += 1
                sleep(delay)
                delay = min(delay * 2, MAX_RETRY_DELAY_S)
                continue
            if exc.code == 429:
                raise error(f"rate limited: HTTP 429 for {safe}") from exc
            detail = exc.read(400).decode(errors="replace").strip() if error_body else ""
            raise error(f"HTTP {exc.code} for {safe}" + (f": {detail}" if detail else "")) from exc
        except urllib.error.URLError as exc:
            raise error(f"request to {safe} failed: {exc.reason}") from exc
        except (TimeoutError, ConnectionError) as exc:
            raise error(f"request to {safe} failed: {exc}") from exc
