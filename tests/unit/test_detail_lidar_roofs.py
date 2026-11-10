"""Plan C3: a flat or pitched roof from the nDSM p25-p75 spread, estimated and recorded with its origin."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from shapely import Polygon, affinity, box

from onward_generator.detail import lidar_roofs
from onward_generator.detail.__main__ import load_roof_spread, main
from onward_generator.detail.lidar_roofs import (
    DEFAULT_MAX_RISE_M,
    MIN_RISE_M,
    REASONS,
    RoofSpread,
    SpreadReport,
    classify,
    fill,
    half_span,
    is_roof_tagged,
    spread_spec,
)
from onward_generator.detail.scene import DetailError

RECT = box(0, 0, 20, 10)  # half span 5 m


def _spread(p25: float | None = 10.0, p75: float | None = 11.5, valid: int = 20, cells: int = 24, status: str = "measured") -> RoofSpread:
    return RoofSpread(status, p25, p75, valid, cells)


def _classify(spread: RoofSpread | None, footprint: Polygon = RECT, **flags: bool) -> str:
    return classify(spread, footprint, **({"tagged": False, "scenic": False, "terrace": False} | flags))


def test_a_small_spread_is_flat_up_to_the_limit() -> None:
    assert _classify(_spread(12.0, 12.0)) == "flat"
    assert _classify(_spread(0.0, 0.6)) == "flat"  # exactly FLAT_MAX_SPREAD_M
    assert _classify(_spread(10.0, 10.61)) == "pitched"


def test_a_moderate_spread_on_a_rectangle_is_pitched_and_drawn_as_a_capped_gable() -> None:
    spread = _spread(10.0, 11.5)
    assert _classify(spread) == "pitched"
    spec = spread_spec(spread, "roof_tile")
    assert (spec.shape, spec.tag, spec.material, spec.rise_m, spec.rise_rule) == ("gabled", "gabled", "roof_tile", 1.5, "ndsm_spread")
    assert spec.direction_deg is None and spec.across is False and spec.angle_deg is None


def test_the_slope_bounds_fall_on_the_documented_side() -> None:
    # s = spread / half span on a 10 m wide rectangle (half span 5 m).
    assert _classify(_spread(10.0, 10.5)) == "flat"  # 0.5 m is flat before s is read
    assert _classify(_spread(10.0, 10.5), box(0, 0, 40, 20)) == "flat"
    wide = box(0, 0, 60, 40)  # half span 20 m: s = 2 / 20 = 0.1 exactly
    assert _classify(_spread(10.0, 12.0), wide) == "pitched"
    assert _classify(_spread(10.0, 11.75), wide) == "ambiguous"  # s = 0.0875
    assert _classify(_spread(10.0, 12.5)) == "pitched"  # s = 0.5 exactly
    assert _classify(_spread(10.0, 12.75)) == "mixed"  # s = 0.55


def test_too_few_cells_and_no_measurement_do_not_classify() -> None:
    assert _classify(_spread(valid=7)) == "too_few_cells"
    assert _classify(_spread(valid=8)) == "pitched"
    assert _classify(None) == "no_data"
    for status in ("no_data", "out_of_bounds"):
        assert _classify(_spread(status=status)) == "no_data"
    assert _classify(_spread(p25=None)) == "no_data"
    assert _classify(_spread(p75=None)) == "no_data"


def test_a_mixed_footprint_is_never_read_as_pitched() -> None:
    assert _classify(_spread(10.0, 13.0), box(0, 0, 20, 6)) == "mixed"  # a 6 m part, a 3 m step: s = 1.0
    notch = Polygon([(0, 0), (20, 0), (20, 5), (14, 5), (14, 10), (0, 10)])  # 170 of 200 m²: fill 0.85
    assert fill(notch) == 0.85
    assert _classify(_spread(10.0, 11.5), notch) == "pitched"
    deeper = Polygon([(0, 0), (20, 0), (20, 6), (12, 6), (12, 10), (0, 10)])  # 168 of 200 m²
    assert fill(deeper) == pytest.approx(0.84)
    assert _classify(_spread(10.0, 11.5), deeper) == "not_rectangular"
    assert _classify(_spread(10.0, 15.0), terrace=True) == "terrace"


def test_the_first_failing_check_names_the_reason() -> None:
    assert _classify(_spread(valid=2), tagged=True, scenic=True, terrace=True) == "tagged"
    assert _classify(None, scenic=True, terrace=True) == "scenic"
    assert _classify(_spread(valid=2), terrace=True) == "terrace"
    assert _classify(_spread(valid=2, p25=None)) == "no_data"
    assert _classify(_spread(10.0, 10.2, valid=3)) == "too_few_cells"
    assert _classify(_spread(10.0, 10.2), Polygon([(0, 0), (20, 0), (20, 2), (2, 2), (2, 10), (0, 10)])) == "flat"
    assert REASONS == ("tagged", "scenic", "terrace", "no_data", "too_few_cells", "not_rectangular", "mixed", "ambiguous", "fallback")


def test_the_rise_is_the_spread_within_the_builder_limits() -> None:
    assert spread_spec(_spread(10.0, 16.0), "roof_tile").rise_m == DEFAULT_MAX_RISE_M
    assert spread_spec(_spread(10.0, 14.0), "roof_tile").rise_m == 4.0
    assert spread_spec(_spread(10.0, 10.05), "roof_tile").rise_m == MIN_RISE_M
    assert spread_spec(_spread(10.0, 10.0), "roof_tile").rise_m == MIN_RISE_M
    assert spread_spec(_spread(10.0, 12.25), "roof_flat_grey").rise_m == 2.25
    for missing in (_spread(p25=None), _spread(p75=None)):
        with pytest.raises(ValueError) as raised:
            spread_spec(missing, "roof_tile")
        assert str(raised.value) == "a gable needs both percentiles; classify gives pitched only with both"


def test_half_span_and_fill_read_the_minimum_rotated_rectangle() -> None:
    turned = affinity.rotate(box(0, 0, 30, 8), 45, origin=(0, 0))
    assert half_span(turned) == pytest.approx(4.0)
    assert fill(turned) == pytest.approx(1.0)
    assert half_span(box(0, 0, 8, 30)) == 4.0
    assert half_span(RECT) == 5.0


def test_a_tag_on_the_roof_form_counts_and_a_colour_does_not() -> None:
    for key in ("roof:shape", "roof:height", "roof:levels", "roof:angle"):
        assert is_roof_tagged({key: "1"})
    assert not is_roof_tagged({"roof:colour": "red", "roof:material": "tiles", "building": "yes"})
    assert not is_roof_tagged({"roof:shape": " "})
    assert not is_roof_tagged({})


def test_the_same_input_classifies_the_same_way() -> None:
    first = [_classify(_spread(10.0, 10.0 + k / 4)) for k in range(20)]
    assert first == [_classify(_spread(10.0, 10.0 + k / 4)) for k in range(20)]
    assert spread_spec(_spread(), "roof_tile") == spread_spec(_spread(), "roof_tile")


def test_the_evidence_names_the_plugin_licence_and_measurement() -> None:
    report = SpreadReport("r.json", "abc", "es.pnoa_ndsm", "IGN MDSn", "CC-BY-4.0", "ign.es", {})
    assert report.evidence(_spread(10.0, 11.5, 20, 24), "pitched") == {
        "source": "es.pnoa_ndsm", "provider": "IGN MDSn", "license": "CC-BY-4.0", "p25_m": 10.0, "p75_m": 11.5,
        "valid": 20, "cells": 24, "rule": "ndsm_spread", "class": "pitched", "origin": "estimated",
    }
    assert lidar_roofs.RULE == "ndsm_spread"


# --- load_roof_spread -----------------------------------------------------------------------------------------


def _scene() -> dict[str, Any]:
    building = {"kind": "building", "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [20, 0], [20, 10], [0, 10], [0, 0]]]}}
    return {"schema": "onward_generator.geo_scene", "frame": {"crs": "EPSG:25830"},
            "features": [building | {"id": "osm-way-1"}, building | {"id": "osm-way-2"}, {"id": "n1", "kind": "poi"}]}


def _entry(**changes: Any) -> dict[str, Any]:
    return {"id": "osm-way-1", "status": "measured", "date": "d", "cells": 24, "valid": 20, "buffer_m": 1.0, "height_m": 10.8,
            "p25_m": 10.0, "p75_m": 11.5} | changes


def _files(tmp_path: Path, buildings: list[Any] | None, **doc: Any) -> tuple[str, str]:
    scene_path = tmp_path / "geo_scene.json"
    scene_path.write_text(json.dumps(_scene()), encoding="utf-8")
    digest = hashlib.sha256(scene_path.read_bytes()).hexdigest()
    document = {"schema": "onward_generator.source_heights_report", "schema_version": "1.0.0", "source": "es.pnoa_ndsm",
                "provider": "IGN MDSn", "license": "CC-BY-4.0", "attribution": "ign.es",
                "scene": {"path": "/elsewhere/geo_scene.json", "sha256": digest}, "buildings": buildings} | doc
    path = tmp_path / "es.pnoa_ndsm_report.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return str(path), str(scene_path)


def test_no_path_reads_nothing_and_a_valid_report_reads_its_spreads(tmp_path: Path) -> None:
    assert load_roof_spread(None, "unused.json") is None
    path, scene_path = _files(tmp_path, [_entry(), _entry(id="osm-way-2", status="no_data", p25_m=None, p75_m=None, valid=1, cells=1)])
    report = load_roof_spread(path, scene_path)
    assert report is not None
    assert (report.path, report.source, report.provider, report.license, report.attribution) == (
        path, "es.pnoa_ndsm", "IGN MDSn", "CC-BY-4.0", "ign.es")
    assert report.sha256 == hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert report.buildings == {"osm-way-1": RoofSpread("measured", 10.0, 11.5, 20, 24),
                                "osm-way-2": RoofSpread("no_data", None, None, 1, 1)}
    assert isinstance(report.buildings["osm-way-1"].p25_m, float)
    whole, scene_path = _files(tmp_path, [_entry(p25_m=10, p75_m=12, valid=0, cells=0)])
    loaded = load_roof_spread(whole, scene_path)
    assert loaded is not None and loaded.buildings["osm-way-1"] == RoofSpread("measured", 10.0, 12.0, 0, 0)
    legacy, scene_path = _files(tmp_path, [], schema="murcia_generator.source_heights_report")
    assert load_roof_spread(legacy, scene_path) is not None
    level, scene_path = _files(tmp_path, [_entry(p25_m=11.5)])
    loaded = load_roof_spread(level, scene_path)
    assert loaded is not None and loaded.buildings["osm-way-1"].p25_m == loaded.buildings["osm-way-1"].p75_m == 11.5
    # A scene without features has no building; a report naming none still loads.
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({"frame": {}}), encoding="utf-8")
    empty, _ = _files(tmp_path, [])
    document = json.loads(Path(empty).read_text(encoding="utf-8"))
    document["scene"]["sha256"] = hashlib.sha256(bare.read_bytes()).hexdigest()
    Path(empty).write_text(json.dumps(document), encoding="utf-8")
    loaded = load_roof_spread(empty, str(bare))
    assert loaded is not None and loaded.buildings == {}


@pytest.mark.parametrize(("entry", "message"), [
    ("x", "an entry needs id and status"),
    (_entry(id=7), "an entry needs id and status"),
    (_entry(status=None), "an entry needs id and status"),
    (_entry(id="osm-way-9"), "building 'osm-way-9' is not in the scene"),
    (_entry(id="n1"), "building 'n1' is not in the scene"),
    (_entry(p25_m="10"), "building osm-way-1: p25_m and p75_m must be numbers or null"),
    (_entry(p75_m=True), "building osm-way-1: p25_m and p75_m must be numbers or null"),
    ({k: v for k, v in _entry().items() if k != "p25_m"}, "building osm-way-1: p25_m and p75_m must be numbers or null"),
    ({k: v for k, v in _entry().items() if k != "p75_m"}, "building osm-way-1: p25_m and p75_m must be numbers or null"),
    (_entry(valid=-1), "building osm-way-1: valid and cells must be counts"),
    (_entry(cells=2.5), "building osm-way-1: valid and cells must be counts"),
    (_entry(valid=True), "building osm-way-1: valid and cells must be counts"),
    ({k: v for k, v in _entry().items() if k != "cells"}, "building osm-way-1: valid and cells must be counts"),
    (_entry(valid=25), "building osm-way-1: 25 valid cells of 24"),
    (_entry(p25_m=12.0), "building osm-way-1: p25_m 12.0 is above p75_m 11.5"),
])
def test_a_malformed_entry_is_refused(tmp_path: Path, entry: Any, message: str) -> None:
    path, scene_path = _files(tmp_path, [entry])
    with pytest.raises(DetailError) as raised:
        load_roof_spread(path, scene_path)
    assert str(raised.value) == f"{path}: {message}"


def test_another_schema_another_scene_a_missing_field_or_file_is_refused(tmp_path: Path) -> None:
    path, scene_path = _files(tmp_path, [_entry()], schema="onward_generator.overture_tints")
    with pytest.raises(DetailError) as raised:
        load_roof_spread(path, scene_path)
    assert str(raised.value) == f"{path} is not onward_generator.source_heights_report"
    path, scene_path = _files(tmp_path, [_entry()], scene={"sha256": "0" * 64})
    with pytest.raises(DetailError) as raised:
        load_roof_spread(path, scene_path)
    assert str(raised.value) == f"{path} was measured on another scene than {scene_path}"
    path, scene_path = _files(tmp_path, [_entry()], scene=None)
    with pytest.raises(DetailError, match="was measured on another scene"):
        load_roof_spread(path, scene_path)
    for key in ("source", "provider", "license", "attribution"):
        path, scene_path = _files(tmp_path, [_entry()], **{key: None})
        with pytest.raises(DetailError) as raised:
            load_roof_spread(path, scene_path)
        assert str(raised.value) == f"{path}: the report needs source, provider, license, attribution"
    path, scene_path = _files(tmp_path, None)
    report = load_roof_spread(path, scene_path)
    assert report is not None and report.buildings == {}
    missing = str(tmp_path / "none.json")
    with pytest.raises(DetailError) as raised:
        load_roof_spread(missing, scene_path)
    assert str(raised.value) == f"{missing} not found; rerun source normalize for this scene"


def test_the_cli_exits_one_before_writing_anything(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path, scene_path = _files(tmp_path, [_entry()], scene={"sha256": "0" * 64})
    out = tmp_path / "detail"
    args = ["--scene", scene_path, "--heights", str(tmp_path / "h.json"), "--base", str(tmp_path / "b"), "--no-preview",
            "--lidar-roofs", path, "--out", str(out)]
    assert main(args) == 1
    assert capsys.readouterr().err == f"detail: {path} was measured on another scene than {scene_path}\n"
    assert not out.exists()


@pytest.mark.parametrize("budget", [False, True])
def test_both_core_modes_read_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], budget: bool) -> None:
    import onward_generator.detail.__main__ as cli

    path, scene_path = _files(tmp_path, [_entry()])
    seen: dict[str, Any] = {}

    def stop(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise DetailError("stop")

    monkeypatch.setattr(cli, "load_base", lambda *args: (None, {}))
    monkeypatch.setattr(cli, "detail_scene_budget" if budget else "detail_scene", stop)
    core = ["--core-radius-m", "100", "--core-target-triangles", "20000"] if budget else []
    args = ["--scene", scene_path, "--heights", str(tmp_path / "h.json"), "--base", str(tmp_path / "b"), "--no-preview", *core,
            "--lidar-roofs", path, "--out", str(tmp_path / "d")]
    assert main(args) == 1
    assert capsys.readouterr().err == "detail: stop\n"
    assert seen["roof_spread"].buildings == {"osm-way-1": RoofSpread("measured", 10.0, 11.5, 20, 24)}
    monkeypatch.setattr(cli, "detail_scene_budget" if budget else "detail_scene", stop)
    assert main([a for a in args if a not in ("--lidar-roofs", path)]) == 1
    assert seen["roof_spread"] is None


def test_the_parser_names_the_flag() -> None:
    from onward_generator.detail.__main__ import build_parser

    action = next(a for a in build_parser()._actions if a.dest == "lidar_roofs")
    assert action.option_strings == ["--lidar-roofs"] and action.default is None and action.metavar == "REPORT"
    assert action.help == ("<id>_report.json of a LiDAR source plugin for this scene: flat or pitched roofs from the nDSM spread "
                           "(plan C3)")


# --- scene helpers --------------------------------------------------------------------------------------------


def test_the_terrace_under_a_taller_part_of_its_parcel_is_not_classified() -> None:
    from shapely import STRtree

    from onward_generator.detail.config import load_detail_config
    from onward_generator.detail.scene import _lidar_classes

    cfg = load_detail_config()
    footprints = {"low": box(0, 0, 20, 10), "high": box(20, 0, 40, 10), "alone": box(100, 0, 120, 10)}
    ids = sorted(footprints)
    tree = STRtree([footprints[i] for i in ids])
    key_of = {"low": "parcel:1", "high": "parcel:1", "alone": "alone"}
    heights = {"low": 9.0, "high": 15.0, "alone": 9.0}
    report = SpreadReport("r", "s", "es.pnoa_ndsm", "p", "CC-BY-4.0", "a", dict.fromkeys(ids, _spread(10.0, 11.5)))
    classes = _lidar_classes(ids, footprints, tree, key_of, {i: {} for i in ids}, heights, dict.fromkeys(ids, "core"), report, cfg)
    assert classes == {"low": "terrace", "high": "pitched", "alone": "pitched"}
    scenic = _lidar_classes(ids, footprints, tree, key_of, {i: {} for i in ids}, heights, {"low": "core", "high": "core", "alone": "scenic"},
                            report, cfg)
    assert scenic["alone"] == "scenic"
    tagged = _lidar_classes(ids, footprints, tree, key_of, {"low": {}, "high": {"roof:levels": "1"}, "alone": {}}, heights,
                            dict.fromkeys(ids, "core"), report, cfg)
    assert tagged["high"] == "tagged"


def test_the_attribution_line_is_merged_once_and_sorted() -> None:
    from types import SimpleNamespace

    from onward_generator.detail.scene import _lidar_record

    report = SpreadReport("r", "s", "es.pnoa_ndsm", "p", "CC-BY-4.0", "ign.es", {})
    other = {"text": "Data from meta; Overture Maps Foundation", "license": "CDLA-Permissive-2.0", "license_url": "u"}
    same = {"text": "ign.es", "license": "CC-BY-4.0", "license_url": "kept"}
    detailed = SimpleNamespace(stats={}, extra_attribution=[same, other])
    _lidar_record(detailed, [], {"b": "pitched"}, report)  # type: ignore[arg-type]
    assert detailed.extra_attribution == [other, same]
    detailed = SimpleNamespace(stats={}, extra_attribution=[other])
    _lidar_record(detailed, [], {"b": "flat"}, report)  # type: ignore[arg-type]
    assert detailed.extra_attribution == [other, {"text": "ign.es", "license": "CC-BY-4.0"}]
    assert detailed.stats["lidar_roofs"]["flat"] == 1 and detailed.stats["lidar_roofs"]["core_buildings"] == 1
