"""Per-building texture variants (plan T4): neighbouring roofs and plaster walls draw different calm photographs.

With the map.json ``texture_variants`` switch, each material of a varied slot (``VARIANT_SLOTS``) gets ``VARIANTS`` more
materials, ``<material>__var<k>``, each bound to another calm photograph of the slot's family, the ones the plan did
not choose, in a seeded order. Each building draws the base or one variant by a hash of its name, so the choice is
stable from run to run. A variant repeats at its base's scale and takes its base's UV rules; trim facades (ADR-020)
already vary per building and are left alone. Each variant is one more material, so up to ``VARIANTS`` more draws
per varied slot in each combine cell that holds it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from typing import Any

from onward_generator.generation.gltf import Node, Primitive
from onward_generator.materials.library_plan import SLOT_FAMILY, is_calm, texture_family

VARIANT_SEP = "__var"
VARIANTS = 2
"""Photographs added to each varied slot: with its base, three looks per material."""
VARIANT_SLOTS = ("roofs", "flat_roofs", "historic_facades", "ochre_facades", "plain_facades", "brick")
"""Slots drawn on buildings: their roofs and walls. Ground surfaces are one node per map and keep the per-cell phase."""
BUILDING_TYPES = frozenset({"building", "building_part"})


def variant_base(material: str) -> str:
    """The material a variant shows the scale and UV rules of: ``roof_tile__var1`` is ``roof_tile``."""
    return material.split(VARIANT_SEP)[0]


def variant_picks(entries: Sequence[Any], rows: Sequence[Mapping[str, Any]], seed: int, *, extended: bool) -> dict[str, list[str]]:
    """For each varied slot of the plan, ``VARIANTS`` calm library ids of its family that no plan entry uses, in the
    order of ``sha256(seed:slot:id)``; fewer when the family has fewer. (Not crc32: it is affine, so changing the seed
    barely reorders ids of one length.)"""
    used = {str(entry.library_id) for entry in entries}
    picks: dict[str, list[str]] = {}
    for entry in entries:
        slot = str(entry.id)
        if slot not in VARIANT_SLOTS:
            continue
        family = SLOT_FAMILY[slot]
        pool = sorted((str(row["id"]) for row in rows
                       if texture_family(row) == family and str(row["id"]) not in used and is_calm(row, extended=extended)),
                      key=lambda library_id: hashlib.sha256(f"{seed}:{slot}:{library_id}".encode()).digest())
        picks[slot] = pool[:VARIANTS]
        used.update(picks[slot])
    return picks


def variant_index(building: str, material: str, count: int) -> int:
    """0 (the base) to ``count``: the variant ``building`` draws of ``material``."""
    return hashlib.sha256(f"variant:{building}:{material}".encode()).digest()[0] % (count + 1)


def assign_variants(nodes: Sequence[Node], counts: Mapping[str, int]) -> tuple[list[Node], dict[str, int]]:
    """``nodes`` with each building's primitives of a varied material (``counts``: material to its variant count) moved
    to the variant the building draws, and how many buildings draw each material."""
    out: list[Node] = []
    drawn: dict[str, int] = {}
    for node in nodes:
        if (node.extras or {}).get("type") not in BUILDING_TYPES:
            out.append(node)
            continue
        prims: list[Primitive] = []
        seen: set[str] = set()
        for prim in node.primitives:
            count = counts.get(prim.material)
            if not count:
                prims.append(prim)
                continue
            k = variant_index(node.name, prim.material, count)
            name = prim.material if k == 0 else f"{prim.material}{VARIANT_SEP}{k}"
            prims.append(Primitive(prim.mesh, name, prim.uvs))
            if name not in seen:
                drawn[name] = drawn.get(name, 0) + 1
                seen.add(name)
        out.append(replace(node, primitives=tuple(prims)))
    return out, dict(sorted(drawn.items()))


def varied_materials(applied: Sequence[Mapping[str, Any]], picks: Mapping[str, Sequence[str]], skip: Collection[str]) -> dict[str, list[str]]:
    """Each applied material of a varied slot (not in ``skip``, e.g. the trim facades) and its variants' library ids."""
    found: dict[str, list[str]] = {}
    for row in applied:
        material = str(row["color_id"])
        ids = list(picks.get(str(row["id"]), ()))
        if ids and material not in skip:
            found[material] = ids
    return found


__all__ = ["BUILDING_TYPES", "VARIANTS", "VARIANT_SEP", "VARIANT_SLOTS", "assign_variants", "variant_base", "variant_index",
           "variant_picks", "varied_materials"]
