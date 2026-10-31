"""The live Panoramax client: which URLs it opens, how much it reads, what it retries. No network."""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from onward_generator.panoramax import cache
from onward_generator.panoramax.cache import PanoramaxError, UrllibTransport

PICTURE = "https://panoramax.openstreetmap.fr/api/pictures/abc/sd.jpg"
SEARCH = "https://api.panoramax.xyz/api/search?limit=100"


class Body(io.BytesIO):
    def __enter__(self) -> Body:
        return self


Seen = list[tuple[urllib.request.Request, float | None]]


def _serve(monkeypatch: pytest.MonkeyPatch, *bodies: bytes | Exception) -> Seen:
    queue = list(bodies)
    seen: Seen = []

    def urlopen(request: urllib.request.Request, timeout: float | None = None, context: object = None) -> Body:
        seen.append((request, timeout))
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return Body(item)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


@pytest.mark.parametrize(
    "href",
    [
        "file:///etc/passwd",
        "http://panoramax.openstreetmap.fr/api/pictures/abc/sd.jpg",
        "https://127.0.0.1/sd.jpg",
        "https://[::1]/sd.jpg",
        "https://localhost/sd.jpg",
    ],
)
def test_a_picture_href_that_is_not_https_on_a_dns_name_is_refused(monkeypatch: pytest.MonkeyPatch, href: str) -> None:
    seen = _serve(monkeypatch)
    with pytest.raises(PanoramaxError, match="refusing to fetch"):
        UrllibTransport().get_bytes(href)
    assert seen == []


def test_a_search_page_from_another_host_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch)
    with pytest.raises(PanoramaxError, match="refusing to fetch host 'panoramax.ign.fr'"):
        UrllibTransport().get_json("https://panoramax.ign.fr/api/search")
    assert seen == []


def test_a_picture_is_read_from_its_own_host_with_the_jpeg_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, b"\xff\xd8jpeg")
    assert UrllibTransport(timeout_s=7.0).get_bytes(PICTURE) == b"\xff\xd8jpeg"
    ((request, timeout),) = seen
    assert timeout == 7.0
    assert request.full_url == PICTURE
    assert request.headers == {"User-agent": cache.USER_AGENT, "Accept": "image/jpeg"}


def test_a_search_page_is_parsed_with_the_json_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, b'{"features": []}')
    assert UrllibTransport().get_json(SEARCH) == {"features": []}
    ((request, timeout),) = seen
    assert timeout == 60.0
    assert request.headers == {"User-agent": cache.USER_AGENT, "Accept": "application/geo+json, application/json"}


def test_an_oversize_picture_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "MAX_JPEG_BYTES", 8)
    _serve(monkeypatch, b"123456789")
    with pytest.raises(PanoramaxError, match="larger than 8 bytes"):
        UrllibTransport().get_bytes(PICTURE)


def test_a_picture_at_the_cap_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "MAX_JPEG_BYTES", 8)
    _serve(monkeypatch, b"12345678")
    assert UrllibTransport().get_bytes(PICTURE) == b"12345678"


def test_an_oversize_search_page_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache, "MAX_JSON_BYTES", 4)
    _serve(monkeypatch, b'{"features": []}')
    with pytest.raises(PanoramaxError, match="larger than 4 bytes"):
        UrllibTransport().get_json(SEARCH)


def test_the_caps_leave_room_for_a_search_page_and_an_hd_picture() -> None:
    assert cache.MAX_JSON_BYTES == 32 * 1024 * 1024
    assert cache.MAX_JPEG_BYTES == 64 * 1024 * 1024


def _busy() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(PICTURE, 503, "busy", {}, io.BytesIO(b""))  # type: ignore[arg-type]


def _slow() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(PICTURE, 429, "slow", {}, io.BytesIO(b""))  # type: ignore[arg-type]


def test_two_transient_failures_and_two_rate_limits_are_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _serve(monkeypatch, _busy(), _slow(), TimeoutError("slow"), _slow(), b"ok")
    waits: list[float] = []
    assert UrllibTransport(sleep=waits.append).get_bytes(PICTURE) == b"ok"
    assert len(seen) == 5
    assert waits == [8.0, 16.0, 32.0, 64.0]


def test_a_third_transient_failure_raises_a_panoramax_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, _busy(), _busy(), _busy())
    waits: list[float] = []
    with pytest.raises(PanoramaxError, match="HTTP 503"):
        UrllibTransport(sleep=waits.append).get_bytes(PICTURE)
    assert len(waits) == 2


def test_a_body_that_is_not_json_or_not_an_object_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve(monkeypatch, b"<html>", b"[]")
    with pytest.raises(PanoramaxError, match="did not return JSON"):
        UrllibTransport().get_json(SEARCH)
    with pytest.raises(PanoramaxError, match="must be an object"):
        UrllibTransport().get_json(SEARCH)


@pytest.mark.parametrize(
    ("url", "hosts"),
    [
        (PICTURE, frozenset({"panoramax.openstreetmap.fr"})),
        ("https://panoramax-storage-public-fast.s3.gra.perf.cloud.ovh.net/x.jpg", frozenset({"panoramax-storage-public-fast.s3.gra.perf.cloud.ovh.net"})),
        ("https://10.0.0.1/x.jpg", frozenset()),
        ("https://intranet/x.jpg", frozenset()),
        ("not a url", frozenset()),
    ],
)
def test_picture_hosts(url: str, hosts: frozenset[str]) -> None:
    assert cache.picture_hosts(url) == hosts
