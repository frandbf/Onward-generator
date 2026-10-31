"""``run`` prints one line per stage from the report that stage wrote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.run_log import (
    Alert,
    Palette,
    RunLog,
    StageReport,
    count_items,
    group,
    plural,
    render,
    stage_line,
    summarize,
    use_color,
    wrap,
)


class _Tty:
    def __init__(self, tty: bool) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def _write(path: Path, document: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_thousands_are_grouped_with_a_space() -> None:
    assert group(0) == "0"
    assert group(999) == "999"
    assert group(21194) == "21 194"
    assert group(1234567) == "1 234 567"


def test_counts_are_listed_largest_first_without_zeros() -> None:
    counts = {"door": 3, "window": 435, "balcony_door": 231, "balcony": 231, "none": 0, "one": 1, "big": 1500}
    assert count_items(counts) == ("big 1 500", "window 435", "balcony 231", "balcony_door 231", "door 3", "one 1")
    assert count_items({}) == ()
    assert count_items({"a": 2}, rename=str.upper) == ("A 2",)


def test_colour_needs_a_terminal_and_no_no_color() -> None:
    assert use_color(_Tty(True), {}) is True
    assert use_color(_Tty(False), {}) is False
    assert use_color(_Tty(True), {"NO_COLOR": "1"}) is False
    assert use_color(_Tty(True), {"NO_COLOR": ""}) is True


def test_colour_reads_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    assert use_color(_Tty(True)) is False
    monkeypatch.delenv("NO_COLOR")
    assert use_color(_Tty(True)) is True


def test_palette_paints_only_when_enabled() -> None:
    plain = Palette()
    assert plain.ok("x") == plain.warning("x") == plain.error("x") == "x"
    tty = Palette(True)
    assert tty.ok("x") == "\033[32mx\033[0m"
    assert tty.warning("x") == "\033[33mx\033[0m"
    assert tty.error("x") == "\033[31mx\033[0m"
    assert tty.level("error", "x") == "\033[31mx\033[0m"
    assert tty.level("warning", "x") == "\033[33mx\033[0m"


def test_run_log_starts_empty_and_plain() -> None:
    log = RunLog()
    assert log.palette == Palette(False)
    assert log.verbose is False
    assert log.times == []
    assert log.warnings == 0
    assert StageReport() == StageReport("", (), ())


def test_plural() -> None:
    assert plural(0, "warning") == "0 warnings"
    assert plural(1, "warning") == "1 warning"
    assert plural(2, "warning") == "2 warnings"


def test_stage_line_aligns_index_name_and_duration() -> None:
    assert stage_line(1, 13, "geo", "0.1 s", "EPSG:25830  kept 81") == " [ 1/13] geo        0.1 s  EPSG:25830  kept 81"
    assert stage_line(13, 13, "materials", "12.0 s", "applied 10") == " [13/13] materials 12.0 s  applied 10"
    assert stage_line(2, 9, "fit", "0.0 s", "") == " [2/9] fit        0.0 s"


def test_a_breakdown_wraps_under_its_first_item() -> None:
    items = ["window 435", "balcony 231", "balcony_door 231", "shopfront 127", "door 3"]
    assert wrap("openings", items, width=62) == [
        "         openings   window 435, balcony 231, balcony_door 231,",
        "                    shopfront 127, door 3",
    ]
    assert wrap("urban", ["tree 3 obs"]) == ["         urban      tree 3 obs"]
    assert wrap("urban", []) == ["         urban      "]


def test_a_breakdown_line_fills_exactly_one_hundred_columns() -> None:
    lead = 20
    first = "a" * (100 - lead - 1 - 3) + ","
    fits = wrap("x", ["a" * (100 - lead - 1 - 3), "bc"])
    assert fits == [" " * 9 + "x" + " " * 10 + first + " bc"]
    assert len(fits[0]) == 100
    spills = wrap("x", ["a" * (100 - lead - 1 - 3), "bcd"])
    assert spills == [" " * 9 + "x" + " " * 10 + first, " " * lead + "bcd"]


def test_render_puts_alerts_on_their_own_coloured_line() -> None:
    report = StageReport(
        "s",
        details=(("openings", ("door 3",)), ("empty", ())),
        alerts=(Alert("warning", "warning 10 (urban_element_not_placed 10)", 10), Alert("error", "critical 1", 1)),
    )
    assert report.warnings == 11
    assert render(report, Palette()) == [
        "         openings   door 3",
        "         ! warning 10 (urban_element_not_placed 10)",
        "         ! critical 1",
    ]
    coloured = render(report, Palette(True))
    assert coloured[1] == "         \033[33m! warning 10 (urban_element_not_placed 10)\033[0m"
    assert coloured[2] == "         \033[31m! critical 1\033[0m"


def _geo_report(tmp_path: Path, **counts: int) -> Path:
    out = tmp_path / "geo"
    base = {"kept": 81, "errors": 0, "warnings": 0}
    _write(out / "geo_report.json", {"frame": {"crs": "EPSG:25830"}, "counts": base | counts})
    return out


def test_geo(tmp_path: Path) -> None:
    out = _geo_report(tmp_path, dropped_outside=1960)
    assert summarize("geo", ["geo", "--out", str(out)]) == StageReport("EPSG:25830  kept 81  dropped 1 960")
    out = _geo_report(tmp_path, rejected_invalid=2, errors=1, warnings=3)
    report = summarize("geo", ["geo", "--out", str(out)])
    assert report.summary == "EPSG:25830  kept 81  dropped 0  rejected 2"
    assert report.alerts == (Alert("error", "1 error", 1), Alert("warning", "3 warnings", 3))
    out = _geo_report(tmp_path, rejected_invalid=0, errors=0, warnings=1)
    assert summarize("geo", ["geo", "--out", str(out)]).alerts == (Alert("warning", "1 warning", 1),)


def test_heights(tmp_path: Path) -> None:
    out = tmp_path / "heights"
    _write(out / "heights_report.json", {
        "counts": {"buildings": 3, "needs_manual_review": 0},
        "percent_by_class": {"measured": 0.0, "inferred": 100.0, "fallback": 0.0},
    })
    assert summarize("heights", ["--out", str(out)]) == StageReport("3 buildings  inferred 100%")
    _write(out / "heights_report.json", {
        "counts": {"buildings": 1, "needs_manual_review": 1},
        "percent_by_class": {"measured": 12.5, "inferred": 0.0, "fallback": 87.5},
    })
    assert summarize("heights", ["--out", str(out)]) == StageReport("1 building  measured 12.5%  fallback 87.5%  review 1")


def _severity(critical: int, warning: int) -> dict[str, Any]:
    issues = (
        [{"code": "broken", "severity": "critical"}] * critical
        + [{"code": "late", "severity": "warning"}] * warning
        + [{"code": "slivers_dropped", "severity": "info"}]
    )
    return {"severity": {"critical": critical, "warning": warning, "info": 1}, "issues": issues}


def test_generate_shows_severity_only_when_not_zero(tmp_path: Path) -> None:
    out = tmp_path / "scene3d"
    counts = {"visual_triangles": 952, "collision_triangles": 1844, "materials": 8}
    _write(out / "generation_report.json", {"counts": counts} | _severity(0, 0))
    assert summarize("generate", ["--out", str(out)]) == StageReport("952 tris  1 844 collision  8 mat")
    _write(out / "generation_report.json", {"counts": counts} | _severity(1, 2))
    assert summarize("generate", ["--out", str(out)]).alerts == (
        Alert("error", "critical 1 (broken 1)", 1),
        Alert("warning", "warning 2 (late 2)", 2),
    )
    issues = [{"code": "late", "severity": "warning"}, {"code": "early", "severity": "warning"}, {"code": "late", "severity": "warning"}]
    _write(out / "generation_report.json", {"counts": counts, "severity": {"critical": 0, "warning": 3}, "issues": issues})
    assert summarize("generate", ["--out", str(out)]).alerts == (Alert("warning", "warning 3 (late 2, early 1)", 3),)


def test_a_severity_without_matching_issues_has_no_codes(tmp_path: Path) -> None:
    out = tmp_path / "scene3d"
    counts = {"visual_triangles": 1, "collision_triangles": 1, "materials": 1}
    _write(out / "generation_report.json", {"counts": counts, "severity": {"critical": 0, "warning": 4}, "issues": []})
    assert summarize("generate", ["--out", str(out)]).alerts == (Alert("warning", "warning 4", 4),)


def test_detail_breaks_down_openings_and_urban_elements(tmp_path: Path) -> None:
    out = tmp_path / "scene_detail"
    _write(out / "detail_report.json", {
        "counts": {
            "visual_triangles": 21194,
            "materials": 17,
            "visual_nodes": 1075,
            "openings": {"window": 435, "door": 3, "balcony": 231},
            "urban_elements": {"tree:observed": 24, "bench:generated": 9, "street lamp:generated": 26, "kiosk": 1},
        },
    } | _severity(0, 1))
    report = summarize("detail", ["--out", str(out)])
    assert report.summary == "21 194 tris  17 mat  1 075 nodes"
    assert report.details == (
        ("openings", ("window 435", "balcony 231", "door 3")),
        ("urban", ("street lamp 26", "tree 24 obs", "bench 9", "kiosk 1")),
        ("tiers", ()),
    )
    assert report.alerts == (Alert("warning", "warning 1 (late 1)", 1),)


def test_dress(tmp_path: Path) -> None:
    out = tmp_path / "scene_dress"
    _write(out / "detail_report.json", {"dress": {"tree_sides": 16, "atlas": True}})
    assert summarize("dress", ["--out", str(out)]) == StageReport("trees 16-side x3  atlas on")
    _write(out / "detail_report.json", {"dress": {"tree_sides": 8, "atlas": False}})
    assert summarize("dress", ["--out", str(out)]) == StageReport("trees 8-side x3  atlas off")


def test_materials_groups_skips_by_reason(tmp_path: Path) -> None:
    out = tmp_path / "scene_materials"
    _write(out / "materials_report.json", {
        "applied": [{}, {}],
        "skipped": [
            {"catalog_id": "stone", "reason": "no_texture"},
            {"id": "plain", "reason": "color_id_not_used"},
            {"catalog_id": "brick", "reason": "no_texture"},
        ],
    })
    assert summarize("materials", ["--out", str(out)]) == StageReport(
        "applied 2  skipped 3",
        details=(("skipped", ("color_id_not_used: plain",)), ("skipped", ("no_texture: stone, brick",))),
    )


def test_materials_names_the_facade_path(tmp_path: Path) -> None:
    out = tmp_path / "scene_materials"
    _write(out / "materials_report.json", {"applied": [{}], "skipped": [], "facades": {"mode": "trim", "style": "mediterranean"}})
    assert summarize("materials", ["--out", str(out)]) == StageReport("applied 1  skipped 0  facades trim mediterranean")
    _write(out / "materials_report.json", {"applied": [], "skipped": [], "facades": {"mode": "legacy", "style": "anglo"}})
    assert summarize("materials", ["--out", str(out)]).summary == "applied 0  skipped 0  facades legacy anglo"


def test_panoramax_signs_fit_bounds(tmp_path: Path) -> None:
    _write(tmp_path / "pano" / "panoramax_report.json", {"assignments": [1, 2]})
    assert summarize("panoramax", ["--out", str(tmp_path / "pano")]) == StageReport("2 facades")
    _write(tmp_path / "signs" / "signs_report.json", {"count": 1})
    assert summarize("signs", ["--out", str(tmp_path / "signs")]) == StageReport("1 plate  0 shop signs")
    _write(tmp_path / "signs" / "signs_report.json", {"count": 2, "shop_signs": {"count": 3}})
    assert summarize("signs", ["--out", str(tmp_path / "signs")]) == StageReport("2 plates  3 shop signs")
    fit = _write(tmp_path / "fit.json", {"checked": 58})
    assert summarize("fit", ["--out", str(fit)]) == StageReport("checked 58")
    _write(tmp_path / "playable" / "bounds_report.json", {"wall": {"height_m": 12.0, "triangles": 1032}})
    assert summarize("bounds", ["--out", str(tmp_path / "playable")]) == StageReport("wall 12 m  1 032 tris")
    _write(tmp_path / "playable" / "bounds_report.json",
           {"wall": {"height_m": 12.0, "triangles": 32, "core_half_side_m": 150.0}})
    assert summarize("bounds", ["--out", str(tmp_path / "playable")]) == StageReport("wall 12 m  32 tris  core 150 m")
    _write(tmp_path / "playable" / "bounds_report.json",
           {"wall": {"height_m": 12.0, "triangles": 32, "core_half_side_m": None}})
    assert summarize("bounds", ["--out", str(tmp_path / "playable")]) == StageReport("wall 12 m  32 tris")


def test_overgrow(tmp_path: Path) -> None:
    out = tmp_path / "scene_overgrow"
    _write(out / "overgrow_report.json", {
        "added_triangles": 32080, "props": 56, "wrecks": 2, "trees": 21, "trees_observed": 15,
        "moss": 89, "grass": 47, "vines": 0,
    })
    assert summarize("overgrow", ["--out", str(out)]) == StageReport(
        "32 080 tris  props 56  wrecks 2  trees 21 (15 obs)",
        details=(("growth", ("moss 89", "grass 47")),),
    )


def test_layout(tmp_path: Path) -> None:
    layout = _write(tmp_path / "layout.json", {
        "routes": [1, 2], "ai_m": [1], "attackers": {"points_m": [1, 2, 3]}, "defenders": {"points_m": [1]},
    })
    assert summarize("layout", ["--out", str(layout)]) == StageReport("routes 2  hunt 1  attackers 3  defenders 1")
    layout = _write(tmp_path / "layout.json", {"routes": [1], "attackers": [], "defenders": {"other": 1}})
    assert summarize("layout", ["--out", str(layout)]) == StageReport("routes 1")


def test_export_hides_the_standing_unverified_adapter_warning(tmp_path: Path) -> None:
    out = tmp_path / "pkg_01"
    _write(out / "onward_export.json", {
        "package_id": "madrid_628626a630",
        "warnings": ["adapter not on the headset yet [export_adapter_unverified]", "thumbnail missing"],
    })
    assert summarize("export", ["--out", str(out)]) == StageReport(
        "madrid_628626a630", alerts=(Alert("warning", "thumbnail missing", 1),),
    )
    _write(out / "onward_export.json", {"package_id": "p"})
    assert summarize("export", ["--out", str(out)]) == StageReport("p")
    _write(out / "onward_export.json", {"package_id": "p", "tiers": None})
    assert summarize("export", ["--out", str(out)]) == StageReport("p")
    _write(out / "onward_export.json", {"package_id": "p", "tiers": {"draws": 1482, "cell_m": 100.0}})
    assert summarize("export", ["--out", str(out)]) == StageReport("p  draws 1 482 at 100 m")


def test_detail_prints_the_tiers_of_a_map_with_a_core(tmp_path: Path) -> None:
    out = tmp_path / "scene_detail"
    _write(out / "detail_report.json", {
        "counts": {
            "visual_triangles": 166694, "materials": 30, "visual_nodes": 678, "openings": {}, "urban_elements": {},
            "tiers": {"core": {"buildings": 108, "visual_triangles": 127033},
                      "scenic": {"buildings": 446, "visual_triangles": 19641, "urban_elements_dropped": 3}},
        },
    } | _severity(0, 0))
    report = summarize("detail", ["--out", str(out)])
    assert report.details[-1] == ("tiers", ("core 108 bldg 127 033 tris", "scenic 446 bldg 19 641 tris"))


@pytest.mark.parametrize("argv", [["geo"], ["geo", "--out"]])
def test_no_out_flag_means_no_summary(argv: list[str]) -> None:
    assert summarize("geo", argv) == StageReport()


def test_an_unknown_stage_or_unreadable_report_means_no_summary(tmp_path: Path) -> None:
    assert summarize("props", ["--out", str(tmp_path)]) == StageReport()
    assert summarize("geo", ["--out", str(tmp_path / "missing")]) == StageReport()
    _write(tmp_path / "fit.json", [1, 2])
    assert summarize("fit", ["--out", str(tmp_path / "fit.json")]) == StageReport()
    _write(tmp_path / "geo" / "geo_report.json", {"counts": {}})
    assert summarize("geo", ["--out", str(tmp_path / "geo")]) == StageReport()


def test_catastro_reads_the_download_sidecar(tmp_path: Path) -> None:
    gml = tmp_path / "raw" / "catastro.gml"
    _write(gml.with_name("catastro.gml.source.json"), {"size_bytes": 2_100_000, "license": "CC-BY-4.0"})
    assert summarize("catastro", ["cadastre", "download", "--out", str(gml)]) == StageReport("2 050 KiB  CC-BY-4.0")


def test_cadastre_reports_the_split(tmp_path: Path) -> None:
    out = tmp_path / "cadastre"
    split: dict[str, Any] = {"osm_buildings_split": 13, "sub_buildings": 1041, "levels_range": [1, 7]}
    _write(out / "cadastre_match_report.json", {"counts": {"cadastre_parts": 1203}, "split": {"counts": split}})
    assert summarize("cadastre", ["--out", str(out)]) == StageReport("parts 1 203  split 13 → 1 041  storeys 1–7")
    split.update(osm_buildings_split=0, sub_buildings=0, levels_range=None)
    _write(out / "cadastre_match_report.json", {"counts": {"cadastre_parts": 0}, "split": {"counts": split}})
    assert summarize("cadastre", ["--out", str(out)]) == StageReport("parts 0  split 0 → 0")
    split.update(status_quarantined=2, quarantined_m2=1234.56)
    _write(out / "cadastre_match_report.json", {"counts": {"cadastre_parts": 0}, "split": {"counts": split}})
    assert summarize("cadastre", ["--out", str(out)]) == StageReport("parts 0  split 0 → 0  kept whole 2 (1 235 m²)")


def test_overrides_reports_the_winners_per_source(tmp_path: Path) -> None:
    out = tmp_path / "sources"
    report = {"buildings": 1250, "sources": [{"source": "es.pnoa_ndsm", "won": 1200}, {"source": "es.catastro", "won": 50}]}
    _write(out / "sources_report.json", report)
    assert summarize("overrides", ["source", "merge", "--out", str(out)]) == StageReport("1 250 buildings  es.pnoa_ndsm 1 200  es.catastro 50")


def test_a_lidar_download_and_its_heights_report_tiles_and_measured_footprints(tmp_path: Path) -> None:
    raw = tmp_path / "raw" / "es.pnoa_ndsm"
    _write(raw.with_name("es.pnoa_ndsm.source.json"), {"license": "CC-BY-4.0", "tiles": [{"size_bytes": 2048}, {"size_bytes": 3072}]})
    assert summarize("pnoa_ndsm", ["source", "fetch", "--out", str(raw)]) == StageReport("5 KiB  2 tiles  CC-BY-4.0")
    out = tmp_path / "es.pnoa_ndsm"
    _write(out / "es.pnoa_ndsm_report.json", {"counts": {"measured": 1066, "buildings": 1084}, "height_range_m": [3.5, 27.0]})
    assert summarize("pnoa_heights", ["--out", str(out)]) == StageReport("measured 1 066/1 084  3.5–27 m")
    _write(out / "es.pnoa_ndsm_report.json", {"counts": {"measured": 0, "buildings": 3}, "height_range_m": None})
    assert summarize("pnoa_heights", ["--out", str(out)]) == StageReport("measured 0/3")
    hd = tmp_path / "fr.lidar_hd"
    _write(hd / "fr.lidar_hd_report.json", {"counts": {"measured": 2, "buildings": 3}, "height_range_m": [6.0, 21.5]})
    assert summarize("lidar_hd_heights", ["--out", str(hd)]) == StageReport("measured 2/3  6–21.5 m")
    hd_raw = tmp_path / "raw" / "fr.lidar_hd"
    _write(hd_raw.with_name("fr.lidar_hd.source.json"), {"license": "etalab-2.0", "tiles": [{"size_bytes": 4096}]})
    assert summarize("lidar_hd", ["--out", str(hd_raw)]) == StageReport("4 KiB  1 tile  etalab-2.0")
