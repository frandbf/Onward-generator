"""Which downloads put the start of an HTTP error body in their message. No network."""

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


@pytest.fixture
def http_503(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(request: urllib.request.Request, timeout: float | None = None, context: object = None) -> None:
        raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(BODY))  # type: ignore[arg-type]

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)


@pytest.mark.usefixtures("http_503")
@pytest.mark.parametrize(
    ("call", "error"),
    [
        (lambda: osm_acquire._http_fetch(osm_acquire.OVERPASS_URL, "[out:xml];", 5.0), osm_acquire.OsmDownloadError),
        (lambda: cadastre_acquire._http_fetch(cadastre_acquire.WFS_BU_URL, 5.0), cadastre_acquire.CadastreError),
    ],
)
def test_overpass_and_catastro_keep_the_error_body(call: Callable[[], bytes], error: type[Exception]) -> None:
    with pytest.raises(error, match="HTTP 503 for .*: runtime error: query timed out"):
        call()


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
