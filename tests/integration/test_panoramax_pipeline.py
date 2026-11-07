"""Panoramax on the synthetic Murcia block. The picture client is fake."""

from __future__ import annotations

import hashlib
import io
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image
from shapely import Point
from shapely.geometry import Polygon, shape

from onward_generator import run_log
from onward_generator.cli import main
from onward_generator.generation.gltf import Node, images_by_material, nodes_from_glb, read_glb
from onward_generator.geo.projection import LocalFrame
from onward_generator.maps.workspace import create_map, load_map
from onward_generator.materials.trim_style import load_style_table, trim_layout
from onward_generator.panoramax.__main__ import run_apply
from onward_generator.panoramax.apply import _safe, cell_material, strip_colours
from onward_generator.panoramax.cache import PanoramaxError
from onward_generator.tiers import combine_cell, tier_summary
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON
from tests.integration.test_materials_pipeline import _cache_asset, _polyhaven_plan

PICTURE_ID = "33333333-3333-3333-3333-333333333333"
FAR_ID = "44444444-4444-4444-4444-444444444444"
PALE = (225, 205, 165)
CLOSE_IDS = ("55555555-5555-5555-5555-555555555555", "66666666-6666-6666-6666-666666666666")


def _jpeg(color: tuple[int, int, int] = (180, 40, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 32), color).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


class _Client:
    def __init__(self, *features: dict[str, Any], color: tuple[int, int, int] = (180, 40, 40)) -> None:
        self.features = list(features)
        self.color = color

    def get_json(self, url: str) -> dict[str, Any]:
        return {"type": "FeatureCollection", "features": self.features, "links": []}

    def get_bytes(self, url: str) -> bytes:
        if url.endswith(".jpg"):
            return _jpeg(self.color)
        if url.endswith(".broken"):
            return b"not a jpeg"
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


@dataclass
class _Block:
    root: Path
    scene: Path
    materials: Path
    camera: Point
    close: tuple[Point, Point]
    chosen_id: str
    interiors: set[str]
    listed_interiors: set[str]

    def client(self, color: tuple[int, int, int] = (180, 40, 40), *, close: bool = False) -> _Client:
        # A picture far from every facade is cached but never decoded, so its broken JPEG does not matter.
        far = _feature(float(self.camera.x), float(self.camera.y) - 0.01)
        far["id"] = FAR_ID
        far["assets"] = {"sd": {"href": f"https://example.test/{FAR_ID}.broken", "type": "image/jpeg"}}
        features = [_feature(float(self.camera.x), float(self.camera.y)), far]
        if close:
            # 3.2 m from the facade, either side of the main stretch: matched, but too close to paint
            # 2 m of wall above 2.2 m. Two of them, so the skipped rows outnumber the applied one.
            for picture_id, point in zip(CLOSE_IDS, self.close, strict=True):
                near = _feature(float(point.x), float(point.y))
                near["id"] = picture_id
                near["assets"] = {"sd": {"href": f"https://example.test/{picture_id}.jpg", "type": "image/jpeg"}}
                features.append(near)
        return _Client(*features, color=color)

    def set_map(self, **values: object) -> None:
        path = self.root / "map.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        for key, value in values.items():
            if value is None:
                document.pop(key, None)
            else:
                document[key] = value
        path.write_text(json.dumps(document), encoding="utf-8")


def _block(detail_inputs: tuple[Path, Path, Path], tmp_path: Path, monkeypatch, facades: tuple[str, ...] = ()) -> _Block:
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
        *facades,
    ]) == 0

    manifest = json.loads((materials / "scene_detail.json").read_text(encoding="utf-8"))
    detail_report = json.loads((materials / "detail_report.json").read_text(encoding="utf-8"))
    listed_interiors = {str(item) for item in detail_report["counts"]["interiors"] + detail_report["counts"]["shop_interiors"]}
    assert detail_report["counts"]["shop_interiors"]  # the bakery: its see-through shopfront gets no photo
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
    # Beside the main camera's stretch of the same face, where the main camera is out of its 30 degrees.
    close = tuple(frame.to_wgs84(Point(midpoint[0] + dx, midpoint[1] - 3.2)) for dx in (-6.5, 6.5))

    return _Block(root, scene, materials, camera, close, chosen_id, interiors, listed_interiors)


def test_a_photo_replaces_only_that_building(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    block = _block(detail_inputs, tmp_path, monkeypatch)
    root, scene, materials, chosen_id = block.root, block.scene, block.materials, block.chosen_id
    interiors, listed_interiors = block.interiors, block.listed_interiors
    out = root / "data" / "processed" / "scene_panoramax"
    assert run_apply(materials, out, client=block.client()) == 0
    report = json.loads((out / "panoramax_report.json").read_text(encoding="utf-8"))
    # With the atlas switch off the report keeps today's keys and every face its own material.
    assert set(report) == {
        "schema", "schema_version", "assignments", "skipped_faces", "skipped_buildings", "license", "origin",
        "collision_sha256",
    }
    assert report["schema_version"] == "0.1.0"
    photo_ids = {str(row["building_id"]) for row in report["assignments"]}
    assert FAR_ID not in {str(row["picture_id"]) for row in report["assignments"]}
    assert FAR_ID in json.loads((root / "data" / "raw" / "panoramax" / "cache_report.json").read_text(encoding="utf-8"))["kept"]
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
    face_material = re.compile(r"^pano-[A-Za-z0-9-]+-[0-9]+$")
    assert all(face_material.match(str(item["name"])) and not str(item["name"]).startswith("pano-cell-") for item in photo_materials)
    assert {str(item["name"]) for item in photo_materials} == {f"pano-{_safe(name)}-{row['segment_index']}" for name in photo_ids for row in report["assignments"] if row["building_id"] == name}
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


def _trim_strip_v(uvs: np.ndarray | None, strip) -> np.ndarray:
    if uvs is None:
        return np.zeros(0, dtype=bool)
    tolerance = 0.5 / 4096
    return (uvs[:, 1] >= strip.v0 - tolerance) & (uvs[:, 1] <= strip.v1 + tolerance)


def test_panoramax_atlas_false_writes_what_no_key_writes(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    block = _block(detail_inputs, tmp_path, monkeypatch)
    plain = block.root / "data" / "processed" / "scene_panoramax"
    assert run_apply(block.materials, plain, client=block.client()) == 0
    block.set_map(panoramax_atlas=False)
    switched = block.root / "data" / "processed" / "scene_panoramax_false"
    assert run_apply(block.materials, switched, client=block.client()) == 0
    for name in ("scene_visual.glb", "panoramax_report.json", "scene_collision.glb"):
        assert (plain / name).read_bytes() == (switched / name).read_bytes()


def test_panoramax_atlas_on_trim_facades(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    block = _block(detail_inputs, tmp_path, monkeypatch)
    block.set_map(panoramax_atlas=True)
    # The red photo's nearest strip is the photographed building's own: the tint is recorded as unchanged.
    red = block.root / "data" / "processed" / "scene_panoramax_red"
    assert run_apply(block.materials, red, client=block.client()) == 0
    red_report = json.loads((red / "panoramax_report.json").read_text(encoding="utf-8"))
    assert red_report["tints"] == []
    assert red_report["tint_skipped"] == [{"building_id": block.chosen_id, "reason": "unchanged"}]
    red_nodes = {node.name: node for node in nodes_from_glb((red / "scene_visual.glb").read_bytes())[0]}
    assert "panoramax_tint" not in red_nodes[block.chosen_id].extras
    # A pale stone photo moves the wall to another tone of the same family. A camera 3.2 m from another
    # facade is matched but paints nothing (elevation_window).
    for item in (tmp_path / "panoramax-catalogue").glob("*.jpg"):
        item.unlink()
    block.set_map(panoramax_atlas=False)
    legacy = block.root / "data" / "processed" / "scene_panoramax_legacy"
    assert run_apply(block.materials, legacy, client=block.client(PALE, close=True)) == 0
    legacy_report = json.loads((legacy / "panoramax_report.json").read_text(encoding="utf-8"))
    block.set_map(panoramax_atlas=True)
    out = block.root / "data" / "processed" / "scene_panoramax"
    assert run_apply(block.materials, out, client=block.client(PALE, close=True)) == 0
    printed = capsys.readouterr().out
    report = json.loads((out / "panoramax_report.json").read_text(encoding="utf-8"))
    assert f"coverage={report['coverage']['percent']}% draws_added={report['draws_added']}" in printed

    # The switch changes the decals' images and adds keys; the matching and the provenance stay.
    assert {key: report[key] for key in legacy_report} == legacy_report
    assert set(report) - set(legacy_report) == {"atlas", "coverage", "decal_cells", "draws_added", "tints", "tint_skipped"}
    assert sum(row["reason"] == "elevation_window" for row in report["skipped_faces"]) == 2
    legacy_doc, _ = read_glb((legacy / "scene_visual.glb").read_bytes())
    atlas_doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    assert atlas_doc["extras"] == legacy_doc["extras"]
    assert atlas_doc["extras"]["panoramax"]["assignments"] == report["assignments"]
    roughness = {item["name"]: item["pbrMetallicRoughness"].get("roughnessFactor") for item in atlas_doc["materials"]}
    legacy_roughness = {item["name"]: item["pbrMetallicRoughness"].get("roughnessFactor") for item in legacy_doc["materials"]}
    face_values = {value for name, value in legacy_roughness.items() if name.startswith("pano-")}
    assert {value for name, value in roughness.items() if name.startswith("pano-cell-")} == face_values

    # An existing output is not overwritten without --overwrite.
    assert run_apply(block.materials, out, client=block.client(PALE, close=True)) == 1
    assert "panoramax:" in capsys.readouterr().err
    materials_report = json.loads((block.materials / "materials_report.json").read_text(encoding="utf-8"))
    style = materials_report["facades"]["style"]
    assert materials_report["facades"]["mode"] == "trim" and style == "mediterranean"
    photo_ids = {str(row["building_id"]) for row in report["assignments"]}
    assert block.chosen_id in photo_ids

    # Collision is the materials collision, byte for byte.
    assert (block.materials / "scene_collision.glb").read_bytes() == (out / "scene_collision.glb").read_bytes()

    before_glb = (block.materials / "scene_visual.glb").read_bytes()
    after_glb = (out / "scene_visual.glb").read_bytes()
    before = {node.name: node for node in nodes_from_glb(before_glb)[0]}
    after = {node.name: node for node in nodes_from_glb(after_glb)[0]}
    assert set(before) == set(after)

    # One decal material per combine cell, named after it; never a trim or CC0 image.
    images_before = images_by_material(before_glb)
    images_after = images_by_material(after_glb)
    trim_png = images_before[f"facade_trim_{style}"]
    assert images_after[f"facade_trim_{style}"] == trim_png
    decal_materials = {prim.material for node in after.values() for prim in node.primitives if prim.material.startswith("pano-")}
    assert decal_materials
    assert all(re.fullmatch(r"pano-cell--?[0-9]+_-?[0-9]+", name) for name in decal_materials)
    assert all(images_after[name] != trim_png for name in decal_materials)
    for name in decal_materials:
        assert not name.startswith("facade_trim_")
    cell_of = {
        name: combine_cell(node.world_primitives(), 100.0)
        for name, node in after.items()
        if any(prim.material.startswith("pano-") for prim in node.primitives)
    }
    for name, cell in cell_of.items():
        assert {prim.material for prim in after[name].primitives if prim.material.startswith("pano-")} == {cell_material(cell)}
    assert len(report["decal_cells"]) == len(set(cell_of.values())) == len(decal_materials)
    for row in report["decal_cells"]:
        members = sorted(name for name, cell in cell_of.items() if cell_material(cell) == row["material"])
        assert row["buildings"] == members
        assert row["cell"] == list(cell_of[members[0]])
        assert row["decals"] == sum(len(after[name].extras["panoramax"]) for name in members)
        assert Image.open(io.BytesIO(images_after[row["material"]])).size == (row["side_px"], row["side_px"])
    assert all(row["decals"] <= 10 and row["scale"] <= 1.0 for row in report["decal_cells"])
    assert report["atlas"] == {"cell_m": 100.0, "side_px": 2048}

    # Draws: what the adapter's (cell, material) combine predicts after minus before.
    draws_before = tier_summary(before_glb, [], Polygon(), cell_m=100.0)["draws"]
    draws_after = tier_summary(after_glb, [], Polygon(), cell_m=100.0)["draws"]
    assert draws_after - draws_before == report["draws_added"] == len(report["decal_cells"])
    assert report["draws_added"] <= 10 * len(report["decal_cells"])

    # Buildings without a photo, interiors and their shells keep their primitives byte for byte.
    for name, node in after.items():
        if name in photo_ids:
            continue
        assert len(node.primitives) == len(before[name].primitives)
        for old, new in zip(before[name].primitives, node.primitives, strict=True):
            assert old.material == new.material
            assert old.mesh.vertices.tobytes() == new.mesh.vertices.tobytes()
            assert old.mesh.triangles.tobytes() == new.mesh.triangles.tobytes()
            assert (old.uvs is None and new.uvs is None) or old.uvs.tobytes() == new.uvs.tobytes()

    # The photographed building keeps its positions and indices; only its wall-strip V moves.
    table = load_style_table()
    layout = trim_layout(table.styles[style].atlas)
    families = {str(k): str(v) for k, v in table.styles[style].atlas["walls"].items()}
    assert report["tints"], report["tint_skipped"]
    tint = report["tints"][0]
    assert tint["building_id"] == block.chosen_id
    assert tint["origin"] == "observed" and tint["source"] == "panoramax"
    assert tint["picture_ids"] == [PICTURE_ID] and tint["authors"] == ["tester"]
    source = layout.tones[tint["from"]["wall"]][tint["from"]["tone"]]
    target = layout.tones[tint["to"]["wall"]][tint["to"]["tone"]]
    assert families[tint["from"]["wall"]] == families[tint["to"]["wall"]]
    rgb = np.asarray(Image.open(io.BytesIO(trim_png)).convert("RGB"), dtype=np.float64)
    colours = strip_colours(rgb, layout)
    expected = min(
        (key for key in colours if families[key[0]] == families[tint["from"]["wall"]]),
        key=lambda key: (sum((a - b) ** 2 for a, b in zip(tint["rgb"], colours[key])), key),
    )
    assert (tint["to"]["wall"], tint["to"]["tone"]) == expected
    # The fake photo is one colour: its median, through JPEG, stays near it.
    assert all(abs(got - want) <= 6 for got, want in zip(tint["rgb"], PALE, strict=True))
    photographed = after[block.chosen_id]
    assert {key: photographed.extras[key] for key in before[block.chosen_id].extras} == dict(before[block.chosen_id].extras)
    assert photographed.extras["panoramax_tint"] == {key: value for key, value in tint.items() if key != "building_id"}
    shell = [prim for prim in photographed.primitives if not prim.material.startswith("pano-")]
    assert len(shell) == len(before[block.chosen_id].primitives)
    moved = 0
    for old, new in zip(before[block.chosen_id].primitives, shell, strict=True):
        assert old.material == new.material
        assert old.mesh.vertices.tobytes() == new.mesh.vertices.tobytes()
        assert old.mesh.triangles.tobytes() == new.mesh.triangles.tobytes()
        if old.uvs is None:
            continue
        inside = _trim_strip_v(old.uvs, source)
        assert np.array_equal(old.uvs[:, 0], new.uvs[:, 0])
        assert np.array_equal(old.uvs[~inside], new.uvs[~inside])
        if inside.any():
            assert np.all(_trim_strip_v(new.uvs[inside], target))
            moved += int(inside.sum())
    assert moved

    # Coverage adds up, and every crop keeps its author, licence and origin.
    cover = report["coverage"]
    assert 0 < cover["photo_m"] <= cover["exposed_m"]
    assert cover["percent"] == round(100 * cover["photo_m"] / cover["exposed_m"], 1)
    rows = {row["building_id"]: row for row in cover["buildings"]}
    assert [row["building_id"] for row in cover["buildings"]] == sorted(rows)
    assert rows[block.chosen_id]["percent"] > 0
    assert block.interiors.isdisjoint(rows)
    assert all(row["photo_m"] == 0 for name, row in rows.items() if name not in photo_ids)
    assert all(row["license"] == "CC-BY-SA-4.0" and row["origin"] == "observed" and row["author"] == "tester" for row in report["assignments"])
    notes = [note for name in photo_ids for note in after[name].extras["panoramax"]]
    assert notes and all(note["license"] == "CC-BY-SA-4.0" and note["origin"] == "observed" and note["author"] == "tester" for note in notes)
    assert run_log._panoramax(out).summary.endswith(f"coverage {cover['percent']} %  +{report['draws_added']} draws")


def test_panoramax_atlas_on_legacy_facades_skips_the_tint(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    block = _block(detail_inputs, tmp_path, monkeypatch, facades=("--facades", "legacy"))
    block.set_map(panoramax_atlas=True)
    out = block.root / "data" / "processed" / "scene_panoramax"
    assert run_apply(block.materials, out, client=block.client()) == 0
    report = json.loads((out / "panoramax_report.json").read_text(encoding="utf-8"))
    assert report["tints"] == []
    assert report["tint_skipped"] == [{"building_id": block.chosen_id, "reason": "no_trim_atlas"}]
    after = nodes_from_glb((out / "scene_visual.glb").read_bytes())[0]
    decals = {prim.material for node in after for prim in node.primitives if prim.material.startswith("pano-")}
    assert decals == {row["material"] for row in report["decal_cells"]}
    assert all(name.startswith("pano-cell-") for name in decals)


def test_panoramax_atlas_cell_that_does_not_fit_writes_nothing(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    block = _block(detail_inputs, tmp_path, monkeypatch)
    block.set_map(panoramax_atlas=True)
    monkeypatch.setattr("onward_generator.panoramax.apply._CELL_SIDE", 4)
    out = block.root / "data" / "processed" / "scene_panoramax"
    assert run_apply(block.materials, out, client=block.client()) == 1
    assert "do not fit in 4 px" in capsys.readouterr().err
    assert not out.exists()


def test_panoramax_atlas_refuses_a_scene_from_another_style_table(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    block = _block(detail_inputs, tmp_path, monkeypatch)
    block.set_map(panoramax_atlas=True)
    path = block.materials / "materials_report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    report["facades"]["style_table_sha256"] = "0" * 64
    path.write_text(json.dumps(report), encoding="utf-8")
    out = block.root / "data" / "processed" / "scene_panoramax"
    assert run_apply(block.materials, out, client=block.client()) == 1
    assert "rerun materials" in capsys.readouterr().err
    assert not out.exists()


def test_panoramax_atlas_reads_the_cell_from_the_card_and_overwrites_on_request(
    detail_inputs: tuple[Path, Path, Path],
    tmp_path: Path,
    monkeypatch,
) -> None:
    from onward_generator.panoramax.apply import apply_pictures

    block = _block(detail_inputs, tmp_path, monkeypatch)
    block.set_map(panoramax_atlas=True)
    card_path = block.root / "config" / "export.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["build"]["combine_cell_m"] = 50
    card_path.write_text(json.dumps(card), encoding="utf-8")
    out = block.root / "data" / "processed" / "scene_panoramax"
    assert run_apply(block.materials, out, client=block.client()) == 0
    assert run_apply(block.materials, out, overwrite=True, client=block.client()) == 0
    report = json.loads((out / "panoramax_report.json").read_text(encoding="utf-8"))
    assert report["atlas"] == {"cell_m": 50.0, "side_px": 2048}
    after = nodes_from_glb((out / "scene_visual.glb").read_bytes())[0]
    for node in after:
        decals = {prim.material for prim in node.primitives if prim.material.startswith("pano-")}
        if decals:
            assert decals == {cell_material(combine_cell(node.world_primitives(), 50.0))}
    # Called directly, the stage keeps the switch off unless asked.
    plain_out = block.root / "data" / "processed" / "scene_panoramax_plain"
    plain = apply_pictures(block.materials, plain_out)
    assert "coverage" not in plain
    with pytest.raises(FileExistsError):
        apply_pictures(block.materials, plain_out)
    # Without materials_report.json there is no trim atlas to tint from.
    (block.materials / "materials_report.json").unlink()
    bare = apply_pictures(block.materials, block.root / "data" / "processed" / "scene_panoramax_bare", atlas=True)
    assert bare["tint_skipped"] == [{"building_id": block.chosen_id, "reason": "no_trim_atlas"}]
