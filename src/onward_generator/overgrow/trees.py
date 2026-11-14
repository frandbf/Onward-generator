"""Library cut-out trees on ``detail``'s street trees (ADR-040, 14 November 2026).

``detail`` stands a procedural tree (a lathed canopy on a trunk) on each OSM tree and along the streets. A local
catalogue may hold trees whose leaves are opaque alpha cut-outs (the *Horned Holly Tree* package): the SDK's
``PP_Opaque_Simple_Lit`` clips them with ``ALPHACLIPPING_ON``, and the importer wrote each leaf triangle twice, since the
shader draws front faces only. Overgrow draws ``detail``'s trees with them: the same node, name, extras and place,
so the scene records and ``detail``'s trunk collider do not change.

Each tree takes one candidate by the seed and its node name. The candidate's trunk (the plan of its lowest
``TRUNK_FOOT_M``) stands on the node's origin, and the node's matrix scales it uniformly so its crown reaches as far
from the axis as the procedural crown did: a crown ``detail`` narrowed to keep off a facade (``crown_scale``, in the
matrix) stays narrowed. A scale outside ``TREE_SCALE`` keeps the procedural tree. One candidate shares one mesh across
the map. The trees nearest the map centre go first, while their added triangles stay within ``TREE_HEADROOM_SHARE`` of
the headroom under the Unity limit; the street props keep the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from onward_generator.detail.facades import stable_unit
from onward_generator.generation.gltf import Node
from onward_generator.generation.mesh import Mesh
from onward_generator.overgrow.fit import TRUNK_FOOT_M
from onward_generator.overgrow.library import Prop

TREE_NODE_PREFIX = "urban/tree"
# A tree repeats on every street: Horned Holly Tree 2 and 3 (2 127 and 1 539 triangles, both faces of the leaves) fit,
# Tree 1 (5 318) does not.
TREE_MAX_TRIANGLES = 2400
# Under this a library "tree" is a bush.
TREE_MIN_HEIGHT_M = 3.0
# The uniform scale that matches a candidate's crown to the procedural one; outside it the candidate would read as
# a sapling or a giant, and the procedural tree stays.
TREE_SCALE = (0.6, 1.6)
# The share of the headroom the trees may spend; the street props, which are cover, keep the rest.
TREE_HEADROOM_SHARE = 0.5


@dataclass
class TreeSwap:
    """What ``swap_trees`` drew: the nodes, the albedos and cut-out thresholds to add, and the report block."""

    nodes: list[Node]
    images: dict[str, bytes] = field(default_factory=dict)
    cutouts: dict[str, float] = field(default_factory=dict)
    added_triangles: int = 0
    report: dict[str, Any] | None = None


def tree_candidates(props: Sequence[Prop]) -> list[Prop]:
    """The library trees with cut-out leaves, tall enough and small enough to repeat, sorted by id."""
    return sorted(
        (prop for prop in props
         if prop.spec.category == "vegetation" and prop.spec.role == "tree" and prop.cutouts()
         and prop.triangles <= TREE_MAX_TRIANGLES and prop.height_m >= TREE_MIN_HEIGHT_M),
        key=lambda prop: prop.spec.id,
    )


def trunk_frame(mesh: Mesh) -> Mesh:
    """``mesh`` with its trunk on the origin and its foot at z = 0."""
    vertices = mesh.vertices - np.array([0.0, 0.0, mesh.vertices[:, 2].min()])
    foot = vertices[vertices[:, 2] < TRUNK_FOOT_M]
    vertices[:, :2] -= (foot[:, :2].min(axis=0) + foot[:, :2].max(axis=0)) / 2
    return Mesh(vertices, mesh.triangles)


def crown_reach(meshes: Sequence[Mesh]) -> float:
    """The farthest the meshes reach from the vertical axis, in plan."""
    return max((float(np.hypot(mesh.vertices[:, 0], mesh.vertices[:, 1]).max()) for mesh in meshes if len(mesh.vertices)), default=0.0)


def swap_trees(nodes: Sequence[Node], props: Sequence[Prop], seed: int, headroom: int) -> TreeSwap:
    """``nodes`` with ``detail``'s street trees drawn as library cut-out trees, nearest the centre first."""
    trees = [index for index, node in enumerate(nodes)
             if node.name.startswith(TREE_NODE_PREFIX) and (node.extras or {}).get("type") == "urban_element"]
    candidates = tree_candidates(props)
    if not trees or not candidates:
        return TreeSwap(list(nodes))
    placed = [replace(prop, mesh=trunk_frame(prop.mesh)) for prop in candidates]
    reaches = [crown_reach([prop.mesh]) for prop in placed]
    budget = int(headroom * TREE_HEADROOM_SHARE)
    low, high = TREE_SCALE
    swapped = list(nodes)
    result = TreeSwap(swapped)
    used: dict[str, int] = {}
    out_of_scale = over_budget = 0

    def distance(index: int) -> tuple[float, str]:
        matrix = nodes[index].matrix
        origin = (0.0, 0.0) if matrix is None else (float(matrix[0, 3]), float(matrix[1, 3]))
        return float(np.hypot(*origin)), nodes[index].name

    for index in sorted(trees, key=distance):
        node = nodes[index]
        pick = min(len(placed) - 1, int(stable_unit(seed, node.name, "tree") * len(placed)))
        prop, reach = placed[pick], reaches[pick]
        scale = crown_reach([primitive.mesh for primitive in node.primitives]) / reach if reach > 0 else 0.0
        if not low <= scale <= high:
            out_of_scale += 1
            continue
        added = prop.triangles - sum(primitive.mesh.triangle_count for primitive in node.primitives)
        if result.added_triangles + added > budget:
            over_budget += 1
            continue
        matrix = (node.matrix if node.matrix is not None else np.eye(4)) @ np.diag([scale, scale, scale, 1.0])
        swapped[index] = Node(node.name, prop.primitives(),
                              {**node.extras, "asset_id": prop.spec.id, "representation": "library_tree", "tree_scale": round(scale, 4)},
                              matrix, f"tree:{prop.spec.id}")
        result.added_triangles += added
        result.images.update(prop.images())
        result.cutouts.update(prop.cutouts())
        used[prop.spec.id] = used.get(prop.spec.id, 0) + 1
    result.report = {"assets": dict(sorted(used.items())), "count": sum(used.values()), "trees": len(trees),
                     "out_of_scale": out_of_scale, "over_budget": over_budget, "added_triangles": result.added_triangles,
                     "budget_triangles": budget}
    return result
