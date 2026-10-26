"""Height of the ground under a point.

Sidewalks, yards and plazas stay at z = 0. The carriageway is the lowered cap
already written in the base scene (``surface/carriageway``). A prop uses that
height so it sits on the asphalt instead of the sidewalk.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from shapely import Point, Polygon, STRtree

from onward_generator.generation.mesh import Mesh, transform

CARRIAGEWAY_NODE = "surface/carriageway"


class RoadFloor:
    """Carriageway polygon in local metres, or an empty floor at z = 0."""

    def __init__(self, tree: STRtree | None, road_z: float) -> None:
        self._tree = tree
        self.road_z = float(road_z)

    @classmethod
    def empty(cls) -> RoadFloor:
        return cls(None, 0.0)

    def z(self, x: float, y: float) -> float:
        if self._tree is None:
            return 0.0
        if len(self._tree.query(Point(float(x), float(y)), predicate="intersects")) == 0:
            return 0.0
        return self.road_z


def road_floor(nodes: Sequence[object]) -> RoadFloor:
    """Read the carriageway cap from the base visual nodes. Missing cap → z = 0."""
    triangles: list[Polygon] = []
    road_z = 0.0
    found = False
    for node in nodes:
        name = str(getattr(node, "name", "")).split("#", 1)[0]
        if name != CARRIAGEWAY_NODE:
            continue
        matrix = getattr(node, "matrix", None)
        for primitive in getattr(node, "primitives", ()) or ():
            mesh = primitive.mesh
            if matrix is not None:
                mesh = transform(mesh, matrix)
            if not isinstance(mesh, Mesh) or mesh.vertex_count == 0 or mesh.triangle_count == 0:
                continue
            found = True
            road_z = float(np.median(mesh.vertices[:, 2]))
            for index in mesh.triangles:
                a, b, c = mesh.vertices[index]
                polygon = Polygon([(float(a[0]), float(a[1])), (float(b[0]), float(b[1])), (float(c[0]), float(c[1]))])
                if polygon.area > 1e-8:
                    triangles.append(polygon)
    if not found or not triangles:
        return RoadFloor.empty()
    return RoadFloor(STRtree(triangles), road_z)
