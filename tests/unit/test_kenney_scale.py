"""Kenney kit boxes become street metres once, and stay there."""

from __future__ import annotations

import json
from pathlib import Path

from pytest import approx

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb, read_glb
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.overgrow.fit import measure
from onward_generator.overgrow.kenney_scale import (
    MODULAR_SCALE,
    SUBURBAN_SCALE,
    URBAN_SCALE,
    bounds_yz_up,
    normalize_installed,
    scale_glb_positions,
    street_factor,
)

REPO = Path(__file__).resolve().parents[2]


def test_street_factor_follows_the_pack_and_leaves_a_pallet_near_life_size() -> None:
    assert street_factor("urban", "vehicle", 0.83, 1.643) == URBAN_SCALE
    assert street_factor("urban", "barrier", 0.664, 0.249) == URBAN_SCALE
    assert street_factor("urban", "debris", 0.5, 0.5) == 2.6
    assert street_factor("urban", "debris", 1.0, 1.0) == 1.25
    assert street_factor("modular", "door", 0.25, 0.05) == MODULAR_SCALE
    assert street_factor("suburban", "fence", 1.675, 0.438) == SUBURBAN_SCALE


def test_positions_scale_and_the_texture_uri_stays() -> None:
    mesh = mesh_box(-0.5, 0.5, -0.25, 0.25, 0.0, 1.0)
    data = build_glb(
        [Node("box", (Primitive(mesh, "concrete"),))],
        {"concrete": Material((1.0, 1.0, 1.0, 1.0), 1.0)},
    )
    doc, blob = read_glb(data)
    doc["images"] = [{"uri": "Textures/concrete.png", "name": "concrete"}]
    data = _pack(doc, blob)
    scaled = scale_glb_positions(data, 3.2)
    length_m, depth_m, height_m = bounds_yz_up(scaled)
    assert length_m == approx(3.2)
    assert depth_m == approx(1.6)
    assert height_m == approx(3.2)
    nodes, _materials = nodes_from_glb(scaled)
    assert measure(nodes[0].primitives[0].mesh)[2] == approx(3.2)
    reread, _blob = read_glb(scaled)
    assert reread["images"][0]["uri"] == "Textures/concrete.png"
    assert reread["extras"]["street_scale"] == 3.2
    assert "NORMAL" in reread["meshes"][0]["primitives"][0]["attributes"]


def test_a_second_pass_does_not_scale_again(tmp_path: Path) -> None:
    mesh = mesh_box(-0.4, 0.4, -0.2, 0.2, 0.0, 0.3)
    data = build_glb(
        [Node("barrier", (Primitive(mesh, "concrete"),))],
        {"concrete": Material((1.0, 1.0, 1.0, 1.0), 1.0)},
    )
    folder = tmp_path / "assets" / "kenney" / "urban"
    folder.mkdir(parents=True)
    (folder / "barrier.glb").write_bytes(data)
    (tmp_path / "assets" / "kenney" / "manifest.json").write_text(json.dumps({
        "schema": "onward_generator.abandoned_prefabs",
        "assets": [{
            "id": "kenney_urban_barrier",
            "file": "urban/barrier.glb",
            "role": "barrier",
            "length_m": 0.8,
            "depth_m": 0.4,
            "height_m": 0.3,
        }],
    }), encoding="utf-8")
    (tmp_path / "assets" / "kenney_review.json").write_text(json.dumps({
        "note": "kit",
        "assets": [{"id": "kenney_urban_barrier", "length_m": 0.8, "depth_m": 0.4, "height_m": 0.3}],
    }), encoding="utf-8")
    (tmp_path / "config" / "dress").mkdir(parents=True)
    (tmp_path / "config" / "dress" / "imported_library.json").write_text(json.dumps({
        "objects": [{"id": "kenney_urban_barrier", "sha256": "0", "bytes": 1}],
    }), encoding="utf-8")
    assert normalize_installed(tmp_path) == 1
    once = bounds_yz_up((folder / "barrier.glb").read_bytes())
    assert normalize_installed(tmp_path) == 0
    assert bounds_yz_up((folder / "barrier.glb").read_bytes()) == once
    manifest = json.loads((tmp_path / "assets" / "kenney" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["assets"][0]["height_m"] == approx(0.3 * URBAN_SCALE, abs=0.01)


def test_installed_kenney_boxes_match_the_street_and_the_files() -> None:
    manifest = json.loads((REPO / "assets" / "kenney" / "manifest.json").read_text(encoding="utf-8"))
    review = json.loads((REPO / "assets" / "kenney_review.json").read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in manifest["assets"]}
    installed = [row for row in review["assets"] if row["installed"] is True]
    assert len(installed) == len(by_id) == 177
    for card in installed:
        row = by_id[card["id"]]
        assert card["length_m"] == row["length_m"]
        assert card["depth_m"] == row["depth_m"]
        assert card["height_m"] == row["height_m"]
        data = (REPO / "assets" / "kenney" / row["file"]).read_bytes()
        length_m, depth_m, height_m = bounds_yz_up(data)
        assert abs(length_m - row["length_m"]) < 0.0015
        assert abs(depth_m - row["depth_m"]) < 0.0015
        assert abs(height_m - row["height_m"]) < 0.0015
        doc, _blob = read_glb(data)
        assert doc["extras"]["street_scale"] == card["street_scale"]
        assert doc["images"]
        assert str(doc["images"][0]["uri"]).startswith("Textures/")
    expect = {
        "kenney_urban_door_type_a": ("height_m", 2.01),
        "kenney_urban_truck_grey": ("depth_m", 5.26),
        "kenney_urban_detail_barrier_strong_type_a": ("height_m", 1.06),
        "kenney_urban_detail_dumpster_closed": ("height_m", 1.74),
        "kenney_urban_pallet": ("length_m", 1.25),
        "kenney_urban_detail_block": ("length_m", 1.30),
        "kenney_suburban_fence_1x4": ("height_m", 1.89),
        "kenney_suburban_tree_large": ("height_m", 5.37),
        "kenney_modular_door_brown_glass": ("height_m", 2.05),
    }
    for asset_id, (axis, metres) in expect.items():
        assert by_id[asset_id][axis] == approx(metres, abs=0.02)
    removed = next(row for row in review["assets"] if row["id"] == "kenney_suburban_building_type_a")
    assert removed["installed"] is False
    assert removed["height_m"] == 0.834
    assert "street_scale" not in removed


def _pack(doc: dict, blob: bytes) -> bytes:
    import json as json_lib
    import struct

    json_chunk = json_lib.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    total = 12 + 8 + len(json_chunk) + 8 + len(blob)
    return b"".join((
        struct.pack("<4sII", b"glTF", 2, total),
        struct.pack("<I4s", len(json_chunk), b"JSON"),
        json_chunk,
        struct.pack("<I4s", len(blob), b"BIN\x00"),
        blob,
    ))
