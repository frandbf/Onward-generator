"""What each download sends, and which ones keep an HTTP error body in their message. No network."""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from collections.abc import Callable

import pytest

from onward_generator.cadastre import acquire as cadastre_acquire
from onward_generator.materials import polyhaven
from onward_generator.osm import acquire as osm_acquire
from onward_generator.overgrow import sketchfab_fetch

BODY = b"runtime error: query timed out"

Seen = list[tuple[urllib.request.Request, float | None]]


@pytest.fixture
def http_503(monkeypatch: pytest.MonkeyPatch) -> Seen:
    seen: Seen = []

    def urlopen(request: urllib.request.Request, timeout: float | None = None, context: object = None) -> None:
        seen.append((request, timeout))
        raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(BODY))  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    return seen


def test_overpass_posts_the_query_and_keeps_the_error_body(http_503: Seen) -> None:
    waits: list[float] = []
    with pytest.raises(osm_acquire.OsmDownloadError) as caught:
        osm_acquire._http_fetch(osm_acquire.OVERPASS_URL, "[out:xml];", 5.0, sleep=waits.append)
    assert str(caught.value) == f"HTTP 503 for {osm_acquire.OVERPASS_URL}: runtime error: query timed out"
    assert waits == [8.0, 16.0, 32.0]
    assert len(http_503) == 4
    (request, timeout) = http_503[0]
    assert timeout == 5.0
    assert request.get_method() == "POST"
    assert request.data == b"data=%5Bout%3Axml%5D%3B"
    assert request.headers == {
        "User-agent": osm_acquire.USER_AGENT,
        "Accept": "application/xml, text/xml",
        "Content-type": "application/x-www-form-urlencoded",
    }


def test_overpass_retries_rate_limits_and_dropped_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    failures: list[Exception] = [
        urllib.error.HTTPError(osm_acquire.OVERPASS_URL, 429, "slow down", {}, io.BytesIO(b"")),  # type: ignore[arg-type]
        urllib.error.HTTPError(osm_acquire.OVERPASS_URL, 429, "slow down", {}, io.BytesIO(b"")),  # type: ignore[arg-type]
        urllib.error.HTTPError(osm_acquire.OVERPASS_URL, 429, "slow down", {}, io.BytesIO(b"")),  # type: ignore[arg-type]
        ConnectionResetError("reset"),
        TimeoutError("slow"),
        urllib.error.URLError("refused"),
    ]

    class Body(io.BytesIO):
        def __enter__(self) -> Body:
            return self

    def urlopen(request: urllib.request.Request, timeout: float | None = None, context: object = None) -> Body:
        if failures:
            raise failures.pop(0)
        return Body(b"<osm/>")

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    waits: list[float] = []
    assert osm_acquire._http_fetch(osm_acquire.OVERPASS_URL, "[out:xml];", 5.0, sleep=waits.append) == b"<osm/>"
    assert waits == [8.0, 16.0, 32.0, 64.0, 90.0, 90.0]


def test_catastro_gets_the_url_and_keeps_the_error_body(http_503: Seen) -> None:
    url = cadastre_acquire.WFS_BU_URL + "?service=WFS"
    with pytest.raises(cadastre_acquire.CadastreError) as caught:
        cadastre_acquire._http_fetch(url, 7.0)
    assert str(caught.value) == f"HTTP 503 for {cadastre_acquire.WFS_BU_URL}: runtime error: query timed out"
    [(request, timeout)] = http_503
    assert timeout == 7.0
    assert request.get_method() == "GET"
    assert request.full_url == url
    assert request.headers == {"User-agent": cadastre_acquire.USER_AGENT, "Accept": "application/xml,text/xml"}


@pytest.mark.usefixtures("http_503")
@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda: polyhaven.default_fetch("https://api.polyhaven.com/info/x"), polyhaven.DownloadError),
        (lambda: sketchfab_fetch._read_bytes("https://media.sketchfab.com/a.zip", {}, 5), sketchfab_fetch.SketchfabError),
    ],
)
def test_poly_haven_and_sketchfab_drop_the_error_body(call: Callable[[], bytes], error: type[Exception]) -> None:
    with pytest.raises(error, match="HTTP 503") as caught:
        call()
    assert "runtime error" not in str(caught.value)
