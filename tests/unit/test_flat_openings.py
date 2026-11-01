"""Openings above ``relief_storeys`` are drawn flush with the wall (ADR-025)."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import replace

import numpy as np
import pytest
from shapely import Polygon

from onward_generator.detail.config import DEFAULT_CONFIG_PATH, DetailConfigError, config_from_document, load_detail_config
from onward_generator.detail.facades import (
    FLAT_KINDS,
    WELD_M,
    Hole,
    Layout,
    Shell,
    build_shell,
    entrance,
    exposed_frontage,
    is_flat,
    layout_segment,
    refine_exposure,
    storeys_for,
)
from onward_generator.detail.modules import flat_opening
from onward_generator.generation.mesh import Mesh, merge, open_edge_defects
from onward_generator.materials.trim import opening_uvs, window_families
from onward_generator.materials.trim_style import SHOP_CELL, load_style_table, trim_layout

CFG = load_detail_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
TEMPLATE = CFG.template_for("residential_multi", False)
WALL = "plaster_ochre"
ROOF = "roof_flat_grey"
FOOTPRINT = Polygon([(0, 0), (14, 0), (14, 9), (0, 9)])
# The 14 × 9 m, five-storey building below, built by the code before ADR-025: every opening recessed.
RECESSED_SHA256 = "245540f4fcf59544275243a94c12a5da27d51c6a30a333ffe58c3338342d6130"


def _hole(kind: str = "window", storey: int = 2, back: str | None = "glass_dark", v0: float | None = None) -> Hole:
    return Hole(kind, 0, 2.0, 3.0 * storey + 0.9 if v0 is None else v0, 1.1, 1.5, CFG.recess_depth_m, back, 0, storey)


def _building(relief_storeys: int, holes: list[Hole] | None = None) -> tuple[Shell, Layout]:
    cfg = replace(CFG, relief_storeys=relief_storeys)
    refined, segments = refine_exposure(FOOTPRINT, Polygon(), cfg)
    layout = Layout()
    if holes is None:
        storeys = storeys_for(15.0, 5, False, cfg)
        main = exposed_frontage(segments)
        kind, door = entrance(False, False, cfg)
        for seg in segments:
            layout_segment(seg, storeys, TEMPLATE, TEMPLATE.ground, door if seg is main else None, kind, cfg.recess_depth_m,
                           "b1", refined, Polygon(), cfg, layout)
    else:
        layout.holes = holes
    return build_shell(refined, segments, layout, 15.0, TEMPLATE, WALL, ROOF, "b1", cfg), layout


def _digest(shell: Shell) -> str:
    h = hashlib.sha256()
    for material in sorted(shell.parts):
        mesh = merge(shell.parts[material])
        h.update(material.encode())
        h.update(np.round(mesh.vertices, 9).tobytes())
        h.update(mesh.triangles.astype(np.int64).tobytes())
    return h.hexdigest()


def _tris(shell: Shell, material: str) -> int:
    return merge(shell.parts.get(material, [])).triangle_count


def _depth(mesh: Mesh) -> np.ndarray:
    """How far each vertex of a south-wall mesh (y = 0, outward −y) lies inside the wall."""
    return mesh.vertices[:, 1]


@pytest.mark.parametrize("kind", sorted(FLAT_KINDS))
def test_the_storey_threshold_is_inclusive(kind: str) -> None:
    assert not is_flat(_hole(kind, storey=1), 2)
    assert is_flat(_hole(kind, storey=2), 2)
    assert is_flat(_hole(kind, storey=7), 2)
    assert is_flat(_hole(kind, storey=0), 0)
    assert not is_flat(_hole(kind, storey=999), 1000)


@pytest.mark.parametrize("kind", ["door", "landmark_door", "shop_door", "shopfront", "interior_door"])
def test_doors_and_shopfronts_stay_recessed_on_any_storey(kind: str) -> None:
    for back in ("glass_dark", "door_wood", None):
        assert not is_flat(_hole(kind, storey=3, back=back), 0)


@pytest.mark.parametrize("back", ["glass_view", "shop_glass", "door_wood", None])
def test_a_window_without_an_opaque_glass_back_stays_recessed(back: str | None) -> None:
    # glass_view and shop_glass are the panes an interior pierces through the wall.
    assert not is_flat(_hole("window", storey=4, back=back), 0)
    assert not is_flat(_hole("balcony_door", storey=4, back=back), 0)


def test_a_very_high_threshold_reproduces_the_recessed_building_exactly() -> None:
    shell, layout = _building(1000)
    assert _digest(shell) == RECESSED_SHA256
    assert shell.defects == 0
    assert shell.relief == {"relief_storeys": 1000, "flat_openings": 0, "recessed_openings": len(layout.holes)}


def test_upper_storeys_are_flat_and_the_shell_stays_closed() -> None:
    recessed, _ = _building(1000)
    shell, layout = _building(2)
    assert shell.defects == 0
    flat = [h for h in layout.holes if h.storey >= 2]
    assert {h.kind for h in flat} == {"window", "balcony_door"}
    assert shell.relief == {"relief_storeys": 2, "flat_openings": len(flat), "recessed_openings": len(layout.holes) - len(flat)}
    # Each flat opening drops its four reveals (8 triangles); its back stays 2 triangles; the wall and balconies do not change.
    assert _tris(shell, "frame_stone") == _tris(recessed, "frame_stone") - 8 * len(flat)
    for material in ("glass_dark", "door_wood", WALL, "railing_metal", ROOF):
        assert _tris(shell, material) == _tris(recessed, material), material
    assert shell.counts == recessed.counts
    # The flat glass lies in the wall plane; the glass below the threshold stays 0.15 m in.
    glass = merge(shell.parts["glass_dark"])
    on_south = np.isclose(glass.vertices[:, 1], 0.0) | np.isclose(glass.vertices[:, 1], CFG.recess_depth_m)
    south = glass.vertices[on_south & (glass.vertices[:, 0] > 0.01) & (glass.vertices[:, 0] < 13.99)]
    upper, lower = south[south[:, 2] > 6.0 + 1e-9], south[south[:, 2] < 6.0 - 1e-9]
    assert len(upper) and len(lower)
    assert np.allclose(upper[:, 1], 0.0) and np.allclose(lower[:, 1], CFG.recess_depth_m)


def test_zero_flattens_every_window_but_keeps_the_door() -> None:
    shell, layout = _building(0)
    assert shell.defects == 0
    doors = [h for h in layout.holes if h.kind == "door"]
    assert len(doors) == 1
    assert shell.relief == {"relief_storeys": 0, "flat_openings": len(layout.holes) - 1, "recessed_openings": 1}
    # Only the door's reveals are left of the openings: the rest of frame_stone is the coping and the balcony slabs.
    recessed, _ = _building(1000)
    assert _tris(shell, "frame_stone") == _tris(recessed, "frame_stone") - 8 * (len(layout.holes) - 1)


def test_one_flat_window_is_two_triangles_and_watertight_in_its_wall() -> None:
    flat, _ = _building(2, [_hole("window", storey=2)])
    deep, _ = _building(3, [_hole("window", storey=2)])
    assert flat.defects == deep.defects == 0
    assert _tris(flat, "glass_dark") == _tris(deep, "glass_dark") == 2
    assert _tris(deep, "frame_stone") - _tris(flat, "frame_stone") == 8
    assert _tris(flat, WALL) == _tris(deep, WALL)
    assert np.allclose(_depth(merge(flat.parts["glass_dark"])), 0.0)
    assert np.allclose(_depth(merge(deep.parts["glass_dark"])), CFG.recess_depth_m)
    assert flat.counts == deep.counts == {"window": 1}
    # Without the flat quad the wall would be open around the hole: the quad closes it.
    walls_only = merge([m for material, ms in flat.parts.items() if material != "glass_dark" for m in ms])
    assert open_edge_defects(walls_only, z_base=0.0, quantum=WELD_M) == 4


def test_flat_opening_faces_out_and_fills_the_hole() -> None:
    proto = flat_opening(1.1, 1.5, "glass_dark")
    assert [material for material, _ in proto.parts] == ["glass_dark"]
    mesh = proto.parts[0][1]
    assert mesh.triangle_count == 2 and proto.triangle_count == 2
    assert mesh.vertices.min(axis=0).tolist() == [0.0, 0.0, 0.0] and mesh.vertices.max(axis=0).tolist() == [1.1, 0.0, 1.5]
    normal = mesh.face_normals()
    assert np.allclose(normal / np.linalg.norm(normal, axis=1)[:, None], [0.0, -1.0, 0.0])  # module frame: outward is −Y
    assert proto.key != flat_opening(1.1, 1.5, "door_wood").key != flat_opening(1.0, 1.5, "glass_dark").key


def test_a_pierced_window_above_the_threshold_keeps_its_through_hole() -> None:
    pane = replace(_hole("window", storey=3), back="glass_view", depth=0.3)
    shell, _ = _building(0, [pane])
    assert shell.relief == {"relief_storeys": 0, "flat_openings": 0, "recessed_openings": 1}
    assert _digest(shell) == _digest(_building(1000, [pane])[0])
    assert np.allclose(_depth(merge(shell.parts["glass_view"])), 0.3)


def test_a_flat_window_maps_to_a_window_cell_like_a_recessed_one() -> None:
    atlas = load_style_table().styles["mediterranean"].atlas or {}
    cells, windows = trim_layout(atlas).cells, window_families(atlas)
    names = {name for family in windows for name in family}
    for relief in (0, 1000):
        shell, layout = _building(relief)
        counts: dict[str, int] = {}
        opening_uvs("glass_dark", merge(shell.parts["glass_dark"]), cells, "b1", windows, counts)
        assert SHOP_CELL not in counts and set(counts) <= names
        assert sum(counts.values()) == sum(1 for h in layout.holes if h.back == "glass_dark")


@pytest.mark.parametrize(("value", "fragment"), [(-1, "minimum"), (1001, "maximum"), (2.5, "integer"), ("2", "integer"), (None, "integer")])
def test_invalid_relief_storeys_is_rejected(value: object, fragment: str) -> None:
    doc = copy.deepcopy(DOC)
    doc["openings"]["relief_storeys"] = value
    with pytest.raises(DetailConfigError, match=fragment):
        config_from_document(doc)


def test_relief_storeys_is_required_and_read_as_given() -> None:
    doc = copy.deepcopy(DOC)
    del doc["openings"]["relief_storeys"]
    with pytest.raises(DetailConfigError, match="'relief_storeys' is a required property"):
        config_from_document(doc)
    assert CFG.relief_storeys == DOC["openings"]["relief_storeys"] == 2
    for value in (0, 1, 1000):
        doc["openings"]["relief_storeys"] = value
        assert config_from_document(doc).relief_storeys == value
