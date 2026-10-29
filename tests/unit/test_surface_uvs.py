"""Repeating surface UVs stay on the wall plane and scale with metres."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import Polygon

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import (
    Node,
    Primitive,
    build_glb,
    nodes_from_glb,
    read_glb,
    recentre_uvs,
    surface_uvs,
    uv_islands,
)
from onward_generator.generation.mesh import Mesh, cap, merge, quad, walls
from onward_generator.materials.openings import window_png
from onward_generator.materials.plan import load_texture_plan


def test_tall_wall_repeats_instead_of_folding_into_one_tile() -> None:
    mesh = Mesh(
        np.array([[0.0, 0.0, 0.0], [8.0, 0.0, 0.0], [8.0, 0.0, 30.0], [0.0, 0.0, 30.0]], dtype=np.float64),
        np.array([[0, 1, 2], [0, 2, 3]]),
    )
    uvs = surface_uvs(mesh, 2.0)
    assert float(uvs[:, 0].max() - uvs[:, 0].min()) == pytest.approx(4.0, abs=0.05)
    assert float(uvs[:, 1].max() - uvs[:, 1].min()) == pytest.approx(15.0, abs=0.05)


def test_diagonal_wall_keeps_height_on_v() -> None:
    mesh = Mesh(
        np.array([[0.0, 0.0, 0.0], [10.0, 10.0, 0.0], [10.0, 10.0, 8.0], [0.0, 0.0, 8.0]], dtype=np.float64),
        np.array([[0, 1, 2], [0, 2, 3]]),
    )
    uvs = surface_uvs(mesh, 2.0)
    assert float(uvs[:, 1].max() - uvs[:, 1].min()) == pytest.approx(4.0, abs=0.05)
    assert float(uvs[:, 0].max() - uvs[:, 0].min()) == pytest.approx(np.hypot(10.0, 10.0) / 2.0, abs=0.05)


def test_each_opening_fits_unit_square() -> None:
    mesh = merge([
        quad((0.0, 0.0, 0.0), (1.2, 0.0, 0.0), (1.2, 0.0, 1.8), (0.0, 0.0, 1.8)),
        quad((20.0, 0.0, 3.0), (21.0, 0.0, 3.0), (21.0, 0.0, 4.6), (20.0, 0.0, 4.6)),
    ])
    uvs = surface_uvs(mesh, 2.0, fit_unit=True)
    assert uvs.min() >= -1e-5
    assert uvs.max() <= 1.0 + 1e-5
    assert float(uvs[:4].max() - uvs[:4].min()) == pytest.approx(1.0, abs=1e-4)
    assert float(uvs[4:].max() - uvs[4:].min()) == pytest.approx(1.0, abs=1e-4)


def test_repeat_images_are_separate_textures() -> None:
    wall = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 0.0, 6.0], [0.0, 0.0, 6.0]], dtype=np.float64),
        np.array([[0, 1, 2], [0, 2, 3]]),
    )
    png = window_png(32)
    data = build_glb(
        [Node("wall", (Primitive(wall, "plaster_ochre"), Primitive(wall, "glass_dark")))],
        {
            "plaster_ochre": Material((1.0, 1.0, 1.0, 1.0), 0.9),
            "glass_dark": Material((1.0, 1.0, 1.0, 1.0), 0.2),
        },
        repeat_images={"plaster_ochre": png, "glass_dark": png},
        repeat_tile_m={"plaster_ochre": 2.0, "glass_dark": 1.0},
        unit_uv_materials={"glass_dark"},
    )
    doc, _ = read_glb(data)
    assert len(doc["images"]) == 2
    assert doc["samplers"][0]["wrapS"] == 10497
    by_name = {m["name"]: m for m in doc["materials"]}
    assert by_name["plaster_ochre"]["pbrMetallicRoughness"]["baseColorTexture"]["index"] != (
        by_name["glass_dark"]["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    )


def test_library_plan_colours_the_plaster_slots(tmp_path: Path) -> None:
    from onward_generator.materials.library_plan import select_texture_plan

    path = tmp_path / "texture_plan.json"
    path.write_text(json.dumps(select_texture_plan(
        name="Las Condes", map_id="lascondes", latitude=-33.40, longitude=-70.56, brief=None, seed=2,
    )), encoding="utf-8")
    plan = load_texture_plan(path)
    assert plan.by_id["ochre_facades"].catalog_ids == ("plaster_ochre_wall",)
    assert plan.by_id["plain_facades"].catalog_ids == ("plaster_white_wall",)
    assert plan.by_id["plain_facades"].albedo_mode == "color_albedo"
    assert plan.by_id["historic_facades"].library_id
    assert plan.by_id["brick"].catalog_ids == ("brick_red_wall",)
    assert "asphalt_road" not in plan.by_id["streets_plazas"].catalog_ids


GREY = Material((1.0, 1.0, 1.0, 1.0), 0.9)
PNG = window_png(16)
PITCH = math.radians(22.0)


def _far_wall(x0: float) -> Mesh:
    return quad((x0, 3000.0, 0.0), (x0 + 9.0, 3000.0, 0.0), (x0 + 9.0, 3000.0, 7.0), (x0, 3000.0, 7.0))


def _exported_uvs(nodes: list[Node], **kwargs: object) -> dict[str, np.ndarray]:
    data = build_glb(nodes, {p.material: GREY for n in nodes for p in n.primitives}, **kwargs)  # type: ignore[arg-type]
    read, _ = nodes_from_glb(data)
    return {f"{n.name}:{p.material}": np.asarray(p.uvs, dtype=np.float64) for n in read for p in n.primitives}


def _fraction_gap(a: np.ndarray, b: np.ndarray) -> float:
    """Largest distance between fractional parts, measured around the unit circle."""
    gap = np.abs(np.mod(a, 1.0) - np.mod(b, 1.0))
    return float(np.minimum(gap, 1.0 - gap).max())


def test_islands_follow_shared_vertices() -> None:
    triangles = np.array([[0, 1, 2], [2, 3, 4], [5, 6, 7], [8, 9, 5]])
    label = uv_islands(triangles, 11)
    assert len(set(label[[0, 1, 2, 3, 4]].tolist())) == 1
    assert len(set(label[[5, 6, 7, 8, 9]].tolist())) == 1
    assert label[0] != label[5]
    assert label[10] not in set(label[:10].tolist())
    assert uv_islands(np.zeros((0, 3), dtype=np.int64), 3).tolist() == [0, 1, 2]


def test_recentring_keeps_fractions_and_bounds_each_island() -> None:
    mesh = merge([_far_wall(4000.0), _far_wall(-2500.0)])
    raw = surface_uvs(mesh, 2.0).astype(np.float64)
    assert float(np.abs(raw).max()) > 1000.0
    shifted = recentre_uvs(raw, mesh.triangles)
    assert _fraction_gap(shifted, raw) < 1e-9
    for island in (slice(0, 4), slice(4, 8)):
        extent = raw[island].max(axis=0) - raw[island].min(axis=0)
        assert (shifted[island].min(axis=0) >= 0.0).all()
        assert (shifted[island].max(axis=0) <= extent + 1.0).all()
    assert np.allclose(shifted[0:4] - shifted[0:4].min(axis=0), raw[0:4] - raw[0:4].min(axis=0))


def test_exported_repeat_uvs_are_small_and_keep_the_building_phase() -> None:
    wall = _far_wall(4000.0)
    phase = (0.3, 0.7)
    node = Node("building/1", (Primitive(wall, "plaster_ochre"),), {"type": "building", "uv_phase": list(phase)})
    first = _exported_uvs([node], repeat_images={"plaster_ochre": PNG}, repeat_tile_m={"plaster_ochre": 2.0})
    uvs = first["building/1:plaster_ochre"]
    reference = surface_uvs(wall, 2.0).astype(np.float64) + np.array(phase)
    extent = reference.max(axis=0) - reference.min(axis=0)
    assert float(np.abs(uvs).max()) <= float(extent.max()) + 1.0
    # float32 at a few repeats keeps about 1e-6 of a tile; the old absolute value kept about 1e-4.
    assert _fraction_gap(uvs, reference) < 1e-5
    again = _exported_uvs([node], repeat_images={"plaster_ochre": PNG}, repeat_tile_m={"plaster_ochre": 2.0})
    assert np.array_equal(again["building/1:plaster_ochre"], uvs)


def test_openings_still_fit_the_unit_square_after_recentring() -> None:
    window = quad((5000.0, 0.0, 3.0), (5001.2, 0.0, 3.0), (5001.2, 0.0, 4.8), (5000.0, 0.0, 4.8))
    node = Node("building/2", (Primitive(window, "glass_dark"),), {"type": "building", "uv_phase": [0.5, 0.5]})
    uvs = _exported_uvs(
        [node], repeat_images={"glass_dark": PNG}, repeat_tile_m={"glass_dark": 1.0}, unit_uv_materials={"glass_dark"},
    )["building/2:glass_dark"]
    assert float(uvs.min()) >= 0.0
    assert float(uvs.max()) == pytest.approx(1.0)


def _gable(run: float = 5.0, length: float = 10.0) -> Mesh:
    rise = math.tan(PITCH) * run
    return merge([
        quad((0.0, 0.0, 6.0), (length, 0.0, 6.0), (length, run, 6.0 + rise), (0.0, run, 6.0 + rise)),
        quad((length, 2 * run, 6.0), (0.0, 2 * run, 6.0), (0.0, run, 6.0 + rise), (length, run, 6.0 + rise)),
    ])


def _hip(side: float = 8.0) -> Mesh:
    rise = math.tan(PITCH) * side / 2.0
    apex = (side / 2.0, side / 2.0, 6.0 + rise)
    ring = [(0.0, 0.0), (side, 0.0), (side, side), (0.0, side), (0.0, 0.0)]
    vertices: list[tuple[float, float, float]] = []
    for (ax, ay), (bx, by) in zip(ring[:-1], ring[1:], strict=True):
        vertices += [(ax, ay, 6.0), (bx, by, 6.0), apex]
    return Mesh(np.array(vertices, dtype=np.float64), np.arange(len(vertices)).reshape(-1, 3))


def _stretch(mesh: Mesh, uvs: np.ndarray, tile_m: float) -> float:
    """Largest relative error between UV edge length × tile and the 3D edge length."""
    worst = 0.0
    for tri in mesh.triangles:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            metres = float(np.linalg.norm(mesh.vertices[tri[b]] - mesh.vertices[tri[a]]))
            repeats = float(np.linalg.norm(uvs[tri[b]] - uvs[tri[a]]))
            worst = max(worst, abs(repeats * tile_m / metres - 1.0))
    return worst


@pytest.mark.parametrize("mesh", [_gable(), _hip()], ids=["gable", "hip"])
def test_roof_uvs_measure_metres_along_a_22_degree_slope(mesh: Mesh) -> None:
    assert float(np.abs(mesh.face_normals()[:, 2] / np.linalg.norm(mesh.face_normals(), axis=1)).min()) > 0.75
    roof = surface_uvs(mesh, 1.2, roof=True)
    assert _stretch(mesh, roof, 1.2) < 0.01
    assert _stretch(mesh, surface_uvs(mesh, 1.2), 1.2) > 0.03


def test_roof_v_runs_up_the_slope() -> None:
    mesh = _gable()
    uvs = surface_uvs(mesh, 1.2, roof=True)
    eave, ridge = uvs[0, 1], uvs[3, 1]
    assert ridge - eave == pytest.approx(5.0 / math.cos(PITCH) / 1.2, rel=1e-3)
    assert abs(float(uvs[1, 0] - uvs[0, 0])) == pytest.approx(10.0 / 1.2, rel=1e-3)
    assert uvs[1, 1] == pytest.approx(eave)


def test_flat_roofs_and_walls_are_unchanged_by_the_roof_rule() -> None:
    footprint = Polygon([(0.0, 0.0), (12.0, 0.0), (12.0, 7.0), (5.0, 9.0), (0.0, 7.0)])
    for mesh in (cap(footprint, 9.0), walls(footprint, 0.0, 9.0)):
        assert surface_uvs(mesh, 1.2, roof=True).tobytes() == surface_uvs(mesh, 1.2).tobytes()


def test_nearly_flat_roof_and_sloped_ground_stay_planar() -> None:
    nearly_flat = quad((0.0, 0.0, 0.0), (6.0, 0.0, 0.0), (6.0, 6.0, 0.5), (0.0, 6.0, 0.5))
    planar = np.column_stack([nearly_flat.vertices[:, 0], nearly_flat.vertices[:, 1]]) / 2.0
    assert np.allclose(surface_uvs(nearly_flat, 2.0, roof=True), planar)
    sloped = quad((0.0, 0.0, 0.0), (6.0, 0.0, 0.0), (6.0, 6.0, 2.4), (0.0, 6.0, 2.4))
    assert np.allclose(surface_uvs(sloped, 2.0), np.column_stack([sloped.vertices[:, 0], sloped.vertices[:, 1]]) / 2.0)


def test_build_glb_aligns_only_roof_materials() -> None:
    mesh = _gable()
    nodes = [Node("roof", (Primitive(mesh, "roof_tile"),)), Node("ramp", (Primitive(mesh, "ground"),))]
    images = {"roof_tile": PNG, "ground": PNG}
    uvs = _exported_uvs(nodes, repeat_images=images, repeat_tile_m={"roof_tile": 1.2, "ground": 1.2},
                        roof_uv_materials={"roof_tile"})
    assert _stretch(mesh, uvs["roof:roof_tile"], 1.2) < 0.01
    assert _stretch(mesh, uvs["ramp:ground"], 1.2) > 0.05
