"""Overture places on the synthetic street block around MURCIA_CENTER: fetch (local parquet) → match → heights → generate → detail.

Building 312 holds the OSM bakery POI 401; an Overture place inside it is ignored (OSM ranks first).
An Overture place inside 313 gives that residential building a shop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely import Polygon

from onward_generator.cli import main as cli_main
from onward_generator.detail.__main__ import main as detail_main
from onward_generator.generation.__main__ import main as generate_main
from onward_generator.generation.gltf import read_glb
from onward_generator.geo.aoi import load_aoi_geojson
from onward_generator.geo.pipeline import run_geo_pipeline, write_outputs
from onward_generator.geo.sources import register_local_source
from onward_generator.maps.workspace import create_map
from onward_generator.overture.acquire import fetch_map
from tests.conftest import AOI_FIXTURE, FIXED_NOW, MURCIA_CENTER_LAT, MURCIA_CENTER_LON, ORIGIN, SYNTHETIC_LICENSE
from tests.detail_fixture import BUILDINGS, POIS, write_block_osm
from tests.overture_fixture import building, local_connect, place, source, write_buildings, write_places


def _pipeline(tmp: Path) -> dict[str, Path]:
    """The whole chain, run inside each test body so mutation testing sees every call."""
    tmp.mkdir(parents=True, exist_ok=True)
    osm = write_block_osm(tmp / "block.osm", ORIGIN, BUILDINGS, POIS)
    src = register_local_source(osm, license_info=SYNTHETIC_LICENSE, retrieved_at=FIXED_NOW, data_timestamp="2026-10-18T00:00:00Z", now=FIXED_NOW)
    scene, _ = write_outputs(run_geo_pipeline(src, load_aoi_geojson(AOI_FIXTURE), origin=ORIGIN), tmp / "geo")

    root = tmp / "map"
    create_map(root, map_id="block", name="Block", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    places = write_places(tmp / "remote" / "places.parquet", [
        place("in-312", -7.0, 14.0, name="Otra Panadería"),
        place("in-313", 7.0, 12.0, name="Farmacia Sintética", primary="pharmacy", basic="health_and_medical",
              sources=[source("Foursquare", "Apache-2.0")]),
        place("in-311", -22.0, 12.0, name="Zapatería", primary="shoe_store", basic="fashion_and_apparel_store",
              sources=[source("AllThePlaces", "CC0-1.0")]),
        place("in-311b", -25.0, 12.0, name="Zapatería Dos", primary="shoe_store", basic="fashion_and_apparel_store",
              sources=[source("AllThePlaces", "CC0-1.0")]),
    ])
    buildings = write_buildings(tmp / "remote" / "buildings.parquet", [
        building("b-313", Polygon(BUILDINGS[313][0]), height=13.0, floors=None),
        building("b-311", Polygon(BUILDINGS[311][0]), height=None, floors=6, sources=[source("Microsoft ML Buildings", "ODbL-1.0")]),
    ])
    fetch_map(root, release="2026-10-21.0", sources={"places": str(places), "buildings": str(buildings)}, connect=local_connect)

    out = {"scene": scene, "overture": tmp / "overture", "heights": tmp / "heights", "base": tmp / "scene3d", "detail": tmp / "detail"}
    assert cli_main(["overture", "match", "--scene", str(scene), "--map", str(root), "--out", str(out["overture"])]) == 0
    survey = tmp / "survey.json"
    survey.write_text(json.dumps({"version": "0.1.0", "overrides": [
        {"id": "osm-way-315", "levels": 1, "source": "Synthetic survey", "license": "CC0-1.0"}]}), encoding="utf-8")
    assert cli_main(["heights", "--scene", str(scene), "--overrides", str(survey), "--out", str(out["heights"])]) == 0
    heights = out["heights"] / "heights.json"
    assert generate_main(["--scene", str(scene), "--heights", str(heights), "--out", str(out["base"]), "--no-preview"]) == 0
    assert detail_main(["--scene", str(scene), "--heights", str(heights), "--base", str(out["base"]), "--no-preview",
                        "--places", str(out["overture"] / "overture_places.json"), "--out", str(out["detail"])]) == 0
    return out


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _building(manifest: dict, bid: str) -> dict:
    return next(o for o in manifest["objects"] if o.get("type") == "building" and o["source_ids"][0] == bid)


def test_match_writes_places_and_a_report(tmp_path: Path) -> None:
    run = _pipeline(tmp_path)
    report = _json(run["overture"] / "overture_match_report.json")
    assert report["places"]["counts"] == {"rows": 4, "kept": 4, "skipped": {}}
    assert not (run["overture"] / "heights_overrides.overture.json").exists()


def test_a_place_gives_a_shop_only_where_osm_has_none(tmp_path: Path) -> None:
    run = _pipeline(tmp_path)
    manifest = _json(run["detail"] / "scene_detail.json")
    bakery = _building(manifest, "osm-way-312")["facade"]
    assert [s["poi_id"] for s in bakery["shops"]] == ["osm-node-1023"]  # the OSM bakery 401 (node ids are renumbered)
    assert "poi_source" not in bakery["shops"][0]
    assert bakery["ground_floor_evidence"]["source"] == "osm_poi"
    pharmacy = _building(manifest, "osm-way-313")["facade"]
    assert pharmacy["ground_floor"] == "shopfront"
    assert pharmacy["ground_floor_evidence"] == {"source": "overture_place", "overture_place_ids": ["overture-place-in-313"]}
    (shop,) = pharmacy["shops"]
    assert shop["name"] == "Farmacia Sintética" and shop["name_origin"] == "observed"
    assert shop["poi_source"] == {"provider": "overture", "gers_id": "in-313", "dataset": "Foursquare", "license": "Apache-2.0",
                                  "confidence": 0.9, "attribute_origin": "observed", "observed_by": "third_party"}
    counts = _json(run["detail"] / "detail_report.json")["counts"]["third_party_shops"]
    assert counts == {"places": 4, "buildings": 2, "shops": 3, "by_dataset": {"AllThePlaces": 2, "Foursquare": 1}}


def test_the_attribution_names_the_datasets_used(tmp_path: Path) -> None:
    run = _pipeline(tmp_path)
    attribution = _json(run["detail"] / "scene_detail.json")["attribution"]
    texts = [a["text"] for a in attribution]
    assert texts[-2:] == ["Data from AllThePlaces; Overture Maps Foundation", "Data from Foursquare; Overture Maps Foundation"]
    assert "Data from Foursquare; Overture Maps Foundation" in texts
    assert "Data from meta; Overture Maps Foundation" not in texts  # the meta place sat in 312, where OSM ranks first
    assert "Synthetic survey" in texts
    doc, _ = read_glb((run["detail"] / "scene_visual.glb").read_bytes())
    extras = doc["asset"]["extras"] if "extras" in doc.get("asset", {}) else doc["extras"]
    assert extras["attribution"] == attribution and extras["manifest"] == "scene_detail.json"
    assert extras["fidelity"] == "generic templates; not a faithful reconstruction"
    foursquare = next(a for a in attribution if a["text"].startswith("Data from Foursquare"))
    assert foursquare["notice"].startswith("Copyright 2024 Foursquare Labs, Inc.")


def test_detail_refuses_places_from_another_grid(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run = _pipeline(tmp_path / "run")
    doc = _json(run["overture"] / "overture_places.json")
    bad = tmp_path / "places.json"
    args = ["--scene", str(run["scene"]), "--heights", str(run["heights"] / "heights.json"), "--base", str(run["base"]),
            "--no-preview", "--places", str(bad), "--out", str(tmp_path / "d")]
    bad.write_text(json.dumps({**doc, "frame": {**doc["frame"], "crs": "EPSG:32630"}}), encoding="utf-8")
    assert detail_main(args) == 1
    assert capsys.readouterr().err == f"detail: {bad} was made for another local grid than {run['scene']}\n"
    doc["frame"]["origin_projected"] = [0.0, 0.0]
    bad.write_text(json.dumps(doc), encoding="utf-8")
    assert detail_main(args) == 1
    assert "was made for another local grid" in capsys.readouterr().err
    bad.write_text(json.dumps({**doc, "schema": "x"}), encoding="utf-8")
    assert detail_main(args) == 1
    assert capsys.readouterr().err == f"detail: {bad} is not onward_generator.overture_places\n"
    assert detail_main([*args[:-4], "--places", str(tmp_path / "missing.json"), "--out", str(tmp_path / "d")]) == 1
    assert not (tmp_path / "d" / "scene_detail.json").exists()
