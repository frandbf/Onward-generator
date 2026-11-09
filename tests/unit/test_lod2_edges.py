"""Plan D6: the edges of ``detail.lod2``: thresholds, bounded reads, messages, rounding and the order of the steps."""

from __future__ import annotations

import inspect
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely import MultiPoint, Polygon, box

from onward_generator.detail import lod2 as lod2_module
from onward_generator.detail.lod2 import (
    LocalGroup,
    Lod2Block,
    Lod2Error,
    Lod2Index,
    Lod2Part,
    Lod2Settings,
    ObjGroup,
    ObjMesh,
    build_lod2_part,
    clip_to_footprint,
    cluster_simplify,
    compact,
    ground_offset,
    group_colliders,
    load_index,
    lod2_parts,
    match_landmark,
    outside_area_m2,
    outward,
    parse_obj,
    read_obj_zip,
    split_roof_wall,
    to_local,
)
from onward_generator.generation.mesh import Mesh, closed_manifold_defects, merge, prism
from onward_generator.generation.mesh import box as box_mesh
from onward_generator.generation.scene import Issue
from onward_generator.geo.projection import LocalFrame
from tests.conftest import MURCIA_CENTER
from tests.lod2_fixture import GROUND_Z, box_group, obj_text, obj_zip, write_index

MURCIA = LocalFrame.create(*MURCIA_CENTER)
FRAME = {"crs": MURCIA.crs, "unit": "m", "origin_projected": [MURCIA.origin_easting, MURCIA.origin_northing]}
INDEX = Lod2Index("idx.json", "a" * 64, "es.madrid_lod2", "Synthetic provider", "CC-BY-4.0", "Origen de los datos: test",
                  "2026-07-23", "EPSG:25830", ())
WALL, ROOF = "stone_warm", "roof_tile"
ELL = Polygon([(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)])
VALID = obj_text([("g1", box_group(0, 0, 10, 10, 12))], (440000.0, 4474000.0))


def group(name: str, mesh: Mesh, code: str = "M-000001") -> LocalGroup:
    return LocalGroup(f"{code}:{name}", code, "2026-10-29", mesh.vertices, mesh.triangles,
                      MultiPoint([tuple(p) for p in mesh.vertices[:, :2]]).convex_hull)


def build(footprint: Polygon, groups: list[LocalGroup], settings: Lod2Settings | None = None,
          max_height_m: float = 120.0) -> tuple[Lod2Part | None, list[Issue]]:
    return build_lod2_part("osm-way-1", footprint, groups, INDEX, wall_material=WALL, roof_material=ROOF,
                           max_height_m=max_height_m, settings=settings or Lod2Settings())


def grid_roof(x0: float, y0: float, size: float, n: int, z: float) -> Mesh:
    step = size / n
    xs = np.arange(n + 1) * step
    vertices = np.array([(x0 + x, y0 + y, z) for y in xs for x in xs], dtype=np.float64)
    tris = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            tris += [(a, a + 1, a + n + 2), (a, a + n + 2, a + n + 1)]
    return Mesh(vertices, np.array(tris, dtype=np.int64))


def soup(meshes: list[Mesh], dz: float = 0.0) -> list[tuple[tuple[float, ...], ...]]:
    """Every triangle as its three corners, sorted: the drawn geometry whatever the vertex order."""
    found = []
    for mesh in meshes:
        for tri in mesh.vertices[mesh.triangles]:
            found.append(tuple(tuple(round(float(c), 9) for c in (x, y, z - dz)) for x, y, z in tri))
    return sorted(found)


# ---------------------------------------------------------------------------------------------------- reading


def test_parse_defaults_are_the_settings_caps() -> None:
    parameters = inspect.signature(parse_obj).parameters
    settings = Lod2Settings()
    assert (parameters["max_vertices"].default, parameters["max_faces"].default) == (settings.max_vertices, settings.max_faces)
    assert (settings.max_vertices, settings.max_faces) == (400_000, 800_000)


def test_a_non_numeric_vertex_names_its_three_tokens() -> None:
    with pytest.raises(Lod2Error, match=r"^OBJ line 2: vertex \['1', 'x', '3'\] is not numeric$"):
        parse_obj("# header\nv 1 x 3\n")


def test_not_a_zip_has_its_own_message() -> None:
    with pytest.raises(Lod2Error, match=r"^not a valid ZIP file$"):
        read_obj_zip(b"not a zip")


def test_the_member_is_read_one_byte_past_the_cap_only(monkeypatch: pytest.MonkeyPatch) -> None:
    sizes: list[Any] = []
    real = zipfile.ZipExtFile.read

    def read(self: zipfile.ZipExtFile, n: int | None = -1) -> bytes:
        sizes.append(n)
        return real(self, n)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", read)
    read_obj_zip(obj_zip(VALID), Lod2Settings(max_unzipped_bytes=len(VALID.encode()) + 7))
    assert sizes == [len(VALID.encode()) + 8]


def test_a_member_that_fails_its_crc_cannot_be_read() -> None:
    body = bytearray(obj_zip(VALID))
    at = body.find(b"usemtl g1")
    body[at:at + 9] = b"usemtl g2"  # the stored bytes change, the CRC in the header does not
    with pytest.raises(Lod2Error, match=r"^M-000001\.obj cannot be read: Bad CRC-32"):
        read_obj_zip(bytes(body))


def test_a_member_that_yields_more_than_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    # zipfile stops at the declared size, so only a reader that does not would get here: the check after the read.
    monkeypatch.setattr(zipfile.ZipExtFile, "read", lambda self, n=-1: b"v" * n)
    size = len(VALID.encode())
    with pytest.raises(Lod2Error, match=rf"^M-000001\.obj is over {size} bytes unzipped$"):
        read_obj_zip(obj_zip(VALID), Lod2Settings(max_unzipped_bytes=size))


def test_load_index_keeps_each_block_and_reads_with_the_given_settings(tmp_path: Path) -> None:
    body = obj_zip(VALID)
    path = write_index(tmp_path / "madrid_lod2", {"M-000001": body})
    index = load_index(path)
    block = index.blocks[0]
    assert (block.path, block.url) == ("M-000001.zip", "https://geoportal.madrid.es/fsdescargas/x/M-000001.zip")
    import hashlib

    assert block.sha256 == hashlib.sha256(body).hexdigest()
    assert index.provider == "Synthetic LoD2 provider"
    with pytest.raises(Lod2Error, match="block M-000001: OBJ has more than 7 vertices"):
        load_index(path, Lod2Settings(max_vertices=7))


def test_a_block_that_is_not_an_object_is_refused_first(tmp_path: Path) -> None:
    missing = {"cod_manzan": "M-1", "path": "M-9.zip", "sha256": "0", "url": "u"}
    with pytest.raises(Lod2Error, match="a block needs cod_manzan, path, sha256 and url"):
        load_index(write_index(tmp_path / "a", {}, blocks=["row"]))
    # A non-object entry sorts before every code, so it is the one refused, not the missing block.
    with pytest.raises(Lod2Error, match="a block needs cod_manzan, path, sha256 and url"):
        load_index(write_index(tmp_path / "b", {}, blocks=[missing, "row"]))


# ---------------------------------------------------------------------------------------------------- transform


def test_a_frame_without_a_unit_is_in_metres() -> None:
    east, north = FRAME["origin_projected"]
    frame = {"crs": "EPSG:25830", "origin_projected": [east, north]}
    assert to_local(np.array([[east + 1.0, north + 2.0, 650.0]]), frame).tolist() == [[1.0, 2.0, 650.0]]


def test_the_degree_range_is_inclusive() -> None:
    message = r"^the OBJ x/y look like degrees, not EPSG:25830 metres$"
    with pytest.raises(Lod2Error, match=message):
        to_local(np.array([[180.0, 10.0, 650.0], [-180.0, 10.0, 650.0]]), FRAME)
    with pytest.raises(Lod2Error, match=message):
        to_local(np.array([[10.0, 90.0, 650.0], [10.0, -90.0, 650.0]]), FRAME)


def test_lod2_parts_pass_their_settings_on() -> None:
    east, north = FRAME["origin_projected"]
    mesh = box_group(0, 0, 10, 10, 12.0)
    obj = ObjMesh(mesh.vertices + np.array([east, north, 0.0]), (ObjGroup("g", mesh.triangles),), "EPSG:25830")
    index = Lod2Index(*[getattr(INDEX, f) for f in ("path", "sha256", "source", "provider", "license", "attribution", "data_date", "crs")],
                      (Lod2Block("M-7", "M-7.zip", "0", "u", "2026-10-29", obj),))
    parts, issues = lod2_parts({"a": box(0, 0, 10, 10)}, index, FRAME, wall_material=WALL, roof_material=ROOF, max_height_m=120.0,
                               settings=Lod2Settings(min_footprint_area_m2=101.0))
    assert parts == {} and [i.code for i in issues["a"]] == ["landmark_lod2_footprint_too_small"]


# ---------------------------------------------------------------------------------------------------- match


def test_a_group_under_one_square_metre_still_matches() -> None:
    tiny = group("tiny", box_group(1, 1, 1.5, 2, 3.0))
    found = match_landmark(box(0, 0, 10, 10), [tiny], Lod2Settings(min_footprint_cover=0.0))
    assert not isinstance(found, str) and [g.id for g in found.groups] == ["M-000001:tiny"]


def test_a_far_group_listed_first_does_not_stop_the_match() -> None:
    far = group("far", box_group(50, 50, 60, 60, 8.0))
    own = group("own", box_group(0, 0, 10, 10, 8.0))
    found = match_landmark(box(0, 0, 10, 10), [far, own], Lod2Settings())
    assert not isinstance(found, str) and [g.id for g in found.groups] == ["M-000001:own"]


# ---------------------------------------------------------------------------------------------------- simplification, compact


def test_one_cell_collapses_everything() -> None:
    out_v, out_t = cluster_simplify(box_group(0, 0, 1, 1, 1.0).vertices, box_group(0, 0, 1, 1, 1.0).triangles, 1000.0)
    assert (out_v.shape, out_t.shape, out_v.dtype, out_t.dtype) == ((0, 3), (0, 3), np.float64, np.int64)


def test_cluster_simplify_keeps_one_of_each_repeated_triangle_in_file_order() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0], [4.0, 4.0, 0.0], [0.1, 0.1, 0.0]])
    triangles = np.array([[1, 3, 2], [0, 1, 2], [4, 1, 2], [2, 1, 4]])
    out_v, out_t = cluster_simplify(vertices, triangles, 1.0)
    # 0 and 4 share a cell; [4, 1, 2] repeats [0, 1, 2] and [2, 1, 4] is the same face wound the other way.
    # Cells in key order: the merged corner, (0, 4), (4, 0), (4, 4).
    assert out_t.tolist() == [[2, 3, 1], [0, 2, 1]]
    assert out_v.tolist() == [[0.05, 0.05, 0.0], [0.0, 4.0, 0.0], [4.0, 0.0, 0.0], [4.0, 4.0, 0.0]]


def test_compact_of_nothing_is_empty_and_typed() -> None:
    out_v, out_t = compact(np.ones((4, 3)), np.zeros((0, 3), dtype=np.int64))
    assert (out_v.shape, out_t.shape, out_v.dtype, out_t.dtype) == ((0, 3), (0, 3), np.float64, np.int64)
    out_v, out_t = compact(np.arange(15, dtype=np.float64).reshape(5, 3), np.array([[4, 2, 4]]))
    assert out_v.tolist() == [[6.0, 7.0, 8.0], [12.0, 13.0, 14.0]] and out_t.tolist() == [[1, 0, 1]] and out_t.dtype == np.int64


# ---------------------------------------------------------------------------------------------------- clip and outside area


def test_clip_of_nothing_is_typed() -> None:
    out_v, out_t, dropped = clip_to_footprint(np.ones((2, 3)), np.zeros((0, 3), dtype=np.int64), box(0, 0, 1, 1), 1.0)
    assert (out_v.shape, out_t.shape, out_v.dtype, out_t.dtype, dropped) == ((0, 3), (0, 3), np.float64, np.int64, 0)


def test_clip_keeps_a_vertex_on_a_courtyard_edge_where_it_is() -> None:
    yard = box(0, 0, 20, 20).difference(box(8, 8, 12, 12))
    vertices = np.array([[8.0, 9.0, 3.0], [2.0, 2.0, 3.0], [2.0, 9.0, 3.0], [8.0, 30.0, 3.0]])
    out_v, out_t, dropped = clip_to_footprint(vertices, np.array([[0, 1, 2], [1, 2, 3]]), yard, 1.0)
    assert dropped == 1 and out_t.tolist() == [[0, 1, 2]]
    assert out_v.tolist() == [[8.0, 9.0, 3.0], [2.0, 2.0, 3.0], [2.0, 9.0, 3.0]]


def test_a_vertex_past_the_tolerance_is_not_snapped() -> None:
    footprint = box(0, 0, 10, 10)
    vertices = np.array([[1.0, 1.0, 0.0], [9.0, 1.0, 0.0], [12.0, 5.0, 0.0], [10.5, 5.0, 0.0]])
    out_v, out_t, dropped = clip_to_footprint(vertices, np.array([[0, 1, 2], [0, 1, 3]]), footprint, 1.0)
    assert dropped == 1 and out_t.tolist() == [[0, 1, 2]]
    assert out_v.tolist() == [[1.0, 1.0, 0.0], [9.0, 1.0, 0.0], [10.0, 5.0, 0.0]]


def test_outside_area_counts_a_small_triangle_and_rounds_to_a_micro_square_metre() -> None:
    a = 0.2469135782
    small = np.array([[20.0, 0.0, 0.0], [20.0 + a, 0.0, 0.0], [20.0, 1.0, 0.0]])
    assert outside_area_m2(small, np.array([[0, 1, 2]]), box(0, 0, 10, 10)) == 0.123457
    assert outside_area_m2(np.array([[20.0, 0.0, 0.0], [21.0, 0.0, 0.0], [20.0, 1.0, 0.0]]), np.array([[0, 1, 2]]), box(0, 0, 10, 10)) == 0.5


# ---------------------------------------------------------------------------------------------------- ground, roof, winding


def test_the_ground_column_is_read_at_its_first_vertex() -> None:
    footprint = box(0, 0, 10, 10)
    edge_first = np.array([[10.0, 4.0, 652.0], [10.0, 4.0, 657.0], [5.0, 5.0, 650.0], [6.0, 6.0, 650.5]])
    assert ground_offset(edge_first, footprint, 1.0) == (652.0, 650.0)


def test_a_column_of_exactly_one_metre_is_a_wall_and_one_metre_from_the_edge_is_outer() -> None:
    footprint = box(0, 0, 10, 10)
    assert ground_offset(np.array([[10.0, 4.0, 651.0], [10.0, 4.0, 652.0], [5.0, 5.0, 650.0]]), footprint, 1.0) == (651.0, 650.0)
    assert ground_offset(np.array([[9.0, 4.0, 652.0], [9.0, 4.0, 657.0], [5.0, 5.0, 650.0]]), footprint, 1.0) == (652.0, 650.0)
    assert ground_offset(np.array([[8.9, 4.0, 652.0], [8.9, 4.0, 657.0], [5.0, 5.0, 650.0]]), footprint, 1.0) == (650.0, 650.0)


def test_a_small_upward_triangle_is_roof() -> None:
    vertices = np.array([[0.0, 0.0, 0.0], [0.1, 0.0, 0.0], [0.0, 0.1, 0.0]])
    roof, wall = split_roof_wall(vertices, np.array([[0, 1, 2]]), 0.35)
    assert (len(roof), len(wall)) == (1, 0)


def test_a_flat_or_small_outward_mesh_keeps_its_winding() -> None:
    flat = np.array([[0, 1, 2]])
    assert outward(np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]), flat) is flat
    small = box_mesh(0, 0.5, 0, 1, 0, 1)
    assert outward(small.vertices, small.triangles) is small.triangles


# ---------------------------------------------------------------------------------------------------- colliders


def test_a_small_group_gets_its_collider() -> None:
    colliders, issues = group_colliders(box(0, 0, 10, 10), [("s", box(0, 0, 0.5, 1), 0.5)], "osm-way-1")
    assert issues == [] and len(colliders) == 1
    assert colliders[0].vertices[:, 2].max() == 0.5


def test_a_group_of_no_height_says_so() -> None:
    colliders, issues = group_colliders(box(0, 0, 10, 10), [("flat", box(0, 0, 5, 5), 0.0)], "osm-way-1")
    assert colliders == []
    assert [(i.code, i.severity, i.object_id, i.message) for i in issues] == [
        ("landmark_lod2_collider_not_closed", "critical", "osm-way-1", "flat: no closed collider (empty plan or height)")]


def test_a_defective_prism_is_reported_and_the_next_part_is_still_built(monkeypatch: pytest.MonkeyPatch) -> None:
    open_box = box_mesh(0, 10, 0, 10, 0, 4, omit=("+z",))
    calls: list[int] = []

    def flaky(part: Polygon, z0: float, z1: float, *, bottom: bool) -> Mesh:
        calls.append(1)
        return open_box if len(calls) == 1 else prism(part, z0, z1, bottom=bottom)

    monkeypatch.setattr(lod2_module, "prism", flaky)
    two = box(0, 0, 10, 10).union(box(20, 0, 30, 10))
    colliders, issues = group_colliders(two, [("g", box(0, 0, 30, 10), 4.0)], "osm-way-1")
    assert len(calls) == 2 and len(colliders) == 1 and closed_manifold_defects(colliders[0]) == 0
    defects = closed_manifold_defects(open_box)
    assert defects > 0
    assert [(i.code, i.severity, i.object_id, i.message) for i in issues] == [
        ("landmark_lod2_collider_not_closed", "critical", "osm-way-1", f"g: {defects} collider defects")]


def test_a_closed_prism_without_volume_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    sheet = Mesh(np.array([[0.0, 0.0, 0.0], [5.0, 0.0, 0.0], [0.0, 5.0, 0.0]]), np.array([[0, 1, 2], [0, 2, 1]]))
    assert closed_manifold_defects(sheet) == 0
    monkeypatch.setattr(lod2_module, "prism", lambda part, z0, z1, *, bottom: sheet)
    colliders, issues = group_colliders(box(0, 0, 10, 10), [("g", box(0, 0, 5, 5), 4.0)], "osm-way-1")
    assert colliders == [] and [(i.code, i.message) for i in issues] == [("landmark_lod2_collider_not_closed", "g: 0 collider defects")]


# ---------------------------------------------------------------------------------------------------- the part


def test_each_refusal_names_the_building() -> None:
    _, issues = build(box(0, 0, 4, 4), [group("g", box_group(0, 0, 4, 4, 8.0))])
    assert [(i.code, i.object_id) for i in issues] == [("landmark_lod2_footprint_too_small", "osm-way-1")]
    house = merge([box_mesh(0, 10, 0, 10, GROUND_Z, GROUND_Z + 8.0, omit=("-z", "+z")), grid_roof(0, 0, 10, 8, GROUND_Z + 8.0)])
    _, issues = build(box(0, 0, 10, 10), [group("g", house)], Lod2Settings(max_triangles=135, max_simplify_passes=3))
    assert [(i.code, i.object_id) for i in issues] == [("landmark_lod2_over_cap", "osm-way-1")]
    spikes = Mesh(np.array([[1, 1, 650], [9, 1, 650], [5, 13, 660], [1, 9, 650], [9, 9, 650], [5, -3, 660]], dtype=np.float64),
                  np.array([[0, 1, 2], [3, 5, 4]]))
    _, issues = build(box(0, 0, 10, 10), [group("g", spikes)], Lod2Settings(min_footprint_cover=0.0, min_group_overlap=0.0))
    assert [(i.code, i.severity, i.object_id) for i in issues] == [("landmark_lod2_no_match", "info", "osm-way-1")]


def test_a_volume_of_no_height_is_refused_and_one_of_half_a_metre_is_kept() -> None:
    part, issues = build(box(0, 0, 10, 10), [group("flat", grid_roof(0, 0, 10, 1, GROUND_Z))])
    assert part is None
    assert [(i.code, i.object_id, i.message) for i in issues] == [
        ("landmark_lod2_height_out_of_range", "osm-way-1", "LoD2 height 0.0 m is outside (0, 120]; the placeholder stays")]
    part, issues = build(box(0, 0, 10, 10), [group("low", box_group(0, 0, 10, 10, 0.5))])
    assert issues == [] and part is not None and part.record["ground_offset"]["height_m"] == 0.5


def test_a_flat_group_beside_the_walls_gets_a_collider_issue_with_the_building() -> None:
    walls = group("walls", box_group(0, 0, 10, 10, 8.0))
    flat = group("flat", grid_roof(0, 0, 10, 1, GROUND_Z))
    part, issues = build(box(0, 0, 10, 10), [walls, flat])
    assert part is not None and len(part.colliders) == 1
    assert [(i.code, i.severity, i.object_id, i.message) for i in issues] == [
        ("landmark_lod2_collider_not_closed", "critical", "osm-way-1", "M-000001:flat: no closed collider (empty plan or height)")]


def test_three_groups_keep_their_own_triangles() -> None:
    meshes = [box_group(0, 0, 4, 10, 6.0), box_group(4, 0, 8, 10, 9.0), box_group(8, 0, 12, 10, 12.0)]
    part, issues = build(box(0, 0, 12, 10), [group(n, m) for n, m in zip("abc", meshes, strict=True)])
    assert issues == [] and part is not None
    assert part.record["clip"]["floor_triangles"] == 0
    assert soup([p.mesh for p in part.visual]) == soup(meshes, GROUND_Z)
    assert sorted(m.vertices[:, 2].max() for m in part.colliders) == [6.0, 9.0, 12.0]


def test_on_a_slope_the_low_walls_end_at_the_street() -> None:
    low = box_group(0, 0, 5, 10, 12.0)
    high = box_group(5, 0, 10, 10, 12.0, base=2.0)
    part, _ = build(box(0, 0, 10, 10), [group("low", low), group("high", high)])
    assert part is not None
    visual = merge([p.mesh for p in part.visual])
    assert visual.triangle_count == 20
    assert sorted({float(z) for x, _, z in visual.vertices if x == 0.0}) == [0.0, 10.0]


def test_a_pass_that_lands_on_the_cap_is_kept() -> None:
    house = merge([box_mesh(0, 10, 0, 10, GROUND_Z, GROUND_Z + 8.0, omit=("-z", "+z")), grid_roof(0, 0, 10, 8, GROUND_Z + 8.0)])
    shifted = house.vertices - np.array([0.0, 0.0, GROUND_Z])
    landed = len(cluster_simplify(shifted, house.triangles, 2.0)[1])
    part, issues = build(box(0, 0, 10, 10), [group("g", house)], Lod2Settings(max_triangles=landed))
    assert issues == [] and part is not None
    assert part.record["simplification"]["cell_m"] == 2.0 and part.record["simplification"]["triangles_after"] == landed


def test_a_cell_under_a_metre_is_a_successful_pass() -> None:
    house = merge([box_mesh(0, 10, 0, 10, GROUND_Z, GROUND_Z + 8.0, omit=("-z", "+z")), grid_roof(0, 0, 10, 40, GROUND_Z + 8.0)])
    part, issues = build(box(0, 0, 10, 10), [group("g", house)], Lod2Settings(max_triangles=3000, simplify_cell_m=0.5))
    assert issues == [] and part is not None
    assert part.record["simplification"]["cell_m"] == 0.5 and part.record["simplification"]["triangles_after"] < 3208


def test_the_simplified_mesh_is_clipped_again() -> None:
    walls = box_mesh(0, 20, 0, 20, GROUND_Z, GROUND_Z + 8.0, omit=("-z", "+z"))
    mesh = merge([walls, grid_roof(0, 0, 20, 16, GROUND_Z + 8.0)])
    settings = Lod2Settings(max_triangles=200, simplify_cell_m=2.0)
    part, issues = build(ELL, [group("g", mesh)], settings)
    assert issues == [] and part is not None
    triangles = outward(mesh.vertices, mesh.triangles)
    v, t, first = clip_to_footprint(mesh.vertices - np.array([0.0, 0.0, GROUND_Z]), triangles, ELL, 1.0)
    sv, st = cluster_simplify(v, t, 2.0)
    _, _, second = clip_to_footprint(sv, st, ELL, 1.0)
    assert first > 0 and second > 0
    assert outside_area_m2(sv, st, ELL) > 0.0  # what the second clip removes
    clip = part.record["clip"]
    assert (clip["dropped_triangles"], clip["outside_area_m2"]) == (first + second, 0.0)
    assert part.record["simplification"]["cell_m"] == 2.0


def test_walls_without_a_roof_or_a_floor_are_one_wall_primitive() -> None:
    tube = box_group(0, 0, 10, 10, 8.0)
    tube = Mesh(tube.vertices, tube.triangles[np.abs(tube.face_normals()[:, 2]) < 1e-9])
    part, issues = build(box(0, 0, 10, 10), [group("tube", tube)])
    assert issues == [] and part is not None
    assert [p.material for p in part.visual] == [WALL]


def test_a_small_uncovered_strip_gets_a_floor() -> None:
    part, _ = build(box(0, 0, 10, 10), [group("g", box_group(0, 0, 10, 9.95, 8.0))])
    assert part is not None and part.record["clip"]["floor_triangles"] == 2
    roof = next(p.mesh for p in part.visual if p.material == ROOF)
    floor = roof.face_normals()[roof.vertices[roof.triangles][:, :, 2].max(axis=1) == 0.0]
    assert len(floor) == 2 and (floor[:, 2] > 0).all()  # facing up, toward the player


def test_the_record_rounds_what_it_estimates() -> None:
    part, issues = build(box(0, 0, 10, 10), [group("g", box_group(0, 0, 10, 8.3333, 12.4691, base=0.1234))])
    assert issues == [] and part is not None
    ground = part.record["ground_offset"]
    assert part.record["match"]["footprint_cover"] == 0.8333
    assert (ground["orthometric_m"], ground["lowest_orthometric_m"], ground["height_m"]) == (650.123, 650.123, 12.346)
    assert part.colliders[0].vertices[:, 2].max() == pytest.approx(12.3457)
