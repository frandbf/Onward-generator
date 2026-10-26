"""props place: surface parking only; no invented cars on the carriageway."""

from __future__ import annotations

import json
from pathlib import Path

from onward_generator.cli import main
from onward_generator.props.catalog import bind_inventory, catalog_document, load_catalog
from onward_generator.props.place import plan_instances
from tests.conftest import MURCIA_CENTER


def _bound_catalog(tmp_path: Path):
    car = tmp_path / "LowPolyCar_02.prefab"
    car.write_bytes(b"prefab")
    return bind_inventory(load_catalog(), [{"pack_id": "327313", "path": str(car)}])


def test_plan_instances_skips_underground_and_uses_surface_parking(tmp_path: Path) -> None:
    _ = MURCIA_CENTER
    catalog = _bound_catalog(tmp_path)
    features = [
        {
            "id": "osm-node-1",
            "tags": {"amenity": "parking_entrance", "parking": "underground"},
            "geometry": {"type": "Point", "coordinates": [-1.12863, 37.98407]},
        },
        {
            "id": "osm-way-park",
            "tags": {"amenity": "parking"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [0.0, 0.0], [4.0, 0.0], [4.0, 4.0], [0.0, 4.0], [0.0, 0.0],
                ]],
            },
        },
    ]
    instances, skipped = plan_instances(features, catalog)
    assert len(instances) == 1
    assert instances[0]["origin"] == "osm"
    assert instances[0]["source_ids"] == ["osm-way-park"]
    assert instances[0]["x"] == 2.0 and instances[0]["y"] == 2.0
    assert not any(s["reason"] == "no_surface_parking_osm" for s in skipped)


def test_place_cli_writes_zero_cars_without_surface_parking(tmp_path: Path) -> None:
    bound = _bound_catalog(tmp_path)
    catalog_path = tmp_path / "bound_catalog.json"
    catalog_path.write_text(json.dumps(catalog_document(bound)), encoding="utf-8")
    detail = tmp_path / "base"
    detail.mkdir()
    scene = tmp_path / "geo_scene.json"
    scene.write_text(json.dumps({
        "features": [{
            "id": "osm-node-u",
            "tags": {"amenity": "parking_entrance", "parking": "underground"},
            "geometry": {"type": "Point", "coordinates": list(MURCIA_CENTER)},
        }],
    }), encoding="utf-8")
    (detail / "scene_visual.glb").write_bytes(b"glTF-fake")
    (detail / "scene_collision.glb").write_bytes(b"glTF-fake")
    (detail / "scene_detail.json").write_text(json.dumps({
        "objects": [{"id": "surface/carriageway", "type": "surface", "source_ids": ["osm-way-2"]}],
        "inputs": {"geo_scene": {"path": str(scene)}},
    }), encoding="utf-8")
    out = tmp_path / "props_01"
    assert main(["props", "place", "--base", str(detail), "--out", str(out), "--catalog", str(catalog_path)]) == 0
    report = json.loads((out / "props_report.json").read_text())
    assert report["vehicles"] == 0
    assert report["geometry"]["identical"] is True
    assert any(s["reason"] == "no_surface_parking_osm" for s in report["skipped"])
    assert (out / "props_instances.json").is_file()
