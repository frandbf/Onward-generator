"""Build the shop furniture ``detail`` planned (ADR-031) on the overgrow copy.

``detail`` writes a ``furnishing`` block on each furnished shop room of ``scene_detail.json``: the
catalogue row, where it stands, its turn, the triangles it was priced at and its GLB's SHA-256. This
module reads those blocks, loads the rows through ``read_interior_catalogue`` (only ``placement:
interior`` rows of the allow-listed CC0 catalogues; street overgrow still refuses them) and emits:

- one visual node per piece, ``furnish/<building>/<n>``, material ``prop_<id>`` (albedo only, like
  overgrow's props), ``mesh_key`` ``prop:<id>`` so copies share one mesh;
- one box collider ``furnish/collider/<building>/<n>`` for each piece the plan marked ``solid``,
  in the collision GLB only.

The ``furnish/`` prefix is not ``overgrow/prop/``: ``overgrow fit`` measures street props only, and a
piece stands inside a footprint on purpose. A GLB that is missing, or whose bytes or triangles differ
from what ``detail`` priced, stops the step: the plan and the meshes must agree.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from onward_generator.detail.config import FURNISH_CATALOGUES
from onward_generator.detail.modules import yaw_matrix
from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import box
from onward_generator.overgrow.library import read_interior_catalogue

NODE_PREFIX = "furnish/"
COLLIDER_PREFIX = "furnish/collider/"


class FurnishBuildError(ValueError):
    """The furniture ``detail`` planned cannot be built from the installed catalogues."""


@dataclass(slots=True)
class FurnishBuild:
    nodes: list[Node] = field(default_factory=list)
    colliders: list[Node] = field(default_factory=list)
    images: dict[str, bytes] = field(default_factory=dict)
    triangles: int = 0
    solid_pieces: int = 0
    """Pieces the plan marked ``solid``; each must have a collider."""

    def report(self) -> dict[str, Any]:
        """``furnishing`` in ``overgrow_report.json``."""
        return {"pieces": len(self.nodes), "triangles": self.triangles, "colliders": len(self.colliders),
                "materials": sorted(self.images)}


def planned_pieces(manifest: Mapping[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """``(building id, piece)`` of every piece in the ``furnishing`` blocks of the shop interiors, in record order."""
    found: list[tuple[str, dict[str, Any]]] = []
    for item in manifest.get("objects") or []:
        if not isinstance(item, dict) or item.get("type") != "interior":
            continue
        building = str((item.get("source_ids") or [""])[0])
        for room in item.get("rooms") or []:
            block = room.get("furnishing") if isinstance(room, dict) else None
            for piece in (block or {}).get("pieces") or []:
                found.append((building, piece))
    return found


def build_furniture(manifest: Mapping[str, Any], library: str | Path | None) -> FurnishBuild:
    """The visual and collision nodes of the planned furniture. No plan builds nothing and reads nothing."""
    pieces = planned_pieces(manifest)
    built = FurnishBuild()
    if not pieces:
        return built
    if library is None:
        raise FurnishBuildError("scene_detail.json plans shop furniture (ADR-031); overgrow place needs --library")
    catalogues = sorted({str(piece["catalogue"]) for _, piece in pieces})
    unknown = [name for name in catalogues if name not in FURNISH_CATALOGUES]
    if unknown:
        raise FurnishBuildError(f"shop furniture names catalogues that are not allowed: {', '.join(unknown)}")
    props, rejects = read_interior_catalogue(library, catalogues, {str(p["asset_id"]) for _, p in pieces})  # pragma: no mutate: the ids only spare the GLBs no piece names; the planned rows load the same
    why = {reject.asset_id: reject.reason for reject in rejects}
    seen: set[str] = set()
    for building, piece in pieces:
        asset_id = str(piece["asset_id"])
        found = props.get(asset_id)
        if found is None:
            raise FurnishBuildError(f"shop furniture {asset_id}: not installed under {library} ({why.get(asset_id, 'missing')})")
        if found.glb_sha256 != piece["glb_sha256"]:
            raise FurnishBuildError(f"shop furniture {asset_id}: the GLB is not the one detail planned (sha256 {found.glb_sha256[:12]}, "
                                    f"plan {str(piece['glb_sha256'])[:12]}); run detail again")
        prop = found.prop
        if prop.triangles != int(piece["triangles"]):
            raise FurnishBuildError(f"shop furniture {asset_id}: {prop.triangles} triangles, detail priced {piece['triangles']}")
        low, high = prop.mesh.vertices.min(axis=0), prop.mesh.vertices.max(axis=0)
        centre = np.eye(4)
        centre[:2, 3] = -(low[:2] + high[:2]) / 2  # the plan places the middle of the model's box
        x, y, z = (float(v) for v in piece["position_m"])
        matrix = yaw_matrix(x, y, z, math.radians(float(piece["yaw_deg"]))) @ centre
        first = asset_id not in seen
        seen.add(asset_id)
        built.nodes.append(Node(
            str(piece["id"]),
            prop.primitives() if first else (),
            {
                "type": "furnishing", "origin": "generated", "attribute_origin": "generated", "semantics_origin": "estimated",
                "asset_id": asset_id, "catalogue": found.catalogue, "licence": found.licence, "role": prop.spec.role,
                "slot": piece["slot"], "building_id": building, "solid": bool(piece["solid"]),
                **({"lod": prop.lod()} if prop.source_triangles else {}),
            },
            matrix,
            f"prop:{asset_id}",
        ))
        built.images.update(prop.images())
        built.triangles += prop.triangles
        built.solid_pieces += bool(piece["solid"])
        if piece["solid"]:
            built.colliders.append(Node(
                COLLIDER_PREFIX + str(piece["id"])[len(NODE_PREFIX):],
                (Primitive(box(low[0], high[0], low[1], high[1], low[2], high[2]), "collider"),),  # the whole box, as overgrow's cover
                {"type": "collision", "origin": "generated", "asset_id": asset_id, "role": prop.spec.role, "furnishing": True},
                matrix,
            ))
    return built
