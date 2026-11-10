"""Overture on the synthetic street block around MURCIA_CENTER: fetch (local parquet) → match → heights → generate → detail.

Building 312 holds the OSM bakery POI 401; an Overture place inside it is ignored (OSM ranks first).
An Overture place inside 313 gives that residential building a shop. Overture footprints equal to
313 (height 13 m) and 311 (6 floors, ML) lend them heights over their OSM building:levels.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from shapely import Polygon

from onward_generator.cli import main as cli_main
from onward_generator.detail.__main__ import main as detail_main
from onward_generator.generation.__main__ import main as generate_main
from onward_generator.generation.gltf import nodes_from_glb, read_glb
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
    assert cli_main(["heights", "--scene", str(scene), "--overrides", str(survey),
                     "--secondary-overrides", str(out["overture"] / "heights_overrides.overture.json"), "--out", str(out["heights"])]) == 0
    heights = out["heights"] / "heights.json"
    assert generate_main(["--scene", str(scene), "--heights", str(heights), "--out", str(out["base"]), "--no-preview"]) == 0
    assert detail_main(["--scene", str(scene), "--heights", str(heights), "--base", str(out["base"]), "--no-preview",
                        "--places", str(out["overture"] / "overture_places.json"), "--out", str(out["detail"])]) == 0
    return out


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _building(manifest: dict, bid: str) -> dict:
    return next(o for o in manifest["objects"] if o.get("type") == "building" and o["source_ids"][0] == bid)


def test_match_writes_overrides_places_and_a_report(tmp_path: Path) -> None:
    run = _pipeline(tmp_path)
    report = _json(run["overture"] / "overture_match_report.json")
    assert report["places"]["counts"] == {"rows": 4, "kept": 4, "skipped": {}}
    assert report["buildings"]["counts"]["matched"] == 2 and report["buildings"]["counts"]["by_origin"] == {"ai_generated": 1, "observed": 1}
    overrides = _json(run["overture"] / "heights_overrides.overture.json")["overrides"]
    assert [(o["id"], o.get("height_m"), o.get("levels"), o["origin"]) for o in overrides] == [
        ("osm-way-311", None, 6, "ai_generated"), ("osm-way-313", 13.0, None, "observed"),
    ]


def test_overture_heights_outrank_osm_levels(tmp_path: Path) -> None:
    run = _pipeline(tmp_path)
    heights = {b["building_id"]: b for b in _json(run["heights"] / "heights.json")["buildings"]}
    assert heights["osm-way-313"]["height_source"] == "third_party_height" and heights["osm-way-313"]["height_m"] == 13.0
    assert heights["osm-way-311"]["height_source"] == "third_party_levels" and heights["osm-way-311"]["estimated_levels"] == 6
    assert heights["osm-way-312"]["height_source"] == "explicit_levels"
    secondary = _json(run["heights"] / "heights.json")["secondary_overrides"]
    assert secondary["used"] == 2 and {s["license"] for s in secondary["sources"]} == {"CC-BY-4.0", "ODbL-1.0"}


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
    assert "Overture Maps buildings 2026-10-21.0: Microsoft ML Buildings" in texts
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


# Plan C2: Overture colours, opt-in, through match --tints, detail --overture-tints and materials.


def _tinted(tmp: Path) -> dict[str, Path]:
    """The block with colours: 312 lends an Esri facade (no height), 313 an OSM facade that is skipped, 311 an ML roof that is refused.

    Two runs share the inputs: ``off`` without any tints flag, ``on`` with ``match --tints`` and ``detail --overture-tints``.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    osm = write_block_osm(tmp / "block.osm", ORIGIN, BUILDINGS, POIS)
    src = register_local_source(osm, license_info=SYNTHETIC_LICENSE, retrieved_at=FIXED_NOW, data_timestamp="2026-10-18T00:00:00Z", now=FIXED_NOW)
    scene, _ = write_outputs(run_geo_pipeline(src, load_aoi_geojson(AOI_FIXTURE), origin=ORIGIN), tmp / "geo")
    root = tmp / "map"
    create_map(root, map_id="block", name="Block", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    places = write_places(tmp / "remote" / "places.parquet", [place("in-313", 7.0, 12.0)])
    buildings = write_buildings(tmp / "remote" / "buildings.parquet", [
        building("b-312", Polygon(BUILDINGS[312][0]), height=None, floors=None, facade="#c88c3c", roof="#b43c28"),
        building("b-313", Polygon(BUILDINGS[313][0]), height=13.0, floors=None, facade="#ffffff",
                 sources=[source("Esri Community Maps", None), source("OpenStreetMap", "ODbL-1.0", "/properties/facade_color")]),
        building("b-311", Polygon(BUILDINGS[311][0]), height=None, floors=6, roof="#323237", sources=[source("Microsoft ML Buildings", "ODbL-1.0")]),
    ])
    fetch_map(root, release="2026-10-21.0", sources={"places": str(places), "buildings": str(buildings)}, connect=local_connect)
    out: dict[str, Path] = {"scene": scene}
    for run, flags in (("off", []), ("on", ["--tints"])):
        match = tmp / run / "overture"
        assert cli_main(["overture", "match", "--scene", str(scene), "--map", str(root), "--out", str(match), *flags]) == 0
        heights_dir = tmp / run / "heights"
        assert cli_main(["heights", "--scene", str(scene), "--secondary-overrides", str(match / "heights_overrides.overture.json"),
                         "--out", str(heights_dir)]) == 0
        heights = heights_dir / "heights.json"
        base = tmp / run / "scene3d"
        assert generate_main(["--scene", str(scene), "--heights", str(heights), "--out", str(base), "--no-preview"]) == 0
        tinted = ["--overture-tints", str(match / "overture_tints.json")] if flags else []
        detail = tmp / run / "detail"
        assert detail_main(["--scene", str(scene), "--heights", str(heights), "--base", str(base), "--no-preview",
                            "--places", str(match / "overture_places.json"), *tinted, "--out", str(detail)]) == 0
        out |= {f"{run}_overture": match, f"{run}_heights": heights, f"{run}_base": base, f"{run}_detail": detail}
    out["plain_detail"] = tmp / "on" / "plain_detail"  # the on-run's inputs, no --overture-tints
    assert detail_main(["--scene", str(scene), "--heights", str(out["on_heights"]), "--base", str(out["on_base"]), "--no-preview",
                        "--places", str(out["on_overture"] / "overture_places.json"), "--out", str(out["plain_detail"])]) == 0
    return out


def test_tints_are_lent_observed_and_off_changes_nothing(tmp_path: Path) -> None:
    import hashlib

    run = _tinted(tmp_path)
    doc = _json(run["on_overture"] / "overture_tints.json")
    assert doc["counts"] == {"rows": 3, "matched": 1, "facade": 1, "roof": 1,
                             "skipped": {"origin_not_observed": 1, "skipped_dataset": 1, "unmatched": 0}}
    assert [(t["building_id"], t["gers_id"], t["facade"]["colour"], t["roof"]["colour"]) for t in doc["tints"]] == [
        ("osm-way-312", "b-312", "#c88c3c", "#b43c28")]
    assert not (run["off_overture"] / "overture_tints.json").exists()

    def digest(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    # The colours never reach the heights, and without --overture-tints detail is what it was.
    for name in ("heights_overrides.overture.json", "overture_places.json"):
        assert digest(run["off_overture"] / name) == digest(run["on_overture"] / name)
    off_heights, on_heights = _json(run["off_heights"]), _json(run["on_heights"])
    assert off_heights["buildings"] == on_heights["buildings"]  # the documents differ only in the path of the overrides they read
    assert off_heights["secondary_overrides"]["used"] == on_heights["secondary_overrides"]["used"] == 2
    off, plain = _json(run["off_detail"] / "scene_detail.json"), _json(run["plain_detail"] / "scene_detail.json")
    assert off["objects"] == plain["objects"] and off["attribution"] == plain["attribution"]
    assert _json(run["off_detail"] / "detail_report.json")["counts"] == _json(run["plain_detail"] / "detail_report.json")["counts"]

    manifest = _json(run["on_detail"] / "scene_detail.json")
    materials = _building(manifest, "osm-way-312")["facade"]["materials"]
    assert (materials["wall"], materials["wall_origin"], materials["wall_source"]) == ("plaster_white__tint_ochre", "observed", "overture:facade_color")
    assert materials["wall_evidence"] == {"provider": "overture", "gers_id": "b-312", "dataset": "Esri Community Maps",
                                          "license": "CC-BY-4.0", "origin": "observed", "colour": "#c88c3c", "iou": 1.0}
    assert materials["roof_source"] == "overture:roof_color"
    assert "wall_evidence" not in _building(manifest, "osm-way-313")["facade"]["materials"]
    counts = _json(run["on_detail"] / "detail_report.json")["counts"]["third_party_tints"]
    assert counts["matched"] == 1 and counts["walls"] == 1 and counts["by_dataset"] == {"Esri Community Maps": 2}
    assert "Esri Community Maps contributors; Overture Maps Foundation" in [a["text"] for a in manifest["attribution"]]
    assert "Esri Community Maps contributors; Overture Maps Foundation" not in [a["text"] for a in off["attribution"]]
    nodes = {n.name: n for n in nodes_from_glb((run["on_detail"] / "scene_visual.glb").read_bytes())[0]}
    assert nodes["building/osm-way-312"].extras["tint_source"] == "overture"
    assert "tint_source" not in nodes["building/osm-way-313"].extras


def test_a_tinted_wall_stays_out_of_the_trim_atlas(tmp_path: Path) -> None:
    from onward_generator.materials.library_plan import select_texture_plan
    from onward_generator.perf.measure import combine_groups

    run = _tinted(tmp_path / "run")
    lon, lat = MURCIA_CENTER_LON, MURCIA_CENTER_LAT
    plan = tmp_path / "texture_plan.json"
    plan.write_text(json.dumps(select_texture_plan(name="Block", map_id="block", latitude=lat, longitude=lon, brief=None, seed=1)))
    draws = {}
    for name in ("off", "on"):
        out = tmp_path / f"materials_{name}"
        assert cli_main(["materials", "apply", "--base", str(run[f"{name}_detail"]), "--scene", str(run["scene"]), "--plan", str(plan),
                         "--out", str(out)]) == 0
        report = _json(out / "materials_report.json")
        kept = report["facades"]["stats"]["kept_materials"]
        if name == "on":
            assert kept.get("plaster_white__tint_ochre", 0) >= 1
            assert "plaster_white__tint_ochre" not in report["facades"]["materials"]
            nodes = {n.name: n for n in nodes_from_glb((out / "scene_visual.glb").read_bytes())[0]}
            assert nodes["building/osm-way-312"].extras["tint_source"] == "overture"
        else:
            assert not any("__tint_" in m for m in kept)
        draws[name] = combine_groups(out / "scene_visual.glb", 100.0)["draws"]
    # Each tint is a material outside the atlas: the wall and the roof tint add draws.
    assert draws["on"] > draws["off"]
    print(f"C2 draws off={draws['off']} on={draws['on']}")
