"""Panoramax on the synthetic Murcia block. The picture client is fake."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from shapely import Point
from shapely.geometry import shape

from onward_generator.cli import main
from onward_generator.generation.gltf import Node, nodes_from_glb, read_glb
from onward_generator.geo.projection import LocalFrame
from onward_generator.maps.workspace import create_map, load_map
from onward_generator.panoramax.__main__ import run_apply
from onward_generator.panoramax.cache import PanoramaxError
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON
from tests.integration.test_materials_pipeline import _cache_asset, _polyhaven_plan

PICTURE_ID = "33333333-3333-3333-3333-333333333333"


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 32), (180, 40, 40)).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class _Client:
    def __init__(self, feature: dict[str, Any]) -> None:
        self.feature = feature

    def get_json(self, url: str) -> dict[str, Any]:
        return {"type": "FeatureCollection", "features": [self.feature], "links": []}

    def get_bytes(self, url: str) -> bytes:
        if url.endswith(".jpg"):
            return _jpeg()
        raise PanoramaxError("offline")


def _feature(longitude: float, latitude: float) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": PICTURE_ID,
        "geometry": {"type": "Point", "coordinates": [longitude, latitude]},
        "properties": {
            "license": "CC-BY-SA-4.0",
            "view:azimuth": 0,
            "quality:horizontal_accuracy": 4,
            "geovisio:producer": "tester",
            "pers:interior_orientation": {"field_of_view": 360},
            "exif": {"Exif.Image.Artist": "tester"},
        },
        "links": [{"rel": "license", "href": "https://spdx.org/licenses/CC-BY-SA-4.0.html"}],
        "assets": {"sd": {"href": f"https://example.test/{PICTURE_ID}.jpg", "type": "image/jpeg"}},
    }


def _south_midpoint(feature: dict[str, Any]) -> tuple[float, float]:
    polygon = shape(feature["geometry"])
    ring = list(polygon.exterior.coords)
    edge = min(zip(ring, ring[1:]), key=lambda pair: pair[0][1] + pair[1][1])
    return ((edge[0][0] + edge[1][0]) / 2.0, (edge[0][1] + edge[1][1]) / 2.0)


def _points(node: Node, *, roof: bool = False) -> np.ndarray:
    chosen = [prim.mesh.vertices for prim in node.primitives if (not roof or "roof" in prim.material) and prim.mesh.vertex_count]
    if not chosen:
        return np.zeros((0, 3))
    stacked = np.vstack(chosen)
    return stacked[np.lexsort(stacked.T)]


def test_a_photo_replaces_only_that_building(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "onward_generator.panoramax.cache.catalogue_directory",
        lambda: tmp_path / "panoramax-catalogue",
    )
    scene, heights, base = detail_inputs
    root = tmp_path / "murcia"
    create_map(
        root,
        map_id="murcia",
        name="Murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        radius_m=200,
        panoramax=True,
    )
    detail = root / "data" / "processed" / "scene_detail"
    assert main([
        "detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
        "--out", str(detail), "--no-preview",
    ]) == 0
    cache = tmp_path / "cache"
    for asset, color in (
        ("coral_fort_wall_02", (200, 180, 140)),
        ("rock_wall_11", (160, 140, 110)),
        ("cobblestone_color", (120, 110, 90)),
        ("clay_roof_tiles_02", (170, 70, 40)),
        ("red_bricks_04", (140, 50, 40)),
        ("asphalt_02", (40, 40, 42)),
        ("concrete_floor_worn_001", (150, 148, 142)),
        ("concrete_floor_02", (170, 168, 160)),
        ("wood_planks", (140, 100, 60)),
        ("bark_brown_02", (90, 60, 40)),
        ("concrete_wall_001", (180, 178, 170)),
    ):
        _cache_asset(cache, asset, color)
    materials = root / "data" / "processed" / "scene_materials"
    assert main([
        "materials", "apply",
        "--base", str(detail),
        "--scene", str(scene),
        "--plan", str(_polyhaven_plan(tmp_path / "texture_plan.json")),
        "--out", str(materials),
        "--cache", str(cache),
    ]) == 0

    manifest = json.loads((materials / "scene_detail.json").read_text(encoding="utf-8"))
    detail_report = json.loads((materials / "detail_report.json").read_text(encoding="utf-8"))
    listed_interiors = {str(item) for item in detail_report["counts"]["interiors"]}
    interiors = {
        str(obj["id"])
        for obj in manifest["objects"]
        if obj.get("type") == "building"
        and (
            str(obj.get("id")) in listed_interiors
            or any(str(source) in listed_interiors for source in obj.get("source_ids") or [])
        )
    }
    geo = json.loads(Path(manifest["inputs"]["geo_scene"]["path"]).read_text(encoding="utf-8"))
    features = {str(feature["id"]): feature for feature in geo["features"] if feature.get("kind") == "building"}
    candidates: list[tuple[float, str, tuple[float, float]]] = []
    for obj in manifest["objects"]:
        if obj.get("type") != "building" or str(obj["id"]) in interiors:
            continue
        source = str((obj.get("source_ids") or [""])[0])
        if source not in features:
            continue
        midpoint = _south_midpoint(features[source])
        candidates.append((midpoint[1], str(obj["id"]), midpoint))
    assert candidates
    _, chosen_id, midpoint = min(candidates)
    document = load_map(root)
    center = document["center"]
    frame = LocalFrame.create(float(center["longitude"]), float(center["latitude"]), str(document["crs"]))
    camera = frame.to_wgs84(Point(midpoint[0], midpoint[1] - 8.0))

    out = root / "data" / "processed" / "scene_panoramax"
    assert run_apply(materials, out, client=_Client(_feature(float(camera.x), float(camera.y)))) == 0
    report = json.loads((out / "panoramax_report.json").read_text(encoding="utf-8"))
    photo_ids = {str(row["building_id"]) for row in report["assignments"]}
    assert chosen_id in photo_ids
    assert photo_ids.isdisjoint(interiors)
    assert all(row["license"] == "CC-BY-SA-4.0" and row["origin"] == "observed" and row["author"] == "tester" for row in report["assignments"])
    assert (materials / "scene_collision.glb").read_bytes() == (out / "scene_collision.glb").read_bytes()
    assert hashlib.sha256((out / "scene_collision.glb").read_bytes()).hexdigest() == report["collision_sha256"]

    before_nodes, _ = nodes_from_glb((materials / "scene_visual.glb").read_bytes())
    after_nodes, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    before = {node.name: node for node in before_nodes}
    after = {node.name: node for node in after_nodes}
    assert set(before) == set(after)
    for name, node in after.items():
        if name in photo_ids:
            photos = [prim for prim in node.primitives if prim.material.startswith("pano-")]
            assert photos
            assert sum(prim.mesh.vertex_count for prim in photos) <= 16
            original = [prim for prim in node.primitives if not prim.material.startswith("pano-")]
            assert np.allclose(_points(Node(name, tuple(original))), _points(before[name]), atol=1e-4)
            noted = (node.extras or {}).get("panoramax") or []
            assert noted and noted[0]["picture_id"] == PICTURE_ID
            assert noted[0]["license"] == "CC-BY-SA-4.0"
            assert noted[0]["origin"] == "observed"
        else:
            assert np.allclose(_points(node), _points(before[name]), atol=1e-4)
    for interior_id in interiors:
        assert np.allclose(_points(after[interior_id]), _points(before[interior_id]), atol=1e-4)
    for source in listed_interiors:
        shell = f"interior/{source}"
        assert shell in after
        assert np.allclose(_points(after[shell]), _points(before[shell]), atol=1e-4)

    document_glb, _blob = read_glb((out / "scene_visual.glb").read_bytes())
    photo_materials = [item for item in document_glb["materials"] if str(item["name"]).startswith("pano-")]
    assert photo_materials
    assert photo_materials[0]["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]

    signed = root / "data" / "processed" / "scene_signs"
    assert main(["signs", "place", "--base", str(out), "--scene", str(scene), "--out", str(signed)]) == 0
    signed_nodes, _ = nodes_from_glb((signed / "scene_visual.glb").read_bytes())
    photo_uvs = [
        prim.uvs
        for node in signed_nodes
        for prim in node.primitives
        if prim.material.startswith("pano-") and prim.uvs is not None
    ]
    assert photo_uvs
    assert all(np.all((uvs > 0.0) & (uvs < 1.0)) for uvs in photo_uvs)
