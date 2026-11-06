"""Poly Haven model batch download, with a fake getter. No network."""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.overgrow import fetch
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.fetch import FILES_URL, INFO_URL, LICENSE_URL, FetchError, fetch_library

SPEC = AssetSpec(
    id="test_crate",
    category="props",
    role="crate",
    surfaces=("sidewalk",),
    anchor="ground",
    use="test crate on a sidewalk",
)
GLTF_URL = "https://dl.polyhaven.org/file/test_crate_1k.gltf"
BIN_URL = "https://dl.polyhaven.org/file/test_crate.bin"
DIFF_URL = "https://dl.polyhaven.org/file/test_crate_diff_1k.png"


def _box_gltf() -> tuple[bytes, bytes, bytes]:
    """A 1 m cube with UVs and a small albedo image, as glTF JSON, bin and PNG."""
    corners = np.array([(x, y, z) for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=np.float32)
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    indices = np.array([tri for a, b, c, d in faces for tri in ((a, b, c), (a, c, d))], dtype=np.uint16).ravel()
    uvs = corners[:, :2].copy()
    blob = corners.tobytes() + uvs.tobytes() + indices.tobytes()
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "textures": [{"source": 0}],
        "images": [{"uri": "test_crate_diff_1k.png"}],
        "buffers": [{"uri": "test_crate.bin", "byteLength": len(blob)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": corners.nbytes},
            {"buffer": 0, "byteOffset": corners.nbytes, "byteLength": uvs.nbytes},
            {"buffer": 0, "byteOffset": corners.nbytes + uvs.nbytes, "byteLength": indices.nbytes},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3", "min": [0, 0, 0], "max": [1, 1, 1]},
            {"bufferView": 1, "componentType": 5126, "count": 8, "type": "VEC2"},
            {"bufferView": 2, "componentType": 5123, "count": len(indices), "type": "SCALAR"},
        ],
    }
    png = io.BytesIO()
    Image.new("RGB", (4, 4), (120, 80, 40)).save(png, "PNG")
    return json.dumps(doc).encode("utf-8"), blob, png.getvalue()


class FakeGet:
    """Serves fixed responses by URL and records every request."""

    def __init__(self, responses: dict[str, tuple[int, bytes]]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def __call__(self, url: str) -> tuple[int, bytes]:
        self.calls.append(url)
        return self.responses.get(url, (404, b""))


def _responses(info: dict) -> dict[str, tuple[int, bytes]]:
    gltf, blob, png = _box_gltf()
    files = {
        "gltf": {
            "1k": {
                "gltf": {
                    "url": GLTF_URL,
                    "include": {"test_crate.bin": {"url": BIN_URL}, "test_crate_diff_1k.png": {"url": DIFF_URL}},
                }
            }
        }
    }
    return {
        LICENSE_URL: (200, b"<html>All assets are CC0</html>"),
        INFO_URL.format(id=SPEC.id): (200, json.dumps(info).encode("utf-8")),
        FILES_URL.format(id=SPEC.id): (200, json.dumps(files).encode("utf-8")),
        GLTF_URL: (200, gltf),
        BIN_URL: (200, blob),
        DIFF_URL: (200, png),
    }


@pytest.fixture
def one_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "ALL_ASSETS", (SPEC,))


def _run(tmp_path: Path, get: FakeGet) -> dict:
    return fetch_library(tmp_path / "raw", tmp_path / "library", tmp_path / "inventory.json", get=get)


def test_licence_page_without_cc0_refuses_the_batch(tmp_path: Path, one_asset: None) -> None:
    get = FakeGet({LICENSE_URL: (200, b"<html>all rights reserved</html>")})
    with pytest.raises(FetchError, match="refusing the batch"):
        _run(tmp_path, get)
    assert get.calls == [LICENSE_URL]
    assert not (tmp_path / "inventory.json").exists()


def test_licence_page_error_refuses_the_batch(tmp_path: Path, one_asset: None) -> None:
    with pytest.raises(FetchError, match="HTTP 503"):
        _run(tmp_path, FakeGet({LICENSE_URL: (503, b"")}))


def test_cc0_model_is_normalized_and_listed(tmp_path: Path, one_asset: None) -> None:
    info = {"name": "Crate", "authors": {"Someone": "All"}, "polycount": 12, "dimensions": [1000, 1000, 1000]}
    document = _run(tmp_path, FakeGet(_responses(info)))
    [row] = document["assets"]
    assert row["import_status"] == "normalized"
    assert row["license"] == "CC0"
    assert row["authors"] == ["Someone"]
    assert row["http_status"] == {"info": 200, "files": 200, "gltf": 200, "bin": 200, "albedo": 200}
    assert (tmp_path / "library" / "test_crate.glb").read_bytes()[:4] == b"glTF"
    assert (tmp_path / "raw" / "test_crate" / "test_crate.bin").is_file()
    manifest = json.loads((tmp_path / "library" / "manifest.json").read_text(encoding="utf-8"))
    assert [asset["id"] for asset in manifest["assets"]] == ["test_crate"]
    assert manifest["assets"][0]["collider"] == "none"
    assert {key: manifest["assets"][0][key] for key in ("license", "license_url", "author", "source_url", "source")} == {
        "license": "CC0-1.0",
        "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "author": "Someone",
        "source_url": "https://polyhaven.com/a/test_crate",
        "source": "polyhaven",
    }
    assert json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8")) == document


def test_second_run_reuses_the_download(tmp_path: Path, one_asset: None) -> None:
    info = {"name": "Crate", "polycount": 12, "authors": {"Rob Tuytel": "All", "Rico Cilliers": "Model"}}
    _run(tmp_path, FakeGet(_responses(info)))
    get = FakeGet({LICENSE_URL: (200, b"CC0")})
    document = _run(tmp_path, get)
    assert get.calls == [LICENSE_URL]
    assert document["assets"][0]["import_status"] == "normalized"
    assert "Reused the download" in document["assets"][0]["license_evidence"]
    [prefab] = json.loads((tmp_path / "library" / "manifest.json").read_text(encoding="utf-8"))["assets"]
    assert (prefab["license"], prefab["author"], prefab["source"]) == ("CC0-1.0", "Rob Tuytel, Rico Cilliers", "polyhaven")


def test_non_cc0_model_is_refused_before_the_mesh(tmp_path: Path, one_asset: None) -> None:
    get = FakeGet(_responses({"name": "Crate", "license": "CC-BY-NC"}))
    document = _run(tmp_path, get)
    [row] = document["assets"]
    assert row["import_status"] == "refused"
    assert "not CC0" in row["license_evidence"]
    assert GLTF_URL not in get.calls
    assert not (tmp_path / "library" / "test_crate.glb").exists()
    assert json.loads((tmp_path / "library" / "manifest.json").read_text(encoding="utf-8"))["assets"] == []


def test_missing_api_entry_is_recorded_not_raised(tmp_path: Path, one_asset: None) -> None:
    document = _run(tmp_path, FakeGet({LICENSE_URL: (200, b"CC0")}))
    [row] = document["assets"]
    assert row["import_status"] == "refused"
    assert row["license"] is None
    assert row["http_status"] == {"info": 404, "files": 404}


def test_missing_albedo_is_not_imported(tmp_path: Path, one_asset: None) -> None:
    responses = _responses({"name": "Crate"})
    del responses[DIFF_URL]
    document = _run(tmp_path, FakeGet(responses))
    [row] = document["assets"]
    assert row["import_status"] == "missing_bin_or_albedo"
    assert row["http_status"]["albedo"] == 404


def test_network_failure_marks_the_asset_failed(tmp_path: Path, one_asset: None) -> None:
    def get(url: str) -> tuple[int, bytes]:
        if url == LICENSE_URL:
            return 200, b"CC0"
        raise FetchError(f"request to {url} failed: timed out")

    document = fetch_library(tmp_path / "raw", tmp_path / "library", tmp_path / "inventory.json", get=get)
    [row] = document["assets"]
    assert row["import_status"] == "failed"
    assert "timed out" in row["error"]


def test_default_get_returns_the_http_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(url: str, **_kwargs: object) -> bytes:
        raise FetchError(f"HTTP 404 for {url}") from urllib.error.HTTPError(url, 404, "missing", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(fetch, "fetch_bytes", refuse)
    assert fetch._get("https://api.polyhaven.com/info/x") == (404, b"")


def test_default_get_raises_on_other_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(url: str, **_kwargs: object) -> bytes:
        raise FetchError(f"{url}: larger than 1 bytes")

    monkeypatch.setattr(fetch, "fetch_bytes", fail)
    with pytest.raises(FetchError, match="larger than"):
        fetch._get("https://api.polyhaven.com/info/x")


def test_default_get_refuses_other_hosts() -> None:
    with pytest.raises(FetchError, match="refusing to fetch host"):
        fetch._get("https://example.com/model.gltf")


def test_a_second_run_keeps_the_curated_fields(tmp_path: Path, one_asset: None) -> None:
    _run(tmp_path, FakeGet(_responses({"name": "Crate", "polycount": 12})))
    path = tmp_path / "library" / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    curated = {"description": "A test crate.", "placement": "open_ground", "grouping": "cluster", "condition": "worn", "tags": ["wood"]}
    manifest["assets"][0].update(curated)
    manifest["schema_version"] = "0.2.0"
    manifest["semantics"] = {"origin": "estimated"}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    _run(tmp_path, FakeGet({LICENSE_URL: (200, b"CC0")}))
    again = json.loads(path.read_text(encoding="utf-8"))
    assert again["schema_version"] == "0.2.0"
    assert again["semantics"] == {"origin": "estimated"}
    assert {key: again["assets"][0][key] for key in curated} == curated
