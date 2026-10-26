"""Mesh primitives, GLB writer, preview and ground partition (local metres around MURCIA_CENTER)."""

from __future__ import annotations

import copy
import json
import struct
from typing import Any

import numpy as np
import pytest
from shapely import LineString, Polygon, box, union_all

from onward_generator.generation.config import DEFAULT_CONFIG_PATH, GenerationConfigError, config_from_document, load_generation_config
from onward_generator.generation.gltf import Node, Primitive, build_glb, read_glb
from onward_generator.generation.mesh import (
    Mesh,
    cap,
    closed_manifold_defects,
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
from onward_generator.generation.preview import render
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


def test_a_there_and_back_hole_is_dropped_and_the_slab_closes() -> None:
    shell = [(0, 0), (30, 0), (30, 20), (0, 20), (0, 0)]
    # Two copies of the tip differ below the 0.1 mm weld, as a clipped road does.
    tip = (5.0, 5.0)
    spike = [tip, (tip[0] + 2e-15, tip[1]), (12.0, 14.0), tip]
    geom = Polygon(shell, [spike])
    assert closed_manifold_defects(prism(geom, -1.0, 0.0, bottom=True)) > 0
    cleaned, dropped = drop_degenerate_holes(geom)
    assert dropped == 1 and not list(polygons_of(cleaned)[0].interiors)
    slab = prism(cleaned, -1.0, 0.0, bottom=True)
    assert closed_manifold_defects(slab) == 0
    assert signed_volume(slab) == pytest.approx(geom.area)


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


def test_read_glb_rejects_other_files() -> None:
    with pytest.raises(ValueError):
        read_glb(b"\x89PNG....")


def test_preview_is_a_png() -> None:
    png = render([Node("b", (Primitive(prism(L_SHAPE, 0, 6, bottom=False), "building_wall"),))], CFG.materials, width=120, height=90)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", png[16:24]) == (120, 90)


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
