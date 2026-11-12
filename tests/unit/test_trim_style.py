"""Facade style table, style selection and the trim-sheet layout (ADR-020)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.materials.trim_style import (
    ERA_PERIODS,
    REGIONAL_TABLE_PATH,
    STYLE_TABLE_PATH,
    StyleError,
    fit_cells,
    load_regional_table,
    load_style_table,
    resolve_style,
    select_style,
    style_atlas,
    trim_layout,
    wall_strip_px,
    window_cells,
)
from tests.conftest import MURCIA_CENTER


def _doc() -> dict[str, Any]:
    return json.loads(STYLE_TABLE_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, doc: Any) -> Path:
    path = tmp_path / "styles.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_the_shipped_table_has_one_atlas_and_four_planned_styles() -> None:
    table = load_style_table()
    assert table.default == "generic"
    assert table.path == STYLE_TABLE_PATH
    assert len(table.sha256) == 64
    assert {name for name, style in table.styles.items() if style.atlas is not None} == {"mediterranean"}
    assert set(table.styles) == {"mediterranean", "central_european", "anglo", "latin_american", "haussmann", "generic"}
    # Plan Q3: Paris comes first, so its box wins over every coarser one.
    assert [style for style, _ in table.rules] == ["haussmann", "mediterranean", "anglo", "latin_american", "central_european"]
    assert table.rules[0][1] == ((48.8, 48.93, 2.2, 2.5),)
    assert table.rules[1][1][0] == (35.0, 44.5, -10.0, 29.0)
    assert table.styles["anglo"].note.startswith("Planned")


@pytest.mark.parametrize(("lat", "lon", "style"), [
    (40.40657, -3.719187, "mediterranean"),  # Madrid
    (MURCIA_CENTER[1], MURCIA_CENTER[0], "mediterranean"),
    (41.9028, 12.4964, "mediterranean"),  # Rome
    (50.0614, 19.9366, "central_european"),  # Kraków
    (51.5072, -0.1276, "anglo"),  # London
    (40.7128, -74.006, "anglo"),  # New York
    (19.4326, -99.1332, "latin_american"),  # Mexico City
    (-34.6037, -58.3816, "latin_american"),  # Buenos Aires
    (35.6762, 139.6503, "generic"),  # Tokyo
    (48.84045, 2.34981, "haussmann"),  # Paris, Rue Mouffetard (plan Q3)
    (48.8566, 2.3522, "haussmann"),  # Paris, Hôtel de Ville
    (48.6, 2.35, "generic"),  # south of Paris, outside its box
])
def test_the_map_centre_picks_the_style(lat: float, lon: float, style: str) -> None:
    assert select_style(load_style_table(), lat, lon) == style


def test_a_box_holds_its_edges(tmp_path: Path) -> None:
    doc = _doc()
    doc["selection"] = [{"style": "anglo", "boxes": [[10.0, 20.0, 30.0, 40.0]]}]
    table = load_style_table(_write(tmp_path, doc))
    for lat, lon in ((10.0, 30.0), (20.0, 40.0), (10.0, 40.0), (20.0, 30.0)):
        assert select_style(table, lat, lon) == "anglo"
    for lat, lon in ((9.999, 35.0), (20.001, 35.0), (15.0, 29.999), (15.0, 40.001)):
        assert select_style(table, lat, lon) == "generic"


def test_the_first_matching_rule_wins(tmp_path: Path) -> None:
    doc = _doc()
    box = [[0.0, 10.0, 0.0, 10.0]]
    doc["selection"] = [{"style": "anglo", "boxes": [[50.0, 60.0, 0.0, 1.0], *box]}, {"style": "mediterranean", "boxes": box}]
    assert select_style(load_style_table(_write(tmp_path, doc)), 5.0, 5.0) == "anglo"


def test_resolve_style_by_name_centre_or_default() -> None:
    table = load_style_table()
    assert resolve_style(table, "anglo", [-3.7, 40.4]) == ("anglo", "config")
    assert resolve_style(table, "auto", [-3.719187, 40.40657]) == ("mediterranean", "map_centre")
    # The origin is [longitude, latitude], as the detail manifest writes it.
    assert resolve_style(table, "auto", [19.9366, 50.0614]) == ("central_european", "map_centre")
    assert resolve_style(table, "auto", None) == ("generic", "default")
    with pytest.raises(StyleError, match="unknown facade style 'nordic'; known: anglo, central_european"):
        resolve_style(table, "nordic", None)


def test_a_table_that_is_not_json_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "styles.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(StyleError, match="styles.json: not JSON"):
        load_style_table(path)


def test_the_schema_is_enforced(tmp_path: Path) -> None:
    doc = _doc()
    doc["styles"]["mediterranean"]["atlas"]["size_px"] = 4096
    with pytest.raises(StyleError, match="styles/mediterranean/atlas/size_px: 4096 is not one of"):
        load_style_table(_write(tmp_path, doc))
    doc = _doc()
    doc["extra"] = 1
    with pytest.raises(StyleError, match=r"\(root\): Additional properties"):
        load_style_table(_write(tmp_path, doc))


def test_a_style_used_but_not_defined_is_refused(tmp_path: Path) -> None:
    doc = _doc()
    doc["default_style"] = "nordic"
    with pytest.raises(StyleError, match="style 'nordic' is used but not defined"):
        load_style_table(_write(tmp_path, doc))
    doc = _doc()
    doc["selection"][0]["style"] = "nordic"
    with pytest.raises(StyleError, match="style 'nordic' is used but not defined"):
        load_style_table(_write(tmp_path, doc))


def test_atlas_checks(tmp_path: Path) -> None:
    doc = _doc()
    doc["styles"]["mediterranean"]["atlas"]["fit_cell_px"] = 257
    with pytest.raises(StyleError, match="style mediterranean: 8 cells of 257 px do not fit the atlas width"):
        load_style_table(_write(tmp_path, doc))
    doc = _doc()
    doc["styles"]["mediterranean"]["atlas"]["fit_cell_px"] = 256
    assert load_style_table(_write(tmp_path, doc)).styles["mediterranean"].atlas is not None
    doc = _doc()
    doc["styles"]["mediterranean"]["atlas"]["scenic_walls"]["scenic_wall_light"] = "plaster_grey"
    with pytest.raises(StyleError, match="scenic wall scenic_wall_light maps to plaster_grey, which has no strip"):
        load_style_table(_write(tmp_path, doc))
    doc = _doc()
    atlas = doc["styles"]["mediterranean"]["atlas"]
    atlas["fit_row_px"] = 3600
    with pytest.raises(StyleError, match="18 wall strips do not fit the atlas height"):
        load_style_table(_write(tmp_path, doc))
    # 32 px a strip is four gutters: the smallest that fits.
    atlas["fit_row_px"] = 4096 - 320 - 18 * 32
    assert wall_strip_px(atlas) == 32
    assert load_style_table(_write(tmp_path, doc)).styles["mediterranean"].atlas is not None


def test_cells_and_strip_height_of_the_mediterranean_atlas() -> None:
    atlas = load_style_table().styles["mediterranean"].atlas
    assert atlas is not None
    assert window_cells(atlas) == ["window_0_0", "window_0_1", "window_1_0", "window_1_1"]
    assert fit_cells(atlas) == [*window_cells(atlas), "door_0", "door_1", "shop_glass", "boarded"]
    # Six wall colours in three tones share the 3392 rows under the shared ones: 188, down to 184.
    assert wall_strip_px(atlas) == 184
    odd = copy.deepcopy(dict(atlas))
    odd["fit_row_px"] = 383
    assert wall_strip_px(odd) == 184
    # 3311 rows left: 183 a strip, down to 176; with one tone, 551 a strip, down to 544.
    odd["fit_row_px"] = 384 + 81
    assert wall_strip_px(odd) == 176
    odd["tones"] = odd["tones"][:1]
    assert wall_strip_px(odd) == 544


def test_layout_rows_and_uvs() -> None:
    atlas = load_style_table().styles["mediterranean"].atlas
    assert atlas is not None
    layout = trim_layout(atlas)
    assert (layout.size, layout.height, layout.gutter) == (2048, 4096, 8)
    assert (layout.cornice.row0, layout.cornice.row1) == (384, 512)
    assert (layout.zocalo.row0, layout.zocalo.row1) == (512, 640)
    assert (layout.stone.row0, layout.stone.row1) == (640, 704)
    assert list(layout.walls) == ["brick_red", "plaster_cream", "plaster_ochre", "plaster_salmon", "plaster_white", "stone_warm"]
    # Each colour's tones lie next to each other; ``walls`` is the first tone.
    assert [(s.row0, s.row1) for s in layout.tones["brick_red"]] == [(704, 888), (888, 1072), (1072, 1256)]
    assert [(s.row0, s.row1) for s in layout.walls.values()][:2] == [(704, 888), (1256, 1440)]
    assert all(layout.walls[name] == layout.tones[name][0] for name in layout.walls)
    assert layout.tones["stone_warm"][-1].row1 == 704 + 18 * 184
    brick = layout.walls["brick_red"]
    assert brick.v0 == pytest.approx(1 - 880 / 4096)
    assert brick.v1 == pytest.approx(1 - 712 / 4096)
    assert brick.repeat_m == 12.0
    assert (layout.zocalo.repeat_m, layout.cornice.repeat_m, layout.stone.repeat_m) == (6.0, 6.0, 1.0)
    first = layout.cells["window_0_0"]
    assert (first.col0, first.col1, first.row0, first.row1) == (0, 192, 0, 384)
    assert (first.u0, first.u1) == pytest.approx((8 / 2048, 184 / 2048))
    assert (first.v0, first.v1) == pytest.approx((1 - 376 / 4096, 1 - 8 / 4096))
    last = layout.cells["boarded"]
    assert (last.col0, last.col1) == (7 * 192, 8 * 192)
    assert last.u1 == pytest.approx((8 * 192 - 8) / 2048)


# The shipped main table, unchanged by plan D1 and changed by plan Q3 (the Paris box): materials reports and the
# Panoramax tint read its hash.
MAIN_TABLE_SHA256 = "f09eb4b54d64fe326e311042cdafb492396bb683c8207ece62a0458aeaeff5bb"
REGIONAL = ("central_european", "anglo", "latin_american", "haussmann")


def _regional_doc() -> dict[str, Any]:
    return json.loads(REGIONAL_TABLE_PATH.read_text(encoding="utf-8"))


def _write_regional(tmp_path: Path, doc: Any) -> Path:
    path = tmp_path / "regional.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_the_main_table_is_unchanged() -> None:
    assert load_style_table().sha256 == MAIN_TABLE_SHA256


def test_the_shipped_regional_table_draws_the_three_planned_styles() -> None:
    table = load_style_table()
    regional = load_regional_table(table)
    assert regional.path == REGIONAL_TABLE_PATH and len(regional.sha256) == 64
    assert set(regional.atlases) == set(REGIONAL)
    kinds = {"central_european": {"casement"}, "anglo": {"sash", "casement"}, "latin_american": {"grille", "casement"},
             "haussmann": {"casement"}}
    for name, atlas in regional.atlases.items():
        # The Panoramax tint moves inside a wall family: every family is still there.
        assert set(atlas["walls"]) == set(table.styles["mediterranean"].atlas["walls"])  # type: ignore[index]
        # Paris is limestone whatever the palette says (plan Q3): ashlar and brick, no render.
        assert set(atlas["walls"].values()) == ({"ashlar", "brick"} if name == "haussmann" else {"plaster", "ashlar", "brick"})
        assert len(fit_cells(atlas)) * atlas["fit_cell_px"] <= atlas["size_px"]
        assert wall_strip_px(atlas) == 184
        assert {f["kind"] for f in atlas["window_families"]} == kinds[name]
        assert set(atlas.get("eras") or ERA_PERIODS) == set(ERA_PERIODS)
        assert atlas["sources"]["plaster"] != "acg_paintedplaster010"
    haussmann = regional.atlases["haussmann"]
    assert set(haussmann["wall_tints"]) == {wall for wall, kind in haussmann["walls"].items() if kind == "ashlar"}
    assert haussmann["dress"] == {"zocalo_share": 1.0, "cornice_share": 0.95, "course_share": 0.9}
    assert "eras" not in haussmann and "wall_tints" not in regional.atlases["central_european"]


def test_era_periods_follow_detail() -> None:
    assert ERA_PERIODS == ("pre_1900", "1900_1939", "1940_1959", "1960_1979", "1980_1999", "2000_plus")
    schema = json.loads((STYLE_TABLE_PATH.parent / "facade_styles.schema.json").read_text(encoding="utf-8"))
    assert schema["$defs"]["atlas"]["properties"]["eras"]["propertyNames"]["enum"] == list(ERA_PERIODS)


def test_style_atlas_takes_the_table_first_then_the_regional_recipe() -> None:
    table = load_style_table()
    regional = load_regional_table(table)
    medi = table.styles["mediterranean"].atlas
    assert style_atlas(table, "mediterranean", None) == (medi, "table")
    assert style_atlas(table, "mediterranean", regional) == (medi, "table")
    assert style_atlas(table, "central_european", None) == (None, None)
    assert style_atlas(table, "central_european", regional) == (regional.atlases["central_european"], "regional")
    assert style_atlas(table, "generic", None) == (None, None)
    assert style_atlas(table, "generic", regional) == (None, None)


def test_regional_table_refusals(tmp_path: Path) -> None:
    table = load_style_table()
    path = tmp_path / "regional.json"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(StyleError, match="regional.json: not JSON"):
        load_regional_table(table, path)

    def refused(change: Any, message: str) -> None:
        doc = _regional_doc()
        change(doc)
        with pytest.raises(StyleError, match=message):
            load_regional_table(table, _write_regional(tmp_path, doc))

    refused(lambda d: d.update(extra=1), r"regional.json: \(root\): Additional properties")
    refused(lambda d: d.update(schema="onward_generator.facade_styles"), r"schema: 'onward_generator.facade_styles_regional' was expected")
    refused(lambda d: d["atlases"].update(nordic=d["atlases"]["anglo"]), "style 'nordic' is not in the style table")
    refused(lambda d: d["atlases"].update(mediterranean=d["atlases"]["anglo"]), "style mediterranean already has an atlas in the style table")
    refused(lambda d: d["atlases"]["anglo"]["eras"].update(victorian={"tones": [0], "window_families": [0]}), "atlases/anglo/eras")
    refused(lambda d: d["atlases"]["anglo"]["eras"]["pre_1900"].update(tones=[3]), "style anglo: era pre_1900 tone 3 has no strip")
    refused(lambda d: d["atlases"]["anglo"]["eras"]["pre_1900"].update(window_families=[3]),
            "style anglo: era pre_1900 window family 3 does not exist")
    refused(lambda d: d["atlases"]["anglo"]["eras"]["pre_1900"].update(tones=[]), "should be non-empty")
    refused(lambda d: d["atlases"]["anglo"]["eras"]["pre_1900"].pop("tones"), "'tones' is a required property")
    refused(lambda d: d["atlases"]["latin_american"]["window_families"][0].pop("iron"), "'iron' is a required property")
    refused(lambda d: d["atlases"]["anglo"]["window_families"][0].update(kind="louvre"), "'louvre' is not one of")
    refused(lambda d: d["atlases"]["anglo"]["window_families"].append(d["atlases"]["anglo"]["window_families"][0]),
            "style anglo: 12 cells of 192 px do not fit the atlas width")
    refused(lambda d: d["atlases"]["anglo"]["scenic_walls"].update(scenic_wall_light="plaster_grey"),
            "style anglo: scenic wall scenic_wall_light maps to plaster_grey, which has no strip")
    # Plan Q3: a fixed tint names a wall that has a strip, and is an RGB triple in 0-1.
    refused(lambda d: d["atlases"]["haussmann"]["wall_tints"].update(plaster_grey=[0.5, 0.5, 0.5]),
            "style haussmann: wall tint plaster_grey has no strip")
    refused(lambda d: d["atlases"]["haussmann"]["wall_tints"].update(plaster_cream=[0.5, 0.5]), "atlases/haussmann/wall_tints/plaster_cream")
    refused(lambda d: d["atlases"]["haussmann"]["wall_tints"].update(plaster_cream=[0.5, 0.5, 1.1]), "atlases/haussmann/wall_tints/plaster_cream")
    refused(lambda d: d["atlases"]["haussmann"].update(wall_tints={}), "atlases/haussmann/wall_tints")
    # An estimated wall and the wall it is drawn as both have a strip.
    refused(lambda d: d["atlases"]["haussmann"]["estimated_walls"].update(brick_red="plaster_grey"),
            "style haussmann: estimated wall brick_red -> plaster_grey needs a strip for both")
    refused(lambda d: d["atlases"]["haussmann"]["estimated_walls"].update(brick_grey="plaster_cream"),
            "style haussmann: estimated wall brick_grey -> plaster_cream needs a strip for both")
    refused(lambda d: d["atlases"]["haussmann"].update(estimated_walls={}), "atlases/haussmann/estimated_walls")
    refused(lambda d: d["atlases"]["haussmann"]["estimated_walls"].update(brick_red=1), "atlases/haussmann/estimated_walls/brick_red")
    # The last valid indices pass.
    doc = _regional_doc()
    doc["atlases"]["anglo"]["eras"]["pre_1900"].update(tones=[2], window_families=[2])
    assert load_regional_table(table, _write_regional(tmp_path, doc)).atlases["anglo"]["eras"]["pre_1900"]["tones"] == [2]


def test_a_main_table_keeps_its_own_eras_checks(tmp_path: Path) -> None:
    doc = _doc()
    doc["styles"]["mediterranean"]["atlas"]["eras"] = {"1960_1979": {"tones": [3], "window_families": [0]}}
    with pytest.raises(StyleError, match="style mediterranean: era 1960_1979 tone 3 has no strip"):
        load_style_table(_write(tmp_path, doc))
