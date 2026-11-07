"""Validated configuration for phase 04 (facades, roofs, urban elements, interiors)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.detail.s3db import tint_base, tint_materials
from onward_generator.generation.config import Material

# A blank party wall keeps its wall's albedo under this prefix, so the export can give it its own lightmap scale.
PARTY_PREFIX = "party_"

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "detail.default.json"
SCHEMA_PATH = REPO_ROOT / "config" / "detail.schema.json"
# Facade uses, most specific first. A building with several uses takes the first one.
FACADE_USES = ("historic", "institutional", "office", "retail", "industrial", "residential")
# A fascia sits FASCIA_GAP_M above the shopfront head. With 2 cm left under the storey line,
# a fascia needs FASCIA_CLEAR_M more than its own height above the head.
FASCIA_GAP_M = 0.05
FASCIA_CLEAR_M = 0.07
# Shop furnishing (ADR-031). The catalogues it may read: placement interior, licence recorded CC0 1.0 in
# docs/legal/data-sources.md. Street overgrow still refuses every interior row (overgrow/library.py).
FURNISH_CATALOGUES = ("kenney_food", "kenney_furniture", "kenney_mini_shop", "polyhaven_shop")
FURNISH_DECOR_CATALOGUE = "kenney_food"
FURNISH_SLOTS = ("counter", "wall", "decor")
# Rows that are not furniture: floor, wall, fence and column tiles, a character, a mannequin, and things a player
# would read as loose clutter on the floor. Refused in any furnishing set.
FURNISH_EXCLUDED_IDS = frozenset({
    "kenney_shop_floor", "kenney_shop_floor_wood", "kenney_shop_wall", "kenney_shop_wall_corner",
    "kenney_shop_wall_door_rotate", "kenney_shop_wall_window", "kenney_shop_fence", "kenney_shop_fence_door_rotate",
    "kenney_shop_column", "kenney_shop_character_employee", "kenney_shop_mannequin", "kenney_shop_shopping_basket",
    "kenney_shop_shopping_cart", "kenney_shop_hand_truck",
})
# The deepest piece a slot takes, across the wall it stands against, after its turn. A deeper piece is refused at load.
MAX_WALL_DEPTH_M = 0.8
MAX_COUNTER_DEPTH_M = 1.0


class DetailConfigError(ValueError):
    def __init__(self, path: Path | str, problems: list[str]) -> None:
        super().__init__(f"{path}: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Opening:
    width_m: float
    height_m: float
    sill_m: float = 0.0


@dataclass(frozen=True, slots=True)
class Shopfront:
    """A display window that fills its bay: as wide and as tall as the bay and the storey allow, within limits."""

    min_width_m: float
    max_width_m: float
    sill_m: float
    min_height_m: float
    max_height_m: float
    head_m: float


@dataclass(frozen=True, slots=True)
class ShopSigns:
    fascia_share: float
    vinyl_share: float
    fascia_height_m: float
    fascia_overhang_m: float
    fascia_depth_m: float
    blade_width_m: float
    blade_height_m: float
    blade_thickness_m: float
    blade_wall_gap_m: float
    blade_clearance_m: float
    vinyl_height_m: float
    vinyl_centre_m: float
    vinyl_width_share: float
    default_name: str
    generic_names: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class Balcony:
    share: float
    door: Opening
    width_m: float
    depth_m: float
    slab_m: float
    railing_m: float


@dataclass(frozen=True, slots=True)
class HipRoof:
    max_area_m2: float
    pitch_deg: float
    max_rise_m: float
    material: str


@dataclass(frozen=True, slots=True)
class PitchedRoof:
    """A roof the OSM ``roof:*`` tags describe (``detail/roofs.py``). Observed, never from the template."""

    shape: str  # drawn: gabled | hipped | pyramidal | skillion | dome
    tag: str  # the OSM roof:shape value
    material: str
    rise_m: float | None  # roof:height or roof:levels × storey; None leaves it to roof:angle or the default pitch
    rise_rule: str  # roof:height | roof:levels | roof:angle | default_pitch
    angle_deg: float | None
    direction_deg: float | None  # roof:direction, the compass bearing the slope faces
    across: bool  # roof:orientation=across


@dataclass(frozen=True, slots=True)
class Template:
    name: str
    applies_to: frozenset[str]
    bay_m: float
    window: Opening
    narrow_window: Opening
    ground: str
    balcony: Balcony | None
    wall_materials: tuple[str, ...]
    parapet_m: float
    parapet_thickness_m: float
    flat_roof_materials: tuple[str, ...]
    hip: HipRoof | None
    pitched: PitchedRoof | None = None


@dataclass(frozen=True, slots=True)
class InteriorRule:
    categories: frozenset[str]
    min_levels: int
    max_levels: int
    min_area_m2: float
    max_area_m2: float
    min_rectangularity: float


@dataclass(frozen=True, slots=True)
class Stair:
    width_m: float
    max_riser_m: float
    tread_m: float
    landing_m: float


@dataclass(frozen=True, slots=True)
class ShopCategory:
    """Interior colours of one kind of shop. ``generic`` has no tags and takes every other shop."""

    name: str
    tags: Mapping[str, frozenset[str]]
    wall: str
    floor: str

    def matches(self, tags: Mapping[str, Any]) -> bool:
        return any(str(tags.get(key)) in values for key, values in self.tags.items())


@dataclass(frozen=True, slots=True)
class FurnishEntry:
    """One catalogue row a furnishing set may place, and the quarter turn applied to it before it is set against a wall."""

    id: str
    turn_deg: int = 0


@dataclass(frozen=True, slots=True)
class FurnishSet:
    """The rows of one shop category: one counter, wall pieces, and small decor on the counter top."""

    counter: tuple[FurnishEntry, ...]
    wall: tuple[FurnishEntry, ...]
    decor: tuple[FurnishEntry, ...]

    def slot(self, name: str) -> tuple[FurnishEntry, ...]:
        return {"counter": self.counter, "wall": self.wall, "decor": self.decor}[name]


@dataclass(frozen=True, slots=True)
class ShopFurnish:
    """Catalogue furniture in the shop rooms (ADR-031). Off by default; an absent block is off."""

    enabled: bool = False
    max_triangles: int = 12000
    max_collision_triangles: int = 1200
    max_pieces_per_room: int = 8
    max_assets: int = 16
    walk_width_m: float = 1.2
    wall_gap_m: float = 0.03
    piece_gap_m: float = 0.1
    pane_clear_m: float = 0.15
    head_clear_m: float = 0.1
    catalogues: tuple[str, ...] = FURNISH_CATALOGUES
    sets: Mapping[str, FurnishSet] = MappingProxyType({})

    def set_for(self, category: str) -> FurnishSet:
        """The set of ``category``, else the ``generic`` one."""
        return self.sets.get(category) or self.sets["generic"]


@dataclass(frozen=True, slots=True)
class ShopInteriors:
    """One-storey interiors behind shopfronts, with their own triangle budget and a small fixed palette."""

    enabled: bool
    max_triangles: int
    max_collision_triangles: int
    max_area_m2: float
    band_depth_m: float
    """A shop above ``max_area_m2`` has its room cut to this depth behind the door's wall."""
    min_room_area_m2: float
    categories: tuple[ShopCategory, ...]
    generic: ShopCategory
    furnish: ShopFurnish = ShopFurnish()


@dataclass(frozen=True, slots=True)
class Interiors:
    enabled: bool
    min_buildings: int
    max_buildings: int
    max_triangles: int
    max_collision_triangles: int
    include_ids: tuple[str, ...]
    exclude_ids: frozenset[str]
    rule: InteriorRule
    floors: int
    wall_thickness_m: float
    slab_m: float
    door: Opening
    stair: Stair
    door_clearance_m: float
    lights_max: int
    shops: ShopInteriors


@dataclass(frozen=True, slots=True)
class LandmarkSettings:
    enabled: bool
    tower_half_width_m: float
    max_tower_height_m: float
    min_clip_area_m2: float
    wall_material: str
    roof_material: str


@dataclass(frozen=True, slots=True)
class StreetLamps:
    enabled: bool
    spacing_m: float
    curb_offset_m: float
    min_building_distance_m: float
    min_spacing_m: float


@dataclass(frozen=True, slots=True)
class FurnitureItem:
    kind: str
    spacing_m: float
    offset_m: float
    max_count: int


@dataclass(frozen=True, slots=True)
class StreetFurniture:
    enabled: bool
    items: tuple[FurnitureItem, ...]


@dataclass(frozen=True, slots=True)
class DetailConfig:
    version: str
    seed: int
    probe_m: float
    min_facade_length_m: float
    courtyard_openings: bool
    storey_min_m: float
    storey_max_m: float
    storey_fallback_m: float
    edge_margin_m: float
    min_pier_m: float
    head_clearance_m: float
    recess_depth_m: float
    relief_storeys: int
    """Storey index (0 at the ground) from which windows and balcony doors are drawn flat (ADR-025)."""
    templates: Mapping[str, Template]
    landmark_template: str
    main_door: Opening
    landmark_door: Opening
    shopfront: Shopfront
    shop_door: Opening
    shops: ShopSigns
    shop_evidence_keys: tuple[str, ...]
    shop_evidence_distance_m: float
    shop_catastro_uses: Mapping[str, str]
    """Catastro ``currentUse`` codes that give a building a commercial ground floor, and the generic name of its shop (ADR-022)."""
    facade_palettes: Mapping[str, tuple[str, ...]]
    interiors: Interiors
    urban_observed: Mapping[str, Mapping[str, frozenset[str]]]
    street_lamps: StreetLamps
    street_furniture: StreetFurniture
    urban_clearance_m: float
    budget: Mapping[str, int]
    closeup_focus: tuple[float, float, float]
    closeup_elevation_deg: float
    materials: Mapping[str, Material]
    landmarks: LandmarkSettings
    document: Mapping[str, Any]
    path: str | None = None
    sha256: str | None = None

    def template_for(self, category: str, landmark: bool) -> Template:
        if landmark:
            return self.templates[self.landmark_template]
        for template in self.templates.values():
            if category in template.applies_to:
                return template
        return self.templates["generic_residential"] if "generic_residential" in self.templates else next(iter(self.templates.values()))


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _furniture(d: Mapping[str, Any] | None) -> StreetFurniture:
    if not d or not d.get("enabled"):
        return StreetFurniture(False, ())
    items = tuple(
        FurnitureItem(item["kind"], float(item["spacing_m"]), float(item["offset_m"]), int(item["max_count"]))
        for item in d.get("items", [])
    )
    return StreetFurniture(True, items)


def _landmarks(d: Mapping[str, Any] | None) -> LandmarkSettings:
    if not d:
        return LandmarkSettings(False, 6.0, 120.0, 8.0, "stone_warm", "roof_tile")
    return LandmarkSettings(bool(d["enabled"]), float(d["tower_half_width_m"]), float(d["max_tower_height_m"]),
                            float(d["min_clip_area_m2"]), d["wall_material"], d["roof_material"])


def _opening(d: Mapping[str, Any]) -> Opening:
    return Opening(float(d["width_m"]), float(d["height_m"]), float(d.get("sill_m", 0.0)))


def _shops(d: Mapping[str, Any]) -> ShopSigns:
    f, b, v = d["fascia"], d["blade"], d["vinyl"]
    return ShopSigns(
        float(d["fascia_share"]), float(d["vinyl_share"]),
        float(f["height_m"]), float(f["overhang_m"]), float(f["depth_m"]),
        float(b["width_m"]), float(b["height_m"]), float(b["thickness_m"]), float(b["wall_gap_m"]), float(b["clearance_m"]),
        float(v["height_m"]), float(v["centre_m"]), float(v["width_share"]),
        d["default_name"], MappingProxyType(dict(sorted(d["generic_names"].items()))),
    )


def _shop_interiors(d: Mapping[str, Any]) -> ShopInteriors:
    categories = tuple(
        ShopCategory(c["name"], MappingProxyType({k: frozenset(v) for k, v in sorted(c["tags"].items())}), c["wall"], c["floor"])
        for c in d["categories"]
    )
    generic = ShopCategory("generic", MappingProxyType({}), d["generic"]["wall"], d["generic"]["floor"])
    return ShopInteriors(bool(d["enabled"]), int(d["max_triangles"]), int(d["max_collision_triangles"]), float(d["max_area_m2"]),
                         float(d["band_depth_m"]), float(d["min_room_area_m2"]), categories, generic, _shop_furnish(d.get("furnish")))


def _furnish_entry(item: str | Mapping[str, Any]) -> FurnishEntry:
    if isinstance(item, str):
        return FurnishEntry(item)
    return FurnishEntry(str(item["id"]), int(item.get("turn_deg", 0)))


def _shop_furnish(d: Mapping[str, Any] | None) -> ShopFurnish:
    """The ``interiors.shops.furnish`` block. Absent, furnishing is off."""
    if d is None:
        return ShopFurnish()
    sets = {
        name: FurnishSet(*(tuple(_furnish_entry(item) for item in block[slot]) for slot in FURNISH_SLOTS))
        for name, block in sorted(d["sets"].items())
    }
    return ShopFurnish(
        enabled=bool(d["enabled"]),
        max_triangles=int(d["max_triangles"]),
        max_collision_triangles=int(d["max_collision_triangles"]),
        max_pieces_per_room=int(d["max_pieces_per_room"]),
        max_assets=int(d["max_assets"]),
        walk_width_m=float(d["walk_width_m"]),
        wall_gap_m=float(d["wall_gap_m"]),
        piece_gap_m=float(d["piece_gap_m"]),
        pane_clear_m=float(d["pane_clear_m"]),
        head_clear_m=float(d["head_clear_m"]),
        catalogues=tuple(sorted(d["catalogues"])),
        sets=MappingProxyType(sets),
    )


def furnish_problems(furnish: ShopFurnish, categories: Sequence[str], assets: Path) -> list[str]:
    """Why an enabled furnishing block cannot load: a set key that is no category, or a row that is not a piece of furniture.

    Each row must be in ``catalogues`` (the allow-listed CC0 interior catalogues under ``assets``), have
    ``placement: interior``, not be a tile, a character or loose clutter (``FURNISH_EXCLUDED_IDS``), and not be
    deeper than its slot allows after its turn. Decor comes from ``kenney_food`` only. Reads the manifests, never a GLB.
    """
    where = "/interiors/shops/furnish"
    problems = [f"{where}/sets: {name!r} is not a shop category or 'generic'" for name in furnish.sets
                if name not in categories and name != "generic"]  # the schema requires the generic set
    rows: dict[str, tuple[str, Mapping[str, Any]]] = {}
    for catalogue in furnish.catalogues:
        try:
            manifest = json.loads((assets / catalogue / "manifest.json").read_text(encoding="utf-8"))  # pragma: no mutate: the image's locale is UTF-8, so its default decodes the same bytes; the argument pins it on other hosts
        except (OSError, json.JSONDecodeError):
            problems.append(f"{where}/catalogues: {catalogue!r} has no readable manifest under {assets}")
            continue
        for row in manifest.get("assets") or []:
            if isinstance(row, Mapping) and row.get("id"):
                rows[str(row["id"])] = (catalogue, row)
    for name, chosen in furnish.sets.items():
        for slot in FURNISH_SLOTS:
            for entry in chosen.slot(slot):
                problems.extend(f"{where}/sets/{name}/{slot}: {entry.id!r} {why}" for why in _entry_problems(entry, slot, rows.get(entry.id)))
    return problems


def _entry_problems(entry: FurnishEntry, slot: str, found: tuple[str, Mapping[str, Any]] | None) -> list[str]:
    if found is None:
        return ["is not a row of an allowed catalogue"]
    catalogue, row = found
    if entry.id in FURNISH_EXCLUDED_IDS:
        return ["is a tile, a character or loose clutter, not furniture"]
    if row.get("placement") != "interior":
        return [f"has placement {row.get('placement')!r}, not 'interior'"]
    if slot == "decor":
        return [] if catalogue == FURNISH_DECOR_CATALOGUE else [f"is decor outside {FURNISH_DECOR_CATALOGUE}"]
    depth = float(row["length_m"] if entry.turn_deg % 180 else row["depth_m"])
    limit = MAX_COUNTER_DEPTH_M if slot == "counter" else MAX_WALL_DEPTH_M
    return [] if depth <= limit else [f"is {depth:g} m deep, more than {limit:g} m for a {slot} piece"]


def _template(name: str, d: Mapping[str, Any]) -> Template:
    b, roof = d["balcony"], d["roof"]
    hip = roof["hip"]
    return Template(
        name=name,
        applies_to=frozenset(d["applies_to"]),
        bay_m=float(d["bay_m"]),
        window=_opening(d["window"]),
        narrow_window=_opening(d["narrow_window"]),
        ground=d["ground"],
        balcony=None if b is None else Balcony(float(b["share"]), Opening(float(b["door_width_m"]), float(b["door_height_m"])),
                                               float(b["width_m"]), float(b["depth_m"]), float(b["slab_m"]), float(b["railing_m"])),
        wall_materials=tuple(d["wall_materials"]),
        parapet_m=float(roof["parapet_m"]),
        parapet_thickness_m=float(roof["parapet_thickness_m"]),
        flat_roof_materials=tuple(roof["flat_materials"]),
        hip=None if hip is None else HipRoof(float(hip["max_area_m2"]), float(hip["pitch_deg"]), float(hip["max_rise_m"]), hip["material"]),
    )


def _semantic(cfg: DetailConfig) -> list[str]:
    problems: list[str] = []
    mats = set(cfg.materials)
    seen: dict[str, str] = {}
    if cfg.landmark_template not in cfg.templates:
        problems.append(f"/landmark_template: {cfg.landmark_template!r} is not a template")
    if not cfg.storey_min_m < cfg.storey_max_m or not cfg.storey_min_m <= cfg.storey_fallback_m <= cfg.storey_max_m:
        problems.append("/storey: need min_m < max_m and fallback_m within [min_m, max_m]")
    for t in cfg.templates.values():
        where = f"/templates/{t.name}"
        for category in t.applies_to:
            if category in seen:
                problems.append(f"{where}/applies_to: {category!r} already mapped to {seen[category]!r}")
            seen[category] = t.name
        used = [*t.wall_materials, *t.flat_roof_materials, *([t.hip.material] if t.hip else [])]
        problems += [f"{where}: material {m!r} is not defined in /materials" for m in used if m not in mats]
        for label, w in (("window", t.window), ("narrow_window", t.narrow_window)):
            if w.width_m + cfg.min_pier_m > t.bay_m:
                problems.append(f"{where}/{label}: width + min_pier_m exceeds bay_m")
        if t.narrow_window.sill_m + t.narrow_window.height_m + cfg.head_clearance_m > cfg.storey_min_m and t.name != cfg.landmark_template:
            problems.append(f"{where}/narrow_window: does not fit the minimum storey ({cfg.storey_min_m} m)")
        if t.balcony is not None:
            if t.balcony.width_m + cfg.min_pier_m > t.bay_m:
                problems.append(f"{where}/balcony: width_m + min_pier_m exceeds bay_m")
            if t.balcony.door.width_m > t.balcony.width_m:
                problems.append(f"{where}/balcony: door wider than the balcony")
    for use, palette in cfg.facade_palettes.items():
        problems += [f"/facade_palettes/{use}: material {m!r} is not defined in /materials" for m in palette if m not in mats]
    shop, signs = cfg.shopfront, cfg.shops
    if shop.min_width_m > shop.max_width_m or shop.min_height_m > shop.max_height_m:
        problems.append("/doors/shopfront: a minimum exceeds its maximum")
    if signs.fascia_height_m + FASCIA_CLEAR_M > shop.head_m:
        problems.append(f"/shops/fascia/height_m: plus {FASCIA_CLEAR_M} m does not fit in /doors/shopfront/head_m")
    if 2 * signs.fascia_overhang_m > cfg.min_pier_m:
        problems.append("/shops/fascia/overhang_m: two overhangs exceed /openings/min_pier_m")
    if 2 * signs.blade_thickness_m > cfg.min_pier_m:
        problems.append("/shops/blade/thickness_m: exceeds half of /openings/min_pier_m")
    if signs.vinyl_height_m > shop.min_height_m:
        problems.append("/shops/vinyl/height_m: taller than /doors/shopfront/min_height_m")
    rule = cfg.interiors.rule
    if cfg.interiors.max_buildings > 0 and cfg.interiors.min_buildings > cfg.interiors.max_buildings:
        problems.append("/interiors: min_buildings must be at most max_buildings")
    if rule.min_area_m2 >= rule.max_area_m2:
        problems.append("/interiors/rule: min_area_m2 must be below max_area_m2")
    shop_rooms = cfg.interiors.shops
    names = [c.name for c in shop_rooms.categories]
    if len(set(names)) != len(names) or "generic" in names:
        problems.append("/interiors/shops/categories: names must be unique and not 'generic'")
    for c in (*shop_rooms.categories, shop_rooms.generic):
        for m in (c.wall, c.floor):
            if m not in mats or not m.startswith("interior_"):
                problems.append(f"/interiors/shops/{c.name}: material {m!r} must be defined in /materials and start with 'interior_'")
    if cfg.recess_depth_m >= cfg.interiors.wall_thickness_m:
        problems.append("/openings/recess_depth_m must be thinner than /interiors/wall_thickness_m")
    for name in ("frame_stone", "glass_dark", "glass_view", "shop_glass", "door_wood", "railing_metal", "urban_metal", "tree_trunk",
                 "tree_canopy", "interior_wall", "interior_floor", "stair"):
        if name not in mats:
            problems.append(f"/materials: required material {name!r} is missing")
    if cfg.landmarks.enabled:
        for key in ("wall_material", "roof_material"):
            name = getattr(cfg.landmarks, key)
            if name not in mats:
                problems.append(f"/landmarks/{key}: material {name!r} is not defined in /materials")
    return problems


def config_from_document(doc: Mapping[str, Any], *, path: str | None = None, sha256: str | None = None) -> DetailConfig:
    errors = sorted(_validator().iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))
    problems = [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in errors]
    if problems:
        raise DetailConfigError(path or "<document>", problems)
    i, lamps, ex, op = doc["interiors"], doc["urban"]["generated_street_lamps"], doc["exposure"], doc["openings"]
    cfg = DetailConfig(
        version=doc["version"],
        seed=int(doc["seed"]),
        probe_m=float(ex["probe_m"]),
        min_facade_length_m=float(ex["min_facade_length_m"]),
        courtyard_openings=bool(ex["courtyard_openings"]),
        storey_min_m=float(doc["storey"]["min_m"]),
        storey_max_m=float(doc["storey"]["max_m"]),
        storey_fallback_m=float(doc["storey"]["fallback_m"]),
        edge_margin_m=float(op["edge_margin_m"]),
        min_pier_m=float(op["min_pier_m"]),
        head_clearance_m=float(op["head_clearance_m"]),
        recess_depth_m=float(op["recess_depth_m"]),
        relief_storeys=int(op["relief_storeys"]),
        templates=MappingProxyType({name: _template(name, t) for name, t in doc["templates"].items()}),
        landmark_template=doc["landmark_template"],
        main_door=_opening(doc["doors"]["main"]),
        landmark_door=_opening(doc["doors"]["landmark"]),
        shopfront=Shopfront(**{k: float(v) for k, v in doc["doors"]["shopfront"].items()}),
        shop_door=_opening(doc["doors"]["shop_door"]),
        shops=_shops(doc["shops"]),
        shop_evidence_keys=tuple(doc["doors"]["shop_evidence_keys"]),
        shop_evidence_distance_m=float(doc["doors"]["shop_evidence_distance_m"]),
        shop_catastro_uses=MappingProxyType(dict(sorted(doc["doors"]["shop_catastro_uses"].items()))),
        facade_palettes=MappingProxyType({use: tuple(doc["facade_palettes"][use]) for use in FACADE_USES}),
        interiors=Interiors(
            enabled=bool(i["enabled"]),
            min_buildings=int(i["min_buildings"]),
            max_buildings=int(i["max_buildings"]),
            max_triangles=int(i["max_triangles"]),
            max_collision_triangles=int(i["max_collision_triangles"]),
            include_ids=tuple(i["include_ids"]),
            exclude_ids=frozenset(i["exclude_ids"]),
            rule=InteriorRule(frozenset(i["rule"]["categories"]), int(i["rule"]["min_levels"]), int(i["rule"]["max_levels"]), float(i["rule"]["min_area_m2"]),
                              float(i["rule"]["max_area_m2"]), float(i["rule"]["min_rectangularity"])),
            floors=int(i["floors"]),
            wall_thickness_m=float(i["wall_thickness_m"]),
            slab_m=float(i["slab_m"]),
            door=_opening(i["door"]),
            stair=Stair(float(i["stair"]["width_m"]), float(i["stair"]["max_riser_m"]), float(i["stair"]["tread_m"]), float(i["stair"]["landing_m"])),
            door_clearance_m=float(i["door_clearance_m"]),
            lights_max=int(i["lights_max"]),
            shops=_shop_interiors(i["shops"]),
        ),
        urban_observed=MappingProxyType(
            {kind: MappingProxyType({k: frozenset(v) for k, v in tags.items()}) for kind, tags in sorted(doc["urban"]["observed"].items())}
        ),
        street_lamps=StreetLamps(bool(lamps["enabled"]), float(lamps["spacing_m"]), float(lamps["curb_offset_m"]),
                                 float(lamps["min_building_distance_m"]), float(lamps["min_spacing_m"])),
        street_furniture=_furniture(doc["urban"].get("generated_furniture")),
        urban_clearance_m=float(doc["urban"]["clearance_m"]),
        budget=MappingProxyType({k: int(v) for k, v in doc["budget"].items() if k != "note"}),
        closeup_focus=tuple(float(v) for v in doc["preview"]["closeup_focus_m"]),  # type: ignore[arg-type]
        closeup_elevation_deg=float(doc["preview"]["closeup_elevation_deg"]),
        materials=MappingProxyType(_with_party(_with_tints(
            {name: Material(tuple(m["color"]), float(m["roughness"])) for name, m in sorted(doc["materials"].items())}  # type: ignore[arg-type]
        ))),
        landmarks=_landmarks(doc.get("landmarks")),
        document=MappingProxyType(json.loads(json.dumps(doc))),
        path=path,
        sha256=sha256,
    )
    problems = _semantic(cfg)
    furnish = cfg.interiors.shops.furnish
    if furnish.enabled:  # a default load never reads assets/
        problems += furnish_problems(furnish, [c.name for c in cfg.interiors.shops.categories], REPO_ROOT / "assets")
    if problems:
        raise DetailConfigError(path or "<document>", problems)
    return cfg


def _with_tints(materials: dict[str, Material]) -> dict[str, Material]:
    """Each material and the palette tints of the wall and roof bases among them (``building:colour``,
    ``roof:colour``; docs/07-detail.md, ADR-023): the base's roughness under the palette colour."""
    tints = tint_materials({name: m.color for name, m in materials.items()})
    return materials | {name: Material(colour, materials[tint_base(name)].roughness) for name, colour in tints.items()}


def _with_party(materials: dict[str, Material]) -> dict[str, Material]:
    """Each material and its ``party_<name>`` copy, which a blank party wall takes (docs/07-detail.md)."""
    return materials | {PARTY_PREFIX + name: material for name, material in materials.items()}


def load_detail_config(path: str | Path | None = None) -> DetailConfig:
    try:
        doc, resolved = read_json_document(path or DEFAULT_CONFIG_PATH)
    except ConfigLoadError as exc:
        raise DetailConfigError(path or DEFAULT_CONFIG_PATH, [exc.diagnostic.message]) from exc
    return config_from_document(doc, path=str(resolved), sha256=hashlib.sha256(resolved.read_bytes()).hexdigest())
