"""Library lamp posts on ``detail``'s street lamps (14 November 2026).

``detail`` stands a procedural lamp (a grey post with an arm, ``urban:street_lamp``) on each OSM ``highway=street_lamp``
node and along the streets. The local Asset Store catalogues hold about forty textured lamp posts that no placement
rule takes, so a map showed none of them. Overgrow, which loads the library with its albedos, now draws one library
lamp post per map in place of every procedural one: the same node, name, extras, position and yaw, so the scene
records do not change, and ``detail``'s post collider stays.

The model is chosen by the map seed among the textured lamp posts of ``LAMP_MIN_TRIANGLES`` to ``LAMP_MAX_TRIANGLES``. It is moved so
its post (the plan of its lowest ``POST_FOOT_M``) stands on the node's origin, turned so its arm points along local
+x as ``detail``'s does, and scaled uniformly into ``LAMP_HEIGHT_M``. One model shares one mesh across the map. No
lamp post in the library, or more added triangles than the headroom under the Unity limit: nothing changes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from onward_generator.detail.facades import stable_unit
from onward_generator.generation.gltf import Node
from onward_generator.generation.mesh import Mesh
from onward_generator.overgrow.fit import POST_FOOT_M
from onward_generator.overgrow.flatness import prop_flat
from onward_generator.overgrow.library import Prop

# The roles the asset-store importer and the curated catalogues give a lamp post.
LAMP_ROLES = frozenset({"lamppost", "streetlight", "lamp_post", "lamp"})
# ``detail``'s procedural lamp is 4.5 m tall; a street lamp post is about 4 to 8 m.
LAMP_HEIGHT_M = (4.0, 7.0)
# The file's own height: a lamp head or a bollard light (1.3 to 3 m in Street Lights Pack) is no post, and scaling it
# threefold made a fat post on Murcia Catedral 2; an 11 m highway mast towers over a narrow street.
LAMP_SOURCE_HEIGHT_M = (3.5, 10.0)
# About twice the triangles of the hydrant-sized props the library keeps whole; a lamp repeats on every street.
LAMP_MAX_TRIANGLES = 1600
# Fewer reads as a block: Kenney's low-poly lamps (68 and 96 triangles) stand beside the Asset Store posts (144 up).
LAMP_MIN_TRIANGLES = 120
LAMP_NODE_PREFIX = "urban/street_lamp/"
# A lamp whose upper part sits less than this off its post has no arm to turn (a double or a plain post).
ARM_MIN_OFFSET_M = 0.1


@dataclass
class LampSwap:
    """What ``swap_lamps`` drew: the nodes, the albedos and materials to add, and the report block."""

    nodes: list[Node]
    images: dict[str, bytes] = field(default_factory=dict)
    added_triangles: int = 0
    report: dict[str, Any] | None = None


def lamp_candidates(props: Sequence[Prop]) -> list[Prop]:
    """The textured library lamp posts detailed enough and small enough to repeat, sorted by id. Kenney files a traffic light under
    ``lamp``; it is no street lamp."""
    return sorted(
        (prop for prop in props
         if prop.spec.role in LAMP_ROLES and "traffic" not in prop.spec.id.lower()
         and LAMP_MIN_TRIANGLES <= prop.triangles <= LAMP_MAX_TRIANGLES and LAMP_SOURCE_HEIGHT_M[0] <= prop.height_m <= LAMP_SOURCE_HEIGHT_M[1] and not prop_flat(prop)),
        key=lambda prop: prop.spec.id,
    )


def lamp_model(props: Sequence[Prop], seed: int) -> Prop | None:
    """One candidate chosen by ``seed``, or None when the library has none."""
    candidates = lamp_candidates(props)
    if not candidates:
        return None
    return candidates[min(len(candidates) - 1, int(stable_unit(seed, "street_lamp") * len(candidates)))]


def post_frame(mesh: Mesh) -> tuple[Mesh, float]:
    """``mesh`` with its post on the origin, its foot at z = 0, its arm along +x and its height in ``LAMP_HEIGHT_M``,
    and the uniform scale used."""
    vertices = mesh.vertices - np.array([0.0, 0.0, mesh.vertices[:, 2].min()])
    height = float(vertices[:, 2].max())
    post = vertices[vertices[:, 2] < POST_FOOT_M]
    centre = (post[:, :2].min(axis=0) + post[:, :2].max(axis=0)) / 2
    vertices[:, :2] -= centre
    upper = vertices[vertices[:, 2] > 0.75 * height]
    offset = upper[:, :2].mean(axis=0)
    if float(np.hypot(*offset)) >= ARM_MIN_OFFSET_M:
        angle = -math.atan2(float(offset[1]), float(offset[0]))
        c, s = math.cos(angle), math.sin(angle)
        vertices[:, :2] = vertices[:, :2] @ np.array([[c, s], [-s, c]])
    low, high = LAMP_HEIGHT_M
    scale = min(high, max(low, height)) / height
    return Mesh(vertices * scale, mesh.triangles), scale


def swap_lamps(nodes: Sequence[Node], props: Sequence[Prop], seed: int, headroom: int) -> LampSwap:
    """``nodes`` with every procedural street lamp drawn as one library lamp post, when one fits the headroom."""
    lamps = [index for index, node in enumerate(nodes)
             if node.name.startswith(LAMP_NODE_PREFIX) and (node.extras or {}).get("type") == "urban_element"]
    model = lamp_model(props, seed)
    if not lamps or model is None:
        return LampSwap(list(nodes))
    mesh, scale = post_frame(model.mesh)
    placed = replace(model, mesh=mesh, fit_scale=model.fit_scale * scale)
    before = sum(primitive.mesh.triangle_count for index in lamps for primitive in nodes[index].primitives)
    added = placed.triangles * len(lamps) - before
    report: dict[str, Any] = {
        "asset_id": model.spec.id, "count": len(lamps), "scale": round(scale, 4),
        "height_m": round(placed.height_m, 3), "triangles": placed.triangles, "added_triangles": added,
    }
    if added > headroom:
        return LampSwap(list(nodes), report={**report, "count": 0, "added_triangles": 0, "skipped": "over_headroom"})
    primitives = placed.primitives()
    swapped = list(nodes)
    for index in lamps:
        node = swapped[index]
        swapped[index] = Node(node.name, primitives, {**node.extras, "asset_id": model.spec.id, "representation": "library_lamp"},
                              node.matrix, f"lamp:{model.spec.id}")
    return LampSwap(swapped, placed.images(), added, report)
