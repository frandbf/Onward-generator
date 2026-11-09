"""The mid shell of the intermediate HLOD ring (plan D3, ADR-017 amendment).

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up); the footprints sit just outside a 100 m core.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from shapely import Point, box
from shapely.geometry import MultiPolygon, Polygon

from onward_generator.detail import load_detail_config
from onward_generator.detail import mid as mid_module
from onward_generator.detail.config import HipRoof
from onward_generator.detail.facades import Segment, Storeys, refine_exposure
from onward_generator.detail.mid import (
    CAP_FALLBACK,
    MID_BAY_M,
    MID_GLASS,
    MID_HEAD_M,
    MID_MAX_TRIANGLES,
    MID_REPRESENTATION,
    MID_SILL_M,
    MID_WINDOW_H_M,
    MID_WINDOW_W_M,
    MID_WINDOWS_ENABLED,
    WALL_TRIANGLES,
    WINDOW_TRIANGLES,
    Row,
    bays,
    build_mid_shell,
    face_with_windows,
    flat_roof,
    mid_roof,
    window_origins,
    window_rows,
)
from onward_generator.detail.roofs import pitched_spec
from onward_generator.generation.mesh import merge, open_edge_defects

CFG = load_detail_config()
FLAT = CFG.templates["generic_residential"]  # a parapet and no hip: the mid shell drops the parapet
MINOR = CFG.templates["generic_minor"]  # no parapet and a hip
WALL = "scenic_wall_light"
TWO = Storeys(2, 3.0, "heights_levels")


def _segment(index: int, length: float, *, exposed: bool = True, y: float = 0.0) -> Segment:
    return Segment(0, 0, index, (110.0, y), (110.0 + length, y), exposed)


def test_constants() -> None:
    assert (MID_MAX_TRIANGLES, MID_BAY_M, MID_WINDOW_W_M, MID_WINDOW_H_M, MID_SILL_M, MID_HEAD_M) == (150, 3.0, 1.2, 1.4, 0.9, 0.3)
    assert (WINDOW_TRIANGLES, WALL_TRIANGLES, MID_GLASS, MID_REPRESENTATION, CAP_FALLBACK) == (8, 2, "glass_dark", "mid_shell", "mid_triangle_cap")
    assert MID_WINDOWS_ENABLED is True
    assert Row(0, 0, 3).triangles == 24


@pytest.mark.parametrize(("length", "n"), [(2.99, 0), (3.0, 1), (8.99, 2), (9.0, 3), (12.5, 4)])
def test_bays(length: float, n: int) -> None:
    assert bays(length) == n


def test_window_origins_centre_each_window_in_its_bay() -> None:
    seg = _segment(0, 10.0)
    assert window_origins(seg, Row(0, 0, 3), 3.0) == pytest.approx([(10 / 6 - 0.6, 0.9), (5.0 - 0.6, 0.9), (50 / 6 - 0.6, 0.9)])
    assert window_origins(seg, Row(0, 2, 1), 3.5) == pytest.approx([(5.0 - 0.6, 7.9)])


def test_face_with_windows_is_closed_and_shares_every_hole_edge_with_its_glass() -> None:
    seg = _segment(0, 9.0)
    origins = [o for storey in (0, 1) for o in window_origins(seg, Row(0, storey, 3), 3.0)]
    wall, glass = face_with_windows(seg, 6.0, origins)
    # A face with n holes and no Steiner points: 4 + 4n vertices, 2 + 6n triangles; each glass quad 2.
    assert wall.triangle_count == WALL_TRIANGLES + 6 * len(origins) == 38
    assert glass.triangle_count == 2 * len(origins) == 12
    x0, x1 = 110.0, 119.0
    outline = [((x0, 0, 6), (x1, 0, 6)), ((x0, 0, 0), (x0, 0, 6)), ((x1, 0, 0), (x1, 0, 6))]
    assert open_edge_defects(merge([wall, glass]), z_base=0.0, allowed=outline, quantum=1e-6) == 0
    # Without its glass, the holes are open.
    assert open_edge_defects(wall, z_base=0.0, allowed=outline, quantum=1e-6) > 0
    # The wall runs east along y = 0 with the building to its left: every face points south, out.
    for mesh in (wall, glass):
        normals = mesh.face_normals()
        assert (normals[:, 1] < 0).all() and np.allclose(normals[:, [0, 2]], 0.0)
    corners = {tuple(np.round(v, 6)) for v in glass.vertices}
    expected = {(round(x0 + s, 6), 0.0, round(v, 6)) for s0, v0 in origins
                for s in (s0, s0 + 1.2) for v in (v0, v0 + 1.4)}
    assert corners == expected
    assert min(c[2] for c in corners) == pytest.approx(0.9) and max(c[2] for c in corners) == pytest.approx(3.0 + 0.9 + 1.4)
    assert min(c[0] for c in corners) == pytest.approx(x0 + 1.5 - 0.6)


def test_face_without_windows_is_one_quad() -> None:
    wall, glass = face_with_windows(_segment(0, 4.0), 5.0, [])
    assert wall.triangle_count == 2 and glass.triangle_count == 0
    assert sorted(map(tuple, wall.vertices.tolist())) == [(110.0, 0.0, 0.0), (110.0, 0.0, 5.0), (114.0, 0.0, 0.0), (114.0, 0.0, 5.0)]
    assert (wall.face_normals()[:, 1] < 0).all()


def test_window_rows_rank_longer_wall_then_lower_storey_then_index() -> None:
    segments = [_segment(0, 9.0), _segment(1, 12.0), _segment(2, 6.0), _segment(3, 9.0)]
    rows = window_rows(segments, TWO, 6.0, 0, cap_triangles=10_000)
    assert rows == [Row(1, 0, 4), Row(1, 1, 4), Row(0, 0, 3), Row(3, 0, 3), Row(0, 1, 3), Row(3, 1, 3), Row(2, 0, 2), Row(2, 1, 2)]


def test_window_rows_respect_the_cap_exactly() -> None:
    segments = [_segment(0, 9.0)]
    assert window_rows(segments, TWO, 6.0, MID_MAX_TRIANGLES - 24) == [Row(0, 0, 3)]  # reaches 150
    assert window_rows(segments, TWO, 6.0, MID_MAX_TRIANGLES - 23) == []  # would reach 151
    assert window_rows(segments, TWO, 6.0, MID_MAX_TRIANGLES - 48) == [Row(0, 0, 3), Row(0, 1, 3)]
    assert window_rows(segments, TWO, 6.0, 200) == []  # walls and roof already over the cap


def test_a_row_that_does_not_fit_leaves_room_for_a_smaller_one() -> None:
    segments = [_segment(0, 9.0), _segment(1, 3.0)]
    one = Storeys(1, 3.0, "heights_levels")
    assert window_rows(segments, one, 3.0, 140) == [Row(1, 0, 1)]  # 140 + 24 > 150; 140 + 8 fits


def test_window_rows_need_an_exposed_wall_of_a_bay() -> None:
    assert window_rows([_segment(0, 2.99)], TWO, 6.0, 0) == []
    assert window_rows([_segment(0, 3.0)], TWO, 6.0, 0) == [Row(0, 0, 1), Row(0, 1, 1)]
    assert window_rows([_segment(0, 30.0, exposed=False)], TWO, 6.0, 0) == []


@pytest.mark.parametrize(("storey_m", "count", "eave", "storeys"), [
    (2.6, 1, 2.6, [0]),     # sill 0.9 + window 1.4 + head 0.3 = 2.6: fits exactly
    (2.59, 1, 2.59, []),    # a storey too low for a window
    (2.6, 1, 2.59, []),     # under the eave
    (3.0, 2, 5.6, [0, 1]),
    (3.0, 2, 5.59, [0]),    # the upper window would touch the eave
])
def test_window_rows_keep_the_head_under_the_storey_and_the_eave(storey_m: float, count: int, eave: float, storeys: list[int]) -> None:
    rows = window_rows([_segment(0, 3.0)], Storeys(count, storey_m, "heights_levels"), eave, 0)
    assert [r.storey for r in rows] == storeys


def test_windows_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mid_module, "MID_WINDOWS_ENABLED", False)
    assert window_rows([_segment(0, 9.0)], TWO, 6.0, 0) == []
    footprint = box(110, 0, 122, 8)
    refined, segments = refine_exposure(footprint, Polygon(), CFG)
    shell = build_mid_shell(refined, segments, TWO, 6.0, FLAT, WALL, "roof_flat_grey")
    assert (shell.windows, shell.triangles, sorted(shell.parts)) == (0, 10, ["roof_flat_grey", WALL])


def test_a_flat_mid_shell_golden() -> None:
    """12 m x 8 m, two storeys: four walls (8) and a cap (2), then whole rows of 4 windows on the long walls (4 x 32)."""
    footprint = box(110, 0, 122, 8)
    refined, segments = refine_exposure(footprint, Polygon(), CFG)
    shell = build_mid_shell(refined, segments, TWO, 6.0, FLAT, WALL, "roof_flat_grey")
    assert shell.roof == {"type": "flat", "material": "roof_flat_grey"}
    assert (shell.windows, shell.exposed_faces, shell.defects, shell.triangles) == (16, 4, 0, 138)
    assert sorted(shell.parts) == [MID_GLASS, "roof_flat_grey", WALL]
    assert shell.parts[MID_GLASS].triangle_count == 32
    assert shell.parts["roof_flat_grey"].triangle_count == 2
    # No parapet: the roof is at the eave and nothing stands above it.
    assert float(max(m.vertices[:, 2].max() for m in shell.parts.values())) == 6.0
    again = build_mid_shell(refined, segments, TWO, 6.0, FLAT, WALL, "roof_flat_grey")
    assert again.roof == shell.roof and sorted(again.parts) == sorted(shell.parts)
    for material, mesh in shell.parts.items():
        assert np.array_equal(again.parts[material].vertices, mesh.vertices)
        assert np.array_equal(again.parts[material].triangles, mesh.triangles)


def test_a_party_wall_gets_no_window() -> None:
    footprint = box(110, 0, 122, 8)
    neighbour = box(122, 0, 130, 8)  # touches the east wall
    refined, segments = refine_exposure(footprint, neighbour, CFG)
    shell = build_mid_shell(refined, segments, TWO, 6.0, FLAT, WALL, "roof_flat_grey")
    assert shell.exposed_faces == 3
    glass = shell.parts[MID_GLASS]
    assert glass.vertices[:, 0].max() < 122.0 - 1e-6  # nothing on x = 122


def test_a_landmark_draws_no_window() -> None:
    refined, segments = refine_exposure(box(110, 0, 122, 8), Polygon(), CFG)
    shell = build_mid_shell(refined, segments, Storeys(1, 12.0, "single_band"), 12.0, FLAT, WALL, "roof_flat_grey", windows=False)
    assert shell.windows == 0 and MID_GLASS not in shell.parts and shell.triangles == 10


def test_mid_roof_gabled_puts_its_gables_in_the_wall() -> None:
    spec = pitched_spec({"roof:shape": "gabled"}, "roof_tile", 3.0)
    assert spec is not None
    template = replace(FLAT, parapet_m=0.0, hip=None, pitched=spec)
    parts, record = mid_roof(box(110, 0, 120, 6), 6.0, template, "roof_tile", WALL)
    assert [m for m, _ in parts] == ["roof_tile", WALL]
    assert record["type"] == "gabled" and record["material"] == "roof_tile" and "fallback" not in record
    assert all(mesh.triangle_count for _, mesh in parts)


def test_mid_roof_hip_follows_the_core_rule() -> None:
    parts, record = mid_roof(box(110, 0, 118, 8), 6.0, MINOR, "roof_flat_grey", WALL)
    assert [m for m, _ in parts] == ["roof_tile"]
    assert record == {"type": "hip", "rise_m": record["rise_m"], "pitch_deg": 22.0, "material": "roof_tile"}
    assert 0.1 < float(record["rise_m"]) <= 2.5  # type: ignore[arg-type]
    # The estimated hip of a parapet template (``choose_roof`` drops the parapet with it).
    estimated = replace(FLAT, parapet_m=0.0, hip=HipRoof(160.0, 28.0, 2.5, "roof_tile"))
    assert mid_roof(box(110, 0, 118, 8), 6.0, estimated, "roof_tile", WALL)[1]["type"] == "hip"
    # A hip with a parapet is never drawn, as in build_shell.
    assert mid_roof(box(110, 0, 118, 8), 6.0, replace(MINOR, parapet_m=0.5), "roof_flat_grey", WALL)[1] == {"type": "flat", "material": "roof_flat_grey"}
    # Too large for the hip: flat.
    assert mid_roof(box(110, 0, 130, 20), 6.0, MINOR, "roof_flat_grey", WALL)[1]["type"] == "flat"


def test_mid_roof_keeps_a_flat_cap_where_the_pitched_roof_cannot_be_drawn() -> None:
    spec = pitched_spec({"roof:shape": "gabled"}, "roof_tile", 3.0)
    template = replace(FLAT, parapet_m=0.0, hip=None, pitched=spec)
    courtyard = box(110, 0, 130, 20).difference(box(115, 5, 125, 15))
    parts, record = mid_roof(courtyard, 6.0, template, "roof_tile", WALL)
    assert record == {"type": "flat", "material": "roof_tile", "fallback": "footprint_has_courtyard"}
    assert [m for m, _ in parts] == ["roof_tile"]
    split = MultiPolygon([box(110, 0, 115, 5), box(120, 0, 125, 5)])
    assert mid_roof(split, 6.0, template, "roof_tile", WALL)[1]["fallback"] == "footprint_not_a_simple_polygon"
    assert flat_roof(box(110, 0, 115, 5), 6.0, "roof_tile", None)[1] == {"type": "flat", "material": "roof_tile"}


def test_a_roof_over_the_cap_falls_back_to_a_flat_cap() -> None:
    circle = Point(130, 0).buffer(8, quad_segs=15)  # 60 edges: 120 wall triangles before the roof
    spec = pitched_spec({"roof:shape": "dome"}, "roof_tile", 3.0)
    template = replace(FLAT, parapet_m=0.0, hip=None, pitched=spec)
    refined, segments = refine_exposure(circle, Polygon(), CFG)
    assert mid_roof(refined, 6.0, template, "roof_tile", WALL)[1]["type"] == "dome"
    shell = build_mid_shell(refined, segments, TWO, 6.0, template, WALL, "roof_tile")
    assert shell.roof == {"type": "flat", "material": "roof_tile", "fallback": CAP_FALLBACK}
    assert shell.windows == 0 and shell.defects == 0
    # A flat cap over the cap is not a fallback: there is nothing flatter to fall back to.
    flat = build_mid_shell(refined, segments, TWO, 6.0, FLAT, WALL, "roof_flat_grey")
    assert flat.roof == {"type": "flat", "material": "roof_flat_grey"} and flat.triangles > MID_MAX_TRIANGLES


def test_a_gabled_shell_is_closed_and_within_the_cap() -> None:
    spec = pitched_spec({"roof:shape": "gabled"}, "roof_tile", 3.0)
    template = replace(FLAT, parapet_m=0.0, hip=None, pitched=spec)
    refined, segments = refine_exposure(box(110, 0, 120, 6), Polygon(), CFG)
    shell = build_mid_shell(refined, segments, TWO, 6.0, template, WALL, "roof_tile")
    assert shell.roof["type"] == "gabled" and shell.defects == 0
    assert 50 <= shell.triangles <= MID_MAX_TRIANGLES
    assert set(shell.parts) == {MID_GLASS, "roof_tile", WALL}
