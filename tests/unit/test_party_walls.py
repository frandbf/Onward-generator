"""Blank party walls: the part a neighbour's wall hides leaves the visual mesh, the rest takes ``party_<wall>``.

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

import numpy as np
import pytest
from shapely import Polygon
from shapely.geometry import box

from onward_generator.detail import load_detail_config
from onward_generator.detail.config import _with_party
from onward_generator.detail.facades import (
    HIDDEN_GAP_M,
    Layout,
    Segment,
    Shell,
    WallAreas,
    build_shell,
    cover_steps,
    party_base,
    party_material,
    refine_exposure,
    split_blank_walls,
    visible_face,
    wall_cover,
)
from onward_generator.generation.config import Material
from onward_generator.generation.mesh import Mesh, merge

CFG = load_detail_config()
TEMPLATE = CFG.template_for("residential_multi", False)
WALL = "plaster_ochre"
ROOF = "roof_flat_grey"
# The segment runs east along y = 0; its outward side is y < 0.
SEG = Segment(0, 0, 0, (0.0, 0.0), (10.0, 0.0), False)


def test_party_material_names() -> None:
    assert party_material("plaster_ochre") == "party_plaster_ochre"
    assert party_base("party_plaster_ochre") == "plaster_ochre"
    assert party_base("plaster_ochre") == "plaster_ochre"
    assert party_base("counterparty_wall") == "counterparty_wall"


def test_every_detail_material_has_a_party_copy() -> None:
    ochre = Material((0.9, 0.6, 0.3, 1.0), 0.9)
    assert _with_party({"plaster_ochre": ochre}) == {"plaster_ochre": ochre, "party_plaster_ochre": ochre}
    assert CFG.materials["party_brick_red"] == CFG.materials["brick_red"]
    assert all(CFG.materials[party_material(name)] == CFG.materials[name]
               for name in CFG.materials if not name.startswith("party_"))


def test_wall_areas_add_and_round() -> None:
    total = WallAreas(1.04, 2.06, 3.0) + WallAreas(street=1.0, hidden=0.12)
    assert total == WallAreas(2.04, 2.06, 3.12)
    assert total.to_record() == {"street": 2.0, "party": 2.1, "hidden": 3.1}
    assert WallAreas().to_record() == {"street": 0.0, "party": 0.0, "hidden": 0.0}


def test_wall_cover_finds_a_touching_neighbour_and_its_top() -> None:
    assert wall_cover(SEG, [(box(2, -5, 6, 0), 7.5)]) == [(2.0, 6.0, 7.5)]
    # Within HIDDEN_GAP_M the neighbour still hides the wall; farther it does not.
    assert wall_cover(SEG, [(box(2, -5, 6, -0.04), 7.5)]) == [(2.0, 6.0, 7.5)]
    assert wall_cover(SEG, [(box(2, -5, 6, -0.06), 7.5)]) == []
    # On the inner side there is nothing to hide.
    assert wall_cover(SEG, [(box(2, 0, 6, 5), 7.5)]) == []
    # Two neighbours give two covers, each with its own top.
    assert wall_cover(SEG, [(box(0, -5, 4, 0), 3.0), (box(4, -5, 12, 0), 9.0)]) == [(0.0, 4.0, 3.0), (4.0, 10.0, 9.0)]
    assert HIDDEN_GAP_M == 0.05


def test_wall_cover_snaps_to_the_segment_ends_and_drops_slivers() -> None:
    assert wall_cover(SEG, [(box(0.04, -5, 9.97, 0), 6.0)]) == [(0.0, 10.0, 6.0)]
    assert wall_cover(SEG, [(box(0.05, -5, 5, 0), 6.0)]) == [(0.05, 5.0, 6.0)]
    # A piece 5 cm long counts; a shorter one is a corner touch.
    assert wall_cover(SEG, [(box(-1, -5, 0.05, 0), 6.0)]) == [(0.0, 0.05, 6.0)]
    assert wall_cover(SEG, [(box(-1, -5, 0.04, 0), 6.0)]) == []
    assert wall_cover(SEG, [(box(5, -5, 5.03, 0), 6.0)]) == []
    short = Segment(0, 0, 0, (0.0, 0.0), (0.1, 0.0), False)
    assert wall_cover(short, [(box(-1, -5, 0.05, 0), 6.0)]) == [(0.0, 0.05, 6.0)]
    assert wall_cover(short, [(box(0.05, -5, 1, 0), 6.0)]) == [(0.05, 0.1, 6.0)]


def test_wall_cover_follows_the_segment_direction() -> None:
    # A wall running north along x = 3 faces east (x > 3).
    north = Segment(0, 0, 0, (3.0, 1.0), (3.0, 9.0), False)
    assert wall_cover(north, [(box(3, 2, 8, 5), 4.0)]) == [(1.0, 4.0, 4.0)]
    assert wall_cover(north, [(box(-2, 2, 3, 5), 4.0)]) == []


def test_cover_steps_take_the_highest_neighbour_up_to_the_wall_top() -> None:
    assert cover_steps(10.0, 9.0, []) == [(0.0, 10.0, 0.0)]
    assert cover_steps(10.0, 9.0, [(2.0, 6.0, 5.0)]) == [(0.0, 2.0, 0.0), (2.0, 6.0, 5.0), (6.0, 10.0, 0.0)]
    assert cover_steps(10.0, 9.0, [(0.0, 10.0, 12.0)]) == [(0.0, 10.0, 9.0)]
    assert cover_steps(10.0, 9.0, [(2.0, 6.0, 5.0), (4.0, 8.0, 7.0)]) == [
        (0.0, 2.0, 0.0), (2.0, 4.0, 5.0), (4.0, 8.0, 7.0), (8.0, 10.0, 0.0),
    ]
    assert cover_steps(10.0, 9.0, [(4.0, 8.0, 7.0), (2.0, 6.0, 5.0)]) == [
        (0.0, 2.0, 0.0), (2.0, 4.0, 5.0), (4.0, 8.0, 7.0), (8.0, 10.0, 0.0),
    ]


def test_cover_steps_close_a_seam_between_two_neighbours() -> None:
    # A 2 cm gap between two covers takes the lower of the two, then merges with it.
    assert cover_steps(10.0, 9.0, [(0.0, 4.98, 6.0), (5.0, 10.0, 8.0)]) == [(0.0, 5.0, 6.0), (5.0, 10.0, 8.0)]
    assert cover_steps(10.0, 9.0, [(0.0, 4.98, 8.0), (5.0, 10.0, 6.0)]) == [(0.0, 4.98, 8.0), (4.98, 10.0, 6.0)]
    # A short step higher than both sides keeps its height.
    assert cover_steps(10.0, 9.0, [(0.0, 5.0, 2.0), (5.0, 5.02, 6.0), (5.02, 10.0, 3.0)]) == [
        (0.0, 5.0, 2.0), (5.0, 5.02, 6.0), (5.02, 10.0, 3.0),
    ]
    # A gap of exactly SNAP_M (5 cm) stays open.
    assert cover_steps(10.0, 9.0, [(0.0, 0.05, 6.0), (0.1, 10.0, 8.0)]) == [
        (0.0, 0.05, 6.0), (0.05, 0.1, 0.0), (0.1, 10.0, 8.0),
    ]
    # The first and last steps are never bridged: the ends are snapped by wall_cover.
    assert cover_steps(10.0, 9.0, [(0.02, 10.0, 4.0)]) == [(0.0, 0.02, 0.0), (0.02, 10.0, 4.0)]
    assert cover_steps(10.0, 9.0, [(0.0, 9.98, 4.0)]) == [(0.0, 9.98, 4.0), (9.98, 10.0, 0.0)]


def _rings(faces: list[Polygon]) -> list[tuple[float, set[tuple[float, float]]]]:
    return [(face.area, set(face.exterior.coords)) for face in faces]


def test_visible_face_is_the_wall_above_the_steps() -> None:
    assert _rings(visible_face(9.0, [(0.0, 10.0, 0.0)])) == [(90.0, {(0, 0), (10, 0), (10, 9), (0, 9)})]
    assert visible_face(9.0, [(0.0, 10.0, 9.0)]) == []
    assert _rings(visible_face(9.0, [(0.0, 2.0, 0.0), (2.0, 6.0, 5.0), (6.0, 10.0, 0.0)])) == [
        (70.0, {(0, 0), (2, 0), (2, 5), (6, 5), (6, 0), (10, 0), (10, 9), (0, 9)}),
    ]
    # A neighbour as tall as the wall splits it in two.
    assert _rings(visible_face(9.0, [(0.0, 2.0, 0.0), (2.0, 6.0, 9.0), (6.0, 10.0, 3.0)])) == [
        (18.0, {(0, 0), (2, 0), (2, 9), (0, 9)}),
        (24.0, {(6, 3), (10, 3), (10, 9), (6, 9)}),
    ]
    assert all(face.is_valid for face in visible_face(9.0, [(0.0, 2.0, 4.0), (2.0, 6.0, 1.0), (6.0, 10.0, 3.0)]))


def _shell(neighbour: Polygon) -> Shell:
    footprint = box(0, 0, 10, 8)
    refined, segments = refine_exposure(footprint, neighbour, CFG)
    return build_shell(refined, segments, Layout(), 6.0, TEMPLATE, WALL, ROOF, "b1", CFG)


def _faces(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    """Triangle vertices and outward unit normals."""
    tri = mesh.vertices[mesh.triangles]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return tri, normal / np.linalg.norm(normal, axis=1)[:, None]


def _area(mesh: Mesh) -> float:
    tri, _ = _faces(mesh)
    return float(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2)


def test_build_shell_lists_its_blank_party_walls() -> None:
    shell = _shell(box(10, 0, 20, 8))
    assert shell.wall_material == WALL and shell.z_top == pytest.approx(7.0)  # 6 m and a 1 m parapet
    assert [(seg.a, seg.b) for seg, _ in shell.blank_walls] == [((10.0, 0.0), (10.0, 8.0))]
    assert all(any(mesh is quad for mesh in shell.parts[WALL]) for _, quad in shell.blank_walls)
    assert _shell(Polygon()).blank_walls == []


def test_split_blank_walls_drops_the_hidden_part_and_keeps_the_rest_as_party() -> None:
    neighbour = box(10, 0, 20, 8)
    shell = _shell(neighbour)
    walls_before = merge(shell.parts[WALL])
    areas = split_blank_walls(shell, [(neighbour, 4.0)])
    assert areas == WallAreas(0.0, pytest.approx(8 * 3.0), pytest.approx(8 * 4.0))
    # The wall material keeps every face but the party wall at x = 10.
    tri, normal = _faces(merge(shell.parts[WALL]))
    assert not np.any(np.all(np.isclose(tri[:, :, 0], 10.0), axis=1) & np.isclose(normal[:, 0], 1.0))
    assert _area(walls_before) - _area(merge(shell.parts[WALL])) == pytest.approx(8 * 7.0)
    # The party material holds the wall above the neighbour's 4 m top, facing out (+x).
    party = merge(shell.parts[party_material(WALL)])
    tri, normal = _faces(party)
    assert np.allclose(tri[:, :, 0], 10.0) and np.allclose(normal, [1.0, 0.0, 0.0])
    assert tri[:, :, 2].min() == pytest.approx(4.0) and tri[:, :, 2].max() == pytest.approx(7.0)
    assert tri[:, :, 1].min() == pytest.approx(0.0) and tri[:, :, 1].max() == pytest.approx(8.0)
    assert _area(party) == pytest.approx(8 * 3.0)


def test_split_blank_walls_sums_every_party_wall() -> None:
    # Neighbours east (y 2–8 of the 8 m wall, 3 m top) and west (whole wall, 5 m top); z_top is 7 m.
    east, west = box(10, 2, 20, 8), box(-10, 0, 0, 8)
    footprint = box(0, 0, 10, 8)
    refined, segments = refine_exposure(footprint, east.union(west), CFG)
    shell = build_shell(refined, segments, Layout(), 6.0, TEMPLATE, WALL, ROOF, "b1", CFG)
    assert len(shell.blank_walls) == 2
    areas = split_blank_walls(shell, [(east, 3.0), (west, 5.0)])
    hidden = 6 * 3.0 + 8 * 5.0
    assert areas == WallAreas(0.0, pytest.approx(2 * 8 * 7.0 - hidden), pytest.approx(hidden))
    assert _area(merge(shell.parts[party_material(WALL)])) == pytest.approx(2 * 8 * 7.0 - hidden)


def test_a_taller_neighbour_hides_the_whole_party_wall() -> None:
    neighbour = box(10, 0, 20, 8)
    shell = _shell(neighbour)
    assert split_blank_walls(shell, [(neighbour, 20.0)]) == WallAreas(0.0, 0.0, pytest.approx(8 * 7.0))
    assert party_material(WALL) not in shell.parts


def test_a_party_wall_with_no_neighbour_wall_stays_whole() -> None:
    # The neighbour is 0.5 m away: a blind party wall for the facade rules, but nothing hides it.
    neighbour = box(10.5, 0, 20, 8)
    shell = _shell(neighbour)
    assert split_blank_walls(shell, [(neighbour, 20.0)]) == WallAreas(0.0, pytest.approx(8 * 7.0), 0.0)
    assert _area(merge(shell.parts[party_material(WALL)])) == pytest.approx(8 * 7.0)


def test_split_blank_walls_without_party_walls_changes_nothing() -> None:
    shell = _shell(Polygon())
    before = [mesh for mesh in shell.parts[WALL]]
    assert split_blank_walls(shell, []) == WallAreas()
    assert shell.parts[WALL] == before and party_material(WALL) not in shell.parts
