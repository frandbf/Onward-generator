"""Facade style table, style selection and the trim-sheet layout (ADR-020)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.materials.trim_style import (
    STYLE_TABLE_PATH,
    StyleError,
    fit_cells,
    load_style_table,
    resolve_style,
    select_style,
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
    assert set(table.styles) == {"mediterranean", "central_european", "anglo", "latin_american", "generic"}
    assert [style for style, _ in table.rules] == ["mediterranean", "anglo", "latin_american", "central_european"]
    assert table.rules[0][1][0] == (35.0, 44.5, -10.0, 29.0)
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
