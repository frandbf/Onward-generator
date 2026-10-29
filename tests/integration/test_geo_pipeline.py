"""IT-001 / IT-005 for the geo module, and the four required demonstrations."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from onward_generator.domain.models import AttributeOrigin, OsmRef
from onward_generator.geo.__main__ import main
from onward_generator.geo.clipping import ClipStatus
from onward_generator.geo.pipeline import GeoOptions, GeoResult, run_geo_pipeline, write_outputs
from onward_generator.geo.sources import SourceError, register_local_source, sha256_file
from tests.conftest import AOI_FIXTURE, ORIGIN, OSM_FIXTURE, SYNTHETIC_LICENSE

WAY = lambda i: OsmRef("way", i)  # noqa: E731 - a short alias for the fixtures below


# --- Demonstration 1: known geometry lands where it should, in metres. ---


def test_known_building_footprint_in_local_metres(pipeline_result: GeoResult) -> None:
    building = pipeline_result.feature(WAY(101))
    assert building is not None
    minx, miny, maxx, maxy = building.geometry_local.bounds
    assert (minx, miny, maxx, maxy) == pytest.approx((-50, -50, -20, -20), abs=0.005)
    assert building.geometry_local.area == pytest.approx(900, abs=0.5)
    multipolygon = pipeline_result.feature(OsmRef("relation", 201))
    assert multipolygon is not None and multipolygon.geometry_local.area == pytest.approx(1500, abs=0.5)


def test_frame_metadata(pipeline_result: GeoResult) -> None:
    frame = pipeline_result.frame.to_dict()
    assert frame["crs"] == "EPSG:25830"
    assert frame["unit"] == "m"
    assert frame["origin_wgs84"] == list(ORIGIN)


# --- Demonstration 2: clipping keeps the expected entities. ---


def test_clipping_keeps_expected_entities(pipeline_result: GeoResult) -> None:
    status = {f.ref: f.clip_status for f in pipeline_result.features}
    assert status == {
        WAY(101): ClipStatus.INSIDE,
        WAY(102): ClipStatus.MARGIN,
        WAY(104): ClipStatus.MARGIN,
        WAY(105): ClipStatus.CLIPPED,
        WAY(106): ClipStatus.INSIDE,
        WAY(108): ClipStatus.INSIDE,
        OsmRef("relation", 201): ClipStatus.INSIDE,
        OsmRef("node", 40): ClipStatus.INSIDE,
    }
    counts = pipeline_result.report.counts
    assert counts["dropped_outside"] == 3  # way 103, way 113, node 41


def test_building_that_leaves_the_map_is_absent(pipeline_result: GeoResult) -> None:
    assert pipeline_result.feature(WAY(113)) is None


def test_road_is_cut_at_the_margin(pipeline_result: GeoResult) -> None:
    road = pipeline_result.feature(WAY(105))
    assert road is not None
    assert road.geometry_local.bounds == pytest.approx((-130, 10, 130, 10), abs=0.01)


def test_smaller_margin_drops_buildings_that_no_longer_fit(synthetic_source, synthetic_aoi) -> None:
    from dataclasses import replace

    result = run_geo_pipeline(synthetic_source, replace(synthetic_aoi, margin_m=5.0), origin=ORIGIN)
    assert result.feature(WAY(104)) is None
    assert result.feature(WAY(102)) is None  # reaches x = 120; a 5 m margin ends at 105
    assert result.feature(WAY(101)) is not None


# --- Demonstration 3: geometric errors are detected. ---


def test_geometric_errors_are_reported(pipeline_result: GeoResult) -> None:
    report = pipeline_result.report
    assert "invalid_geometry" in report.codes_for(WAY(107))
    assert "missing_node_reference" in report.codes_for(WAY(109))
    assert "degenerate_ring" in report.codes_for(WAY(112))
    assert "duplicate_geometry" in report.codes_for(WAY(108))
    assert "duplicate_element_id" in report.codes_for(WAY(101))
    assert pipeline_result.feature(WAY(107)) is None
    assert report.counts["rejected_invalid"] == 1


def test_repair_option_keeps_invalid_geometry_as_repaired(synthetic_source, synthetic_aoi) -> None:
    result = run_geo_pipeline(synthetic_source, synthetic_aoi, origin=ORIGIN, options=GeoOptions(repair_invalid=True))
    bowtie = result.feature(WAY(107))
    assert bowtie is not None and bowtie.geometry_repaired
    assert bowtie.geometry_local.is_valid
    assert result.report.counts["repaired"] == 1


# --- Demonstration 4: the source is never modified. ---


def test_source_file_is_not_modified(tmp_path: Path) -> None:
    source_copy = tmp_path / "input.osm"
    shutil.copy2(OSM_FIXTURE, source_copy)
    before_hash, before_stat = sha256_file(source_copy), source_copy.stat()

    source = register_local_source(source_copy, license_info=SYNTHETIC_LICENSE)
    from onward_generator.geo.aoi import load_aoi_geojson

    result = run_geo_pipeline(source, load_aoi_geojson(AOI_FIXTURE), origin=ORIGIN, options=GeoOptions(repair_invalid=True))
    write_outputs(result, tmp_path / "out")

    after_stat = source_copy.stat()
    assert sha256_file(source_copy) == before_hash == source.sha256
    assert (after_stat.st_size, after_stat.st_mtime_ns) == (before_stat.st_size, before_stat.st_mtime_ns)
    assert source_copy.read_bytes() == OSM_FIXTURE.read_bytes()


def test_pipeline_refuses_a_source_changed_after_registration(tmp_path: Path, synthetic_aoi) -> None:
    source_copy = tmp_path / "input.osm"
    shutil.copy2(OSM_FIXTURE, source_copy)
    source = register_local_source(source_copy, license_info=SYNTHETIC_LICENSE)
    with source_copy.open("a", encoding="utf-8") as handle:
        handle.write("\n<!-- edited -->\n")
    with pytest.raises(SourceError, match="changed"):
        run_geo_pipeline(source, synthetic_aoi, origin=ORIGIN)


def test_data_timestamp_is_read_from_the_file(synthetic_aoi) -> None:
    source = register_local_source(OSM_FIXTURE, license_info=SYNTHETIC_LICENSE)
    assert source.data_timestamp is None
    result = run_geo_pipeline(source, synthetic_aoi, origin=ORIGIN)
    assert result.source.data_timestamp == "2026-10-18T00:00:00Z"
    assert result.report_dict()["source"]["data_timestamp"] == "2026-10-18T00:00:00Z"


def test_original_tags_are_preserved_verbatim(pipeline_result: GeoResult) -> None:
    building = pipeline_result.feature(WAY(101))
    assert building is not None
    assert dict(building.tags) == {"building": "yes", "height": "12", "building:levels": "4", "name": "Edificio Sintético A"}


# --- Attributes and report. ---


def test_observed_and_inferred_attributes_are_distinguished(pipeline_result: GeoResult) -> None:
    b1, b2, road = (pipeline_result.feature(WAY(i)) for i in (101, 102, 105))
    assert b1 and b2 and road
    assert b1.attributes["height_m"].origin is AttributeOrigin.OBSERVED
    assert "height_m" not in b2.attributes  # heights are estimated by onward_generator.heights
    assert b2.attributes["levels"].origin is AttributeOrigin.OBSERVED
    assert road.attributes["width_m"].origin is AttributeOrigin.INFERRED
    assert "unparseable_tag" in pipeline_result.report.codes_for(WAY(104))
    counts = pipeline_result.report.counts
    assert counts["attributes_observed"] == 4
    assert counts["attributes_inferred"] == 1
    assert counts["attributes_generated"] == 0


def test_report_counts(pipeline_result: GeoResult) -> None:
    counts = pipeline_result.report.counts
    assert counts["osm_nodes"] == 41 and counts["osm_ways"] == 13 and counts["osm_relations"] == 2
    assert counts["kept"] == 8
    assert counts["kept_building"] == 5 and counts["kept_highway"] == 2 and counts["kept_poi"] == 1
    assert counts["status_inside"] == 5 and counts["status_margin"] == 2 and counts["status_clipped"] == 1
    assert counts["errors"] >= 3 and counts["warnings"] >= 3


def test_outputs_written_and_reproducible(pipeline_result: GeoResult, tmp_path: Path, synthetic_source, synthetic_aoi) -> None:
    scene_path, report_path = write_outputs(pipeline_result, tmp_path)
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert scene["schema"] == "onward_generator.geo_scene"
    assert scene["attribution"]["license"] == "CC0-1.0"
    assert scene["source"]["sha256"] == sha256_file(OSM_FIXTURE)
    feature = next(f for f in scene["features"] if f["id"] == "osm-way-101")
    assert feature["geometry"]["unit"] == "m"
    assert feature["source"] == {"provider": "synthetic-fixture", "license": "CC0-1.0", "osm_type": "way", "osm_id": 101}
    assert feature["attributes"]["height_m"] == {"value": 12.0, "origin": "observed", "source_tag": "height"}
    assert report["counts"]["kept"] == 8
    assert report["issues_by_code"]["invalid_geometry"] == 1
    assert "tiny_polygon" not in report["issues_by_code"]
    coords = [c for f in scene["features"] if f["geometry"]["type"] == "Polygon" for c in f["geometry"]["coordinates"][0]]
    assert all(str(v) != "-0.0" for c in coords for v in c)

    again = run_geo_pipeline(synthetic_source, synthetic_aoi, origin=ORIGIN)
    assert again.scene_dict() == pipeline_result.scene_dict()


def test_outputs_get_normal_file_permissions(pipeline_result: GeoResult, tmp_path: Path) -> None:
    import os

    umask = os.umask(0)
    os.umask(umask)
    for path in write_outputs(pipeline_result, tmp_path):
        assert path.stat().st_mode & 0o777 == 0o666 & ~umask


def test_outputs_are_not_overwritten_without_confirmation(pipeline_result: GeoResult, tmp_path: Path) -> None:
    write_outputs(pipeline_result, tmp_path)
    with pytest.raises(FileExistsError):
        write_outputs(pipeline_result, tmp_path)
    write_outputs(pipeline_result, tmp_path, overwrite=True)


def test_cli_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "geo"
    args = ["--source", str(OSM_FIXTURE), "--aoi", str(AOI_FIXTURE), "--out", str(out), "--origin", *map(str, ORIGIN)]
    assert main(args) == 0
    assert (out / "geo_scene.json").is_file() and (out / "geo_report.json").is_file()
    assert "kept:     8" in capsys.readouterr().out
    assert main(args) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
