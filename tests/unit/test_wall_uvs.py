"""Wall UVs follow exposed runs and storeys (ADR-012)."""

from __future__ import annotations

from collections import defaultdict

import numpy as np
import pytest
from shapely import Polygon
from shapely.geometry import box

from onward_generator.detail import load_detail_config
from onward_generator.detail.facades import (
    Layout,
    Segment,
    Storeys,
    build_shell,
    exposed_runs,
    layout_segment,
    refine_exposure,
    storeys_for,
    wall_uv_frame,
)
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import (
    Node,
    Primitive,
    _accessor,
    build_glb,
    read_glb,
    recentre_uvs,
    surface_uvs,
)
from onward_generator.generation.mesh import Mesh, cap, merge, quad
from onward_generator.materials.openings import window_png
from tests.conftest import MURCIA_CENTER

CFG = load_detail_config()
TEMPLATE = CFG.template_for("residential_multi", False)
WALL = "plaster_ochre"
ROOF = "roof_flat_grey"
TILE = {WALL: 2.0, ROOF: 4.0, "frame_stone": 1.0, "glass_dark": 1.0, "ground": 8.0}
PNG = window_png(32)
# Local metres east and north of MURCIA_CENTER, the frame origin, so recentring has work to do.
EAST, NORTH = 180.0, -95.0




def _building(
    footprint: Polygon, others: Polygon | None, height: float, levels: int | None, *, openings: bool = True,
) -> tuple[list[Primitive], dict[str, object], list[Segment], Storeys]:
    near = others if others is not None else Polygon()
    refined, segments = refine_exposure(footprint, near, CFG)
    storeys = storeys_for(height, levels, False, CFG)
    layout = Layout()
    if openings:
        for seg in segments:
            if seg.exposed:
                layout_segment(seg, storeys, TEMPLATE, TEMPLATE.ground, None, "door", CFG.recess_depth_m,
                               "b1", refined, near, CFG, layout)
    shell = build_shell(refined, segments, layout, height, TEMPLATE, WALL, ROOF, "b1", CFG)
    prims = [Primitive(merge(ms), m) for m, ms in sorted(shell.parts.items()) if any(x.triangle_count for x in ms)]
    return prims, wall_uv_frame(segments, storeys, levels), segments, storeys


def _glb(nodes: list[Node]) -> dict[str, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]]:
    data = build_glb(
        nodes, {p.material: Material((1.0, 1.0, 1.0, 1.0), 0.9) for n in nodes for p in n.primitives},
        repeat_images={p.material: PNG for n in nodes for p in n.primitives}, repeat_tile_m=TILE,
        unit_uv_materials={"glass_dark"}, roof_uv_materials={ROOF},
    )
    doc, blob = read_glb(data)
    out: dict[str, dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]] = {}
    for node in doc["nodes"]:
        prims = {}
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            attrs = prim["attributes"]
            prims[doc["materials"][prim["material"]]["name"]] = (
                _accessor(doc, blob, attrs["POSITION"]),
                _accessor(doc, blob, attrs["TEXCOORD_0"]),
                _accessor(doc, blob, prim["indices"]),
            )
        out[node["name"]] = prims
    return out


def _local(positions: np.ndarray) -> np.ndarray:
    """glTF (x, z, −y) back to local (x, y, z)."""
    p = np.asarray(positions, dtype=np.float64)
    return np.stack([p[:, 0], -p[:, 2], p[:, 1]], axis=1)


def _on_run(points: np.ndarray, triangles: np.ndarray, run: list[list[float]]) -> np.ndarray:
    """Vertices of outward triangles on the plane of a run segment."""
    tris = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    corners = points[tris]
    normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    hit = np.zeros(len(points), dtype=bool)
    for ax, ay, bx, by in run:
        d = np.array([bx - ax, by - ay])
        length = float(np.hypot(*d))
        d /= length
        out = np.array([d[1], -d[0]])
        rel = corners[:, :, :2] - (ax, ay)
        t = rel @ d
        inside = (np.abs(rel @ out) < 1e-3).all(axis=1) & (t > -1e-3).all(axis=1) & (t < length + 1e-3).all(axis=1)
        hit[tris[inside & (normals[:, :2] @ out > 0.999)].reshape(-1)] = True
    return hit


def _wall_points(prims: list[Primitive], frame: dict[str, object]) -> tuple[np.ndarray, np.ndarray]:
    wall = next(p for p in prims if p.material == WALL)
    uvs = surface_uvs(wall.mesh, TILE[WALL], frame=frame)
    return np.asarray(wall.mesh.vertices), uvs.astype(np.float64)


def _corners_continuous(points: np.ndarray, uvs: np.ndarray, mask: np.ndarray, run: list[list[float]]) -> int:
    """Every position shared by run vertices has one UV, except where a closed run meets its own start.

    There U ends on a whole number of repeats, which Repeat wrapping shows as the same texel.
    Returns how many shared positions were checked.
    """
    start = np.array(run[0][:2])
    closed = bool(np.allclose(run[-1][2:], start))
    by_pos: dict[tuple[float, float, float], list[int]] = defaultdict(list)
    for i in np.flatnonzero(mask):
        by_pos[tuple(np.round(points[i], 3))].append(int(i))
    checked = 0
    for indices in by_pos.values():
        if len(indices) < 2:
            continue
        spread = uvs[indices].max(axis=0) - uvs[indices].min(axis=0)
        assert spread[1] < 1e-4, (points[indices[0]], uvs[indices])
        if closed and np.allclose(points[indices[0], :2], start, atol=1e-3):
            assert spread[0] == pytest.approx(round(spread[0]), abs=1e-4)
            assert round(spread[0]) >= 1
        else:
            assert spread[0] < 1e-4, (points[indices[0]], uvs[indices])
        checked += 1
    return checked


# --- runs and frame ---------------------------------------------------------


def _seg(i: int, exposed: bool) -> Segment:
    return Segment(0, 0, i, (float(i), 0.0), (float(i + 1), 0.0), exposed)


def test_runs_break_at_party_walls_and_join_over_the_ring_start() -> None:
    segments = [_seg(0, True), _seg(1, False), _seg(2, True), _seg(3, True), _seg(4, False), _seg(5, True)]
    runs = [[s.index for s in run] for run in exposed_runs(segments)]
    assert runs == [[2, 3], [5, 0]]


def test_fully_exposed_ring_is_one_run_from_its_first_segment() -> None:
    courtyard = Segment(0, 1, 9, (0.0, 0.0), (1.0, 0.0), False)
    runs = [[s.index for s in run] for run in exposed_runs([_seg(0, True), _seg(1, True), _seg(2, True), courtyard])]
    assert runs == [[0, 1, 2]]
    assert exposed_runs([_seg(0, False), courtyard]) == []


def test_frame_records_storeys_and_their_origin() -> None:
    frame = wall_uv_frame([_seg(0, True)], Storeys(3, 3.1, "heights_levels"), 3)
    assert frame == {"runs": [[[0.0, 0.0, 1.0, 0.0]]], "storey_h": 3.1, "storeys_origin": "heights_levels"}
    estimated = wall_uv_frame([_seg(0, True)], Storeys(3, 2.8, "interior_storey"), 3)
    assert estimated["storeys_origin"] == "interior_storey"


def test_frame_without_levels_or_on_a_single_band_keeps_tile_m() -> None:
    for storeys, levels in ((Storeys(3, 3.0, "recomputed"), None), (Storeys(1, 20.0, "single_band"), 5)):
        frame = wall_uv_frame([_seg(0, True)], storeys, levels)
        assert frame["storey_h"] is None and frame["storeys_origin"] is None


def test_frame_rounds_coordinates_to_a_micrometre() -> None:
    seg = Segment(0, 0, 0, (1.23456789, 2.0), (3.0, 4.0000004), True)
    assert wall_uv_frame([seg], Storeys(1, 3.0, "heights_levels"), 1)["runs"] == [[[1.234568, 2.0, 3.0, 4.0]]]


# --- facade UVs on built shells ----------------------------------------------


def _rect() -> Polygon:
    return box(EAST, NORTH, EAST + 17.3, NORTH + 11.9)


def _l_shape() -> Polygon:
    return Polygon([(EAST, NORTH), (EAST + 21.0, NORTH), (EAST + 21.0, NORTH + 8.4), (EAST + 9.5, NORTH + 8.4),
                    (EAST + 9.5, NORTH + 19.0), (EAST, NORTH + 19.0)])


@pytest.mark.parametrize("footprint", [_rect(), _l_shape()], ids=["rectangle", "l_shape"])
def test_u_is_continuous_at_every_corner_and_v_whole_at_every_storey(footprint: Polygon) -> None:
    prims, frame, _, storeys = _building(footprint, None, 9.6, 3)
    points, uvs = _wall_points(prims, frame)
    runs = frame["runs"]
    assert isinstance(runs, list) and len(runs) == 1
    wall = next(p for p in prims if p.material == WALL)
    mask = _on_run(points, wall.mesh.triangles, runs[0])
    corners = len(footprint.exterior.coords) - 1
    # Each corner joins two faces at the ground, storey and top heights; opening edges add more.
    assert _corners_continuous(points, uvs, mask, runs[0]) >= 2 * corners
    length = footprint.exterior.length
    n = round(length / TILE[WALL])
    assert uvs[mask, 0].min() == pytest.approx(0.0, abs=1e-9)
    assert uvs[mask, 0].max() == pytest.approx(n, abs=1e-9)
    assert abs(length / n - TILE[WALL]) <= TILE[WALL] / (2 * n) + 1e-9
    # V is linear in height on every face, so a whole number of repeats per storey puts
    # V = level × k on every storey line, from the ground (V = 0) up.
    k = round(storeys.height_m / TILE[WALL])
    assert k == 2
    assert np.allclose(uvs[mask, 1], points[mask, 2] * k / storeys.height_m, atol=1e-9)
    assert np.allclose(uvs[mask & (points[:, 2] == 0.0), 1], 0.0)


def test_party_wall_restarts_u_and_keeps_the_face_projection() -> None:
    footprint = _rect()
    east = box(EAST + 17.3, NORTH - 2.0, EAST + 30.0, NORTH + 14.0)
    west = box(EAST - 12.0, NORTH - 2.0, EAST, NORTH + 14.0)
    prims, frame, segments, _ = _building(footprint, east.union(west), 9.6, 3, openings=False)
    assert sum(not s.exposed for s in segments) == 2
    runs = frame["runs"]
    assert isinstance(runs, list) and len(runs) == 2
    points, uvs = _wall_points(prims, frame)
    wall = next(p for p in prims if p.material == WALL)
    plain = surface_uvs(wall.mesh, TILE[WALL]).astype(np.float64)
    on_any = np.zeros(len(points), dtype=bool)
    for run in runs:
        mask = _on_run(points, wall.mesh.triangles, run)
        on_any |= mask
        assert uvs[mask, 0].min() == pytest.approx(0.0, abs=1e-9)
        assert uvs[mask, 0].max() == pytest.approx(round(17.3 / TILE[WALL]), abs=1e-9)
    assert np.array_equal(uvs[~on_any], plain[~on_any])


def test_without_levels_v_keeps_tile_m_and_u_follows_the_run() -> None:
    prims, frame, _, _ = _building(_rect(), None, 9.6, None)
    assert frame["storey_h"] is None
    points, uvs = _wall_points(prims, frame)
    wall = next(p for p in prims if p.material == WALL)
    mask = _on_run(points, wall.mesh.triangles, frame["runs"][0])  # type: ignore[index]
    assert np.allclose(uvs[mask, 1], points[mask, 2] / TILE[WALL])
    assert uvs[mask, 0].min() == pytest.approx(0.0, abs=1e-9)


def test_glb_keeps_storeys_through_phase_and_other_surfaces_byte_for_byte() -> None:
    prims, frame, _, storeys = _building(_rect(), None, 9.6, 3)
    ground = cap(box(EAST - 30.0, NORTH - 30.0, EAST + 40.0, NORTH + 40.0), 0.0)
    phase = [0.37, 0.61]
    framed = _glb([Node("building/b1", tuple(prims), {"type": "building", "uv_frame": frame, "uv_phase": phase}),
                   Node("surface/g", (Primitive(ground, "ground"),), {"type": "surface"})])
    plain = _glb([Node("building/b1", tuple(prims), {"type": "building", "uv_phase": phase}),
                  Node("surface/g", (Primitive(ground, "ground"),), {"type": "surface"})])
    for material in framed["building/b1"]:
        assert np.array_equal(framed["building/b1"][material][2], plain["building/b1"][material][2])
    assert np.array_equal(framed["surface/g"]["ground"][1], plain["surface/g"]["ground"][1])
    for material in (ROOF, "glass_dark"):
        assert framed["building/b1"][material][1].tobytes() == plain["building/b1"][material][1].tobytes()
    positions, uvs, indices = framed["building/b1"][WALL]
    points = _local(positions)
    mask = _on_run(points, indices, frame["runs"][0])  # type: ignore[index]
    assert _corners_continuous(points, uvs.astype(np.float64), mask, frame["runs"][0]) >= 8  # type: ignore[index]
    k = round(storeys.height_m / TILE[WALL])
    assert np.allclose(uvs[mask, 1], points[mask, 2] * k / storeys.height_m, atol=1e-4)
    assert uvs[mask, 0].min() == pytest.approx(phase[0], abs=1e-5)
    rest = ~mask
    assert np.array_equal(uvs[rest], plain["building/b1"][WALL][1][rest])


def test_glb_with_a_frame_on_a_moved_node_is_ignored() -> None:
    prims, frame, _, _ = _building(_rect(), None, 9.6, 3, openings=False)
    moved = np.eye(4)
    nodes = [Node("building/b1", tuple(prims), {"type": "building", "uv_frame": frame}, moved)]
    framed = _glb(nodes)["building/b1"][WALL][1]
    plain = _glb([Node("building/b1", tuple(prims), {"type": "building"}, moved)])["building/b1"][WALL][1]
    assert framed.tobytes() == plain.tobytes()


def test_frame_skips_inward_faces_off_plane_faces_and_empty_runs() -> None:
    wall = quad((0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 0.0, 3.0), (0.0, 0.0, 3.0))  # faces −y: on the run
    inward = quad((4.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 3.0), (4.0, 0.0, 3.0))
    offset = quad((0.0, -0.5, 0.0), (4.0, -0.5, 0.0), (4.0, -0.5, 3.0), (0.0, -0.5, 3.0))
    beyond = quad((4.5, 0.0, 0.0), (6.0, 0.0, 0.0), (6.0, 0.0, 3.0), (4.5, 0.0, 3.0))
    mesh = merge([wall, inward, offset, beyond])
    frame = {"runs": [[[0.0, 0.0, 0.0, 0.0]], [[0.0, 0.0, 4.0, 0.0]]], "storey_h": 3.0}
    uvs = surface_uvs(mesh, 2.0, frame=frame).astype(np.float64)
    plain = surface_uvs(mesh, 2.0).astype(np.float64)
    assert np.allclose(uvs[:4], [[0, 0], [2, 0], [2, 2], [0, 2]])
    assert np.array_equal(uvs[4:], plain[4:])


def test_recentre_moves_each_run_as_one_island() -> None:
    a = quad((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 1.0), (0.0, 0.0, 1.0))
    mesh = merge([a, a])
    uvs = np.array([[3.2, 0.5], [4.2, 0.5], [4.2, 1.5], [3.2, 1.5], [4.2, 0.5], [5.2, 0.5], [5.2, 1.5], [4.2, 1.5]])
    split = recentre_uvs(uvs, mesh.triangles)
    assert split[4, 0] == pytest.approx(0.2)
    joined = recentre_uvs(uvs, mesh.triangles, np.array([0, 0, 0, 0, 0, 0, 0, 0]))
    assert joined[4, 0] == pytest.approx(1.2)
    assert joined[0, 0] == pytest.approx(0.2)


def test_frame_on_an_empty_mesh_returns_nothing() -> None:
    empty = Mesh.empty()
    assert surface_uvs(empty, 2.0, frame={"runs": [[[0.0, 0.0, 1.0, 0.0]]], "storey_h": None}).shape == (0, 2)
    assert MURCIA_CENTER == (-1.12863, 37.98407)
