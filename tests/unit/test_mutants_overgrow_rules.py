"""Boundaries of the overgrow rules: level of detail, solid props, allowed furniture catalogues and the visual GLB's extras.

The scene is local metres around MURCIA_CENTER. It is not the Murcia extract.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, read_glb
from onward_generator.generation.mesh import Mesh, box
from onward_generator.overgrow import library as library_module
from onward_generator.overgrow.ambient import is_solid
from onward_generator.overgrow.atlas import PROP_ATLAS_EXTRA
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.furnish import FurnishBuildError, build_furniture
from onward_generator.overgrow.library import Prop, level_of_detail
from onward_generator.overgrow.place import overgrow
from tests.conftest import MURCIA_CENTER

# --- level of detail, its collaborators stubbed so each threshold is read on its own ---------------------------------


def _prop(spec: AssetSpec, vertices: np.ndarray, triangles: int = 300) -> Prop:
    tris = np.tile(np.arange(min(len(vertices), 3)), (triangles, 1)) if len(vertices) >= 3 else np.zeros((0, 3), dtype=np.int64)
    return Prop(spec, Mesh(vertices, tris), np.zeros((len(vertices), 2), dtype=np.float32), b"png")


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """Street scale 2, a target of 100 triangles, a fixed box shift; the reduced copy and the impostor are markers."""
    knobs = {"scale": 2.0, "shift": 0.0}
    monkeypatch.setattr(library_module, "apply_fit_scale", lambda mesh, role: (mesh, knobs["scale"]))
    monkeypatch.setattr(library_module, "lod_target", lambda mesh, scale: 100)
    monkeypatch.setattr(library_module, "box_shift", lambda a, b: knobs["shift"])
    monkeypatch.setattr(library_module, "_decimated", lambda prop, target: Prop(prop.spec, prop.mesh, prop.uvs, b"reduced"))
    monkeypatch.setattr(library_module, "flat_impostor", lambda prop: Prop(prop.spec, prop.mesh, prop.uvs, b"impostor"))
    return knobs


FERN = AssetSpec("fern", "vegetation", "plant", ("park",), "ground", "a fern")
CAR = AssetSpec("car", "vehicles", "wreck", ("road",), "ground", "a car")
CRATE = AssetSpec("crate", "waste", "crate", ("sidewalk",), "ground", "a crate")


def _corners(x: tuple[float, float], y: tuple[float, float], z: tuple[float, float]) -> np.ndarray:
    return np.array([(a, b, c) for a in x for b in y for c in z], dtype=np.float64)


def test_a_vehicle_is_kept_whole(stubbed: dict[str, float]) -> None:
    car = _prop(CAR, _corners((0, 4), (0, 2), (0, 1.5)))
    assert level_of_detail(car) is car
    assert level_of_detail(_prop(CRATE, _corners((0, 4), (0, 2), (0, 1.5)))).albedo == b"reduced"


def test_only_a_prop_lower_than_the_flat_height_becomes_an_impostor(stubbed: dict[str, float]) -> None:
    stubbed["scale"] = 1.0
    at_limit = _prop(CRATE, _corners((0, 1), (0, 1), (0.0, 0.1)))  # 0.1 m tall: not lower than FLAT_PROP_M
    assert level_of_detail(at_limit).albedo == b"reduced"
    below = _prop(CRATE, _corners((0, 1), (0, 1), (0.0, 0.0999)))
    assert level_of_detail(below).albedo == b"impostor"


def test_decorative_vegetation_may_move_a_tenth_of_its_largest_side_at_street_size(stubbed: dict[str, float]) -> None:
    # 2 m long at x 10..12 (largest side), scale 2: the box may move 0.1 * 2 * 2 = 0.4 m at street size.
    fern = _prop(FERN, _corners((10, 12), (0, 1), (0, 0.8)))
    stubbed["shift"] = 0.15  # 0.3 m at street size: inside 0.4
    assert level_of_detail(fern).albedo == b"reduced"
    stubbed["shift"] = 0.5  # 1.0 m: outside, and every larger target saves too little, so the file's mesh stays
    assert level_of_detail(fern) is fern


# --- solid props -----------------------------------------------------------------------------------------------------


def test_a_prop_one_metre_tall_is_solid_from_the_tall_depth() -> None:
    assert is_solid("sign", "misc", (1.0, 0.15, 1.0))
    assert not is_solid("sign", "misc", (1.0, 0.15, 0.999))


# --- furniture catalogues --------------------------------------------------------------------------------------------


def _plan(catalogue: str) -> dict:
    piece = {
        "id": "furnish/b1/0",
        "asset_id": "Shelf_01",
        "catalogue": catalogue,
        "slot": "wall",
        "position_m": [0, 0, 0],
        "yaw_deg": 0.0,
        "triangles": 1,
        "solid": True,
        "glb_sha256": "0" * 64,
    }
    return {"objects": [{"type": "interior", "source_ids": ["b1"], "rooms": [{"furnishing": {"pieces": [piece]}}]}]}


def test_a_local_catalogue_is_allowed_only_while_local_catalogues_are_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "0")
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(_plan("asset_store_shop"), tmp_path)
    assert str(caught.value) == "interior furniture names catalogues that are not allowed: asset_store_shop"
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(_plan("kenney"), tmp_path)
    assert str(caught.value) == "interior furniture names catalogues that are not allowed: kenney"
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(_plan("asset_store_shop"), tmp_path)
    assert str(caught.value) == f"interior furniture Shelf_01: not installed under {tmp_path} (missing)"


# --- the visual GLB's extras -----------------------------------------------------------------------------------------


def _feature(kind: str, geometry: dict, tags: dict, feature_id: str) -> dict:
    return {"id": feature_id, "kind": kind, "tags": tags, "geometry": {**geometry, "unit": "m", "coordinate_system": "local_grid"}}


def test_a_scene_without_library_props_names_its_manifest_and_count_and_no_atlas(tmp_path: Path) -> None:
    scene = tmp_path / "geo_scene.json"
    scene.write_text(
        json.dumps(
            {
                "schema": "onward_generator.geo_scene",
                "origin": {"longitude": MURCIA_CENTER[0], "latitude": MURCIA_CENTER[1]},
                "features": [
                    _feature(
                        "highway", {"type": "LineString", "coordinates": [[-30, 0], [60, 0]]}, {"highway": "residential"}, "osm-way-1"
                    ),
                    _feature(
                        "building",
                        {"type": "Polygon", "coordinates": [[[0, 12], [24, 12], [24, 30], [0, 30], [0, 12]]]},
                        {"building": "yes", "height": "12"},
                        "osm-way-2",
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    base = tmp_path / "base"
    base.mkdir()
    ground = box(0, 4, 0, 4, 0, 0.1)
    (base / "scene_visual.glb").write_bytes(
        build_glb([Node("ground", (Primitive(ground, "ground"),), {"type": "surface"})], {"ground": Material((0.4, 0.4, 0.4, 1), 1)})
    )
    (base / "scene_collision.glb").write_bytes(b"collision-bytes")
    (base / "scene_detail.json").write_text(
        json.dumps(
            {
                "schema": "onward_generator.scene_detail",
                "objects": [{"id": "building/osm-way-2", "type": "building", "source_ids": ["osm-way-2"]}],
                "files": {"visual": "scene_visual.glb", "collision": "scene_collision.glb"},
            }
        ),
        encoding="utf-8",
    )
    empty = tmp_path / "library"
    empty.mkdir()
    out = tmp_path / "out"
    overgrow(base, scene, out, library=empty)
    doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    count = json.loads((out / "detail_report.json").read_text(encoding="utf-8"))["overgrow"]["count"]
    assert count > 0
    assert doc["extras"] == {"manifest": "scene_detail.json", "overgrow": count}
    assert PROP_ATLAS_EXTRA not in doc["extras"]
