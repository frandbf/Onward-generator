"""Concrete CC0 props and, separately, the rules that place them.

Rules name categories, surfaces and caps. They do not name a file or a vendor id.
Wear is the abandoned texture plan applied by ``materials``, not a mesh.
"""

from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = (
    "vegetation",
    "vehicles",
    "barriers",
    "waste",
    "furniture",
    "structures",
    "facade",
    "wear",
)

WEAR_NOTE = (
    "Wear materials are the abandoned albedo plan (stained plaster, mossy rock, "
    "cracked concrete, leaf litter). They are not instanced meshes."
)


@dataclass(frozen=True, slots=True)
class AssetSpec:
    """One downloadable prop. Placement still goes through :class:`PlacementRule`."""

    id: str
    category: str
    role: str
    surfaces: tuple[str, ...]
    anchor: str  # ground, facade, flat
    use: str
    lane: str = "forbid"
    clearance_m: float = 0.0


@dataclass(frozen=True, slots=True)
class PlacementRule:
    """Where a category may sit. No asset ids."""

    id: str
    category: str
    surfaces: tuple[str, ...]
    lane: str
    cap: int
    roles: tuple[str, ...] = ()
    clearance_m: float = 0.0


SIDEWALK_COVER_RULE = "manhole_sidewalk"


def _spec(asset_id: str, category: str, role: str, surfaces: tuple[str, ...], anchor: str, use: str, **kwargs: object) -> AssetSpec:
    return AssetSpec(asset_id, category, role, surfaces, anchor, use, **kwargs)  # type: ignore[arg-type]


# Named Poly Haven models the pipeline is allowed to download. Vegetation is a
# small checked subset, not the nature/plants/trees catalog indexes.
_WRECK = ("curb", "carriageway", "sidewalk", "yard", "empty_lot", "plaza")
_HELICOPTER = ("curb", "carriageway", "courtyard", "yard", "empty_lot", "plaza", "park")
_DEBRIS = ("curb", "carriageway", "sidewalk", "courtyard", "yard", "empty_lot", "plaza", "park")
_WASTE = ("curb", "carriageway", "sidewalk", "courtyard", "yard", "empty_lot", "plaza")
_FURNITURE = ("curb", "sidewalk", "courtyard", "yard", "empty_lot", "plaza", "park")
_BUILDING = ("block_edge", "curb", "yard", "empty_lot", "plaza", "park")
_EDGE = ("block_edge", "carriageway")
_BARRIER = ("curb", "carriageway", "sidewalk", "plaza")

NAMED_ASSETS: tuple[AssetSpec, ...] = (
    _spec("covered_car", "vehicles", "wreck", _WRECK, "ground", "abandoned car at the curb, in the street, on a sidewalk, in a yard or lot, or in a plaza"),
    _spec("rusted_wheel_rim_02", "vehicles", "debris", _DEBRIS, "ground", "loose rim on open ground"),
    _spec("old_tyre", "vehicles", "debris", _DEBRIS, "ground", "tyre on open ground"),
    _spec("large_iron_gate", "structures", "gate", _EDGE, "ground", "gate at a block edge or across a street"),
    _spec("modular_fire_escape", "facade", "fire_escape", ("facade",), "facade", "fire escape against a wall"),
    _spec("metal_trash_can", "waste", "trash", _WASTE, "ground", "trash can in a yard, on a sidewalk or in the street"),
    _spec("Barrel_01", "waste", "barrel", _WASTE, "ground", "barrel in a yard, on a sidewalk or in the street"),
    _spec("barrel_stove", "waste", "barrel", _WASTE, "ground", "barrel stove in a yard, on a sidewalk or in the street"),
    _spec("Barrel_02", "waste", "barrel", _WASTE, "ground", "barrel in a yard, on a sidewalk or in the street"),
    _spec("can_rusted", "waste", "can", _WASTE, "ground", "rusted can in a yard, on a sidewalk or in the street"),
    _spec("small_lpg_tank", "waste", "tank", _WASTE, "ground", "LPG tank in a yard, on a sidewalk or in the street"),
    _spec("propane_tank", "waste", "tank", _WASTE, "ground", "propane tank in a yard, on a sidewalk or in the street"),
    _spec("industrial_storage_cart", "furniture", "cart", _FURNITURE + ("carriageway",), "ground", "cart in a yard, on a sidewalk or left in the street"),
    _spec("old_bed_frame", "furniture", "bed", _FURNITURE, "ground", "bed frame in a yard, on a sidewalk, at the curb or in a plaza"),
    _spec("wooden_picnic_table", "furniture", "table", _FURNITURE, "ground", "picnic table in a yard, park, plaza or at the curb"),
    _spec("exterior_aircon_unit", "facade", "aircon", ("facade",), "facade", "air conditioner on a wall"),
    _spec("security_camera_02", "facade", "camera", ("facade",), "facade", "camera on a wall"),
    _spec("security_camera_01", "facade", "camera", ("facade",), "facade", "camera on a wall"),
    _spec("concrete_road_barrier", "barriers", "barrier", _BARRIER, "ground", "barrier at the curb, across the street, on a sidewalk or in a plaza"),
    _spec("concrete_road_barrier_02", "barriers", "barrier", _BARRIER, "ground", "barrier at the curb, across the street, on a sidewalk or in a plaza"),
    _spec("water_manhole_cover", "barriers", "manhole", ("carriageway", "sidewalk"), "flat", "manhole on the carriageway or a sidewalk", lane="allow"),
)

# Few plants and one bush. No ivy model exists on the Poly Haven model API.
VEGETATION_ASSETS: tuple[AssetSpec, ...] = (
    _spec("fern_02", "vegetation", "plant", ("sidewalk", "yard", "empty_lot"), "ground", "fern at sidewalk edges and yards"),
    _spec("flower_empodium", "vegetation", "plant", ("sidewalk", "yard", "empty_lot"), "ground", "small flower in cracks and yards"),
    _spec("weed_plant_02", "vegetation", "plant", ("sidewalk", "yard", "empty_lot"), "ground", "weed at edges and empty lots"),
    _spec("shrub_03", "vegetation", "bush", ("courtyard", "yard", "plaza", "park"), "ground", "bush in a yard, park or plaza"),
)

ALL_ASSETS: tuple[AssetSpec, ...] = NAMED_ASSETS + VEGETATION_ASSETS
ASSETS_BY_ID = {spec.id: spec for spec in ALL_ASSETS}

PLACEMENT_RULES: tuple[PlacementRule, ...] = (
    PlacementRule("vehicles_curb", "vehicles", ("curb", "carriageway", "sidewalk", "courtyard", "yard", "empty_lot", "plaza", "park"), "forbid", 6),
    PlacementRule("waste_ground", "waste", ("curb", "carriageway", "sidewalk", "courtyard", "yard", "empty_lot", "plaza"), "forbid", 12),
    PlacementRule("furniture_off_lane", "furniture", ("curb", "carriageway", "sidewalk", "courtyard", "yard", "empty_lot", "plaza", "park"), "forbid", 6),
    PlacementRule("structures_edge", "structures", ("block_edge", "curb", "carriageway", "yard", "empty_lot", "plaza", "park"), "forbid", 4),
    PlacementRule("facade_wall", "facade", ("facade",), "forbid", 8),
    PlacementRule("barriers_curb", "barriers", ("curb", "carriageway", "sidewalk", "plaza"), "forbid", 4, ("barrier",)),
    PlacementRule("manhole_lane", "barriers", ("carriageway",), "allow", 2, ("manhole",)),
    # Placed by ``dressing.sidewalk_covers`` at seeded spacing, not on the shared spot grid.
    PlacementRule(SIDEWALK_COVER_RULE, "barriers", ("sidewalk",), "forbid", 6, ("manhole",)),
    PlacementRule("plants", "vegetation", ("sidewalk", "yard", "empty_lot"), "forbid", 8, ("plant",)),
    PlacementRule("bushes", "vegetation", ("courtyard", "yard", "plaza", "park"), "forbid", 4, ("bush",)),
    PlacementRule("trees", "vegetation", ("park", "plaza", "sidewalk", "courtyard", "yard", "empty_lot"), "forbid", 2, ("tree",), 3.5),
)

RULES_BY_ID = {rule.id: rule for rule in PLACEMENT_RULES}

FACADE_Z_M = {"aircon": 2.4, "camera": 3.1, "fire_escape": 0.0}


def rules_for(spec: AssetSpec) -> tuple[PlacementRule, ...]:
    """Rules that can place this spec. Matching uses category, role and surface, not the asset id."""
    found: list[PlacementRule] = []
    surfaces = set(spec.surfaces)
    for rule in PLACEMENT_RULES:
        if rule.category != spec.category:
            continue
        if rule.roles and spec.role not in rule.roles:
            continue
        if not surfaces & set(rule.surfaces):
            continue
        found.append(rule)
    return tuple(found)
