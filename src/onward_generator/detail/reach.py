"""The ground a player reaches in the playable core, and the walls that face none of it (plan P0, ADR-017 amendment).

``perf.layout`` bakes the same ground for the navmesh; ``detail`` runs first and only needs to know which walls a player
can see from where he walks, so it keeps a copy of the rule here instead of importing the layout.
"""

from __future__ import annotations

from shapely import union_all
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry

from onward_generator.detail.facades import Segment
from onward_generator.generation.mesh import polygons_of
from onward_generator.generation.scene import GeneratedScene
from onward_generator.generation.surfaces import SurfaceClass

# The navmesh agent radius of ``perf.layout``: the ground is shrunk by it before its islands are counted.
AGENT_RADIUS_M = 0.35
# A wall whose foot is farther than this from the reached ground is drawn plain (``layout_segment(hidden=True)``):
# about the depth of a closed block's courtyard from the street behind it.
HIDDEN_FACE_M = 6.0
# The point in front of a wall measured from the reached ground: past a sidewalk's edge, off the facade.
PROBE_OUT_M = 1.0


def reached_ground(base: GeneratedScene) -> BaseGeometry:
    """The largest island of walkable ground of the map: every surface but the carriageway and water, plus the
    crossings, less the buildings, shrunk by the agent radius. The courtyards a block closes are other islands.

    The whole map, not the core: a wall is hidden for what is in front of it, so a budget core (ADR-017), solved from a
    probe at its largest size, draws each building the same at any size it settles on."""
    walkable = [s.geometry for c, s in base.partition.surfaces.items() if c not in (SurfaceClass.CARRIAGEWAY, SurfaceClass.WATER)]
    ground = union_all([*walkable, base.partition.crossings]).difference(base.partition.buildings)
    islands = list(polygons_of(ground.buffer(-AGENT_RADIUS_M)))
    return max(islands, key=lambda island: island.area) if islands else ground.buffer(-AGENT_RADIUS_M)


def hidden_face(seg: Segment, reached: BaseGeometry) -> bool:
    """Whether the ground in front of ``seg`` lies farther than ``HIDDEN_FACE_M`` from ``reached``."""
    mid = seg.point(seg.length / 2) + seg.n_out * PROBE_OUT_M
    return bool(reached.distance(Point(float(mid[0]), float(mid[1]))) > HIDDEN_FACE_M)


__all__ = ["AGENT_RADIUS_M", "HIDDEN_FACE_M", "PROBE_OUT_M", "hidden_face", "reached_ground"]
