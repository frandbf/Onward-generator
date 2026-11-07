"""A UV phase per combine cell for the map-wide ground surfaces (plan C1, ADR-009 "Repetition per cell").

Local metres around the Murcia centre (MURCIA_CENTER is the local origin).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import (
    CellPhase,
    Node,
    Primitive,
    build_glb,
    cell_uv_phase,
    island_cell_phases,
    nodes_from_glb,
    read_glb,
    recentre_uvs,
    surface_uvs,
)
from onward_generator.generation.mesh import Mesh
from onward_generator.tiers import combine_cell
from tests.conftest import MURCIA_CENTER

GROUND = frozenset({"carriageway", "sidewalk", "pedestrian", "cobblestone_paving"})
assert MURCIA_CENTER  # the local frame's origin


def _quad(x0: float, y0: float, x1: float, y1: float, z: float = 0.0) -> Mesh:
    vertices = np.array([[x0, y0, z], [x1, y0, z], [x1, y1, z], [x0, y1, z]], dtype=np.float64)
    return Mesh(vertices, np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int64))


def _merge(*meshes: Mesh) -> Mesh:
    vertices, triangles, offset = [], [], 0
    for mesh in meshes:
        vertices.append(mesh.vertices)
        triangles.append(mesh.triangles + offset)
        offset += len(mesh.vertices)
    return Mesh(np.concatenate(vertices), np.concatenate(triangles))


def test_the_phase_is_the_sha256_of_material_and_cell() -> None:
    assert cell_uv_phase("sidewalk", (0, 0)) == (179 / 255.0, 78 / 255.0)
    assert cell_uv_phase("sidewalk", (-1, 2)) == (57 / 255.0, 206 / 255.0)
    assert cell_uv_phase("sidewalk", (0, 0)) == cell_uv_phase("sidewalk", (0, 0))
    assert cell_uv_phase("carriageway", (0, 0)) != cell_uv_phase("sidewalk", (0, 0))
    assert cell_uv_phase("sidewalk", (1, 0)) != cell_uv_phase("sidewalk", (0, 0))
    assert cell_uv_phase("sidewalk", (0, 1)) != cell_uv_phase("sidewalk", (1, 0))


@pytest.mark.parametrize("bounds", [
    (10.0, 10.0, 20.0, 20.0),
    (-30.0, -40.0, -10.0, -5.0),
    (-250.0, 120.0, -220.0, 180.0),
    (90.0, -110.0, 110.0, -90.0),   # centre exactly on 100 m east and -(-100) north
    (199.0, 0.0, 201.0, 2.0),       # centre exactly on 200 m
    (5.0, -2.0, 7.0, 0.0),          # centre at -y = +1
    (5.0, 0.0, 7.0, 2.0),           # centre at -y = -1
])
def test_the_island_cell_is_the_adapter_combine_cell(bounds: tuple[float, float, float, float]) -> None:
    mesh = _quad(*bounds)
    phase = island_cell_phases(mesh.vertices, mesh.triangles, "sidewalk", 100.0)
    cell = combine_cell([Primitive(mesh, "sidewalk")], 100.0)
    assert np.array_equal(phase, np.tile(cell_uv_phase("sidewalk", cell), (4, 1)))


def test_the_cell_follows_the_cell_size() -> None:
    mesh = _quad(60.0, -10.0, 80.0, -5.0)
    assert island_cell_phases(mesh.vertices, mesh.triangles, "sidewalk", 50.0)[0].tolist() == list(cell_uv_phase("sidewalk", (1, 0)))
    assert island_cell_phases(mesh.vertices, mesh.triangles, "sidewalk", 100.0)[0].tolist() == list(cell_uv_phase("sidewalk", (0, 0)))


def test_islands_take_the_phase_of_their_own_cell_and_shift_rigidly() -> None:
    near, same, far = _quad(10, -20, 14, -16), _quad(40, -60, 44, -56), _quad(130, -20, 134, -16)
    mesh = _merge(near, same, far)
    phase = island_cell_phases(mesh.vertices, mesh.triangles, "pedestrian", 100.0)
    assert np.array_equal(phase[:8], np.tile(cell_uv_phase("pedestrian", (0, 0)), (8, 1)))
    assert np.array_equal(phase[8:], np.tile(cell_uv_phase("pedestrian", (1, 0)), (4, 1)))
    assert not np.array_equal(phase[0], phase[8])


def test_a_strip_over_three_cells_takes_its_centre_cell() -> None:
    strip = _quad(-120.0, -2.0, 130.0, 2.0)
    phase = island_cell_phases(strip.vertices, strip.triangles, "carriageway", 100.0)
    assert np.array_equal(phase, np.tile(cell_uv_phase("carriageway", (0, 0)), (4, 1)))


def test_an_empty_mesh_has_no_phase() -> None:
    assert island_cell_phases(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), "sidewalk", 100.0).shape == (0, 2)


def test_after_recentring_the_fraction_is_the_planar_uv_plus_the_phase() -> None:
    mesh = _merge(_quad(310.0, -415.0, 318.0, -409.0), _quad(-512.0, 233.0, -505.0, 240.0))
    planar = surface_uvs(mesh, 2.0).astype(np.float64)
    phase = island_cell_phases(mesh.vertices, mesh.triangles, "sidewalk", 100.0)
    out = recentre_uvs(planar + phase, mesh.triangles)
    assert np.allclose(np.mod(out, 1.0), np.mod(planar + phase, 1.0), atol=1e-9)
    assert out.min() >= 0.0 and out.max() < 8.0
    # Inside each island, UV differences are unchanged.
    assert np.allclose(out[1:4] - out[0], planar[1:4] - planar[0])


@pytest.mark.parametrize("cell_m", [0.0, -5.0, math.nan, math.inf])
def test_a_cell_size_that_is_not_a_finite_positive_number_is_refused(cell_m: float) -> None:
    with pytest.raises(ValueError, match=r"cell phase needs a finite cell size above 0 m, got"):
        CellPhase(cell_m, GROUND)


def test_a_small_positive_cell_is_accepted() -> None:
    assert CellPhase(1e-3, GROUND).cell_m == 1e-3


MATERIALS = {name: Material((0.5, 0.5, 0.5, 1.0), 0.9) for name in
             ("sidewalk", "carriageway", "ground", "green", "door_wood", "roof_tile", "plaster_white")}
IMAGES = {name: f"png-{name}".encode() for name in MATERIALS}


def _nodes() -> list[Node]:
    two_cells = _merge(_quad(10, -20, 14, -16), _quad(130, -20, 134, -16))
    placed = np.eye(4)
    placed[0, 3] = 250.0
    return [
        Node("surface/sidewalk", (Primitive(two_cells, "sidewalk"),), {"type": "surface"}),
        Node("surface/carriageway", (Primitive(_quad(-130, 5, -110, 9), "carriageway"),), {"type": "surface"}),
        Node("surface/ground", (Primitive(_quad(-30, -30, -20, -20), "ground"),), {"type": "surface"}),
        Node("surface/green", (Primitive(_quad(-60, -30, -50, -20), "green"),), {"type": "surface"}),
        Node("prop/placed", (Primitive(_quad(0, 0, 3, 3), "sidewalk"),), {"type": "urban_element"}, matrix=placed),
        Node("building/b", (Primitive(_quad(150, 150, 160, 160), "sidewalk"), Primitive(_quad(0, 0, 1, 2, 3.0), "door_wood"),
                            Primitive(_quad(150, 150, 160, 160, 9.0), "roof_tile")),
             {"type": "building", "uv_phase": [0.25, 0.5]}),
    ]


def _build(cell_phase: CellPhase | None = None, *, default: bool = False) -> bytes:
    kwargs = {} if default else {"cell_phase": cell_phase}
    return build_glb(_nodes(), MATERIALS, repeat_images=IMAGES, repeat_tile_m={"sidewalk": 2.0, "carriageway": 10.0},
                     unit_uv_materials={"door_wood"}, roof_uv_materials={"roof_tile"}, **kwargs)


def _same_fraction(a: np.ndarray, b: np.ndarray) -> bool:
    """True when ``a`` and ``b`` differ by whole repeats (float32 tolerance, either side of a wrap)."""
    gap = np.mod(np.asarray(a, dtype=np.float64) - np.asarray(b, dtype=np.float64), 1.0)
    return bool(np.all(np.minimum(gap, 1.0 - gap) < 1e-5))


def _uvs(data: bytes) -> dict[str, list[np.ndarray]]:
    nodes, _ = nodes_from_glb(data)
    return {node.name: [np.asarray(p.uvs) for p in node.primitives] for node in nodes}


def test_without_a_cell_phase_the_bytes_are_unchanged() -> None:
    assert _build(default=True) == _build(None)


def test_the_cell_phase_changes_only_the_ground_texcoords() -> None:
    off, on = _build(None), _build(CellPhase(100.0, frozenset({"sidewalk", "carriageway"})))
    doc_off, doc_on = read_glb(off)[0], read_glb(on)[0]
    for key in ("materials", "images", "textures", "meshes", "nodes", "samplers"):
        assert doc_off[key] == doc_on[key], key
    assert [a["count"] for a in doc_off["accessors"]] == [a["count"] for a in doc_on["accessors"]]
    assert len(off) == len(on)
    uv_off, uv_on = _uvs(off), _uvs(on)
    sidewalk = uv_on["surface/sidewalk"][0]
    assert not np.allclose(sidewalk, uv_off["surface/sidewalk"][0])
    planar = uv_off["surface/sidewalk"][0].astype(np.float64)
    for rows, cell in ((slice(0, 4), (0, 0)), (slice(4, 8), (1, 0))):
        assert _same_fraction(sidewalk[rows], planar[rows] + np.array(cell_uv_phase("sidewalk", cell)))
    assert not _same_fraction(sidewalk[0:4], planar[0:4] + np.array(cell_uv_phase("sidewalk", (1, 0))))
    carriageway = uv_on["surface/carriageway"][0]
    assert _same_fraction(carriageway, uv_off["surface/carriageway"][0] + np.array(cell_uv_phase("carriageway", (-2, -1))))
    # The open ground and the green, a placed node, a building with its own phase (ADR-012), the door and the roof keep their UVs.
    for name in ("surface/ground", "surface/green", "prop/placed", "building/b"):
        for before, after in zip(uv_off[name], uv_on[name], strict=True):
            assert np.array_equal(before, after), name


def test_two_builds_with_a_cell_phase_are_the_same_bytes() -> None:
    phase = CellPhase(100.0, GROUND)
    assert _build(phase) == _build(phase)
