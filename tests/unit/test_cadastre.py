"""Catastro INSPIRE BU: acquisition (offline, fake fetcher), parsing and matching around MURCIA_CENTER."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from onward_generator.cadastre import (
    CadastreError,
    MatchOptions,
    bbox_from_scene,
    building_parts_url,
    download_building_parts,
    load_download_record,
    match_buildings,
    overrides_document,
    parse_building_parts,
)
from onward_generator.cadastre.match import weighted_median
from onward_generator.geo.pipeline import GeoResult
from onward_generator.heights import HeightSource, estimate_heights, load_height_config
from onward_generator.heights.estimator import buildings_from_scene

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cadastre" / "synthetic_bu.gml"
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
CENTER_BBOX = (664243.98, 4205599.65, 664443.98, 4205799.65)  # MURCIA_CENTER ± 100 m in EPSG:25830


def fake_fetch(payload: bytes):
    calls: list[str] = []

    def fetch(url: str, timeout: float) -> bytes:
        calls.append(url)
        return payload

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


@pytest.fixture
def downloaded(tmp_path: Path) -> Path:
    target = tmp_path / "raw" / "bu.gml"
    download_building_parts(CENTER_BBOX, target, fetch=fake_fetch(FIXTURE.read_bytes()), now=NOW)
    return target


# --- Acquisition. ---


def test_url_is_https_bbox_in_projected_metres() -> None:
    url = building_parts_url(CENTER_BBOX)
    assert url.startswith("https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx?")
    assert "bbox=664243.98,4205599.65,664443.98,4205799.65" in url and "srsName=EPSG:25830" in url


@pytest.mark.parametrize(
    "bbox",
    [(-1.13, 37.98, -1.12, 37.99), (664443.98, 4205599.65, 664243.98, 4205799.65), (664000.0, 4205000.0, 666000.0, 4207000.0)],
)
def test_bbox_rejects_degrees_inverted_or_too_large(bbox) -> None:
    with pytest.raises(CadastreError):
        building_parts_url(bbox)


def test_download_writes_file_and_provenance(downloaded: Path) -> None:
    record = load_download_record(downloaded)
    assert record.license == "CC-BY-4.0" and "Catastro" in record.attribution
    assert "transformation" in record.license_notes
    assert record.retrieved_at == "2026-10-18T12:00:00+00:00" and record.size_bytes == downloaded.stat().st_size
    with pytest.raises(FileExistsError):
        download_building_parts(CENTER_BBOX, downloaded, fetch=fake_fetch(FIXTURE.read_bytes()), now=NOW)


def test_download_rejects_html_error_page(tmp_path: Path) -> None:
    with pytest.raises(CadastreError, match="not a WFS FeatureCollection"):
        download_building_parts(CENTER_BBOX, tmp_path / "x.gml", fetch=fake_fetch(b"<html>No se puede procesar</html>"))
    assert not (tmp_path / "x.gml").exists()


def test_tampered_file_is_detected(downloaded: Path) -> None:
    downloaded.write_bytes(downloaded.read_bytes() + b"\n")
    with pytest.raises(CadastreError, match="sha256"):
        load_download_record(downloaded)


def test_bbox_from_scene(pipeline_result: GeoResult) -> None:
    xmin, ymin, xmax, ymax = bbox_from_scene(pipeline_result.scene_dict(), 10.0)
    assert xmin == pytest.approx(664343.977 - 60, abs=0.01) and ymax == pytest.approx(4205699.652 + 40, abs=0.01)


# --- Parsing. ---


def test_parse_parts_and_report_bad_ones() -> None:
    parts, issues = parse_building_parts(FIXTURE)
    by_id = {p.local_id: p for p in parts}
    assert sorted(by_id) == [
        "0000001AA0000A_part1", "0000001AA0000A_part2", "0000002AA0000A_part1", "0000003AA0000A_part1", "0000004AA0000A_part1",
    ]
    p1 = by_id["0000001AA0000A_part1"]
    assert p1.refcat == "0000001AA0000A" and p1.floors_above == 5 and p1.floors_below == 1
    assert p1.geometry.area == pytest.approx(750.0)
    assert len(by_id["0000004AA0000A_part1"].geometry.interiors) == 1
    codes = sorted((i.code, i.local_id) for i in issues)
    assert codes == [
        ("duplicate_part", "0000001AA0000A_part1"),
        ("invalid_part_geometry", "0000006AA0000A_part1"),
        ("malformed_part", "0000005AA0000A_part1"),
        ("malformed_part", "0000007AA0000A_part1"),
    ]
    assert "srsName" in next(i.message for i in issues if i.local_id == "0000007AA0000A_part1")


def test_parse_rejects_entities(tmp_path: Path) -> None:
    bomb = tmp_path / "bomb.gml"
    bomb.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><gml:FeatureCollection/>', encoding="utf-8")
    with pytest.raises(CadastreError, match="DTD"):
        parse_building_parts(bomb)


# --- Matching. ---


def test_weighted_median() -> None:
    assert weighted_median([(5, 750.0), (2, 150.0)]) == 5
    assert weighted_median([(1, 10.0), (11, 1.0)]) == 1
    assert weighted_median([(3, 1.0), (4, 1.0)]) == 3


def test_match_statuses(pipeline_result: GeoResult) -> None:
    parts, _ = parse_building_parts(FIXTURE)
    matches = {m.building_id: m for m in match_buildings(pipeline_result.scene_dict(), parts)}
    a = matches["osm-way-101"]
    assert a.status == "matched" and a.levels == 5 and a.coverage == pytest.approx(1.0, abs=1e-3)
    assert (a.levels_min, a.levels_max) == (2, 5) and a.median_share == pytest.approx(0.833, abs=0.01)
    assert a.confidence == 0.85 and a.osm_levels == 4 and a.refcats == ("0000001AA0000A",)
    assert matches["osm-relation-201"].levels == 7 and matches["osm-relation-201"].coverage > 0.95
    assert matches["osm-way-102"].status == "no_parts"  # only an underground part
    assert matches["osm-way-104"].status == "low_coverage" and matches["osm-way-104"].coverage == pytest.approx(1 / 3, abs=0.01)
    assert "osm-way-113" not in matches  # dropped by geo: not fully inside the map


def test_overrides_feed_heights_as_authoritative_levels(pipeline_result: GeoResult, downloaded: Path, tmp_path: Path) -> None:
    scene = pipeline_result.scene_dict()
    parts, _ = parse_building_parts(downloaded)
    doc = overrides_document(match_buildings(scene, parts, MatchOptions()), load_download_record(downloaded))
    assert {e["id"] for e in doc["overrides"]} == {"osm-way-101", "osm-way-108", "osm-relation-201"}
    assert all("height_m" not in e and e["license"] == "CC-BY-4.0" for e in doc["overrides"])
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps(doc), encoding="utf-8")

    config = load_height_config(overrides_path=path)
    buildings, _ = buildings_from_scene(scene)
    result = estimate_heights(buildings, config)
    way101, way108, rel201 = (result.get(i) for i in ("osm-way-101", "osm-way-108", "osm-relation-201"))
    assert way101 and way101.height_source is HeightSource.EXPLICIT_HEIGHT and way101.height_m == 12.0  # OSM height kept
    assert way108 and way108.height_source is HeightSource.AUTHORITATIVE_LEVELS and way108.height_m == 15.0
    assert way108.levels_source == "override" and way108.height_confidence == 0.85
    assert rel201 and rel201.height_m == 21.0


def test_landmark_from_authoritative_levels_keeps_category_floor(tmp_path: Path) -> None:
    from shapely.geometry import box

    from onward_generator.heights import BuildingInput

    path = tmp_path / "o.json"
    entry = {"id": "osm-way-1", "levels": 1, "source": "synthetic", "license": "CC0-1.0", "confidence": 0.85}
    path.write_text(json.dumps({"version": "0.1.0", "overrides": [entry]}), encoding="utf-8")
    config = load_height_config(overrides_path=path)
    r = estimate_heights([BuildingInput("osm-way-1", {"building": "cathedral"}, box(0, 0, 50, 90), None, None)], config).get("osm-way-1")
    assert r and r.height_source is HeightSource.AUTHORITATIVE_LEVELS and r.height_m == 15.0
    assert r.needs_manual_review and "raised to landmark religious default" in r.rule


def test_cli_match_writes_overrides_and_report(pipeline_result: GeoResult, downloaded: Path, tmp_path: Path) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs

    scene, _ = write_outputs(pipeline_result, tmp_path / "geo")
    out = tmp_path / "cad"
    args = ["cadastre", "match", "--scene", str(scene), "--gml", str(downloaded), "--out", str(out)]
    assert main(args) == 0
    report = json.loads((out / "cadastre_match_report.json").read_text(encoding="utf-8"))
    assert report["counts"]["status_matched"] == 3 and report["counts"]["cadastre_parts"] == 5
    assert report["attribution"]["license"] == "CC-BY-4.0" and len(report["part_issues"]) == 4
    assert main(args) == 1  # refuses to overwrite


@pytest.mark.parametrize(("key", "value"), [("coordinate_system", "wgs84"), ("unit", "deg")])
def test_scene_must_be_a_local_metric_grid(pipeline_result: GeoResult, key: str, value: str) -> None:
    scene = pipeline_result.scene_dict()
    scene["frame"] = {**scene["frame"], key: value}
    with pytest.raises(CadastreError, match="^scene must be a local grid in metres$"):
        match_buildings(scene, [])


# --- Split (ADR-016). ---


def test_cli_split_writes_scene_overrides_and_report(
    pipeline_result: GeoResult, downloaded: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs
    from onward_generator.heights import run_heights

    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    out = tmp_path / "cad"
    args = [
        "cadastre", "split", "--scene", str(scene_path), "--gml", str(downloaded), "--out", str(out),
        "--min-coverage", "0.5", "--split-min-coverage", "0.8", "--min-piece-area-m2", "6", "--min-piece-width-m", "2",
    ]
    assert main(args) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == "parts=5 buildings=5 split=2 sub_buildings=4 levels=2-5"
    names = ("geo_scene.json", "heights_overrides.catastro.json", "cadastre_match_report.json")
    assert printed[1:] == [f"wrote {out / name}" for name in names]

    scene = json.loads((out / "geo_scene.json").read_text(encoding="utf-8"))
    buildings = [f["id"] for f in scene["features"] if f["kind"] == "building"]
    assert sorted(buildings) == [
        "osm-relation-201", "osm-way-101:0000001AA0000A_part1", "osm-way-101:0000001AA0000A_part2",
        "osm-way-102", "osm-way-104", "osm-way-108:0000001AA0000A_part1", "osm-way-108:0000001AA0000A_part2",
    ]
    assert scene["schema"] == "onward_generator.geo_scene" and scene["subdivision"]["counts"]["sub_buildings"] == 4
    assert scene["subdivision"]["geo_scene"]["path"] == str(scene_path.resolve())

    overrides = json.loads((out / "heights_overrides.catastro.json").read_text(encoding="utf-8"))
    assert [(e["id"], e["levels"]) for e in overrides["overrides"]] == [
        ("osm-relation-201", 7),
        ("osm-way-101:0000001AA0000A_part1", 5), ("osm-way-101:0000001AA0000A_part2", 2),
        ("osm-way-108:0000001AA0000A_part1", 5), ("osm-way-108:0000001AA0000A_part2", 2),
    ]
    report = json.loads((out / "cadastre_match_report.json").read_text(encoding="utf-8"))
    assert report["counts"]["status_matched"] == 3 and report["split"]["counts"]["osm_buildings_split"] == 2
    assert report["split"]["options"] == {
        "min_coverage": 0.8, "min_piece_area_m2": 6.0, "min_piece_width_m": 2.0, "confident_share": 0.8,
        "confidence_high": 0.85, "confidence_low": 0.7,
    }
    assert report["options"]["min_coverage"] == 0.5
    assert [b["building_id"] for b in report["split"]["buildings"]] == [
        "osm-relation-201", "osm-way-101", "osm-way-102", "osm-way-104", "osm-way-108",
    ]

    run = run_heights(out / "geo_scene.json", load_height_config(overrides_path=out / "heights_overrides.catastro.json"))
    heights = {b.id: b for b in run.result.buildings}
    low = heights["osm-way-101:0000001AA0000A_part2"]
    assert low.height_source is HeightSource.AUTHORITATIVE_LEVELS and low.estimated_levels == 2
    assert heights["osm-way-101:0000001AA0000A_part1"].estimated_levels == 5
    assert run.report_dict()["counts"]["subdivided"] == {"osm_buildings_split": 2, "sub_buildings": 4, "levels_range": [2, 5]}
    assert main(args) == 1  # refuses to overwrite


def test_cli_split_options_reach_the_split(pipeline_result: GeoResult, downloaded: Path, tmp_path: Path) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs

    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    out = tmp_path / "cad"
    args = ["cadastre", "split", "--scene", str(scene_path), "--gml", str(downloaded), "--out", str(out),
            "--split-min-coverage", "0.9", "--min-piece-area-m2", "7", "--min-piece-width-m", "1.5"]
    assert main(args) == 0
    report = json.loads((out / "cadastre_match_report.json").read_text(encoding="utf-8"))
    assert report["split"]["options"]["min_coverage"] == 0.9
    assert report["split"]["options"]["min_piece_area_m2"] == 7.0
    assert report["split"]["options"]["min_piece_width_m"] == 1.5


def test_cli_split_without_parts_copies_the_scene(pipeline_result: GeoResult, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs
    from onward_generator.heights import run_heights

    empty = FIXTURE.read_bytes().split(b"<gml:featureMember>")[0] + b"</gml:FeatureCollection>\n"
    gml = tmp_path / "raw" / "empty.gml"
    download_building_parts(CENTER_BBOX, gml, fetch=fake_fetch(empty), now=NOW)
    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    assert main(["cadastre", "split", "--scene", str(scene_path), "--gml", str(gml), "--out", str(tmp_path / "cad")]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "parts=0 buildings=5 split=0 sub_buildings=0 levels=none"
    original = json.loads(scene_path.read_text(encoding="utf-8"))
    split = json.loads((tmp_path / "cad" / "geo_scene.json").read_text(encoding="utf-8"))
    assert split["features"] == original["features"]
    overrides = json.loads((tmp_path / "cad" / "heights_overrides.catastro.json").read_text(encoding="utf-8"))
    assert overrides["overrides"] == []
    report = run_heights(tmp_path / "cad" / "geo_scene.json").report_dict()
    assert report["counts"]["subdivided"] == {"osm_buildings_split": 0, "sub_buildings": 0, "levels_range": None}


def test_match_report_has_no_split_section(pipeline_result: GeoResult, downloaded: Path, tmp_path: Path) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs

    scene, _ = write_outputs(pipeline_result, tmp_path / "geo")
    assert main(["cadastre", "match", "--scene", str(scene), "--gml", str(downloaded), "--out", str(tmp_path / "m")]) == 0
    assert "split" not in json.loads((tmp_path / "m" / "cadastre_match_report.json").read_text(encoding="utf-8"))


def _split_args(scene: Path, gml: Path, out: Path, *extra: str) -> list[str]:
    return ["cadastre", "split", "--scene", str(scene), "--gml", str(gml), "--out", str(out), *extra]


def test_cli_split_report_match_options_and_overwrite(pipeline_result: GeoResult, downloaded: Path, tmp_path: Path) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs

    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    out = tmp_path / "cad"
    assert main(_split_args(scene_path, downloaded, out, "--min-coverage", "0.3")) == 0
    report = json.loads((out / "cadastre_match_report.json").read_text(encoding="utf-8"))
    assert report["counts"]["cadastre_parts"] == 5
    assert report["cadastre"]["path"] == str(downloaded.resolve())
    assert report["counts"]["status_matched"] == 4  # way 104, a third covered, now matches
    overrides = json.loads((out / "heights_overrides.catastro.json").read_text(encoding="utf-8"))
    assert "osm-way-104" in [e["id"] for e in overrides["overrides"]]
    assert main(_split_args(scene_path, downloaded, out, "--overwrite")) == 0
    report = json.loads((out / "cadastre_match_report.json").read_text(encoding="utf-8"))
    assert report["counts"]["status_matched"] == 3


def test_cli_split_refuses_to_write_over_its_input_scene(pipeline_result: GeoResult, downloaded: Path, tmp_path: Path) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs

    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    before = scene_path.read_bytes()
    assert main(_split_args(scene_path, downloaded, scene_path.parent, "--overwrite")) == 1
    assert scene_path.read_bytes() == before


def test_cli_split_keeps_landmarks_whole(pipeline_result: GeoResult, downloaded: Path, tmp_path: Path) -> None:
    from onward_generator.cli import main
    from onward_generator.geo.pipeline import write_outputs

    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    for feature in scene["features"]:
        if feature["id"] == "osm-way-101":
            feature["tags"]["building"] = "cathedral"
    scene_path.write_text(json.dumps(scene), encoding="utf-8")
    assert main(_split_args(scene_path, downloaded, tmp_path / "cad")) == 0
    ids = [f["id"] for f in json.loads((tmp_path / "cad" / "geo_scene.json").read_text(encoding="utf-8"))["features"]]
    assert "osm-way-101" in ids and "osm-way-108:0000001AA0000A_part1" in ids


@pytest.mark.parametrize("missing", ["--scene", "--gml", "--out"])
def test_cli_split_requires_every_path(missing: str, tmp_path: Path) -> None:
    from onward_generator.cadastre.__main__ import main as cadastre_main

    args = ["split", "--scene", str(tmp_path / "s.json"), "--gml", str(tmp_path / "x.gml"), "--out", str(tmp_path / "o")]
    at = args.index(missing)
    assert cadastre_main(args[:at] + args[at + 2:]) == 2


@pytest.mark.parametrize(("extra", "margin"), [([], 20.0), (["--margin-m", "5"], 5.0)])
def test_cli_download_passes_the_margin(
    extra: list[str], margin: float, pipeline_result: GeoResult, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from onward_generator.cadastre import __main__ as cadastre_cli
    from onward_generator.geo.pipeline import write_outputs

    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    seen: list[tuple[tuple[float, ...], str]] = []

    def fake_download(bbox, out, *, crs, overwrite):
        seen.append((tuple(bbox), crs))
        return download_building_parts(CENTER_BBOX, out, fetch=fake_fetch(FIXTURE.read_bytes()), now=NOW)

    monkeypatch.setattr(cadastre_cli, "download_building_parts", fake_download)
    assert cadastre_cli.main(["download", "--scene", str(scene_path), "--out", str(tmp_path / "c.gml"), *extra]) == 0
    assert seen == [(bbox_from_scene(json.loads(scene_path.read_text(encoding="utf-8")), margin), "EPSG:25830")]
