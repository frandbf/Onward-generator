"""Static measurement of the GLB files the generator writes (phase 05).

These numbers describe the files, not the device. They are what Unity will
receive; draw calls on the Quest depend on culling and batching and are only
known from the device run (``device.py``).
"""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path
from typing import Any

from onward_generator.generation.gltf import read_glb

UINT16_MAX_VERTICES = 65535


def measure_glb(path: str | Path) -> dict[str, Any]:
    """Counts for one GLB: nodes, meshes, primitives, triangles, vertices, materials, textures, lights."""
    p = Path(path).expanduser()
    raw = p.read_bytes()
    doc, _ = read_glb(raw)
    accessors = doc.get("accessors", [])
    meshes = doc.get("meshes", [])
    materials = [m.get("name", f"material_{i}") for i, m in enumerate(doc.get("materials", []))]

    def prim_counts(prim: dict[str, Any]) -> tuple[int, int]:
        return accessors[prim["indices"]]["count"] // 3, accessors[prim["attributes"]["POSITION"]]["count"]

    mesh_tris = [sum(prim_counts(pr)[0] for pr in m["primitives"]) for m in meshes]
    mesh_verts = [sum(prim_counts(pr)[1] for pr in m["primitives"]) for m in meshes]
    drawn = [n for n in doc.get("nodes", []) if "mesh" in n]
    by_material_tris: Counter[str] = Counter()
    by_material_prims: Counter[str] = Counter()
    max_prim_vertices = 0
    for node in drawn:
        for prim in meshes[node["mesh"]]["primitives"]:
            tris, verts = prim_counts(prim)
            name = materials[prim["material"]] if "material" in prim else "(none)"
            by_material_tris[name] += tris
            by_material_prims[name] += 1
            max_prim_vertices = max(max_prim_vertices, verts)
    uses = Counter(n["mesh"] for n in drawn)
    attributes = sorted({k for m in meshes for pr in m["primitives"] for k in pr["attributes"]})
    biggest = sorted(((mesh_tris[n["mesh"]], n["name"]) for n in drawn), key=lambda t: (-t[0], t[1]))[:10]
    return {
        "file": str(p),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "bytes": len(raw),
        "nodes_with_mesh": len(drawn),
        "unique_meshes": len(meshes),
        "instanced_nodes": sum(c - 1 for c in uses.values() if c > 1),
        # One Unity renderer per node, one submesh per primitive: the draw count with no culling and no batching.
        "primitives_drawn": sum(by_material_prims.values()),
        "triangles": sum(mesh_tris[n["mesh"]] for n in drawn),
        "vertices": sum(mesh_verts[n["mesh"]] for n in drawn),
        "unique_triangles": sum(mesh_tris),
        "unique_vertices": sum(mesh_verts),
        "max_primitive_vertices": max_prim_vertices,
        "needs_32bit_indices": max_prim_vertices > UINT16_MAX_VERTICES,
        "materials": len(materials),
        "triangles_by_material": dict(sorted(by_material_tris.items())),
        "primitives_by_material": dict(sorted(by_material_prims.items())),
        "textures": len(doc.get("textures", [])),
        "images": len(doc.get("images", [])),
        "lights": len(doc.get("extensions", {}).get("KHR_lights_punctual", {}).get("lights", [])),
        "vertex_attributes": attributes,
        "has_lightmap_uv": "TEXCOORD_1" in attributes,
        "largest_meshes": [{"node": name, "triangles": t} for t, name in biggest],
    }


def _node_centre(node: dict[str, Any], mesh: dict[str, Any], accessors: list[dict[str, Any]]) -> tuple[float, float, float]:
    """Centre of a node's mesh bounds in glTF scene space (node ``matrix`` only; TRS nodes are not written)."""
    lo = [min(accessors[p["attributes"]["POSITION"]]["min"][k] for p in mesh["primitives"]) for k in range(3)]
    hi = [max(accessors[p["attributes"]["POSITION"]]["max"][k] for p in mesh["primitives"]) for k in range(3)]
    c = [(a + b) / 2.0 for a, b in zip(lo, hi)]
    m = node.get("matrix")
    if m is None:
        return c[0], c[1], c[2]
    if len(m) != 16:
        raise ValueError(f"node {node.get('name')!r}: matrix must have 16 values")
    # Column-major 4x4.
    return tuple(m[r] * c[0] + m[4 + r] * c[1] + m[8 + r] * c[2] + m[12 + r] for r in range(3))  # type: ignore[return-value]


def combine_groups(path: str | Path, cell_m: float) -> dict[str, Any]:
    """Visual draws left if primitives are merged per (ground cell, material).

    Cells are squares of ``cell_m`` metres on the glTF ground plane (x, z = east,
    -north); a node goes to the cell holding its bounds centre. This predicts the
    draw count with no culling, the upper bound a device frame can reach.
    """
    if not cell_m > 0:
        raise ValueError(f"cell_m must be positive, got {cell_m}")
    doc, _ = read_glb(Path(path).expanduser().read_bytes())
    accessors, meshes = doc.get("accessors", []), doc.get("meshes", [])
    groups: Counter[tuple[int, int, int]] = Counter()
    tris: Counter[tuple[int, int, int]] = Counter()
    for node in (n for n in doc.get("nodes", []) if "mesh" in n):
        mesh = meshes[node["mesh"]]
        x, _, z = _node_centre(node, mesh, accessors)
        cell = (int(x // cell_m), int(z // cell_m))
        for prim in mesh["primitives"]:
            key = (cell[0], cell[1], prim.get("material", -1))
            groups[key] += 1
            tris[key] += accessors[prim["indices"]]["count"] // 3
    return {
        "cell_m": cell_m,
        "cells": len({(k[0], k[1]) for k in groups}),
        "draws": len(groups),
        "primitives_merged": sum(groups.values()),
        "max_group_triangles": max(tris.values(), default=0),
    }


def measure_scene_dir(directory: str | Path) -> dict[str, Any]:
    d = Path(directory).expanduser()
    out: dict[str, Any] = {"dir": str(d)}
    for key, name in (("visual", "scene_visual.glb"), ("collision", "scene_collision.glb")):
        path = d / name
        if not path.is_file():
            raise FileNotFoundError(f"{path} not found")
        out[key] = measure_glb(path)
    return out
