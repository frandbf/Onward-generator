"""Mesh primitives, GLB writer, preview and ground partition (local metres around MURCIA_CENTER)."""

from __future__ import annotations

import copy
import json
import struct
from typing import Any

import numpy as np
import pytest
from shapely import LineString, MultiPolygon, Point, Polygon, box, set_precision, union_all

from onward_generator.generation.config import DEFAULT_CONFIG_PATH, GenerationConfigError, config_from_document, load_generation_config
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb, read_glb
from onward_generator.generation.mesh import (
    Mesh,
    cap,
    closed_manifold_defects,
    collapse_short_edges,
    drop_degenerate_holes,
    merge,
    open_base_defects,
    open_point_contacts,
    polygons_of,
    prism,
    signed_volume,
    upward_area,
    walls,
)
from onward_generator.generation.preview import _splat, render, render_rgb
from onward_generator.generation.surfaces import AreaFeature, LinearWay, SurfaceClass, build_partition

COURTYARD = Polygon([(0, 0), (20, 0), (20, 12), (0, 12)], [[(5, 4), (5, 8), (9, 8), (9, 4)]])
L_SHAPE = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
CFG = load_generation_config()


@pytest.mark.parametrize("polygon", [COURTYARD, L_SHAPE, box(-50, -50, -20, -20)])
def test_cap_covers_polygon_exactly_and_faces_up(polygon: Polygon) -> None:
    mesh = cap(polygon, 3.0)
    assert upward_area(mesh) == pytest.approx(polygon.area)
    assert (mesh.face_normals()[:, 2] > 0).all() and np.allclose(mesh.vertices[:, 2], 3.0)
    down = cap(polygon, 0.0, up=False)
    assert (down.face_normals()[:, 2] < 0).all()


@pytest.mark.parametrize("polygon", [COURTYARD, L_SHAPE])
def test_closed_prism_is_manifold_with_exact_volume(polygon: Polygon) -> None:
    mesh = prism(polygon, 0.0, 12.5, bottom=True)
    assert closed_manifold_defects(mesh) == 0
    assert signed_volume(mesh) == pytest.approx(polygon.area * 12.5)


def test_a_hole_that_kisses_the_boundary_becomes_a_closed_slab() -> None:
    shell = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    hole = [(10, 5), (8, 6), (8, 4), (10, 5)]
    pinched = Polygon(shell, [hole])
    assert closed_manifold_defects(prism(pinched, -1.0, 0.0, bottom=True)) > 0
    opened = open_point_contacts(pinched)
    slab = prism(opened, -1.0, 0.0, bottom=True)
    assert closed_manifold_defects(slab) == 0
    assert signed_volume(slab) == pytest.approx(opened.area)
    assert pinched.area - opened.area < 0.02


def test_polygons_that_touch_at_a_corner_become_closed_slabs() -> None:
    """A carriageway cut by two crossings can leave two pieces that share one corner."""
    kissing = MultiPolygon([box(0, 0, 10, 10), box(10, 10, 20, 20)])
    assert closed_manifold_defects(prism(kissing, -1.0, 0.0, bottom=True)) > 0
    opened = open_point_contacts(kissing)
    slab = prism(opened, -1.0, 0.0, bottom=True)
    assert closed_manifold_defects(slab) == 0
    assert signed_volume(slab) == pytest.approx(opened.area)
    assert 0 < kissing.area - opened.area < 0.001
    apart = MultiPolygon([box(0, 0, 10, 10), box(11, 11, 20, 20)])
    assert open_point_contacts(apart).equals(apart)


@pytest.mark.parametrize("skew", [0.001, 0.003, 0.01])
def test_a_corner_cut_through_a_disk_vertex_stays_closed(skew: float) -> None:
    """Two crossings that share a corner, one edge leaving it a hair off 45°.

    The 1 cm cut meets that edge microns from one of its own vertices; unsnapped,
    the 0.1 mm weld merged the pair and the slab had 16 open edges (Madrid r300).
    """
    kissing = MultiPolygon([Polygon([(0, 0), (6, 0), (5, 5 + skew)]), Polygon([(0, 0), (-5, -5 - skew), (-6, 0)])])
    opened = open_point_contacts(kissing)
    assert closed_manifold_defects(prism(opened, -1.0, -0.15, bottom=True)) == 0
    assert set_precision(opened, 0.001).equals(opened)


def test_pieces_that_leave_their_contact_almost_parallel_are_cut_wider() -> None:
    """Two crossings whose sides leave the shared corner 0.5° apart: a 1 cm cut snaps back into a contact."""
    kissing = MultiPolygon([
        Polygon([(0, 0), (-1.8, -2.25), (3, -1), (2, 2.4)]),
        Polygon([(0, 0), (-7, 5.7), (-4, 0), (-1.83, -2.23)]),
    ])
    once = set_precision(kissing.difference(Point(0, 0).buffer(0.01)), 0.001)
    assert closed_manifold_defects(prism(once, -1.0, -0.15, bottom=True)) > 0
    opened = open_point_contacts(kissing)
    assert closed_manifold_defects(prism(opened, -1.0, -0.15, bottom=True)) == 0
    assert len(polygons_of(opened)) == 2 and not polygons_of(opened)[0].touches(polygons_of(opened)[1])
    assert kissing.area - opened.area == pytest.approx(0.0249, abs=0.001)


def test_a_hole_along_a_few_millimetres_of_the_outline_is_opened_too() -> None:
    """Not a valid polygon, but a cut can leave one: the hole shares 5 mm of the outer ring."""
    shell = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    hole = [(4.995, 0), (5, 0), (5.5, 2), (4.5, 2), (4.995, 0)]
    opened = open_point_contacts(Polygon(shell, [hole]))
    assert len(polygons_of(opened)) == 1 and not polygons_of(opened)[0].interiors
    assert closed_manifold_defects(prism(opened, -1.0, 0.0, bottom=True)) == 0


def test_a_contact_that_no_cut_can_open_leaves_the_slab_unchanged() -> None:
    """Two 1 mm squares that share a corner: any disk wide enough swallows both."""
    tiny = MultiPolygon([box(0, 0, 0.001, 0.001), box(0.001, 0.001, 0.002, 0.002)])
    assert open_point_contacts(tiny) is tiny


def test_a_there_and_back_hole_is_dropped_and_the_slab_closes() -> None:
    shell = [(0, 0), (30, 0), (30, 20), (0, 20), (0, 0)]
    # Two copies of the tip differ below the 0.1 mm weld, as a clipped road does.
    tip = (5.0, 5.0)
    spike = [tip, (tip[0] + 2e-15, tip[1]), (12.0, 14.0), tip]
    geom = Polygon(shell, [spike])
    # Extrusion drops the duplicate tip, the hole no longer has three corners, and the slab closes.
    assert closed_manifold_defects(prism(geom, -1.0, 0.0, bottom=True)) == 0
    cleaned, dropped = drop_degenerate_holes(geom)
    assert dropped == 1 and not list(polygons_of(cleaned)[0].interiors)
    slab = prism(cleaned, -1.0, 0.0, bottom=True)
    assert closed_manifold_defects(slab) == 0
    assert signed_volume(slab) == pytest.approx(geom.area)


def test_a_vertex_in_the_same_weld_cell_is_removed_and_the_slab_closes() -> None:
    """MadridTextura32: the site corner kept two vertices 7.5 µm apart.

    The 0.1 mm weld merged them, the walkable slab reported 8 defects, and the
    volume still matched. The duplicate is removed before the walls are built.
    """
    corner = (-125.0, -125.0)
    jog = (corner[0], corner[1] + 7.5e-6)
    polygon = Polygon([corner, jog, (-110.0, -125.0), (-110.0, -110.0), (-125.0, -110.0)])
    collapsed, removed = collapse_short_edges(polygon)
    assert removed == 1 and collapsed is not polygon
    assert len(list(collapsed.exterior.coords)) == 5
    slab = prism(polygon, -1.0, 0.0, bottom=True)
    assert closed_manifold_defects(slab) == 0
    assert signed_volume(slab) == pytest.approx(collapsed.area)


def test_an_edge_longer_than_the_weld_is_kept() -> None:
    polygon = Polygon([(0, 0), (0.001, 0), (0.001, 1), (0, 1)])
    same, removed = collapse_short_edges(polygon)
    assert removed == 0 and same is polygon


def test_a_courtyard_hole_is_kept() -> None:
    cleaned, dropped = drop_degenerate_holes(COURTYARD)
    assert dropped == 0 and cleaned.equals(COURTYARD)


def test_open_prism_is_only_open_at_the_base() -> None:
    mesh = prism(COURTYARD, 0.0, 9.0, bottom=False)
    assert open_base_defects(mesh, 0.0) == 0
    assert closed_manifold_defects(mesh) > 0
    assert open_base_defects(mesh, 1.0) > 0


def test_defect_detection_for_a_missing_face() -> None:
    mesh = prism(box(0, 0, 4, 4), 0.0, 3.0, bottom=True)
    broken = Mesh(mesh.vertices, mesh.triangles[1:])
    assert closed_manifold_defects(broken) > 0


def test_walls_face_outward_or_inward() -> None:
    out = walls(box(0, 0, 4, 4), 0.0, 1.0)
    centres = out.vertices[out.triangles].mean(axis=1)
    outward = np.einsum("ij,ij->i", out.face_normals()[:, :2], centres[:, :2] - 2.0)
    assert (outward > 0).all()
    inward = walls(box(0, 0, 4, 4), -0.15, 0.0, inward=True)
    centres = inward.vertices[inward.triangles].mean(axis=1)
    assert (np.einsum("ij,ij->i", inward.face_normals()[:, :2], centres[:, :2] - 2.0) < 0).all()


def test_drop_degenerate_and_merge() -> None:
    m = Mesh(np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0], [0, 1, 0]], dtype=float), np.array([[0, 1, 2], [0, 1, 3]]))
    kept, dropped = m.drop_degenerate()
    assert dropped == 1 and kept.triangle_count == 1
    assert merge([kept, kept]).triangle_count == 2 and merge([]).triangle_count == 0


# --- GLB. ---


def test_glb_roundtrip_axes_and_alignment() -> None:
    mesh = Mesh(np.array([[1.0, 2.0, 3.0], [2.0, 2.0, 3.0], [1.0, 3.0, 3.0]]), np.array([[0, 1, 2]]))
    data = build_glb([Node("a", (Primitive(mesh, "ground"),), {"object_id": "a"})], CFG.materials, extras={"attribution": "x"})
    assert data[:4] == b"glTF" and struct.unpack_from("<I", data, 8)[0] == len(data) and len(data) % 4 == 0
    doc, blob = read_glb(data)
    assert doc["asset"]["version"] == "2.0" and doc["nodes"][0]["extras"] == {"object_id": "a"}
    pos = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
    assert pos["min"] == [1.0, 3.0, -3.0] and pos["max"] == [2.0, 3.0, -2.0]  # (x, y, z) -> (x, z, -y)
    view = doc["bufferViews"][pos["bufferView"]]
    assert view["byteOffset"] % 4 == 0 and doc["buffers"][0]["byteLength"] == len(blob)
    normal = np.frombuffer(blob, np.float32, 3, doc["bufferViews"][doc["accessors"][1]["bufferView"]]["byteOffset"])
    assert np.allclose(normal, [0, 1, 0])  # local +z (up) is glTF +Y
    assert build_glb([Node("a", (Primitive(mesh, "ground"),))], CFG.materials) == build_glb([Node("a", (Primitive(mesh, "ground"),))], CFG.materials)


def test_read_back_keeps_instancing_and_names_unnamed_nodes() -> None:
    mesh = Mesh(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), np.array([[0, 1, 2]]))
    prims = (Primitive(mesh, "ground"),)
    data = build_glb(
        [Node("lamp/1", prims, mesh_key="lamp"), Node("lamp/2", prims, mesh_key="lamp"), Node("", prims)], CFG.materials,
    )
    nodes, _ = nodes_from_glb(data)
    assert [(n.name, n.mesh_key) for n in nodes] == [("lamp/1", "lamp"), ("lamp/2", "lamp"), ("node", None)]
    # Written again, the instanced pair still shares one glTF mesh.
    doc, _ = read_glb(build_glb(nodes, CFG.materials))
    assert len(doc["meshes"]) == 2 and doc["nodes"][0]["mesh"] == doc["nodes"][1]["mesh"]


def test_read_glb_rejects_other_files() -> None:
    with pytest.raises(ValueError):
        read_glb(b"\x89PNG....")


def test_preview_is_a_png() -> None:
    png = render([Node("b", (Primitive(prism(L_SHAPE, 0, 6, bottom=False), "building_wall"),))], CFG.materials, width=120, height=90)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (120, 90)


def _splat_one_by_one(index, px, py, depth, c, zbuf, canvas) -> None:
    """The loop ``_splat`` replaced: one centroid sample per triangle, strict depth test."""
    height, width = zbuf.shape
    for i in index:
        x, y = int(px[i].mean()), int(py[i].mean())
        if 0 <= x < width and 0 <= y < height:
            z = depth[i].mean()
            if z < zbuf[y, x]:
                zbuf[y, x] = z
                canvas[y, x] = c[i]


def _splat_both(px: np.ndarray, py: np.ndarray, depth: np.ndarray, index: np.ndarray) -> tuple[np.ndarray, ...]:
    width, height = 6, 4
    c = np.linspace(0.0, 1.0, len(px) * 3).reshape(len(px), 3)
    out = []
    for draw in (_splat, _splat_one_by_one):
        zbuf = np.full((height, width), np.inf)
        canvas = np.zeros((height, width, 3))
        draw(index, px, py, depth, c, zbuf, canvas)
        out.extend([zbuf, canvas])
    return tuple(out)


def test_splat_matches_drawing_each_triangle_in_turn() -> None:
    # Centroids at every edge of a 6x4 canvas, outside it, and just left of 0 (truncates to 0).
    cx = np.array([0.2, 5.9, 6.0, -0.5, -1.5, 2.5, 2.5, 2.2, 2.9, 1.0, 1.0])
    cy = np.array([0.0, 3.9, 1.0, 1.0, 1.0, 4.0, -0.4, 2.0, 2.0, 2.0, 2.0])
    z = np.array([5.0, 4.0, 1.0, 3.0, 1.0, 1.0, 2.0, 7.0, 6.0, 9.0, 9.0])
    spread = np.array([-0.1, 0.0, 0.1])
    px, py = cx[:, None] + spread, cy[:, None] - spread
    depth = z[:, None] + spread
    zbuf, canvas, want_z, want_canvas = _splat_both(px, py, depth, np.arange(len(cx)))
    assert np.array_equal(zbuf, want_z) and np.array_equal(canvas, want_canvas)
    # (2.2, 2.0) and (2.9, 2.0) share a pixel: the nearer one wins although it comes later.
    assert zbuf[2, 2] == 6.0
    # Equal depths on one pixel: the first triangle in submission order keeps its colour.
    assert np.array_equal(canvas[2, 1], np.linspace(0.0, 1.0, 33).reshape(11, 3)[9])
    assert zbuf[1, 0] == 3.0 and np.isinf(zbuf[1, 5])


def test_splat_matches_drawing_each_triangle_in_turn_on_a_random_crowd() -> None:
    rng = np.random.default_rng(48152)
    count = 400
    px = rng.uniform(-2.0, 8.0, (count, 3))
    py = rng.uniform(-2.0, 6.0, (count, 3))
    depth = rng.integers(0, 4, (count, 3)).astype(float)
    index = np.sort(rng.choice(count, 300, replace=False))
    zbuf, canvas, want_z, want_canvas = _splat_both(px, py, depth, index)
    assert np.array_equal(zbuf, want_z) and np.array_equal(canvas, want_canvas)


def test_sub_pixel_triangles_are_splatted_only_when_asked() -> None:
    # A field of 0.1 m triangles seen from 400 m: each one is far below a pixel.
    tiny = Mesh(
        np.array([corner for x in range(0, 400, 20) for y in range(0, 400, 20) for corner in ((x, y, 0.0), (x + 0.1, y, 0.0), (x, y + 0.1, 0.0))]),
        np.arange(1200).reshape(400, 3),
    )
    nodes = [Node("t", (Primitive(tiny, "building_wall"),))]
    background = np.array([0.93, 0.95, 0.97])
    drawn = render_rgb(nodes, CFG.materials, width=80, height=60, margin_px=2, splat_subpixel=True)
    skipped = render_rgb(nodes, CFG.materials, width=80, height=60, margin_px=2, splat_subpixel=False)
    assert (np.abs(drawn - background).sum(axis=2) > 1e-9).sum() > 100
    assert (np.abs(skipped - background).sum(axis=2) > 1e-9).sum() < (np.abs(drawn - background).sum(axis=2) > 1e-9).sum()


# --- Ground partition. ---


SITE = box(-100, -100, 100, 100)


def partition(**kw: Any):
    buildings = kw.get("buildings", [box(-50, -50, -20, -20), box(20, 20, 60, 50)])
    ways = kw.get("ways", [
        LinearWay("osm-way-1", "vehicular", LineString([(-100, 0), (100, 0)]), 8.0, "observed"),
        LinearWay("osm-way-2", "pedestrian", LineString([(0, -100), (0, -10)]), 4.0, "inferred"),
    ])
    areas = kw.get("areas", [AreaFeature("osm-way-3", SurfaceClass.GREEN, box(-90, 60, -60, 90)),
                             AreaFeature("osm-way-4", SurfaceClass.WATER, box(70, -90, 90, -70))])
    return build_partition(SITE, buildings, ways, areas, CFG)


def test_partition_is_disjoint_and_covers_site() -> None:
    p = partition()
    assert p.overlap_area() < 1e-3 and p.gap_area() < 1e-3
    assert set(p.surfaces) == set(SurfaceClass)
    assert p.surfaces[SurfaceClass.CARRIAGEWAY].geometry.area == pytest.approx(200 * 8, rel=1e-3)
    assert p.surfaces[SurfaceClass.SIDEWALK].geometry.area == pytest.approx(2 * 200 * 2, rel=1e-3)  # footway stops at y = -8
    assert p.surfaces[SurfaceClass.WATER].geometry.area == pytest.approx(400)
    assert p.surfaces[SurfaceClass.CARRIAGEWAY].sources == ("osm-way-1",)


def test_pedestrian_cobble_is_cut_from_remainder() -> None:
    from onward_generator.generation.surfaces import COBBLE_PAVING, split_pedestrian_by_paving

    assert "sett" in COBBLE_PAVING
    ways = [
        LinearWay("osm-way-1", "vehicular", LineString([(-100, 0), (100, 0)]), 8.0, "observed"),
        LinearWay("osm-way-plain", "pedestrian", LineString([(0, -80), (0, -20)]), 4.0, "inferred", None),
        LinearWay("osm-way-sett", "pedestrian", LineString([(40, -80), (40, -20)]), 4.0, "observed", "sett"),
    ]
    p = partition(ways=ways, areas=[])
    ped = p.surfaces[SurfaceClass.PEDESTRIAN]
    rest, cobble = split_pedestrian_by_paving(ped, ways, (), CFG)
    assert cobble is not None
    assert cobble.sources == ("osm-way-sett",)
    assert rest.geometry.intersection(cobble.geometry).area < 1e-4
    assert cobble.geometry.area > 1.0


def test_buildings_cut_roads_and_roads_cut_pedestrian() -> None:
    p = partition(buildings=[box(-10, -5, 10, 5)])
    road = p.surfaces[SurfaceClass.CARRIAGEWAY].geometry
    assert road.intersection(box(-10, -5, 10, 5)).area < 1e-6
    ped = p.surfaces[SurfaceClass.PEDESTRIAN].geometry
    assert ped.intersection(road).area < 1e-6


def test_slivers_are_dropped() -> None:
    p = partition(areas=[AreaFeature("osm-way-9", SurfaceClass.GREEN, box(0, 50, 0.1, 50.1))])
    assert SurfaceClass.GREEN not in p.surfaces


def test_union_matches_site() -> None:
    p = partition()
    covered = union_all([s.geometry for s in p.surfaces.values()] + [p.buildings])
    assert covered.symmetric_difference(SITE).area < 1e-3


# --- Config. ---


def doc() -> dict[str, Any]:
    return copy.deepcopy(json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda d: d["road_classes"]["pedestrian"].append("residential"), "both vehicular and pedestrian"),
        (lambda d: d.update(carriageway_depth_m=-0.1), "/carriageway_depth_m"),
        (lambda d: d["materials"].pop("curb"), "curb"),
        (lambda d: d["materials"]["ground"].update(color=[1, 1, 1]), "/materials/ground/color"),
        (lambda d: d.update(extra=1), "Additional properties"),
    ],
)
def test_invalid_generation_config(mutate, fragment: str) -> None:
    d = doc()
    mutate(d)
    with pytest.raises(GenerationConfigError) as exc:
        config_from_document(d)
    assert any(fragment in p for p in exc.value.problems), exc.value.problems
