"""Mapping facade parts onto the trim-sheet atlas (ADR-020): bands, strips, cells, merging."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from onward_generator.detail.facades import face_with_openings
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh, cap, merge, quad, transform
from onward_generator.materials import trim as trim_module
from onward_generator.materials.tile_cache import TileCache
from onward_generator.materials.trim import (
    PLAIN_V,
    TRIM_BAKE_VERSION,
    Bands,
    Look,
    TrimStats,
    apply_trim,
    band_mesh,
    building_bands,
    building_look,
    era_band,
    look_key,
    observed_period,
    opening_cell,
    opening_uvs,
    scenic_trim_material,
    stable_index,
    stable_unit,
    stone_uvs,
    trim_building,
    trim_cache_key,
    trim_material,
    trim_nodes,
    wall_uvs,
    window_families,
)
from onward_generator.materials.trim_style import (
    REGIONAL_TABLE_PATH,
    STYLE_TABLE_PATH,
    StyleError,
    load_regional_table,
    load_style_table,
    trim_layout,
)
from tests.conftest import MURCIA_CENTER

SHIPPED = load_style_table().styles["mediterranean"].atlas or {}
# One tone and every dress on, so each building takes the strips below whatever its hash; the tones and
# the dress shares of the shipped style are tested on their own.
ATLAS = {**SHIPPED, "tones": [[1.0, 1.0, 1.0]], "dress": {"zocalo_share": 1.0, "cornice_share": 1.0, "course_share": 1.0}}
LAYOUT = trim_layout(ATLAS)
WINDOWS = window_families(ATLAS)
UP = np.array([0.0, 0.0, 1.0])


def facade(length: float, height: float, holes: list[tuple[float, float, float, float]], x0: float = 0.0) -> Mesh:
    """A south-facing wall from (x0, 0) to (x0 + length, 0) with openings ``(s0, v0, s1, v1)``, as detail builds it."""
    face = face_with_openings(length, 0.0, height, holes)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = (1.0, 0.0, 0.0), UP, (0.0, -1.0, 0.0), (x0, 0.0, 0.0)
    return transform(cap(face, 0.0), m)


def area(mesh: Mesh) -> float:
    v = mesh.vertices[mesh.triangles]
    return float(np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1).sum() / 2)


WINDOWS_2X3 = [(1.0 + 3.0 * c, 1.0 + 3.0 * r, 2.2 + 3.0 * c, 2.5 + 3.0 * r) for c in range(3) for r in range(2)]
BANDS = Bands(0.9, 3.0, 3, 9.0)


def test_bands_cut_a_face_with_openings_at_every_level() -> None:
    wall = facade(9.0, 10.0, WINDOWS_2X3)
    out, band, plain = band_mesh(wall, BANDS.levels, BANDS.plain_levels)
    assert BANDS.levels == [0.9, 3.0, 6.0, 9.0]
    assert area(out) == pytest.approx(area(wall))
    assert not plain.any()
    normals = out.face_normals()
    assert (normals[:, 1] < 0).all()  # still facing south
    z = out.vertices[:, 2]
    lower = np.array([0.0, 0.9, 3.0, 6.0, 9.0])[band]
    upper = np.array([0.9, 3.0, 6.0, 9.0, 10.0])[band]
    assert ((z >= lower - 1e-9) & (z <= upper + 1e-9)).all()
    assert sorted(set(band.tolist())) == [0, 1, 2, 3, 4]
    # Each band is triangulated on its own: a vertex on a level is written once per band.
    assert int((np.isclose(z, 3.0)).sum()) >= 4
    for tri in out.triangles:
        assert len(set(band[tri].tolist())) == 1
    # Five bands of a rectangle, three with two holes of four corners each: 5 x 2 + 6 x 6 triangles.
    assert out.triangle_count == 5 * 2 + 6 * 6


def test_a_plain_face_takes_only_the_zocalo_and_parapet_lines() -> None:
    wall = facade(8.0, 10.0, [])
    out, band, plain = band_mesh(wall, BANDS.levels, BANDS.plain_levels)
    assert plain.all()
    assert out.triangle_count == 6
    assert sorted(set(band.tolist())) == [0, 1, 2]
    assert area(out) == pytest.approx(80.0)
    assert out.vertex_count == 12
    assert band.dtype == np.int64 and plain.dtype == bool and out.triangles.dtype == np.int64
    assert sorted({round(float(z), 6) for z in out.vertices[:, 2]}) == [0.0, 0.9, 9.0, 10.0]
    assert np.allclose(out.vertices[:, 1], 0.0)
    assert (out.face_normals()[:, 1] < 0).all()


def test_coplanar_faces_join_and_a_sloped_triangle_stays_whole() -> None:
    left, right = facade(4.0, 6.0, []), facade(4.0, 6.0, [], x0=4.0)
    sloped = Mesh(np.array([[0.0, 0.0, 1.0], [2.0, 0.0, 1.0], [1.0, 1.0, 5.0]]), np.array([[0, 1, 2]]))
    flat = Mesh(np.array([[0.0, 0.0, 2.0], [1.0, 0.0, 2.0], [2.0, 0.0, 2.0]]), np.array([[0, 1, 2]]))
    out, band, plain = band_mesh(merge([sloped, flat, left, right]), [0.9, 3.0, 6.0], [0.9, 6.0])
    # The two quads join into one plain 8 x 6 m rectangle cut at 0.9 m: four triangles, plus the sloped one.
    assert out.triangle_count == 5
    assert area(out) == pytest.approx(48.0 + area(sloped))
    tilted = [t for t in out.triangles if abs(out.face_normals()[list(out.triangles.tolist()).index(t.tolist())][2]) > 1e-6]
    assert len(tilted) == 1
    assert set(band[tilted[0]].tolist()) == {1}  # centre at z = 7/3, between 0.9 and 3.0 of the full list
    assert not plain[tilted[0]].any()


def test_an_empty_mesh_stays_empty() -> None:
    out, band, plain = band_mesh(Mesh.empty(), [1.0], [1.0])
    assert out.triangle_count == 0
    assert band.shape == (0,) and plain.shape == (0,)
    assert band.dtype == np.int64 and plain.dtype == bool
    mesh, uvs = wall_uvs(Mesh.empty(), BANDS, LAYOUT.walls["plaster_white"], LAYOUT.zocalo, LAYOUT.cornice, None, 0.0)
    assert mesh.triangle_count == 0 and uvs.shape == (0, 2) and uvs.dtype == np.float32


def test_building_bands_from_the_record() -> None:
    record = {"facade": {"storeys": {"count": 4, "height_m": 3.2}}, "roof": {"type": "flat_parapet", "parapet_m": 1.0}}
    bands = building_bands(record, 13.8, 0.9, 4.5)
    assert bands == Bands(0.9, 3.2, 4, 12.8)
    assert bands.levels == pytest.approx([0.9, 3.2, 6.4, 9.6, 12.8])
    assert bands.plain_levels == [0.9, 12.8]
    # Without storeys the wall top is the mesh top less the parapet, in fallback storeys.
    assert building_bands({"roof": {"type": "flat_parapet", "parapet_m": 1.0}}, 13.0, 0.9, 4.5) == Bands(0.9, 3.0, 4, 12.0)
    assert building_bands(None, 7.0, 0.9, 4.5) == Bands(0.9, 3.5, 2, 7.0)
    assert building_bands({"roof": {"type": "hip", "parapet_m": 1.0}}, 7.0, 0.9, 4.5).wall_top_m == 7.0
    # A landmark's single 20 m band becomes bands near 3 m.
    tower = building_bands({"facade": {"storeys": {"count": 1, "height_m": 20.0}}}, 20.0, 0.9, 4.5)
    assert tower == Bands(0.9, 20.0 / 7, 7, 20.0)
    assert building_bands({"facade": {"storeys": {"count": 1, "height_m": 4.5}}}, 4.5, 0.9, 4.5) == Bands(0.9, 4.5, 1, 4.5)
    # A low storey keeps half of it for the zocalo at most.
    assert building_bands({"facade": {"storeys": {"count": 1, "height_m": 1.2}}}, 1.2, 0.9, 4.5).zocalo_m == 0.6
    assert building_bands({"facade": {"storeys": {"count": 1, "height_m": 2.0}}}, 1.0, 0.9, 4.5).zocalo_m == 0.9
    assert building_bands(None, 1.0, 0.9, 4.5) == Bands(0.5, 1.0, 1, 1.0)


def test_wall_uvs_put_each_band_in_its_strip() -> None:
    wall_strip = LAYOUT.walls["plaster_ochre"]
    mesh, uvs = wall_uvs(facade(9.0, 10.0, WINDOWS_2X3), BANDS, wall_strip, LAYOUT.zocalo, LAYOUT.cornice, None, 0.0)
    z = mesh.vertices[:, 2]
    v = uvs[:, 1]
    _, band, _ = band_mesh(facade(9.0, 10.0, WINDOWS_2X3), BANDS.levels, BANDS.plain_levels)
    zocalo, cornice = band == 0, band == 4
    assert np.allclose(v[zocalo & (z < 1e-9)], LAYOUT.zocalo.v0)
    assert np.allclose(v[zocalo & np.isclose(z, 0.9)], LAYOUT.zocalo.v1)
    assert np.allclose(v[cornice & np.isclose(z, 9.0)], LAYOUT.cornice.v0)
    assert np.allclose(v[cornice & np.isclose(z, 10.0)], LAYOUT.cornice.v1)
    storey = band == 2
    assert np.allclose(v[storey & np.isclose(z, 3.0)], wall_strip.v0)
    assert np.allclose(v[storey & np.isclose(z, 6.0)], wall_strip.v1)
    middle = storey & np.isclose(z, 4.0)
    assert np.allclose(v[middle], wall_strip.v0 + (wall_strip.v1 - wall_strip.v0) / 3)
    # The ground storey above the zocalo spans 0.9–3.0 m.
    ground = band == 1
    assert np.allclose(v[ground & np.isclose(z, 0.9)], wall_strip.v0)
    # U: face plane in wall repeats on the wall, in zocalo and cornice repeats there (twice as many).
    x = mesh.vertices[:, 0]
    assert np.allclose(np.diff(uvs[storey][np.argsort(x[storey])][:, 0]) >= 0, True)
    span_wall = np.ptp(uvs[storey, 0])
    span_zocalo = np.ptp(uvs[zocalo, 0])
    assert span_wall == pytest.approx(9.0 / 12.0)
    assert span_zocalo == pytest.approx(9.0 / 6.0)
    assert uvs.dtype == np.float32


def test_a_plain_face_samples_the_middle_of_its_strip() -> None:
    strip = LAYOUT.walls["plaster_white"]
    mesh, uvs = wall_uvs(facade(8.0, 10.0, []), BANDS, strip, LAYOUT.zocalo, LAYOUT.cornice, None, 0.0)
    z, v = mesh.vertices[:, 2], uvs[:, 1]
    body = (z > 0.9 - 1e-9) & (z < 9.0 + 1e-9) & (v > LAYOUT.zocalo.v1 + 1e-6)
    body &= v < LAYOUT.cornice.v0 - 1e-6
    low, high = (strip.v0 + p * (strip.v1 - strip.v0) for p in PLAIN_V)
    assert np.allclose(v[body & np.isclose(z, 0.9)], low)
    assert np.allclose(v[body & np.isclose(z, 9.0)], high)


def test_a_run_frame_keeps_u_continuous_round_a_corner_and_takes_the_phase() -> None:
    south = facade(6.0, 6.0, [(2.0, 1.0, 3.0, 2.0)])
    rot = np.eye(4)
    rot[:3, :3] = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    rot[:3, 3] = (6.0, 0.0, 0.0)
    east = transform(facade(6.0, 6.0, [(2.0, 1.0, 3.0, 2.0)]), rot)  # from (6, 0) to (6, 6), facing east
    frame = {"runs": [[[0.0, 0.0, 6.0, 0.0], [6.0, 0.0, 6.0, 6.0]]], "storey_h": 3.0}
    bands = Bands(0.9, 3.0, 2, 6.0)
    strip = LAYOUT.walls["plaster_cream"]
    mesh, uvs = wall_uvs(merge([south, east]), bands, strip, LAYOUT.zocalo, LAYOUT.cornice, frame, 0.75)
    upper = mesh.vertices[:, 2] > 0.9 - 1e-9
    upper &= (uvs[:, 1] >= strip.v0 - 1e-6) & (uvs[:, 1] <= strip.v1 + 1e-6)
    corner = np.isclose(mesh.vertices[:, 0], 6.0) & np.isclose(mesh.vertices[:, 1], 0.0) & upper
    assert corner.sum() >= 4
    assert np.ptp(uvs[corner, 0]) == pytest.approx(0.0, abs=1e-5)
    # 12 m of run, one 12 m repeat: U runs from the phase to the phase plus one.
    assert uvs[upper, 0].min() == pytest.approx(0.75, abs=1e-5)
    assert uvs[upper, 0].max() == pytest.approx(1.75, abs=1e-5)


def test_stone_pieces_fill_the_stone_strip() -> None:
    reveal = quad((0, 0, 0), (0.15, 0, 0), (0.15, 0, 1.5), (0, 0, 1.5))
    sill = quad((0, 0, 3.0), (1.2, 0, 3.0), (1.2, 0, 3.1), (0, 0, 3.1))
    uvs = stone_uvs(merge([reveal, sill]), LAYOUT.stone)
    assert uvs.dtype == np.float32
    for piece in (uvs[:4], uvs[4:]):
        assert piece[:, 1].min() == pytest.approx(LAYOUT.stone.v0)
        assert piece[:, 1].max() == pytest.approx(LAYOUT.stone.v1)
    assert np.ptp(uvs[4:, 0]) == pytest.approx(1.2)
    assert uvs[:, 0].min() >= 0.0
    flat = quad((0, 0, 1.0), (1.0, 0, 1.0), (1.0, 0, 1.0), (0, 0, 1.0))
    assert np.allclose(stone_uvs(flat, LAYOUT.stone)[:, 1], LAYOUT.stone.v0)


def test_stable_index_is_a_hash() -> None:
    assert stable_index(7, "a", 1) == stable_index(7, "a", 1)
    assert {stable_index(4, "b", k) for k in range(40)} == {0, 1, 2, 3}


def _back(width: float, height: float, z0: float, x0: float = 0.0) -> Mesh:
    return quad((x0, 0, z0), (x0 + width, 0, z0), (x0 + width, 0, z0 + height), (x0, 0, z0 + height))


def test_opening_cells() -> None:
    def cell(material: str, mesh: Mesh, building: str = "b1") -> str:
        return opening_cell(material, mesh, np.arange(mesh.vertex_count), building, WINDOWS)

    assert cell("glass_boarded", _back(1.2, 1.5, 4.0)) == "boarded"
    assert cell("door_wood", _back(1.2, 2.4, 0.0)) in ("door_0", "door_1")
    assert {cell("door_wood", _back(1.2, 2.4, 0.0), f"b{k}") for k in range(20)} == {"door_0", "door_1"}
    assert cell("glass_dark", _back(1.9, 1.5, 0.25)) == "shop_glass"
    assert cell("glass_dark", _back(1.89, 1.5, 3.25)) != "shop_glass"
    assert cell("glass_dark", _back(1.6, 2.5, 0.0)) == "shop_glass"
    assert cell("glass_dark", _back(1.2, 1.5, 0.05)) != "shop_glass"
    # One family per building; the shutter position varies window by window.
    family = cell("glass_dark", _back(1.2, 1.5, 4.0))[:-2]
    seen = {cell("glass_dark", _back(1.2, 1.5, 4.0, x0=3.0 * k)) for k in range(12)}
    assert {name[:-2] for name in seen} == {family}
    assert len(seen) == 2
    assert {cell("glass_dark", _back(1.2, 1.5, 4.0), f"b{k}")[:-2] for k in range(20)} == {"window_0", "window_1"}


def test_opening_uvs_fit_each_back_into_its_cell() -> None:
    counts: dict[str, int] = {}
    mesh = merge([_back(1.2, 1.5, 4.0), _back(2.4, 2.3, 0.25, x0=5.0)])
    uvs = opening_uvs("glass_dark", mesh, LAYOUT.cells, "b1", WINDOWS, counts)
    shop = LAYOUT.cells["shop_glass"]
    assert uvs[4:, 0].min() == pytest.approx(shop.u0) and uvs[4:, 0].max() == pytest.approx(shop.u1)
    assert uvs[4:, 1].min() == pytest.approx(shop.v0) and uvs[4:, 1].max() == pytest.approx(shop.v1)
    window = next(name for name in counts if name.startswith("window"))
    cell = LAYOUT.cells[window]
    assert uvs[:4, 0].min() == pytest.approx(cell.u0) and uvs[:4, 1].max() == pytest.approx(cell.v1)
    assert counts == {window: 1, "shop_glass": 1}
    opening_uvs("glass_dark", mesh, LAYOUT.cells, "b1", WINDOWS, counts)
    assert counts == {window: 2, "shop_glass": 2}


def _building(wall: str = "plaster_ochre", extra: tuple[Primitive, ...] = ()) -> Node:
    prims = (
        Primitive(facade(9.0, 10.0, WINDOWS_2X3), wall),
        Primitive(quad((0, 0, 0), (0.15, 0, 0), (0.15, 0, 1.5), (0, 0, 1.5)), "frame_stone"),
        Primitive(_back(1.2, 1.5, 1.0), "glass_dark"),
        Primitive(_back(1.2, 2.4, 0.0, x0=4.0), "door_wood"),
        Primitive(_back(1.2, 1.5, 4.0), "glass_boarded"),
        Primitive(_back(1.2, 1.5, 4.0), "glass_view"),
        Primitive(_back(1.2, 0.9, 4.0), "railing_metal"),
        Primitive(Mesh.empty(), "plaster_white"),
        *extra,
    )
    return Node("building/b1", prims, {"type": "building", "object_id": "building/b1", "uv_phase": [0.5, 0.5]})


RECORD = {"facade": {"storeys": {"count": 3, "height_m": 3.0}}, "roof": {"type": "flat_parapet", "parapet_m": 1.0}}


def test_trim_building_merges_every_covered_part_into_one_primitive() -> None:
    stats = TrimStats()
    node = trim_building(_building(), RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
    names = [p.material for p in node.primitives]
    assert names == ["facade_trim_mediterranean", "glass_view", "railing_metal", "plaster_white"]
    trim = node.primitives[0]
    assert trim.uvs is not None and len(trim.uvs) == trim.mesh.vertex_count
    assert trim.uvs.dtype == np.float32
    assert (trim.uvs[:, 1] >= 0).all() and (trim.uvs[:, 1] <= 1).all()
    assert node.extras == _building().extras and node.name == "building/b1"
    assert stats.buildings == 1
    assert stats.primitives_merged == 5
    assert stats.wall_triangles_before == 38
    assert stats.wall_triangles_after == 46
    assert stats.cells["boarded"] == 1 and sum(stats.cells.values()) == 3
    assert stats.kept_materials == {}
    # The wall phase moves U only.
    plain = trim_building(_building(), RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert np.array_equal(plain.primitives[0].uvs, trim.uvs)


def test_a_wall_colour_without_a_strip_keeps_its_material() -> None:
    stats = TrimStats()
    node = trim_building(_building("stone_light"), RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
    assert [p.material for p in node.primitives][:2] == ["facade_trim_mediterranean", "stone_light"]
    assert stats.kept_materials == {"stone_light": 1}
    assert stats.wall_triangles_before == 0
    other = trim_building(_building("building_wall"), RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert "building_wall" in [p.material for p in other.primitives]
    kept = TrimStats()
    trim_building(_building("glass_view"), RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, kept)
    assert kept.kept_materials == {}
    # An observed OSM colour (ADR-023) is a tint of its own: it keeps that material, its openings join the atlas.
    tinted = TrimStats()
    node = trim_building(_building("plaster_white__tint_ab12"), RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, tinted)
    assert [p.material for p in node.primitives][:2] == ["facade_trim_mediterranean", "plaster_white__tint_ab12"]
    assert tinted.kept_materials == {"plaster_white__tint_ab12": 1}


def test_party_walls_keep_their_lightmap_class_on_the_atlas() -> None:
    party = Primitive(facade(8.0, 10.0, []), "party_plaster_ochre")
    stats = TrimStats()
    node = trim_building(_building(extra=(party, Primitive(facade(2.0, 3.0, []), "party_stone_light"))), RECORD,
                         "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
    names = [p.material for p in node.primitives]
    assert names[:2] == ["facade_trim_mediterranean", "party_facade_trim_mediterranean"]
    assert "party_stone_light" in names and stats.kept_materials == {"party_stone_light": 1}
    v = node.primitives[1].uvs[:, 1]
    ochre = LAYOUT.walls["plaster_ochre"]
    inside = [(s.v0, s.v1) for s in (ochre, LAYOUT.zocalo, LAYOUT.cornice)]
    assert all(any(lo - 1e-6 <= x <= hi + 1e-6 for lo, hi in inside) for x in v)
    assert node.primitives[1].mesh.triangle_count == 6


def test_scenic_walls_take_their_own_trim_material() -> None:
    shell = Node("building/s1", (Primitive(facade(8.0, 6.0, []), "scenic_wall_warm"), Primitive(Mesh.empty(), "roof_flat_grey")),
                 {"type": "building"})
    node = trim_building(shell, None, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert [p.material for p in node.primitives] == ["scenic_trim_mediterranean", "roof_flat_grey"]
    v = node.primitives[0].uvs[:, 1]
    cream = LAYOUT.walls["plaster_cream"]
    assert ((v >= LAYOUT.zocalo.v0 - 1e-6) & (v <= LAYOUT.zocalo.v1 + 1e-6) | (v >= cream.v0) & (v <= cream.v1)).all()
    assert scenic_trim_material("x") == "scenic_trim_x" and trim_material("x") == "facade_trim_x"


def test_a_building_with_nothing_to_merge_is_returned_as_it_is() -> None:
    node = Node("building/r", (Primitive(_back(2.0, 2.0, 0.0), "roof_flat_grey"),), {"type": "building"})
    stats = TrimStats()
    assert trim_building(node, None, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats) is node
    assert stats.buildings == 0


def test_trim_nodes_touches_buildings_only() -> None:
    street = Node("surface/s", (Primitive(_back(2.0, 2.0, 0.0), "plaster_ochre"),), {"type": "surface"})
    placed = Node("building/m", (Primitive(_back(2.0, 2.0, 0.0), "plaster_ochre"),), {"type": "building"}, np.eye(4))
    bare = Node("building/x", (Primitive(facade(9.0, 10.0, WINDOWS_2X3), "plaster_ochre"),), {"type": "building"})
    out, stats = trim_nodes([street, placed, _building(), bare], {"building/b1": RECORD}, "mediterranean", ATLAS, LAYOUT)
    assert out[0] is street and out[1] is placed
    assert out[2].primitives[0].material == "facade_trim_mediterranean"
    assert out[3].primitives[0].material == "facade_trim_mediterranean"
    assert stats.buildings == 2
    for kind in ("building_part", "landmark_part"):
        part = Node(f"{kind}/p", (Primitive(facade(4.0, 6.0, []), "stone_warm"),), {"type": kind})
        assert trim_nodes([part], {}, "mediterranean", ATLAS, LAYOUT)[0][0].primitives[0].material == "facade_trim_mediterranean"
    interior = Node("interior/b1", (Primitive(facade(4.0, 6.0, []), "plaster_white"),), {"type": "interior"})
    assert trim_nodes([interior], {}, "mediterranean", ATLAS, LAYOUT)[0][0] is interior
    # The record's storeys (3 m) and the fallback for x (10 m: 3 bands of 3.33 m, no parapet) cut differently.
    assert stats.wall_triangles_after == 46 + 2 + 20 + 20 + 2
    assert stats.as_dict()["buildings"] == 2
    assert list(stats.as_dict()) == ["buildings", "primitives_merged", "wall_triangles_before", "wall_triangles_after",
                                     "kept_materials", "openings_by_cell", "looks"]
    assert stats.as_dict()["looks"] == {"cornice": 2, "courses": 2, "tone_0": 2, "zocalo": 2}


MANIFEST = {"frame": {"origin_wgs84": list(MURCIA_CENTER)}, "objects": [{"id": "building/b1", **RECORD}, "not a record"]}
COLORS = {name: (0.8, 0.7, 0.6) for name in ATLAS["walls"]}


@pytest.fixture
def small_table(tmp_path: Path) -> Path:
    """The shipped table with the mediterranean atlas at 1024 px, so a bake is quick, and every dress on."""
    doc = json.loads(STYLE_TABLE_PATH.read_text(encoding="utf-8"))
    doc["styles"]["mediterranean"]["atlas"].update(size_px=1024, height_px=1024, fit_row_px=192, fit_cell_px=96, cornice_px=64,
                                                   zocalo_px=64, stone_px=32, dress=ATLAS["dress"])
    path = tmp_path / "styles.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_apply_trim_bakes_and_maps(tmp_path: Path, small_table: Path) -> None:
    result = apply_trim([_building()], MANIFEST, COLORS, table_path=small_table)
    assert result.mode == "trim" and result.style == "mediterranean"
    assert result.materials == ("facade_trim_mediterranean",)
    assert result.png is not None and result.png.startswith(b"\x89PNG")
    report = result.report
    assert report["style_origin"] == "map_centre"
    assert report["materials"] == ["facade_trim_mediterranean"]
    assert report["atlas"]["size"] == [1024, 1024]
    assert report["style_table_sha256"] == hashlib.sha256(small_table.read_bytes()).hexdigest()
    assert [s["asset_id"] for s in report["atlas"]["sources"]] == ["acg_bricks105", "ph_plastered_wall", "acg_rock030"]
    assert all(s["license"].startswith("CC0") for s in report["atlas"]["sources"])
    assert report["stats"]["buildings"] == 1
    cache = TileCache(tmp_path / "tiles")
    cold = apply_trim([_building()], MANIFEST, COLORS, cache=cache, table_path=small_table)
    assert cold.png == result.png
    assert (tmp_path / "tiles" / f"{report['atlas']['cache_key']}.tile").is_file()
    entry = tmp_path / "tiles" / f"{report['atlas']['cache_key']}.tile"
    entry.write_bytes(entry.read_bytes()[:32] + b"tampered")
    assert apply_trim([_building()], MANIFEST, COLORS, cache=cache, table_path=small_table).png == result.png
    other = apply_trim([_building()], MANIFEST, {**COLORS, "plaster_ochre": (0.9, 0.5, 0.2)}, table_path=small_table)
    assert other.png != result.png


def test_apply_trim_keeps_the_legacy_path_when_asked_or_without_an_atlas(small_table: Path) -> None:
    nodes = [_building()]
    legacy = apply_trim(nodes, MANIFEST, COLORS, mode="legacy")
    assert legacy.mode == "legacy" and legacy.nodes == nodes and legacy.png == b"" and legacy.materials == ()
    assert legacy.report["reason"] == "config" and legacy.report["style"] == "mediterranean"
    krakow = apply_trim(nodes, {"frame": {"origin_wgs84": [19.9366, 50.0614]}}, COLORS)
    assert krakow.mode == "legacy" and krakow.style == "central_european"
    assert krakow.report["reason"] == "style_has_no_atlas" and krakow.report["mode"] == "legacy"
    forced = apply_trim(nodes, {"frame": {"origin_wgs84": [19.9366, 50.0614]}}, COLORS, style="mediterranean", table_path=small_table)
    assert forced.mode == "trim" and forced.report["style_origin"] == "config"
    assert apply_trim(nodes, {}, COLORS).report["style_origin"] == "default"


def test_apply_trim_refusals(monkeypatch: pytest.MonkeyPatch, small_table: Path) -> None:
    with pytest.raises(StyleError, match="unknown facade mode 'fancy'; known: trim, legacy"):
        apply_trim([], MANIFEST, COLORS, mode="fancy")
    with pytest.raises(StyleError, match="no colour for wall strips plaster_salmon, stone_warm"):
        apply_trim([], MANIFEST, {k: v for k, v in COLORS.items() if k not in ("plaster_salmon", "stone_warm")}, table_path=small_table)
    real = trim_module.texture_record
    monkeypatch.setattr(trim_module, "texture_record", lambda i: replace(real(i), license="CC-BY-4.0") if i == "acg_rock030" else real(i))
    with pytest.raises(StyleError, match=r"source stone \(acg_rock030\) is CC-BY-4.0, not CC0"):
        apply_trim([], MANIFEST, COLORS, table_path=small_table)
    monkeypatch.setattr(trim_module, "texture_record", lambda i: replace(real(i), license="cc0-1.0"))
    assert apply_trim([], MANIFEST, COLORS, table_path=small_table).mode == "trim"


def test_the_cache_key_follows_every_input() -> None:
    base = trim_cache_key("m", {"a": 1}, {"w": (1.0, 0.5, 0.2)}, {"plaster": "x"})
    assert len(base) == 64
    assert trim_cache_key("m", {"a": 1}, {"w": (1.0, 0.5, 0.2)}, {"plaster": "x"}) == base
    assert trim_cache_key("n", {"a": 1}, {"w": (1.0, 0.5, 0.2)}, {"plaster": "x"}) != base
    assert trim_cache_key("m", {"a": 2}, {"w": (1.0, 0.5, 0.2)}, {"plaster": "x"}) != base
    assert trim_cache_key("m", {"a": 1}, {"w": (1.0, 0.5, 0.3)}, {"plaster": "x"}) != base
    assert trim_cache_key("m", {"a": 1}, {"w": (1.0, 0.5, 0.2)}, {"plaster": "y"}) != base


def _rotated(wall: Mesh, degrees: float) -> Mesh:
    m = np.eye(4)
    c, s_ = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
    m[:2, :2] = [[c, -s_], [s_, c]]
    return transform(wall, m)


def test_planes_are_told_apart_by_direction_and_offset() -> None:
    # Two plain walls of 4 x 6 m, one turned 37 degrees, one 53: each keeps its own plane.
    a, b = _rotated(facade(4.0, 6.0, []), 37.0), _rotated(facade(4.0, 6.0, []), 53.0)
    out, band, plain = band_mesh(merge([a, b]), [0.9, 3.0], [0.9, 6.0])
    assert out.triangle_count == 2 * 2 * 2
    assert area(out) == pytest.approx(48.0)
    normals = out.face_normals() / np.linalg.norm(out.face_normals(), axis=1, keepdims=True)
    for source in (a, b):
        n = source.face_normals()[0] / np.linalg.norm(source.face_normals()[0])
        on_plane = np.isclose((out.vertices - source.vertices[0]) @ n, 0.0, atol=1e-9)
        # Its own eight corners, and the four the other face writes on the edge they share.
        assert on_plane.sum() == 12
        assert (np.isclose(normals @ n, 1.0)).sum() == 4
    # Two parallel south walls 0.3 m apart do not join either.
    back = transform(facade(4.0, 6.0, []), np.array([[1.0, 0, 0, 0], [0, 1, 0, -0.3], [0, 0, 1, 0], [0, 0, 0, 1]]))
    out, _, _ = band_mesh(merge([facade(4.0, 6.0, []), back]), [0.9], [0.9])
    assert out.triangle_count == 8 and area(out) == pytest.approx(48.0)
    assert sorted({round(float(y), 6) for y in out.vertices[:, 1]}) == [-0.3, 0.0]


def test_a_single_triangle_plane_and_small_faces_keep_their_side() -> None:
    gable = Mesh(np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [2.0, 0.0, 2.0]]), np.array([[0, 1, 2]]))
    out, band, _ = band_mesh(gable, [0.9], [0.9])
    assert area(out) == pytest.approx(4.0)
    assert (out.face_normals()[:, 1] < 0).all()
    assert sorted(set(band.tolist())) == [0, 1]
    small = facade(0.6, 0.5, [(0.1, 0.1, 0.3, 0.3)])
    out, _, _ = band_mesh(small, [0.2], [0.2])
    assert (out.face_normals()[:, 1] < 0).all()
    assert area(out) == pytest.approx(area(small))


def test_building_bands_measured_or_not() -> None:
    # Storeys need both a count and a height to count as measured.
    assert building_bands({"facade": {"storeys": {"height_m": 3.0}}}, 7.0, 0.9, 4.5) == Bands(0.9, 3.5, 2, 7.0)
    assert building_bands({"facade": {"storeys": {"count": 2}}}, 7.0, 0.9, 4.5) == Bands(0.9, 3.5, 2, 7.0)
    assert building_bands({"facade": {"storeys": {"count": 2, "height_m": 0.0}}}, 7.0, 0.9, 4.5) == Bands(0.9, 3.5, 2, 7.0)
    assert building_bands({"facade": {"storeys": {"count": 1, "height_m": 3.0}}}, 9.0, 0.9, 4.5) == Bands(0.9, 3.0, 1, 3.0)
    assert building_bands({"facade": {"storeys": {"count": 2, "height_m": 0.8}}}, 9.0, 0.9, 4.5) == Bands(0.4, 0.8, 2, 1.6)
    # A parapet without its height counts as none.
    assert building_bands({"roof": {"type": "flat_parapet"}}, 6.0, 0.9, 4.5).wall_top_m == 6.0


def test_a_plain_face_keeps_its_zocalo_and_parapet_strips() -> None:
    strip = LAYOUT.walls["plaster_white"]
    mesh, uvs = wall_uvs(facade(8.0, 10.0, []), BANDS, strip, LAYOUT.zocalo, LAYOUT.cornice, None, 0.0)
    z, v = mesh.vertices[:, 2], uvs[:, 1]
    _, band, plain = band_mesh(facade(8.0, 10.0, []), BANDS.levels, BANDS.plain_levels)
    assert plain.all()
    body = band == 1
    low, high = (strip.v0 + p * (strip.v1 - strip.v0) for p in PLAIN_V)
    assert np.allclose(v[body & np.isclose(z, 0.9)], low) and (body & np.isclose(z, 0.9)).sum() == 2
    assert np.allclose(v[body & np.isclose(z, 9.0)], high) and (body & np.isclose(z, 9.0)).sum() == 2
    zocalo = band == 0
    assert np.allclose(v[zocalo & np.isclose(z, 0.0)], LAYOUT.zocalo.v0)
    assert np.allclose(v[zocalo & np.isclose(z, 0.9)], LAYOUT.zocalo.v1) and (zocalo & np.isclose(z, 0.9)).sum() == 2
    cornice = band == 2
    assert np.allclose(v[cornice & np.isclose(z, 10.0)], LAYOUT.cornice.v1)
    assert np.allclose(v[cornice & np.isclose(z, 9.0)], LAYOUT.cornice.v0) and (cornice & np.isclose(z, 9.0)).sum() == 2
    assert np.ptp(uvs[cornice, 0]) == pytest.approx(8.0 / 6.0)


def test_faces_off_a_run_recentre_on_their_own() -> None:
    near, far = facade(9.0, 6.0, []), facade(9.0, 6.0, [], x0=100.0)
    mesh, uvs = wall_uvs(merge([near, far]), Bands(0.9, 3.0, 2, 6.0), LAYOUT.walls["plaster_white"], LAYOUT.zocalo,
                         LAYOUT.cornice, None, 0.0)
    for side in (mesh.vertices[:, 0] < 50.0, mesh.vertices[:, 0] > 50.0):
        assert 0.0 <= uvs[side, 0].min() < 1.0


def _hash_index(count: int, *parts: object) -> int:
    return int.from_bytes(hashlib.sha256(":".join(map(str, parts)).encode()).digest()[:8], "big") % count


def test_stable_index_and_opening_cells_follow_their_hashes() -> None:
    assert stable_index(7, "a", 1) == _hash_index(7, "a", 1)
    assert stable_index(1000003, "b", 2.5) == _hash_index(1000003, "b", 2.5)
    for building in [f"b{k}" for k in range(24)]:
        door = opening_cell("door_wood", _back(1.2, 2.4, 0.0), np.arange(4), building, WINDOWS)
        assert door == ("door_0", "door_1")[_hash_index(2, building, "door")]
        back = _back(1.2, 1.5, 4.0, x0=0.003)
        family = WINDOWS[_hash_index(2, building, "window")]
        centre = [0.6, 0.0, 4.75]  # the centre (0.603, 0, 4.75) to the centimetre
        assert opening_cell("glass_dark", back, np.arange(4), building, WINDOWS) == family[_hash_index(2, building, *centre)]


def test_opening_uvs_map_each_corner() -> None:
    counts: dict[str, int] = {}
    uvs = opening_uvs("glass_dark", _back(2.4, 2.3, 0.25), LAYOUT.cells, "b1", WINDOWS, counts)
    shop = LAYOUT.cells["shop_glass"]
    # The quad runs (x0, z0), (x1, z0), (x1, z1), (x0, z1).
    assert uvs.reshape(-1).tolist() == pytest.approx([shop.u0, shop.v0, shop.u1, shop.v0, shop.u1, shop.v1, shop.u0, shop.v1])
    assert uvs.dtype == np.float32


def _framed(phase: list[float] | None) -> Node:
    extras: dict[str, object] = {"type": "building", "object_id": "building/f",
                                 "uv_frame": {"runs": [[[3.0, 0.0, 12.0, 0.0]]], "storey_h": 3.0}}
    if phase is not None:
        extras["uv_phase"] = phase
    return Node("building/f", (Primitive(facade(9.0, 6.0, [], x0=3.0), "plaster_ochre"),), extras)


def test_a_building_uses_its_run_frame_and_its_phase() -> None:
    record = {"facade": {"storeys": {"count": 2, "height_m": 3.0}}}
    plain = trim_building(_framed(None), record, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    u = plain.primitives[0].uvs[:, 0]
    v = plain.primitives[0].uvs[:, 1]
    wall = (v >= LAYOUT.walls["plaster_ochre"].v0 - 1e-6) & (v <= LAYOUT.walls["plaster_ochre"].v1 + 1e-6)
    # On the run the 9 m wall is one whole repeat (0-1); on the face plane it would be 3/12-1.
    assert u[wall].min() == pytest.approx(0.0, abs=1e-6) and u[wall].max() == pytest.approx(1.0, abs=1e-6)
    shifted = trim_building(_framed([0.25, 0.5]), record, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert np.allclose(shifted.primitives[0].uvs[wall, 0], u[wall] + 0.25, atol=1e-6)
    assert np.allclose(shifted.primitives[0].uvs[:, 1], v)


def test_trim_building_counts_every_wall_and_kept_colour() -> None:
    stats = TrimStats()
    node = Node("building/k", (
        Primitive(facade(4.0, 6.0, []), "plaster_ochre"), Primitive(facade(4.0, 6.0, [], x0=10.0), "plaster_ochre"),
        Primitive(facade(2.0, 3.0, []), "plaster_lime"), Primitive(facade(2.0, 3.0, []), "plaster_lime"),
        Primitive(facade(2.0, 3.0, []), "brick_yellow"), Primitive(facade(2.0, 3.0, []), "stone_grey"),
        Primitive(facade(2.0, 3.0, []), "building_wall"), Primitive(facade(2.0, 3.0, []), "roof_flat_grey"),
    ), {"type": "building"})
    trim_building(node, None, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
    assert stats.wall_triangles_before == 4
    assert stats.kept_materials == {"brick_yellow": 1, "building_wall": 1, "plaster_lime": 2, "stone_grey": 1}


def test_trim_nodes_reads_the_record_by_object_id() -> None:
    node = Node("visual name", (Primitive(facade(9.0, 10.0, WINDOWS_2X3), "plaster_ochre"),), {"type": "building", "object_id": "building/b1"})
    _, by_id = trim_nodes([node], {"building/b1": RECORD}, "mediterranean", ATLAS, LAYOUT)
    _, by_name = trim_nodes([node], {"visual name": RECORD}, "mediterranean", ATLAS, LAYOUT)
    # The record's 3 m storeys and parapet cut the wall at 0.9, 3, 6 and 9 m; without it, 3.33 m bands.
    assert by_id.wall_triangles_after == 46
    assert by_name.wall_triangles_after == 44


def test_apply_trim_reports_its_key_png_and_materials(small_table: Path) -> None:
    party = Primitive(facade(8.0, 10.0, []), "party_plaster_ochre")
    shell = Node("building/s1", (Primitive(facade(8.0, 6.0, []), "scenic_wall_warm"),), {"type": "building"})
    result = apply_trim([_building(extra=(party,)), shell], MANIFEST, COLORS, table_path=small_table)
    assert result.materials == ("facade_trim_mediterranean", "party_facade_trim_mediterranean", "scenic_trim_mediterranean")
    table = load_style_table(small_table)
    atlas = table.styles["mediterranean"].atlas
    assert atlas is not None
    tints = {wall: tuple(float(c) for c in COLORS[wall][:3]) for wall in sorted(atlas["walls"])}
    report = result.report
    key = trim_cache_key("mediterranean", atlas, tints, {s["key"]: s["sha256"] for s in report["atlas"]["sources"]})
    assert report["atlas"]["cache_key"] == key
    assert report["atlas"]["sha256"] == hashlib.sha256(result.png).hexdigest()
    assert report["atlas"]["sha256"] == SMALL_ATLAS_SHA256_BY_BAKE[TRIM_BAKE_VERSION]
    # The record (3 storeys of 3 m, a parapet) cuts the 10 m wall into 46 triangles, not the fallback's 44.
    assert report["stats"]["wall_triangles_after"] == 46 + 6 + 4
    assert report["atlas"]["license"] == "project-generated from CC0 photographs"
    assert [s["credit"] for s in report["atlas"]["sources"]] == [
        "Created using Bricks105 from ambientCG.com, licensed under CC0 1.0.",
        "plastered_wall by Amal Kumar from Poly Haven, licensed under CC0 1.0.",
        "Created using Rock030 from ambientCG.com, licensed under CC0 1.0.",
    ]


# The small atlas by TRIM_BAKE_VERSION. New bytes need a new version, or the cache keeps serving the old
# atlas to every map: add an entry for the bumped version, never edit an existing one.
SMALL_ATLAS_SHA256_BY_BAKE = {
    2: "b1757ac438582a5d27f0ac13c75bc893abc740bfb22bbd2b06640c0f7c319ed3",
    3: "220fd5b12c6e486304d2cad2e52857428f02459a22bf7690ad0570b6067dfbfb",
}


def test_each_bake_version_names_its_own_atlas_bytes() -> None:
    assert TRIM_BAKE_VERSION == max(SMALL_ATLAS_SHA256_BY_BAKE) == 3
    assert len(set(SMALL_ATLAS_SHA256_BY_BAKE.values())) == len(SMALL_ATLAS_SHA256_BY_BAKE)


def test_the_cache_key_is_the_hash_of_its_inputs() -> None:
    import PIL

    from onward_generator.dress.atlas import TILE_PNG_LEVEL
    from onward_generator.materials.trim import TRIM_BAKE_VERSION

    doc = {
        "bake": TRIM_BAKE_VERSION, "pillow": PIL.__version__, "numpy": np.__version__, "png_level": TILE_PNG_LEVEL,
        "style": "m", "atlas": {"a": 1}, "tints": {"w": [1.0, 0.5, 0.2]}, "sources": {"plaster": "x"},
    }
    expected = hashlib.sha256(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert trim_cache_key("m", {"a": 1}, {"w": (1.0, 0.5, 0.2)}, {"plaster": "x"}) == expected


def test_openings_pick_their_cells_by_the_building_name() -> None:
    for k in range(24):
        name = f"building/n{k}"
        stats = TrimStats()
        node = Node(name, (Primitive(_back(1.2, 2.4, 0.0), "door_wood"), Primitive(_back(1.2, 1.5, 4.0), "glass_dark")), {"type": "building"})
        trim_building(node, None, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
        door = opening_cell("door_wood", _back(1.2, 2.4, 0.0), np.arange(4), name, WINDOWS)
        window = opening_cell("glass_dark", _back(1.2, 1.5, 4.0), np.arange(4), name, WINDOWS)
        assert stats.cells == {door: 1, window: 1}


def test_a_corner_keeps_one_vertex_on_each_face() -> None:
    rot = np.eye(4)
    rot[:3, :3] = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    rot[:3, 3] = (4.0, 0.0, 0.0)
    out, _, _ = band_mesh(merge([facade(4.0, 6.0, []), transform(facade(4.0, 6.0, []), rot)]), [0.9], [0.9])
    corner = np.isclose(out.vertices[:, 0], 4.0) & np.isclose(out.vertices[:, 1], 0.0)
    # Two faces, two bands each meeting at z = 0, 0.9 (twice) and 6: eight vertices, not four.
    assert corner.sum() == 8
    assert out.vertex_count == 16


def test_look_keys_join_the_parts_of_one_parcel() -> None:
    assert look_key("building/osm-way-1:9131402VK3793A_part10.10") == "building/osm-way-1:9131402VK3793A"
    assert look_key("building/osm-way-1:8933103VK3783D_part12") == "building/osm-way-1:8933103VK3783D"
    assert look_key("building/osm-way-1:9131402VK3793A") == "building/osm-way-1:9131402VK3793A"
    assert look_key("building/osm-way-1_party") == "building/osm-way-1_party"
    assert look_key("building/part7_x") == "building/part7_x"


def test_stable_unit_is_a_hash_in_the_unit_interval() -> None:
    digest = hashlib.sha256(b"a:1").digest()
    assert stable_unit("a", 1) == int.from_bytes(digest[:8]) / 2.0**64
    values = [stable_unit("k", i) for i in range(400)]
    assert all(0.0 <= v < 1.0 for v in values)
    assert 0.4 < sum(values) / len(values) < 0.6


def _dress(look: Look) -> tuple[bool, bool, bool]:
    return look.zocalo, look.cornice, look.courses


def test_building_look_follows_the_shares_and_spreads_the_tones() -> None:
    every = {**SHIPPED, "dress": {"zocalo_share": 1.0, "cornice_share": 1.0, "course_share": 1.0}}
    none = {**SHIPPED, "dress": {"zocalo_share": 0.0, "cornice_share": 0.0, "course_share": 0.0}}
    keys = [f"building/b{i}" for i in range(300)]
    assert all(_dress(building_look(k, every)) == (True, True, True) for k in keys)
    assert all(_dress(building_look(k, none)) == (False, False, False) for k in keys)
    looks = [building_look(k, SHIPPED) for k in keys]
    assert {look.tone for look in looks} == {0, 1, 2}
    assert looks == [
        Look(stable_index(3, k, "tone"), stable_unit(k, "zocalo") < 0.7, stable_unit(k, "cornice") < 0.6,
             stable_unit(k, "courses") < 0.5)
        for k in keys
    ]
    # A share equal to the building's own draw leaves it out: the draw must fall below the share.
    key = keys[0]
    edge = {**SHIPPED, "dress": {"zocalo_share": stable_unit(key, "zocalo"), "cornice_share": stable_unit(key, "cornice"),
                                 "course_share": stable_unit(key, "courses")}}
    assert _dress(building_look(key, edge)) == (False, False, False)
    for index, share in ((0, 0.7), (1, 0.6), (2, 0.5)):
        assert abs(sum(_dress(look)[index] for look in looks) / len(looks) - share) < 0.1
    # The parts of one parcel draw one look.
    assert building_look(look_key("building/w:R_part1.2"), SHIPPED) == building_look(look_key("building/w:R_part9"), SHIPPED)


def test_bands_without_a_zocalo_start_with_the_ground_storey() -> None:
    bands = Bands(0.9, 3.0, 3, 9.0, zocalo=False)
    assert bands.levels == [3.0, 6.0, 9.0]
    assert bands.plain_levels == [9.0]
    assert building_bands(RECORD, 10.0, 0.9, 4.5, zocalo=False) == Bands(0.9, 3.0, 3, 9.0, False)
    assert building_bands(RECORD, 10.0, 0.9, 4.5).zocalo


def test_a_wall_without_zocalo_cornice_or_courses_stays_on_its_strip() -> None:
    strip = LAYOUT.walls["plaster_ochre"]
    bands = Bands(0.9, 3.0, 3, 9.0, zocalo=False)
    mesh, uvs = wall_uvs(facade(9.0, 10.0, WINDOWS_2X3), bands, strip, LAYOUT.zocalo, None, None, 0.0, courses=False)
    z, v = mesh.vertices[:, 2], uvs[:, 1]
    low, high = (strip.v0 + p * (strip.v1 - strip.v0) for p in PLAIN_V)
    # Every band, the ground and the parapet included, samples the middle of the wall strip.
    assert (v >= low - 1e-6).all() and (v <= high + 1e-6).all()
    assert np.allclose(v[np.isclose(z, 0.0)], low)
    _, band, _ = band_mesh(facade(9.0, 10.0, WINDOWS_2X3), bands.levels, bands.plain_levels)
    assert np.allclose(v[(band == 1) & np.isclose(z, 3.0)], low) and np.allclose(v[(band == 0) & np.isclose(z, 3.0)], high)
    # U keeps the wall repeat on every band: 9 m of 12.
    assert np.ptp(uvs[:, 0]) == pytest.approx(9.0 / 12.0)
    # With courses, a storey spans its whole strip, a string course at its foot.
    _, coursed = wall_uvs(facade(9.0, 10.0, WINDOWS_2X3), bands, strip, LAYOUT.zocalo, None, None, 0.0)
    assert coursed[:, 1].min() == pytest.approx(strip.v0)
    assert coursed[:, 1].max() == pytest.approx(strip.v1)
    # A plain face without a zocalo is one body band and the parapet.
    plain, puv = wall_uvs(facade(8.0, 10.0, []), bands, strip, LAYOUT.zocalo, LAYOUT.cornice, None, 0.0)
    top = np.isclose(plain.vertices[:, 2], 10.0)
    assert np.allclose(puv[top, 1], LAYOUT.cornice.v1)
    assert np.allclose(puv[np.isclose(plain.vertices[:, 2], 0.0), 1], low)
    assert plain.triangle_count == 4


def test_a_building_takes_the_strip_of_its_tone_and_its_dress() -> None:
    node = _building()
    look = building_look("building/b1", SHIPPED)
    layout = trim_layout(SHIPPED)
    stats = TrimStats()
    out = trim_building(node, RECORD, "mediterranean", SHIPPED, layout, window_families(SHIPPED), stats)
    v = out.primitives[0].uvs[:, 1]
    strip = layout.tones["plaster_ochre"][look.tone]
    assert ((v >= strip.v0 - 1e-6) & (v <= strip.v1 + 1e-6)).any()
    for tone, other in enumerate(layout.tones["plaster_ochre"]):
        if tone != look.tone:
            assert not ((v > other.v0 + 1e-6) & (v < other.v1 - 1e-6)).any()
    assert (((v >= layout.zocalo.v0 - 1e-6) & (v <= layout.zocalo.v1 + 1e-6)).any()) == look.zocalo
    assert (((v >= layout.cornice.v0 - 1e-6) & (v <= layout.cornice.v1 + 1e-6)).any()) == look.cornice
    assert stats.looks[f"tone_{look.tone}"] == 1
    assert sum(stats.looks.get(name, 0) for name in ("zocalo", "cornice", "courses")) == sum(_dress(look))


def test_the_parts_of_one_parcel_share_their_openings() -> None:
    names = ["building/w:R_part1.2", "building/w:R_part3"]
    cells = []
    for name in names:
        node = replace(_building(), name=name)
        stats = TrimStats()
        trim_building(node, RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
        cells.append(stats.cells)
    assert cells[0] == cells[1]
    assert cells[0] == {opening_cell("glass_dark", _back(1.2, 1.5, 1.0), np.arange(4), "building/w:R", WINDOWS): 1,
                        opening_cell("door_wood", _back(1.2, 2.4, 0.0), np.arange(4), "building/w:R", WINDOWS): 1, "boarded": 1}


def test_a_building_without_a_zocalo_is_not_cut_at_the_zocalo_line() -> None:
    bare = {**ATLAS, "dress": {"zocalo_share": 0.0, "cornice_share": 1.0, "course_share": 1.0}}
    stats = TrimStats()
    out = trim_building(_building(), RECORD, "mediterranean", bare, LAYOUT, WINDOWS, stats)
    v = out.primitives[0].uvs[:, 1]
    assert not ((v >= LAYOUT.zocalo.v0 - 1e-6) & (v <= LAYOUT.zocalo.v1 + 1e-6)).any()
    # The record's 3 storeys and the parapet, no zocalo line: fewer triangles than the 46 with one.
    assert stats.wall_triangles_after < 46
    assert "zocalo" not in stats.looks


def test_a_building_with_every_dress_takes_the_cornice_and_the_string_courses() -> None:
    def wall_v(atlas: dict) -> tuple[np.ndarray, np.ndarray]:
        out = trim_building(_building(), RECORD, "mediterranean", atlas, LAYOUT, WINDOWS, TrimStats())
        v = out.primitives[0].uvs[:, 1]
        strip = LAYOUT.walls["plaster_ochre"]
        return v, v[(v >= strip.v0 - 1e-6) & (v <= strip.v1 + 1e-6)]

    strip = LAYOUT.walls["plaster_ochre"]
    low, high = (strip.v0 + p * (strip.v1 - strip.v0) for p in PLAIN_V)
    v, wall = wall_v(ATLAS)
    assert ((v >= LAYOUT.cornice.v0 - 1e-6) & (v <= LAYOUT.cornice.v1 + 1e-6)).any()
    assert wall.min() == pytest.approx(strip.v0) and wall.max() == pytest.approx(strip.v1)
    plain = {**ATLAS, "dress": {"zocalo_share": 1.0, "cornice_share": 0.0, "course_share": 0.0}}
    v, wall = wall_v(plain)
    assert not ((v >= LAYOUT.cornice.v0 - 1e-6) & (v <= LAYOUT.cornice.v1 + 1e-6)).any()
    assert wall.min() == pytest.approx(low) and wall.max() == pytest.approx(high)


def test_an_rgba_scene_colour_tints_its_strip_by_rgb_only(small_table: Path) -> None:
    rgba = {name: (*color, 1.0) for name, color in COLORS.items()}
    result = apply_trim([_building()], MANIFEST, rgba, table_path=small_table)
    atlas = load_style_table(small_table).styles["mediterranean"].atlas
    assert atlas is not None
    tints = {wall: tuple(float(c) for c in COLORS[wall]) for wall in sorted(atlas["walls"])}
    sources = {s["key"]: s["sha256"] for s in result.report["atlas"]["sources"]}
    assert result.report["atlas"]["cache_key"] == trim_cache_key("mediterranean", atlas, tints, sources)


# ------------------------------------------------------------------ plan D1: regional atlases and era bands

KRAKOW = [19.9366, 50.0614]
LONDON = [-0.1276, 51.5072]
MEXICO_CITY = [-99.1332, 19.4326]
PARIS = [2.34981, 48.84045]
CENTRES = {"central_european": KRAKOW, "anglo": LONDON, "latin_american": MEXICO_CITY, "haussmann": PARIS}
REGIONAL_ATLAS = load_regional_table(load_style_table()).atlases["central_european"]


def _picked(wall: str, origin: str = "generated") -> dict[str, object]:
    return {**RECORD, "facade": {**RECORD["facade"], "materials": {"wall": wall, "wall_origin": origin}}}


def test_an_estimated_wall_is_drawn_in_the_strip_the_atlas_names() -> None:
    """Paris takes no estimated brick: brick_red the palette picked samples the plaster_cream strip."""
    atlas = {**ATLAS, "estimated_walls": {"brick_red": "plaster_cream"}}
    stats = TrimStats()
    redrawn = trim_building(_building("brick_red"), _picked("brick_red"), "mediterranean", atlas, LAYOUT, WINDOWS, stats)
    cream = trim_building(_building("plaster_cream"), _picked("plaster_cream"), "mediterranean", atlas, LAYOUT, WINDOWS,
                          TrimStats())
    brick = trim_building(_building("brick_red"), RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert np.array_equal(redrawn.primitives[0].uvs, cream.primitives[0].uvs)
    assert not np.array_equal(redrawn.primitives[0].uvs, brick.primitives[0].uvs)
    assert stats.estimated_walls == {"brick_red": 1}
    assert stats.as_dict()["estimated_walls"] == {"brick_red": 1}
    # Each wall primitive counts: a party wall of the same building is a second.
    twice = TrimStats()
    two = _building("brick_red", extra=(Primitive(facade(9.0, 10.0, WINDOWS_2X3), "party_brick_red"),))
    trim_building(two, _picked("brick_red"), "mediterranean", atlas, LAYOUT, WINDOWS, twice)
    assert twice.estimated_walls == {"brick_red": 2}
    # An observed wall keeps its strip: OSM building:material, an observed origin, another wall, no record.
    cases = [
        (_picked("brick_red"), frozenset({"building/b1"})),
        (_picked("brick_red", origin="observed"), frozenset()),
        (_picked("plaster_ochre"), frozenset()),
        (None, frozenset()),
    ]
    for record, observed in cases:
        kept_stats = TrimStats()
        kept = trim_building(_building("brick_red"), record, "mediterranean", atlas, LAYOUT, WINDOWS, kept_stats, observed)
        plain = trim_building(_building("brick_red"), record, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
        assert np.array_equal(kept.primitives[0].uvs, plain.primitives[0].uvs)
        assert kept_stats.estimated_walls == {} and "estimated_walls" not in kept_stats.as_dict()


def test_apply_trim_keeps_the_brick_of_an_osm_brick_building(small_regional_table: Path) -> None:
    manifest = {"frame": {"origin_wgs84": PARIS}, "objects": [{"id": "building/b1", **_picked("brick_red")}]}
    redrawn = apply_trim([_building("brick_red")], manifest, COLORS, regional=True, regional_table_path=small_regional_table)
    assert redrawn.report["stats"]["estimated_walls"] == {"brick_red": 1}
    kept = apply_trim([_building("brick_red")], manifest, COLORS, regional=True, regional_table_path=small_regional_table,
                      observed_walls=["building/b1"])
    assert "estimated_walls" not in kept.report["stats"]
    assert not np.array_equal(kept.nodes[0].primitives[0].uvs, redrawn.nodes[0].primitives[0].uvs)


def _era(period: object, origin: str = "observed") -> dict[str, object]:
    return {**RECORD, "facade": {**RECORD["facade"], "typology": {"era": {"year": 1890, "period": period, "origin": origin}}}}


@pytest.fixture
def small_regional_table(tmp_path: Path) -> Path:
    """The shipped regional recipes at 1024 px, so a bake is quick."""
    doc = json.loads(REGIONAL_TABLE_PATH.read_text(encoding="utf-8"))
    for atlas in doc["atlases"].values():
        atlas.update(size_px=1024, height_px=1024, fit_row_px=192, fit_cell_px=96, cornice_px=64, zocalo_px=64, stone_px=32)
    path = tmp_path / "regional.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_without_the_option_the_regional_styles_keep_the_legacy_path() -> None:
    nodes = [_building()]
    for style, centre in CENTRES.items():
        for options in ({}, {"regional": False}, {"regional": True, "mode": "legacy"}):
            result = apply_trim(nodes, {"frame": {"origin_wgs84": centre}}, COLORS, **options)  # type: ignore[arg-type]
            assert result.mode == "legacy" and result.style == style and result.nodes == nodes and result.png == b""
            assert set(result.report) == {"mode", "reason", "style", "style_origin", "style_table_sha256"}
            reason = "config" if options.get("mode") == "legacy" else "style_has_no_atlas"
            assert result.report["reason"] == reason


def test_the_legacy_path_never_reads_the_regional_table(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    manifest = {"frame": {"origin_wgs84": KRAKOW}}
    assert apply_trim([], manifest, COLORS, regional_table_path=broken).mode == "legacy"
    assert apply_trim([], manifest, COLORS, mode="legacy", regional=True, regional_table_path=broken).mode == "legacy"
    with pytest.raises(StyleError, match="broken.json: not JSON"):
        apply_trim([], manifest, COLORS, regional=True, regional_table_path=broken)
    # A style with an atlas in the main table never reads it either.
    assert apply_trim([], MANIFEST, COLORS, regional=True, regional_table_path=broken).mode == "trim"


def test_a_style_without_a_regional_recipe_stays_legacy(small_regional_table: Path) -> None:
    tokyo = apply_trim([_building()], {"frame": {"origin_wgs84": [139.6503, 35.6762]}}, COLORS, regional=True,
                       regional_table_path=small_regional_table)
    assert tokyo.mode == "legacy" and tokyo.style == "generic" and tokyo.report["reason"] == "style_has_no_atlas"
    assert "regional_table_sha256" not in tokyo.report


@pytest.mark.parametrize("style", ["central_european", "anglo", "latin_american"])
def test_the_option_bakes_the_regional_atlas(style: str, small_regional_table: Path, tmp_path: Path) -> None:
    manifest = {"frame": {"origin_wgs84": CENTRES[style]}, "objects": [{"id": "building/b1", **_era("1900_1939")}]}
    result = apply_trim([_building()], manifest, COLORS, regional=True, regional_table_path=small_regional_table)
    assert result.mode == "trim" and result.style == style
    assert result.materials == (f"facade_trim_{style}",)
    report = result.report
    assert report["style_origin"] == "map_centre"
    assert report["regional_table_sha256"] == hashlib.sha256(small_regional_table.read_bytes()).hexdigest()
    assert report["atlas_origin"] == "regional"
    assert report["atlas"]["size"] == [1024, 1024]
    recipe = json.loads(small_regional_table.read_text())["atlases"][style]
    assert sorted(s["asset_id"] for s in report["atlas"]["sources"]) == sorted(recipe["sources"].values())
    for source in report["atlas"]["sources"]:
        assert source["license"].startswith("CC0") and len(source["sha256"]) == 64
    assert report["era_bands"] == {"era_origin": "observed", "band_origin": "estimated", "buildings": {"1900_1939": 1}}
    assert report["stats"]["eras"] == {"1900_1939": 1}
    assert list(report)[:6] == ["mode", "style", "style_origin", "style_table_sha256", "regional_table_sha256", "atlas_origin"]
    again = apply_trim([_building()], manifest, COLORS, regional=True, regional_table_path=small_regional_table,
                       cache=TileCache(tmp_path / "tiles"))
    assert again.png == result.png and again.report == report


def test_the_paris_atlas_fixes_its_limestone_whatever_the_palette(small_regional_table: Path) -> None:
    """Plan Q3: the ashlar strips take the recipe's wall_tints; only brick still reads the palette."""
    manifest = {"frame": {"origin_wgs84": PARIS}}
    result = apply_trim([_building()], manifest, COLORS, regional=True, regional_table_path=small_regional_table)
    assert result.mode == "trim" and result.style == "haussmann" and result.report["atlas_origin"] == "regional"
    assert result.materials == ("facade_trim_haussmann",)
    other = {k: (0.1, 0.9, 0.1) if k.startswith(("plaster_", "stone_")) else v for k, v in COLORS.items()}
    same = apply_trim([_building()], manifest, other, regional=True, regional_table_path=small_regional_table)
    assert same.png == result.png
    # A palette without the ashlar colours still bakes: the recipe has them.
    bare = {k: v for k, v in COLORS.items() if k == "brick_red"}
    assert apply_trim([_building()], manifest, bare, regional=True, regional_table_path=small_regional_table).png == result.png
    redder = apply_trim([_building()], manifest, {**COLORS, "brick_red": (0.9, 0.1, 0.1)}, regional=True,
                        regional_table_path=small_regional_table)
    assert redder.png != result.png
    with pytest.raises(StyleError, match="style haussmann: no colour for wall strips brick_red$"):
        apply_trim([], manifest, {}, regional=True, regional_table_path=small_regional_table)


def test_a_regional_source_must_be_cc0(monkeypatch: pytest.MonkeyPatch, small_regional_table: Path) -> None:
    manifest = {"frame": {"origin_wgs84": KRAKOW}}
    with pytest.raises(StyleError, match="style central_european: no colour for wall strips brick_red"):
        apply_trim([], manifest, {k: v for k, v in COLORS.items() if k != "brick_red"}, regional=True,
                   regional_table_path=small_regional_table)
    real = trim_module.texture_record
    monkeypatch.setattr(trim_module, "texture_record", lambda i: replace(real(i), license="CC-BY-4.0") if i == "acg_rock022" else real(i))
    with pytest.raises(StyleError, match=r"style central_european: source stone \(acg_rock022\) is CC-BY-4.0, not CC0"):
        apply_trim([], manifest, COLORS, regional=True, regional_table_path=small_regional_table)


def test_the_option_changes_nothing_on_a_mediterranean_map(small_table: Path, small_regional_table: Path) -> None:
    off = apply_trim([_building()], MANIFEST, COLORS, table_path=small_table)
    on = apply_trim([_building()], MANIFEST, COLORS, table_path=small_table, regional=True, regional_table_path=small_regional_table)
    assert on.png == off.png and on.report == off.report
    for a, b in zip(on.nodes, off.nodes, strict=True):
        assert [p.material for p in a.primitives] == [p.material for p in b.primitives]
        for pa, pb in zip(a.primitives, b.primitives, strict=True):
            assert np.array_equal(pa.mesh.vertices, pb.mesh.vertices) and np.array_equal(pa.mesh.triangles, pb.mesh.triangles)
            assert (pa.uvs is None) == (pb.uvs is None) and (pa.uvs is None or np.array_equal(pa.uvs, pb.uvs))
    assert "regional_table_sha256" not in on.report and "eras" not in on.report["stats"]


def test_observed_period_reads_only_an_observed_era() -> None:
    assert observed_period(_era("pre_1900")) == "pre_1900"
    assert observed_period(_era("pre_1900", origin="estimated")) is None
    assert observed_period(_era(1890)) is None
    assert observed_period({"facade": {"typology": {"era": None}}}) is None
    assert observed_period({"facade": {"typology": None}}) is None
    assert observed_period({"facade": {}}) is None
    assert observed_period({"facade": "brick"}) is None
    assert observed_period({"facade": {"typology": "terrace"}}) is None
    assert observed_period({"facade": {"typology": {"era": "1890"}}}) is None
    assert observed_period({}) is None
    assert observed_period(None) is None


def test_era_band_needs_a_period_and_a_band() -> None:
    assert era_band(REGIONAL_ATLAS, None) is None
    assert era_band(REGIONAL_ATLAS, "pre_1900") == REGIONAL_ATLAS["eras"]["pre_1900"]
    assert era_band(SHIPPED, "pre_1900") is None
    assert era_band({**REGIONAL_ATLAS, "eras": {"pre_1900": REGIONAL_ATLAS["eras"]["pre_1900"]}}, "2000_plus") is None


def test_building_look_without_a_band_is_unchanged() -> None:
    for k in (f"building/b{i}" for i in range(50)):
        expected = Look(stable_index(3, k, "tone"), stable_unit(k, "zocalo") < 0.8, stable_unit(k, "cornice") < 0.75,
                        stable_unit(k, "courses") < 0.6)
        assert building_look(k, REGIONAL_ATLAS) == building_look(k, REGIONAL_ATLAS, None) == expected


def test_an_era_band_narrows_the_tones_and_overrides_the_dress() -> None:
    keys = [f"building/b{i}" for i in range(80)]
    assert {building_look(k, REGIONAL_ATLAS, {"tones": [2], "window_families": [0]}).tone for k in keys} == {2}
    spread = {building_look(k, REGIONAL_ATLAS, {"tones": [0, 2], "window_families": [0]}).tone for k in keys}
    assert spread == {0, 2}
    assert all(building_look(k, REGIONAL_ATLAS, {"tones": [0, 2], "window_families": [0]}).tone ==
               [0, 2][stable_index(2, k, "tone")] for k in keys)
    never = {"tones": [0], "window_families": [0], "dress": {"zocalo_share": 0.0}}
    always = {"tones": [0], "window_families": [0], "dress": {"zocalo_share": 1.0}}
    assert not any(building_look(k, REGIONAL_ATLAS, never).zocalo for k in keys)
    assert all(building_look(k, REGIONAL_ATLAS, always).zocalo for k in keys)
    # The shares a band does not name stay the style's.
    for k in keys:
        look = building_look(k, REGIONAL_ATLAS, never)
        assert (look.cornice, look.courses) == (stable_unit(k, "cornice") < 0.75, stable_unit(k, "courses") < 0.6)
    plain = {"tones": [1], "window_families": [0]}
    assert all(_dress(building_look(k, REGIONAL_ATLAS, plain)) == _dress(building_look(k, REGIONAL_ATLAS)) for k in keys)


def test_a_band_limits_the_window_families_and_counts_the_eras() -> None:
    atlas = {**REGIONAL_ATLAS, "tones": [[1.0, 1.0, 1.0]] * 3}
    layout = trim_layout(atlas)
    windows = window_families(atlas)
    stats = TrimStats()
    for i in range(12):
        node = replace(_building(), name=f"building/b{i}")
        trim_building(node, _era("1960_1979"), "central_european", atlas, layout, windows, stats)
    assert {name[:8] for name in stats.cells if name.startswith("window")} == {"window_2"}
    assert stats.eras == {"1960_1979": 12}
    # The band's tone (1960_1979: tone 1 only) reaches the look of every building.
    assert {k: v for k, v in stats.looks.items() if k.startswith("tone_")} == {"tone_1": 12}
    free = TrimStats()
    for i in range(12):
        trim_building(replace(_building(), name=f"building/b{i}"), RECORD, "central_european", atlas, layout, windows, free)
    assert {name[:8] for name in free.cells if name.startswith("window")} == {"window_0", "window_1", "window_2"}
    assert free.eras == {"unknown": 12}
    other = TrimStats()
    no_band = {**atlas, "eras": {"pre_1900": atlas["eras"]["pre_1900"]}}
    trim_building(_building(), _era("2000_plus"), "central_european", no_band, layout, windows, other)
    trim_building(_building(), _era("pre_1900"), "central_european", no_band, layout, windows, other)
    trim_building(_building(), _era("pre_1900", origin="estimated"), "central_european", no_band, layout, windows, other)
    assert other.as_dict()["eras"] == {"no_band": 1, "pre_1900": 1, "unknown": 1}
    assert list(other.as_dict())[-1] == "eras"
    # An atlas without eras counts none, and the report keeps today's keys.
    plain = TrimStats()
    trim_building(_building(), _era("pre_1900"), "mediterranean", ATLAS, LAYOUT, WINDOWS, plain)
    assert plain.eras == {} and "eras" not in plain.as_dict()
    # A building no part of which the atlas covers counts no era.
    bare = TrimStats()
    trim_building(Node("building/x", (Primitive(_back(1.2, 1.5, 4.0), "glass_view"),), {"type": "building"}),
                  _era("pre_1900"), "central_european", atlas, layout, windows, bare)
    assert bare.eras == {}


def test_an_era_band_changes_the_cache_key() -> None:
    tints = {"w": (1.0, 0.5, 0.2)}
    base = trim_cache_key("central_european", REGIONAL_ATLAS, tints, {"plaster": "x"})
    eras = {**REGIONAL_ATLAS["eras"], "pre_1900": {"tones": [1], "window_families": [0]}}
    assert trim_cache_key("central_european", {**REGIONAL_ATLAS, "eras": eras}, tints, {"plaster": "x"}) != base


MID_RECORD = {"tier": "mid", "facade": {"storeys": {"count": 2, "height_m": 3.0}}, "roof": {"type": "flat"}}


def _mid_shell(name: str = "building/m1") -> Node:
    holes = [(0.9 + 3.0 * c, 0.9 + 3.0 * r, 2.1 + 3.0 * c, 2.3 + 3.0 * r) for c in range(3) for r in range(2)]
    glass = merge([_back(1.2, 1.4, 0.9 + 3.0 * r, x0=0.9 + 3.0 * c) for c in range(3) for r in range(2)])
    return Node(name, (Primitive(facade(9.0, 6.0, holes), "scenic_wall_warm"), Primitive(glass, "glass_dark"),
                       Primitive(_back(2.0, 2.0, 6.0), "roof_tile")), {"type": "building", "object_id": name})


def test_a_mid_shell_joins_its_painted_windows_to_the_scenic_trim() -> None:
    """Plan D3: walls and glass of a mid node are one scenic_trim primitive; the windows sit in window cells."""
    stats = TrimStats()
    node = trim_building(_mid_shell(), MID_RECORD, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
    assert [p.material for p in node.primitives] == ["scenic_trim_mediterranean", "roof_tile"]
    trim = node.primitives[0]
    glass_vertices = 6 * 4
    uvs = trim.uvs[-glass_vertices:]
    assert sum(stats.cells.values()) == 6 and all(name.startswith("window_") for name in stats.cells)
    cells = [LAYOUT.cells[name] for name in stats.cells]
    for u, v in uvs:
        assert any(c.u0 - 1e-6 <= u <= c.u1 + 1e-6 and c.v0 - 1e-6 <= v <= c.v1 + 1e-6 for c in cells)
    assert stats.primitives_merged == 2


def test_without_the_mid_tier_the_same_node_keeps_its_glass_on_the_facade_trim() -> None:
    node = trim_building(_mid_shell(), {**MID_RECORD, "tier": "scenic"}, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert [p.material for p in node.primitives] == ["facade_trim_mediterranean", "scenic_trim_mediterranean", "roof_tile"]
    plain = trim_building(_mid_shell(), None, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert [p.material for p in plain.primitives] == ["facade_trim_mediterranean", "scenic_trim_mediterranean", "roof_tile"]


def test_a_mid_building_part_takes_the_scenic_trim() -> None:
    part = Node("building_part/m1/p", (Primitive(facade(6.0, 9.0, []), "plaster_ochre"),), {"type": "building_part"})
    node = trim_building(part, {"tier": "mid"}, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert [p.material for p in node.primitives] == ["scenic_trim_mediterranean"]
    core = trim_building(part, {"tier": "core"}, "mediterranean", ATLAS, LAYOUT, WINDOWS, TrimStats())
    assert [p.material for p in core.primitives] == ["facade_trim_mediterranean"]
