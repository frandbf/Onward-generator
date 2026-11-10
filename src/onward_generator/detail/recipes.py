"""Furnishing sets chosen from the described library by room type (ADR-036).

With ``selection: semantic`` a furnishing block's sets are not hand lists: each room type has a recipe, the kinds of
piece each slot takes (``overgrow/semantics.py``), and the sets are filled from every allowed interior catalogue whose
rows carry a matching ``semantic`` block. Per slot, a few models are chosen deterministically from the map's seed: the
lightest candidates first, kinds taken in turn so a room mixes them, each within the slot's size limits. A slot no row
fills keeps the configuration's hand-written entries. Nothing is observed: the kinds are estimated from names.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

from onward_generator.assetstore import local
from onward_generator.detail.config import (
    ALL_SLOTS,
    FURNISH_CATALOGUES,
    FURNISH_EXCLUDED_IDS,
    DetailConfig,
    FurnishEntry,
    FurnishSet,
    ShopFurnish,
)
from onward_generator.detail.facades import stable_unit
from onward_generator.overgrow.semantics import HOME, SHOPS

SHOP_RECIPES: Mapping[str, Mapping[str, Any]] = {
    "generic": {"settings": ("shop", "grocery", "pharmacy"), "slots": {
        "counter": ("counter",), "decor": ("register", "decor_misc"), "wall": ("shelf_tall", "shelf_low", "display_case"),
        "center": ("shelf_low", "display_case"), "corner": ("plant_pot", "storage")}},
    "bakery": {"settings": ("bakery", "kitchen", "shop"), "slots": {
        "counter": ("counter", "display_case"), "decor": ("decor_food", "register"),
        "wall": ("shelf_tall", "display_case", "fridge", "kitchen_unit"), "table": ("table",), "seat": ("chair", "stool"),
        "corner": ("plant_pot",), "top": ("decor_food", "decor_tableware")}},
    "bar": {"settings": ("bar", "dining", "kitchen"), "slots": {
        "counter": ("counter",), "decor": ("decor_tableware", "appliance", "register"),
        "wall": ("fridge", "shelf_low", "cabinet_low", "shelf_tall"), "table": ("table",), "seat": ("chair", "stool"),
        "corner": ("plant_pot",), "top": ("decor_tableware",)}},
    "pharmacy": {"settings": ("pharmacy", "shop"), "slots": {
        "counter": ("counter",), "decor": ("register", "decor_misc"), "wall": ("shelf_tall", "shelf_low", "display_case"),
        "center": ("shelf_low",), "corner": ("plant_pot",)}},
}
FLOOR_RECIPES: Mapping[str, Mapping[str, Any]] = {
    "generic": {"settings": (*HOME, "office"), "slots": {
        "wall": ("kitchen_unit", "fridge", "shelf_tall", "wardrobe", "cabinet_low", "tv", "desk", "washer"),
        "wall_deep": ("sofa", "bed"), "table": ("table",), "seat": ("chair",), "center": ("coffee_table",),
        "corner": ("plant_pot", "lamp_floor", "armchair"), "rug": ("rug",), "top": ("decor_misc", "decor_tableware", "lamp_small")}},
}
# Models per slot, chosen once per map: enough to vary, few enough to stay under max_assets.
MODELS_PER_SLOT: Mapping[str, int] = {"counter": 2, "decor": 4, "wall": 5, "wall_deep": 3, "center": 2, "table": 2, "seat": 2,
                                      "corner": 3, "rug": 2, "top": 4}
# The lightest this many candidates of a kind are drawn from, so a heavy model never takes a slot a light one fills.
POOL = 4
MAX_TRIANGLES = {"decor": 400, "top": 400, "rug": 300}
# A piece of furniture costs at most this: every shop of a map shares one budget, and a light model furnishes more rooms.
FURNITURE_MAX_TRIANGLES = 900
ENV = "ONWARD_SEMANTIC_SETS"
"""``ONWARD_SEMANTIC_SETS=0`` keeps the hand-written sets even where a block asks for ``semantic`` (the test suite)."""


@dataclass(frozen=True, slots=True)
class Candidate:
    asset_id: str
    catalogue: str
    kind: str
    settings: frozenset[str]
    length_m: float
    depth_m: float
    height_m: float
    triangles: int


def _fits(slot: str, c: Candidate) -> tuple[bool, int]:
    """Whether the row fits the slot's size limits, and the quarter turn that lays its long side along a wall."""
    along, across, height = c.length_m, c.depth_m, c.height_m
    turn = 90 if slot in ("wall", "counter", "center") and across > 1.2 * along else 0
    if turn:
        along, across = across, along
    biggest = max(along, across, height)
    limits = {
        "counter": across <= 1.0 and 0.7 <= height <= 1.3 and along >= 0.6,
        "wall": across <= 0.8 and 0.4 <= height <= 2.3,
        "wall_deep": 0.6 <= across <= 2.4 and along <= 2.6 and 0.3 <= height <= 1.6,
        "center": across <= 1.2 and along <= 2.5 and 0.5 <= height <= 1.9,
        "table": along <= 2.0 and across <= 1.2 and 0.4 <= height <= 1.15,
        "seat": biggest <= 1.1 and height >= 0.4,
        "corner": max(along, across) <= 1.0 and height >= 0.3,
        "rug": height <= 0.06 and max(along, across) <= 3.5 and min(along, across) >= 0.5,
        "decor": biggest <= 0.45 and height <= 0.5,
        "top": biggest <= 0.45 and height <= 0.5,
    }
    return bool(limits.get(slot, False)), turn


def library(root: Path, catalogues: Sequence[str]) -> list[Candidate]:
    """Every placeable interior row of ``catalogues`` under ``root`` with its semantic block (no GLB is read)."""
    found: list[Candidate] = []
    for name in sorted(catalogues):
        try:
            document = json.loads((root / name / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for row in document.get("assets") or []:
            semantic = row.get("semantic") if isinstance(row, dict) else None
            if (not isinstance(semantic, dict) or row.get("placement") != "interior" or semantic.get("mount") == "none"
                    or row.get("refused") or row.get("id") in FURNISH_EXCLUDED_IDS):
                continue
            found.append(Candidate(str(row["id"]), name, str(semantic.get("kind")), frozenset(semantic.get("settings") or ()),
                                   float(row.get("length_m") or 0), float(row.get("depth_m") or 0), float(row.get("height_m") or 0),
                                   int(row.get("triangles") or 0)))
    return found


def choose(slot: str, kinds: Sequence[str], settings: Sequence[str], pool: Sequence[Candidate], seed: int, key: str) -> list[FurnishEntry]:
    """Up to ``MODELS_PER_SLOT[slot]`` entries: per kind the ``POOL`` lightest fitting rows, one kind after another."""
    cap = MAX_TRIANGLES.get(slot, FURNITURE_MAX_TRIANGLES)
    wanted = set(settings)
    by_kind: dict[str, list[tuple[Candidate, int]]] = {}
    for kind in kinds:
        rows = []
        for c in pool:
            if c.kind != kind or c.triangles > cap or (slot != "corner" and not (c.settings & wanted)):
                continue
            ok, turn = _fits(slot, c)
            if ok:
                rows.append((c, turn))
        if slot in ("decor", "top"):
            # Small things are chosen big first: a cake or a coffee machine reads from the door, a chopstick does not.
            rows.sort(key=lambda item: (-max(item[0].length_m, item[0].depth_m, item[0].height_m), item[0].asset_id))
        else:
            rows.sort(key=lambda item: (item[0].triangles, item[0].asset_id))
        lightest = rows[:POOL]
        if lightest:
            start = int(stable_unit(seed, key, slot, kind) * len(lightest))
            by_kind[kind] = lightest[start:] + lightest[:start]
    chosen: list[FurnishEntry] = []
    seen: set[str] = set()
    limit = MODELS_PER_SLOT.get(slot, 2)
    depth = 0
    while len(chosen) < limit and any(len(rows) > depth for rows in by_kind.values()):
        for kind in kinds:
            rows = by_kind.get(kind, [])
            if depth < len(rows) and len(chosen) < limit and rows[depth][0].asset_id not in seen:
                c, turn = rows[depth]
                chosen.append(FurnishEntry(c.asset_id, turn))
                seen.add(c.asset_id)
        depth += 1
    return chosen


def semantic_sets(furnish: ShopFurnish, recipes: Mapping[str, Mapping[str, Any]], pool: Sequence[Candidate], seed: int,
                  scope: str) -> tuple[dict[str, FurnishSet], set[str]]:
    """Each recipe's set, filled from ``pool``; a slot nothing fills keeps the hand-written set's entries."""
    sets: dict[str, FurnishSet] = {}
    used: set[str] = set()
    catalogue_of = {c.asset_id: c.catalogue for c in pool}
    for name, recipe in recipes.items():
        written = furnish.sets.get(name) or furnish.sets.get("generic")
        slots: dict[str, tuple[FurnishEntry, ...]] = {}
        for slot in ALL_SLOTS:
            kinds = recipe["slots"].get(slot, ())
            picked = choose(slot, kinds, recipe["settings"], pool, seed, f"{scope}:{name}") if kinds else []
            fallback = written.slot(slot) if written is not None and slot in ("counter", "wall", "decor") else ()
            slots[slot] = tuple(picked) or tuple(fallback)
            used |= {catalogue_of[e.id] for e in picked}
        sets[name] = FurnishSet(**slots)
    return sets, used


def allowed_catalogues(root: Path) -> list[str]:
    """The shared furnishing catalogues present under ``root``, and this machine's local Asset Store ones."""
    shared = [name for name in FURNISH_CATALOGUES if (root / name / "manifest.json").is_file()]
    return [*shared, *(name for name, _document in local.local_manifests(root))]


def with_semantic_sets(cfg: DetailConfig, root: Path) -> DetailConfig:
    """``cfg`` with the sets of every enabled ``selection: semantic`` block chosen from the library (ADR-036)."""
    shops, floors = cfg.interiors.shops.furnish, cfg.interiors.furnish
    if os.environ.get(ENV, "1") == "0":
        return cfg
    if not ((shops.enabled and shops.selection == "semantic") or (floors.enabled and floors.selection == "semantic")):
        return cfg
    pool = library(root, allowed_catalogues(root))
    if shops.enabled and shops.selection == "semantic":
        names = {"generic", *(c.name for c in cfg.interiors.shops.categories)}
        recipes = {name: recipe for name, recipe in SHOP_RECIPES.items() if name in names}
        sets, used = semantic_sets(shops, recipes, pool, cfg.seed, "shops")
        shops = replace(shops, sets=MappingProxyType({**shops.sets, **sets}), catalogues=tuple(sorted({*shops.catalogues, *used})))
    if floors.enabled and floors.selection == "semantic":
        sets, used = semantic_sets(floors, FLOOR_RECIPES, pool, cfg.seed, "floors")
        floors = replace(floors, sets=MappingProxyType({**floors.sets, **sets}), catalogues=tuple(sorted({*floors.catalogues, *used})))
    interiors = replace(cfg.interiors, furnish=floors, shops=replace(cfg.interiors.shops, furnish=shops))
    return replace(cfg, interiors=interiors)


__all__ = ["FLOOR_RECIPES", "SHOP_RECIPES", "SHOPS", "Candidate", "choose", "library", "semantic_sets", "with_semantic_sets"]
