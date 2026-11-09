"""Plan D6: an observed LoD2 volume on a landmark footprint (``detail.lod2``): transform, ground, match, clip, cap, collider, record."""

from __future__ import annotations

import numpy as np
import pytest
from shapely import LineString, MultiPoint, Polygon, box

from onward_generator.detail.lod2 import (
    LocalGroup,
    Lod2Error,
    Lod2Index,
    Lod2Part,
    Lod2Settings,
    ObjGroup,
    ObjMesh,
    build_lod2_part,
    clip_to_footprint,
    cluster_simplify,
    ground_offset,
    group_colliders,
    local_groups,
    lod2_parts,
    match_landmark,
    outside_area_m2,
    outward,
    split_roof_wall,
    to_local,
)
from onward_generator.generation.mesh import Mesh, closed_manifold_defects, merge, signed_volume
from onward_generator.generation.scene import Issue
from onward_generator.geo.projection import LocalFrame
from tests.conftest import MURCIA_CENTER
from tests.lod2_fixture import GROUND_Z, box_group

MURCIA = LocalFrame.create(*MURCIA_CENTER)
FRAME = {"crs": MURCIA.crs, "unit": "m", "origin_projected": [MURCIA.origin_easting, MURCIA.origin_northing]}
INDEX = Lod2Index("idx.json", "a" * 64, "es.madrid_lod2", "Synthetic provider", "CC-BY-4.0", "Origen de los datos: test",
                  "2026-07-23", "EPSG:25830", ())
WALL, ROOF = "stone_warm", "roof_tile"


def group(name: str, mesh: Mesh, code: str = "M-000001") -> LocalGroup:
    return LocalGroup(f"{code}:{name}", code, "2026-10-29", mesh.vertices, mesh.triangles,
                      MultiPoint([tuple(p) for p in mesh.vertices[:, :2]]).convex_hull)


def build(footprint: Polygon, groups: list[LocalGroup], settings: Lod2Settings | None = None, max_height_m: float = 120.0) -> tuple[Lod2Part | None, list[Issue]]:
    return build_lod2_part("osm-way-1", footprint, groups, INDEX, wall_material=WALL, roof_material=ROOF,
                           max_height_m=max_height_m, settings=settings or Lod2Settings())


def grid_roof(x0: float, y0: float, size: float, n: int, z: float) -> Mesh:
    """A flat roof of ``n`` x ``n`` squares, two upward triangles each."""
    step = size / n
    xs = np.arange(n + 1) * step
    vertices = np.array([(x0 + x, y0 + y, z) for y in xs for x in xs], dtype=np.float64)
    tris = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            tris += [(a, a + 1, a + n + 2), (a, a + n + 2, a + n + 1)]
    return Mesh(vertices, np.array(tris, dtype=np.int64))


# ---------------------------------------------------------------------------------------------------- transform


def test_to_local_subtracts_the_frame_origin_and_keeps_z() -> None:
    east, north = FRAME["origin_projected"]
    moved = to_local(np.array([[east + 12.5, north - 3.0, 655.25]]), FRAME)
    assert moved.tolist() == [[12.5, -3.0, 655.25]]
    assert FRAME["crs"] == "EPSG:25830"


@pytest.mark.parametrize("crs", ["EPSG:4326", "EPSG:32630", None])
def test_to_local_refuses_another_frame_crs(crs: str | None) -> None:
    with pytest.raises(Lod2Error, match=f"the scene frame is {crs}, not EPSG:25830 in metres"):
        to_local(np.array([[440000.0, 4474000.0, 650.0]]), {**FRAME, "crs": crs})


def test_to_local_refuses_a_frame_not_in_metres_and_degrees_in_the_obj() -> None:
    with pytest.raises(Lod2Error, match="in metres"):
        to_local(np.array([[440000.0, 4474000.0, 650.0]]), {**FRAME, "unit": "ft"})
    with pytest.raises(Lod2Error, match="look like degrees"):
        to_local(np.array([[-3.7, 40.4, 650.0], [-3.69, 40.41, 655.0]]), FRAME)
    # One coordinate past the degree range is enough to read them as metres.
    assert to_local(np.array([[-3.7, 40.4, 650.0], [181.0, 40.4, 650.0]]), FRAME).shape == (2, 3)
    assert to_local(np.array([[-3.7, 40.4, 650.0], [-3.7, 91.0, 650.0]]), FRAME).shape == (2, 3)


def test_local_groups_move_every_block_group_with_its_own_vertices() -> None:
    east, north = FRAME["origin_projected"]
    vertices = np.array([[east, north, 650.0], [east + 4, north, 650.0], [east, north + 4, 650.0], [east + 9, north + 9, 1.0]])
    mesh = ObjMesh(vertices, (ObjGroup("a", np.array([[0, 1, 2]])), ObjGroup("b", np.array([[2, 1, 0]]))), "EPSG:25830")
    from onward_generator.detail.lod2 import Lod2Block

    index = Lod2Index(*[getattr(INDEX, f) for f in ("path", "sha256", "source", "provider", "license", "attribution", "data_date", "crs")],
                      (Lod2Block("M-7", "M-7.zip", "0", "u", "2026-10-29", mesh),))
    found = local_groups(index, FRAME)
    assert [g.id for g in found] == ["M-7:a", "M-7:b"]
    assert found[0].vertices.tolist() == [[0, 0, 650], [4, 0, 650], [0, 4, 650]]
    assert found[0].triangles.tolist() == [[0, 1, 2]] and found[1].triangles.tolist() == [[2, 1, 0]]
    assert found[0].hull.area == pytest.approx(8.0)
    assert (found[0].cod_manzan, found[0].obj_date) == ("M-7", "2026-10-29")


def test_lod2_parts_in_another_frame_give_each_landmark_a_crs_warning() -> None:
    from onward_generator.detail.lod2 import Lod2Block

    mesh = ObjMesh(np.array([[440000.0, 4474000.0, 650.0]] * 3), (ObjGroup("a", np.array([[0, 1, 2]])),), "EPSG:25830")
    index = Lod2Index(*[getattr(INDEX, f) for f in ("path", "sha256", "source", "provider", "license", "attribution", "data_date", "crs")],
                      (Lod2Block("M-7", "M-7.zip", "0", "u", "2026-10-29", mesh),))
    parts, issues = lod2_parts({"b": box(0, 0, 10, 10), "a": box(20, 0, 30, 10)}, index, {**FRAME, "crs": "EPSG:32630"},
                               wall_material=WALL, roof_material=ROOF, max_height_m=120.0)
    assert parts == {}
    assert list(issues) == ["a", "b"]
    assert [(i.code, i.severity, i.object_id) for i in issues["a"]] == [("landmark_lod2_crs_mismatch", "warning", "a")]
    assert issues["a"][0].message.endswith("; the placeholder stays")


# ---------------------------------------------------------------------------------------------------- ground


def test_flat_ground_puts_the_base_at_zero_and_keeps_the_eave() -> None:
    mesh = box_group(0, 0, 10, 10, 12.0)
    assert ground_offset(mesh.vertices, box(0, 0, 10, 10), 1.0) == (GROUND_Z, GROUND_Z)
    part, issues = build(box(0, 0, 10, 10), [group("g", mesh)])
    assert issues == [] and part is not None
    allv = merge([p.mesh for p in part.visual]).vertices
    assert allv[:, 2].min() == 0.0 and allv[:, 2].max() == 12.0
    assert part.record["ground_offset"]["height_m"] == 12.0


def test_on_a_slope_the_highest_outer_wall_base_is_the_ground_and_the_low_walls_sink() -> None:
    low = box_group(0, 0, 5, 10, 12.0)
    high = box_group(5, 0, 10, 10, 12.0, base=2.0)
    vertices = np.concatenate([low.vertices, high.vertices])
    assert ground_offset(vertices, box(0, 0, 10, 10), 1.0) == (GROUND_Z + 2.0, GROUND_Z)
    part, _ = build(box(0, 0, 10, 10), [group("low", low), group("high", high)])
    assert part is not None
    ground = part.record["ground_offset"]
    assert (ground["orthometric_m"], ground["lowest_orthometric_m"], ground["height_m"]) == (652.0, 650.0, 10.0)
    assert ground["sunk_vertices"] == 8  # the low box's base corners, two per wall face, end at the street instead of 2 m under it
    assert merge([p.mesh for p in part.visual]).vertices[:, 2].min() == 0.0


def test_ground_without_an_outer_wall_column_is_the_lowest_vertex() -> None:
    roof_only = grid_roof(0, 0, 10, 1, GROUND_Z + 9.0)
    assert ground_offset(roof_only.vertices, box(0, 0, 10, 10), 1.0) == (GROUND_Z + 9.0, GROUND_Z + 9.0)
    # Walls only in the middle of the footprint, farther than 1 m from its edge, are not outer walls.
    inner = box_group(4, 4, 6, 6, 5.0, base=3.0)
    vertices = np.concatenate([inner.vertices, box_group(0, 0, 10, 10, 1.0, base=0.5).vertices[:0]])
    assert ground_offset(vertices, box(0, 0, 10, 10), 1.0) == (GROUND_Z + 3.0, GROUND_Z + 3.0)
    assert ground_offset(inner.vertices, box(0, 0, 10, 10), 2.0) == (GROUND_Z + 3.0, GROUND_Z + 3.0)
    # A wall column shorter than 1 m is not a wall.
    assert ground_offset(box_group(0, 0, 10, 10, 0.99, base=0.0).vertices, box(0, 0, 10, 10), 1.0) == (GROUND_Z, GROUND_Z)


def test_the_height_must_be_inside_the_landmark_range() -> None:
    mesh = box_group(0, 0, 10, 10, 12.0)
    part, issues = build(box(0, 0, 10, 10), [group("g", mesh)], max_height_m=12.0)
    assert part is not None and issues == []
    part, issues = build(box(0, 0, 10, 10), [group("g", mesh)], max_height_m=11.999)
    assert part is None
    assert [(i.code, i.severity, i.object_id) for i in issues] == [("landmark_lod2_height_out_of_range", "warning", "osm-way-1")]
    assert issues[0].message == "LoD2 height 12.0 m is outside (0, 11.999]; the placeholder stays"


# ---------------------------------------------------------------------------------------------------- match


def test_a_group_matches_from_half_its_hull_on_the_footprint() -> None:
    footprint = box(0, 0, 10, 10)
    inside = group("inside", box_group(0, 0, 10, 6, 8.0))
    half = group("half", box_group(5, 6, 15, 10, 8.0))
    under = group("under", box_group(5.1, 6, 15.1, 10, 8.0))
    found = match_landmark(footprint, [inside, half], Lod2Settings())
    assert not isinstance(found, str)
    assert [g.id for g in found.groups] == ["M-000001:inside", "M-000001:half"]
    assert found.cover == pytest.approx(0.8)
    found = match_landmark(footprint, [inside, under], Lod2Settings())
    assert not isinstance(found, str) and [g.id for g in found.groups] == ["M-000001:inside"]


def test_the_matched_groups_must_cover_enough_of_the_footprint() -> None:
    footprint = box(0, 0, 10, 10)
    found = match_landmark(footprint, [group("g", box_group(0, 0, 10, 6, 8.0))], Lod2Settings())
    assert not isinstance(found, str) and found.cover == pytest.approx(0.6)
    assert match_landmark(footprint, [group("g", box_group(0, 0, 10, 5.9, 8.0))], Lod2Settings()) == "low_cover"
    part, issues = build(footprint, [group("g", box_group(0, 0, 10, 5.9, 8.0))])
    assert part is None
    assert [(i.code, i.severity, i.message) for i in issues] == [
        ("landmark_lod2_no_match", "info", "the LoD2 groups cover less than 0.6 of it; the placeholder and its tower stay")]


def test_a_small_footprint_is_left_alone() -> None:
    small = box(0, 0, 4, 4)
    assert match_landmark(small, [group("g", box_group(0, 0, 4, 4, 8.0))], Lod2Settings()) == "too_small"
    part, issues = build(small, [group("g", box_group(0, 0, 4, 4, 8.0))])
    assert part is None
    assert [(i.code, i.severity, i.message) for i in issues] == [
        ("landmark_lod2_footprint_too_small", "warning", "footprint 16.0 m² is below 20 m²; the placeholder stays")]
    exact = box(0, 0, 4, 5)
    assert not isinstance(match_landmark(exact, [group("g", box_group(0, 0, 4, 5, 8.0))], Lod2Settings()), str)


def test_no_overlap_and_the_neighbours_group_give_no_part() -> None:
    footprint = box(0, 0, 10, 10)
    neighbour = group("next", box_group(10, 0, 20, 10, 8.0))
    far = group("far", box_group(50, 50, 60, 60, 8.0))
    flat = LocalGroup("M-1:flat", "M-1", "d", np.zeros((3, 3)), np.array([[0, 1, 2]]), LineString([(0, 0), (5, 5)]))
    assert match_landmark(footprint, [neighbour, far, flat], Lod2Settings()) == "no_overlap"
    part, issues = build(footprint, [neighbour, far])
    assert part is None
    assert [(i.code, i.severity, i.message) for i in issues] == [
        ("landmark_lod2_no_match", "info", "no LoD2 group lies on it; the placeholder and its tower stay")]
    found = match_landmark(footprint, [neighbour, group("own", box_group(0, 0, 10, 10, 8.0))], Lod2Settings())
    assert not isinstance(found, str) and [g.id for g in found.groups] == ["M-000001:own"]


# ---------------------------------------------------------------------------------------------------- clip


def _clip(mesh: Mesh, footprint: Polygon, tolerance: float = 1.0) -> tuple[np.ndarray, np.ndarray, int]:
    return clip_to_footprint(mesh.vertices, mesh.triangles, footprint, tolerance)


def test_a_vertex_just_outside_moves_onto_the_footprint_edge() -> None:
    footprint = box(0, 0, 10, 10)
    before = footprint.wkt
    vertices, triangles, dropped = _clip(box_group(0, 0, 10.4, 10, 8.0), footprint)
    assert dropped == 0 and len(triangles) == 10
    assert vertices[:, 0].max() == pytest.approx(10.0)
    grown = footprint.buffer(1e-6)
    assert all(grown.covers(Polygon([(x, y), (x, y + 1e-9), (x + 1e-9, y)]).centroid) for x, y in vertices[:, :2])
    assert outside_area_m2(vertices, triangles, footprint) == 0.0
    assert footprint.wkt == before


def test_a_vertex_past_the_tolerance_drops_its_triangles_and_one_at_it_is_kept() -> None:
    footprint = box(0, 0, 10, 10)
    _, triangles, dropped = _clip(box_group(0, 0, 11.0, 10, 8.0), footprint)
    assert (len(triangles), dropped) == (10, 0)
    vertices, triangles, dropped = _clip(box_group(0, 0, 11.01, 10, 8.0), footprint)
    # Every face but the -x wall has a corner at x = 11.01: only its two triangles stay.
    assert dropped == 8 and len(triangles) == 2
    assert vertices[:, 0].max() == pytest.approx(0.0)
    assert outside_area_m2(vertices, triangles, footprint) == 0.0


def test_a_triangle_whose_centroid_is_outside_goes() -> None:
    ell = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
    # All three corners are inside the L, but the plan centroid (4.67, 4.67) is in its notch.
    vertices = np.array([[9.0, 2.0, 5.0], [2.0, 9.0, 5.0], [3.0, 3.0, 5.0], [0, 0, 5], [3, 0, 5], [0, 3, 5]])
    out_v, out_t, dropped = clip_to_footprint(vertices, np.array([[0, 1, 2]]), ell, 1.0)
    assert dropped == 1 and len(out_t) == 0 and len(out_v) == 0
    out_v, out_t, dropped = clip_to_footprint(vertices, np.array([[3, 4, 5]]), ell, 1.0)
    assert dropped == 0 and out_t.tolist() == [[0, 1, 2]] and out_v.tolist() == [[0, 0, 5], [3, 0, 5], [0, 3, 5]]


def test_on_a_concave_footprint_a_triangle_across_the_notch_goes() -> None:
    ell = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
    before = ell.wkt
    vertices = np.array([[9.5, 0.5, 5], [0.5, 9.5, 5], [0.5, 0.5, 5], [9.5, 3.5, 5], [1, 1, 5], [3, 9, 5]], dtype=np.float64)
    triangles = np.array([[2, 0, 1], [4, 3, 2], [2, 5, 1]])
    out_v, out_t, dropped = clip_to_footprint(vertices, triangles, ell, 1.0)
    assert dropped == 1 and len(out_t) == 2
    assert outside_area_m2(out_v, out_t, ell) == 0.0
    assert outside_area_m2(vertices, triangles, ell) > 0.0
    # A vertical wall across the notch has no plan area, but its line still leaves the footprint.
    wall = np.array([[9.5, 3.5, 0], [3.5, 9.5, 0], [3.5, 9.5, 5]], dtype=np.float64)
    assert clip_to_footprint(wall, np.array([[0, 1, 2]]), ell, 1.0)[2] == 1
    inside_wall = np.array([[1, 1, 0], [3, 3, 0], [3, 3, 5]], dtype=np.float64)
    assert clip_to_footprint(inside_wall, np.array([[0, 1, 2]]), ell, 1.0)[2] == 0
    assert ell.wkt == before


def test_clip_of_nothing_and_outside_area_of_vertical_triangles() -> None:
    out_v, out_t, dropped = clip_to_footprint(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), box(0, 0, 1, 1), 1.0)
    assert (out_v.shape, out_t.shape, dropped) == ((0, 3), (0, 3), 0)
    wall = np.array([[20, 0, 0], [30, 0, 0], [30, 0, 5]], dtype=np.float64)
    assert outside_area_m2(wall, np.array([[0, 1, 2]]), box(0, 0, 1, 1)) == 0.0


# ---------------------------------------------------------------------------------------------------- cap and simplification


def _house(n: int) -> Mesh:
    """Four walls (8 triangles) under a flat roof of ``n`` x ``n`` squares (2 n² triangles)."""
    from onward_generator.generation.mesh import box as box_mesh

    return merge([box_mesh(0, 10, 0, 10, GROUND_Z, GROUND_Z + 8.0, omit=("-z", "+z")), grid_roof(0, 0, 10, n, GROUND_Z + 8.0)])


def test_a_mesh_at_the_cap_is_not_simplified() -> None:
    house = _house(4)  # 8 wall + 32 roof triangles
    part, issues = build(box(0, 0, 10, 10), [group("g", house)], Lod2Settings(max_triangles=40))
    assert issues == [] and part is not None
    simplification = part.record["simplification"]
    assert simplification["cell_m"] is None
    assert (simplification["triangles_before"], simplification["triangles_after_clip"], simplification["triangles_after"]) == (40, 40, 40)


def test_one_triangle_over_the_cap_is_simplified_under_it() -> None:
    house = _house(8)  # 136 triangles
    settings = Lod2Settings(max_triangles=135)
    part, issues = build(box(0, 0, 10, 10), [group("g", house)], settings)
    assert issues == [] and part is not None
    simplification = part.record["simplification"]
    # 1.25 m roof squares: the 0.25, 0.5 and 1 m cells merge no vertex, the 2 m cell does.
    assert simplification["cell_m"] == 2.0 and simplification["max_triangles"] == 135
    assert simplification["triangles_before"] == 136 and simplification["triangles_after"] <= 135
    drawn = sum(p.mesh.triangle_count for p in part.visual)
    assert drawn == simplification["triangles_after"]
    again, _ = build(box(0, 0, 10, 10), [group("g", house)], settings)
    assert again is not None
    assert [(p.mesh.vertices.tobytes(), p.mesh.triangles.tobytes()) for p in part.visual] == [
        (p.mesh.vertices.tobytes(), p.mesh.triangles.tobytes()) for p in again.visual]
    assert all(p.mesh.drop_degenerate()[1] == 0 for p in part.visual)


def test_a_mesh_that_cannot_reach_the_cap_keeps_the_placeholder() -> None:
    house = _house(8)
    part, issues = build(box(0, 0, 10, 10), [group("g", house)], Lod2Settings(max_triangles=135, max_simplify_passes=3))
    assert part is None
    assert [(i.code, i.severity, i.message) for i in issues] == [
        ("landmark_lod2_over_cap", "warning", "136 triangles stay over the cap of 135 after 3 passes; the placeholder stays")]


def test_cluster_simplify_merges_a_cell_into_its_mean_and_drops_collapsed_triangles() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.05, 0.05, 0.0]])
    triangles = np.array([[0, 2, 3], [1, 2, 3], [0, 1, 4], [4, 2, 3]])
    out_v, out_t = cluster_simplify(vertices, triangles, 0.5)
    assert out_t.tolist() == [[0, 2, 1]]  # cells in key order: the merged corner, (0, 1), (1, 0)
    assert out_v.tolist()[0] == pytest.approx([0.05, 0.05 / 3, 0.0])
    assert out_v.tolist()[1:] == [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]
    assert out_t.dtype == np.int64


# ---------------------------------------------------------------------------------------------------- materials, winding


def test_the_roof_threshold_is_inclusive() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [0.0, 1.0, 0.0], [-3.0, 0.0, 4.0]])  # unit normal (0.8, 0, 0.6)
    triangles = np.array([[0, 1, 2]])
    roof, wall = split_roof_wall(vertices, triangles, 0.6)
    assert (len(roof), len(wall)) == (1, 0)
    roof, wall = split_roof_wall(vertices, triangles, 0.6000001)
    assert (len(roof), len(wall)) == (0, 1)
    roof, wall = split_roof_wall(np.zeros((3, 3)), triangles, 0.0)  # a zero-area triangle counts as wall, not a NaN roof
    assert (len(roof), len(wall)) == (1, 0)
    roof, wall = split_roof_wall(np.zeros((3, 3)), triangles, 0.01)
    assert (len(roof), len(wall)) == (0, 1)


def test_the_part_uses_only_the_landmark_materials_and_faces_out() -> None:
    inward = box_group(0, 0, 10, 10, 8.0)
    flipped = Mesh(inward.vertices, inward.triangles[:, ::-1].copy())
    assert outward(flipped.vertices, flipped.triangles).tolist() == inward.triangles.tolist()
    assert outward(inward.vertices, inward.triangles) is inward.triangles
    part, _ = build(box(0, 0, 10, 10), [group("g", flipped)])
    assert part is not None
    assert [p.material for p in part.visual] == [WALL, ROOF]
    roof = next(p.mesh for p in part.visual if p.material == ROOF)
    assert (roof.face_normals()[:, 2] > 0).all() and roof.triangle_count == 2


def test_an_uncovered_strip_gets_a_floor_at_street_level() -> None:
    part, _ = build(box(0, 0, 10, 10), [group("g", box_group(0, 0, 10, 8, 8.0))])
    assert part is not None
    roof = next(p.mesh for p in part.visual if p.material == ROOF)
    assert part.record["clip"]["floor_triangles"] == 2
    assert roof.triangle_count == 4 and roof.vertices[:, 2].min() == 0.0


# ---------------------------------------------------------------------------------------------------- collider


def test_each_group_gets_a_closed_prism_collider() -> None:
    colliders, issues = group_colliders(box(0, 0, 10, 10), [("a", box(0, 0, 6, 10), 9.0), ("b", box(6, 0, 12, 10), 5.0)], "osm-way-1")
    assert issues == []
    assert len(colliders) == 2
    for mesh, top in zip(colliders, (9.0, 5.0), strict=True):
        assert closed_manifold_defects(mesh) == 0 and signed_volume(mesh) > 0
        assert mesh.vertices[:, 2].min() == 0.0 and mesh.vertices[:, 2].max() == top
    assert colliders[1].vertices[:, 0].max() == 10.0  # the hull is clipped to the footprint


def test_a_degenerate_or_flat_group_gets_no_collider_and_a_critical_issue() -> None:
    colliders, issues = group_colliders(box(0, 0, 10, 10), [("line", LineString([(0, 0), (5, 5)]), 9.0),
                                                            ("flat", box(0, 0, 5, 5), 0.0),
                                                            ("away", box(20, 20, 25, 25), 4.0)], "osm-way-1")
    assert colliders == []
    assert [(i.code, i.severity, i.object_id) for i in issues] == [("landmark_lod2_collider_not_closed", "critical", "osm-way-1")] * 3
    assert issues[0].message == "line: no closed collider (empty plan or height)"


def test_the_part_colliders_are_separate_from_its_visual() -> None:
    part, issues = build(box(0, 0, 10, 10), [group("a", box_group(0, 0, 6, 10, 9.0)), group("b", box_group(6, 0, 10, 10, 5.0))])
    assert issues == [] and part is not None
    assert len(part.colliders) == 2
    assert all(closed_manifold_defects(m) == 0 for m in part.colliders)
    visual_ids = {id(p.mesh) for p in part.visual}
    assert not visual_ids & {id(m) for m in part.colliders}
    assert part.record["collider"] == {"method": "group_hull_prism_clipped_to_footprint", "parts": 2,
                                       "triangles": sum(m.triangle_count for m in part.colliders)}


# ---------------------------------------------------------------------------------------------------- record


def test_the_record_says_what_is_observed_and_what_is_estimated() -> None:
    part, issues = build(box(0, 0, 10, 10), [group("g", box_group(0, 0, 10.4, 10, 12.0), "M-000214")])
    assert issues == [] and part is not None
    assert part.record == {
        "provider": "Synthetic provider",
        "source": "es.madrid_lod2",
        "license": "CC-BY-4.0",
        "attribution": "Origen de los datos: test",
        "data_date": "2026-07-23",
        "cod_manzan": ["M-000214"],
        "obj_dates": ["2026-10-29"],
        "objects": ["M-000214:g"],
        "representation": "observed_lod2",
        "representative_of_real_volume": True,
        "representative_of_real_facade": False,
        "needs_custom_model": False,
        "geometry": {"origin": "observed", "crs": "EPSG:25830", "note": "LoD2 triangles of the municipal 3D model, moved to the local grid"},
        "match": {"origin": "estimated", "method": "plan_hull_overlap", "footprint_cover": 1.0, "min_group_overlap": 0.5,
                  "min_footprint_cover": 0.6},
        "ground_offset": {"origin": "estimated", "method": "highest_outer_wall_base", "orthometric_m": 650.0,
                          "lowest_orthometric_m": 650.0, "sunk_vertices": 0, "height_m": 12.0},
        "clip": {"origin": "estimated", "method": "clip_to_osm_footprint", "tolerance_m": 1.0, "dropped_triangles": 0,
                 "outside_area_m2": 0.0, "floor_triangles": 0},
        "simplification": {"origin": "estimated", "method": "vertex_clustering", "cell_m": None, "max_triangles": 2000,
                           "triangles_before": 10, "triangles_after_clip": 10, "triangles_after": 10},
        "collider": {"method": "group_hull_prism_clipped_to_footprint", "parts": 1, "triangles": 12},
        "materials": {"wall": WALL, "roof": ROOF},
    }


def test_nothing_left_after_the_clip_keeps_the_placeholder() -> None:
    footprint = box(0, 0, 10, 10)
    # The hull lies on the footprint, but every triangle reaches 3 m past its edge.
    spikes = Mesh(np.array([[1, 1, 650], [9, 1, 650], [5, 13, 660], [1, 9, 650], [9, 9, 650], [5, -3, 660]], dtype=np.float64),
                  np.array([[0, 1, 2], [3, 5, 4]]))
    part, issues = build(footprint, [group("g", spikes)], Lod2Settings(min_footprint_cover=0.0, min_group_overlap=0.0))
    assert part is None
    assert [(i.code, i.message) for i in issues] == [
        ("landmark_lod2_no_match", "nothing of the LoD2 groups is left on the footprint; the placeholder and its tower stay")]


def test_settings_record() -> None:
    assert Lod2Settings().to_record() == {"max_triangles": 2000, "simplify_cell_m": 0.25, "min_group_overlap": 0.5,
                                          "min_footprint_cover": 0.6, "clip_tolerance_m": 1.0, "roof_min_normal_z": 0.35}
