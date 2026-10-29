"""Ground height under a prop: the lowered carriageway cap, else z = 0.

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

import numpy as np
import pytest
from shapely import box

from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh, cap
from onward_generator.overgrow.ground import CARRIAGEWAY_NODE, RoadFloor, road_floor

ROAD_Z = -0.15


def _carriageway(name: str = CARRIAGEWAY_NODE, matrix: np.ndarray | None = None, mesh: Mesh | None = None) -> Node:
    return Node(name, (Primitive(mesh if mesh is not None else cap(box(0.0, -3.0, 40.0, 3.0), ROAD_Z), "asphalt"),), matrix=matrix)


def test_empty_floor_is_zero_everywhere() -> None:
    floor = RoadFloor.empty()
    assert floor.road_z == 0.0
    assert floor.z(0.0, 0.0) == 0.0 and floor.z(-1e6, 1e6) == 0.0


def test_carriageway_cap_lowers_points_on_the_road_only() -> None:
    floor = road_floor([Node("surface/sidewalk", (Primitive(cap(box(0.0, 3.0, 40.0, 5.0), 0.0), "paving"),)), _carriageway()])
    assert floor.road_z == pytest.approx(ROAD_Z)
    assert floor.z(10.0, 0.0) == pytest.approx(ROAD_Z)
    assert floor.z(40.0, 3.0) == pytest.approx(ROAD_Z)  # the boundary counts as road
    assert floor.z(10.0, 4.0) == 0.0  # sidewalk
    assert floor.z(-1.0, 0.0) == 0.0


def test_suffixed_node_name_is_the_same_carriageway() -> None:
    floor = road_floor([_carriageway(f"{CARRIAGEWAY_NODE}#2")])
    assert floor.z(10.0, 0.0) == pytest.approx(ROAD_Z)


def test_node_matrix_moves_the_cap() -> None:
    m = np.eye(4)
    m[0, 3], m[2, 3] = 100.0, -0.05
    floor = road_floor([_carriageway(matrix=m)])
    assert floor.road_z == pytest.approx(ROAD_Z - 0.05)
    assert floor.z(110.0, 0.0) == pytest.approx(ROAD_Z - 0.05)
    assert floor.z(10.0, 0.0) == 0.0


@pytest.mark.parametrize(
    "nodes",
    [
        [],
        [Node("surface/sidewalk", (Primitive(cap(box(0.0, -3.0, 40.0, 3.0), ROAD_Z), "paving"),))],
        [_carriageway(mesh=Mesh.empty())],
        # A cap whose only triangle is degenerate has no area to stand on.
        [_carriageway(mesh=Mesh(np.array([[0.0, 0.0, ROAD_Z], [1.0, 0.0, ROAD_Z], [2.0, 0.0, ROAD_Z]]), np.array([[0, 1, 2]])))],
        [Node(CARRIAGEWAY_NODE, ())],
    ],
    ids=["no-nodes", "other-node", "empty-mesh", "degenerate-triangle", "no-primitives"],
)
def test_missing_or_unusable_cap_gives_a_flat_floor(nodes: list[Node]) -> None:
    floor = road_floor(nodes)
    assert floor.road_z == 0.0
    assert floor.z(10.0, 0.0) == 0.0


def test_objects_without_node_attributes_are_ignored() -> None:
    floor = road_floor([object(), _carriageway()])
    assert floor.z(10.0, 0.0) == pytest.approx(ROAD_Z)
