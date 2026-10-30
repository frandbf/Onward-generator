"""The shared HTTPS client, driven by a fake opener. No network."""

from __future__ import annotations

import io
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable

import pytest

from onward_generator.net import fetch_bytes, redact, tls_context

HOSTS = frozenset({"example.org"})
URL = "https://example.org/data?token=secret"


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = io.BytesIO(body)

    def read(self, size: int = -1, /) -> bytes:
        return self._body.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class FetchFailed(ValueError):
    pass


FakeOpener = Callable[[urllib.request.Request, float], FakeResponse]


def _http_error(code: int, body: bytes = b"") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(URL, code, "error", {}, io.BytesIO(body))  # type: ignore[arg-type]


def _opener(*outcomes: bytes | Exception) -> tuple[FakeOpener, list[urllib.request.Request]]:
    """Returns each outcome in turn: bytes become a response, an exception is raised."""
    queue = list(outcomes)
    seen: list[urllib.request.Request] = []

    def open_(request: urllib.request.Request, timeout: float) -> FakeResponse:
        assert timeout == 5.0
        seen.append(request)
        outcome = queue.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    return open_, seen


def _fetch(opener: FakeOpener, **kwargs: object) -> bytes:
    options: dict[str, object] = {
        "allowed_hosts": HOSTS,
        "max_bytes": 1024,
        "timeout": 5.0,
        "headers": {"User-Agent": "test"},
        "error": FetchFailed,
        "opener": opener,
    }
    options.update(kwargs)
    return fetch_bytes(URL, **options)  # type: ignore[arg-type]


def test_fetch_bytes_returns_body_and_sends_headers() -> None:
    opener, seen = _opener(b"hello")
    assert _fetch(opener) == b"hello"
    [request] = seen
    assert request.get_method() == "GET"
    assert request.get_header("User-agent") == "test"


def test_fetch_bytes_posts_data() -> None:
    opener, seen = _opener(b"ok")
    assert _fetch(opener, data=b"q=1") == b"ok"
    assert seen[0].get_method() == "POST"
    assert seen[0].data == b"q=1"


@pytest.mark.parametrize(
    "url",
    ["https://evil.example/data", "http://example.org/data", "file:///etc/passwd", "example.org/data"],
)
def test_fetch_bytes_refuses_host(url: str) -> None:
    opener, seen = _opener(b"never")
    with pytest.raises(FetchFailed, match="refusing to fetch host"):
        fetch_bytes(url, allowed_hosts=HOSTS, max_bytes=10, timeout=5.0, headers={}, error=FetchFailed, opener=opener)
    assert seen == []


def test_fetch_bytes_names_a_missing_scheme() -> None:
    opener, _ = _opener(b"never")
    with pytest.raises(FetchFailed) as caught:
        fetch_bytes("//example.org/data", allowed_hosts=HOSTS, max_bytes=10, timeout=5.0, headers={}, error=FetchFailed, opener=opener)
    assert str(caught.value) == "refusing to fetch host 'example.org' over no scheme"


def test_fetch_bytes_needs_a_positive_cap() -> None:
    opener, _ = _opener(b"x")
    with pytest.raises(ValueError) as caught:
        _fetch(opener, max_bytes=0)
    assert str(caught.value) == "max_bytes must be positive"
    assert _fetch(opener, max_bytes=1) == b"x"


def test_fetch_bytes_caps_size() -> None:
    opener, _ = _opener(b"x" * 1024)
    assert len(_fetch(opener)) == 1024
    opener, _ = _opener(b"x" * 1025)
    with pytest.raises(FetchFailed) as caught:
        _fetch(opener)
    assert str(caught.value) == "https://example.org/data: larger than 1024 bytes"


def test_fetch_bytes_maps_http_error_without_body_by_default() -> None:
    opener, _ = _opener(_http_error(404, b"no such asset"))
    with pytest.raises(FetchFailed) as caught:
        _fetch(opener)
    assert str(caught.value) == "HTTP 404 for https://example.org/data"
    assert isinstance(caught.value.__cause__, urllib.error.HTTPError)


def test_fetch_bytes_appends_body_when_asked() -> None:
    opener, _ = _opener(_http_error(504, b"  server overloaded " + b"x" * 500))
    with pytest.raises(FetchFailed) as caught:
        _fetch(opener, error_body=True)
    message = str(caught.value)
    assert message.startswith("HTTP 504 for https://example.org/data: server overloaded x")
    assert len(message) == len("HTTP 504 for https://example.org/data: ") + 400 - 2
    assert "secret" not in message
    opener, _ = _opener(_http_error(502, b"bad \xff byte"))
    with pytest.raises(FetchFailed) as caught:
        _fetch(opener, error_body=True)
    assert str(caught.value) == "HTTP 502 for https://example.org/data: bad \ufffd byte"
    opener, _ = _opener(_http_error(500, b"   "))
    with pytest.raises(FetchFailed) as caught:
        _fetch(opener, error_body=True)
    assert str(caught.value) == "HTTP 500 for https://example.org/data"


def test_fetch_bytes_maps_network_errors() -> None:
    opener, _ = _opener(urllib.error.URLError("name not known"))
    with pytest.raises(FetchFailed, match="request to https://example.org/data failed: name not known"):
        _fetch(opener)
    opener, _ = _opener(TimeoutError("timed out"))
    with pytest.raises(FetchFailed, match="timed out"):
        _fetch(opener)


def test_fetch_bytes_defaults_to_oserror() -> None:
    opener, _ = _opener(_http_error(500))
    with pytest.raises(OSError, match="HTTP 500"):
        fetch_bytes(URL, allowed_hosts=HOSTS, max_bytes=10, timeout=5.0, headers={}, opener=opener)


def test_fetch_bytes_retries_429() -> None:
    waits: list[float] = []
    opener, seen = _opener(_http_error(429), _http_error(429), b"done")
    assert _fetch(opener, retry_429=2, retry_delay_s=50.0, sleep=waits.append) == b"done"
    assert len(seen) == 3
    assert waits == [50.0, 90.0]


def test_fetch_bytes_doubles_the_429_wait() -> None:
    waits: list[float] = []
    opener, _ = _opener(_http_error(429), _http_error(429), _http_error(429), b"done")
    assert _fetch(opener, retry_429=3, retry_delay_s=10.0, sleep=waits.append) == b"done"
    assert waits == [10.0, 20.0, 40.0]


def test_fetch_bytes_gives_up_after_429_retries() -> None:
    waits: list[float] = []
    opener, seen = _opener(_http_error(429), _http_error(429), _http_error(429), b"never")
    with pytest.raises(FetchFailed, match="rate limited"):
        _fetch(opener, retry_429=2, sleep=waits.append)
    assert len(seen) == 3
    assert waits == [8.0, 16.0]


def test_fetch_bytes_does_not_retry_429_by_default() -> None:
    opener, seen = _opener(_http_error(429))
    with pytest.raises(FetchFailed, match="rate limited"):
        _fetch(opener, sleep=lambda _s: pytest.fail("no retry expected"))
    assert len(seen) == 1


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_fetch_bytes_retries_a_server_error(code: int) -> None:
    waits: list[float] = []
    opener, seen = _opener(_http_error(code), b"done")
    assert _fetch(opener, retry_transient=1, retry_delay_s=10.0, sleep=waits.append) == b"done"
    assert len(seen) == 2
    assert waits == [10.0]


@pytest.mark.parametrize(
    "failure",
    [urllib.error.URLError("refused"), TimeoutError("slow"), ConnectionResetError("reset")],
)
def test_fetch_bytes_retries_a_network_error(failure: Exception) -> None:
    waits: list[float] = []
    opener, seen = _opener(failure, failure, b"done")
    assert _fetch(opener, retry_transient=2, retry_delay_s=10.0, sleep=waits.append) == b"done"
    assert len(seen) == 3
    assert waits == [10.0, 20.0]


def test_fetch_bytes_gives_up_after_transient_retries() -> None:
    waits: list[float] = []
    opener, seen = _opener(_http_error(503, b"busy"), _http_error(503, b"busy"), _http_error(503, b"busy"), b"never")
    with pytest.raises(FetchFailed, match=r"^HTTP 503 for https://example.org/data: busy$"):
        _fetch(opener, retry_transient=2, error_body=True, sleep=waits.append)
    assert len(seen) == 3
    assert waits == [8.0, 16.0]


def test_fetch_bytes_gives_up_after_network_retries() -> None:
    waits: list[float] = []
    opener, seen = _opener(urllib.error.URLError("refused"), TimeoutError("slow"), b"never")
    with pytest.raises(FetchFailed, match=r"^request to https://example.org/data failed: slow$"):
        _fetch(opener, retry_transient=1, sleep=waits.append)
    assert len(seen) == 2
    assert waits == [8.0]


def test_fetch_bytes_counts_every_network_retry() -> None:
    waits: list[float] = []
    failures = [TimeoutError("slow"), ConnectionResetError("reset"), TimeoutError("slow again")]
    opener, seen = _opener(*failures, b"never")
    with pytest.raises(FetchFailed, match="slow again"):
        _fetch(opener, retry_transient=2, retry_delay_s=1.0, sleep=waits.append)
    assert len(seen) == 3
    assert waits == [1.0, 2.0]


def test_fetch_bytes_counts_429_and_transient_retries_apart() -> None:
    waits: list[float] = []
    opener, seen = _opener(_http_error(429), _http_error(503), _http_error(429), b"done")
    assert _fetch(opener, retry_429=2, retry_transient=1, retry_delay_s=1.0, sleep=waits.append) == b"done"
    assert len(seen) == 4
    assert waits == [1.0, 2.0, 4.0]


@pytest.mark.parametrize("code", [400, 404, 501])
def test_fetch_bytes_does_not_retry_a_client_or_unknown_error(code: int) -> None:
    opener, seen = _opener(_http_error(code))
    with pytest.raises(FetchFailed, match=f"HTTP {code}"):
        _fetch(opener, retry_transient=3, sleep=lambda _s: pytest.fail("no retry expected"))
    assert len(seen) == 1


def test_fetch_bytes_does_not_retry_transient_errors_by_default() -> None:
    opener, seen = _opener(_http_error(503), b"never")
    with pytest.raises(FetchFailed, match="HTTP 503"):
        _fetch(opener, sleep=lambda _s: pytest.fail("no retry expected"))
    assert len(seen) == 1
    opener, seen = _opener(TimeoutError("slow"), b"never")
    with pytest.raises(FetchFailed, match="slow"):
        _fetch(opener, sleep=lambda _s: pytest.fail("no retry expected"))
    assert len(seen) == 1


def test_tls_context_verifies_certificates() -> None:
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname


def test_redact_drops_query() -> None:
    assert redact("https://cdn.example/a/b.zip?X-Amz-Signature=abc#frag") == "https://cdn.example/a/b.zip"
