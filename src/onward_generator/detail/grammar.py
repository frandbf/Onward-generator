"""Roof and wall choices from OSM tags, with an estimated grammar when the tag is absent.

Observed tags win. Estimated choices are marked ``estimated`` and are not written
back into the OSM extract. Brick is a minority estimated wall, not the default.
"""

from __future__ import annotations

from dataclasses import replace

from onward_generator.detail.config import HipRoof, Template
from onward_generator.detail.facades import stable_choice, stable_unit

HIP_SHAPES = frozenset({"hipped", "gabled", "pyramidal", "skillion", "half-hipped", "gambrel", "round"})
FLAT_SHAPES = frozenset({"flat"})

# OSM roof:material → a material the detail catalogue already defines.
ROOF_MATERIALS = {
    "tiles": "roof_tile",
    "roof_tiles": "roof_tile",
    "tile": "roof_tile",
    "concrete": "roof_flat_grey",
    "tar_paper": "roof_flat_grey",
    "metal": "roof_flat_grey",
    "eternit": "roof_flat_grey",
}

# OSM building:material / material → catalogue id. Brick only when the tag says brick.
WALL_MATERIALS = {
    "brick": "brick_red",
    "stone": "stone_warm",
    "sandstone": "stone_warm",
    "limestone": "stone_light",
    "plaster": "plaster_cream",
    "concrete": "plaster_white",
    "cement_block": "plaster_white",
}

# Estimated hip only on small convex footprints. The phase-04 fixture building 311 is
# 180 m² and must keep the template parapet.
ESTIMATED_HIP_MAX_AREA_M2 = 160.0
ESTIMATED_BRICK_SHARE = 0.08
ESTIMATED_STONE_SHARE = 0.22


def _first(tags: dict[str, str], key: str) -> str:
    raw = tags.get(key) or ""
    return raw.split(";")[0].strip().lower()


def choose_wall(tags: dict[str, str], template: Template, seed: int, building_id: str) -> tuple[str, str]:
    """Return ``(material, origin)``. Origin is ``observed``, ``estimated`` or ``generated``."""
    raw = _first(tags, "building:material") or _first(tags, "material")
    if raw in WALL_MATERIALS:
        return WALL_MATERIALS[raw], "observed"
    unit = stable_unit(seed, building_id, "wall_grammar")
    if unit < ESTIMATED_BRICK_SHARE:
        return "brick_red", "estimated"
    if unit < ESTIMATED_STONE_SHARE:
        return "stone_warm", "estimated"
    return stable_choice(seed, template.wall_materials, building_id, "wall"), "generated"


def choose_roof(
    tags: dict[str, str],
    template: Template,
    seed: int,
    building_id: str,
    area_m2: float,
    *,
    convex: bool = True,
) -> tuple[Template, str, str, str | None]:
    """Return ``(template, roof_material, origin, roof:shape or None)``.

    A hipped/gabled OSM tag forces a hip on footprints up to 800 m².
    Without a tag, convex footprints of 160 m² or less may get an estimated hip.
    Everything else keeps the template roof.
    """
    shape = _first(tags, "roof:shape")
    tagged_material = ROOF_MATERIALS.get(_first(tags, "roof:material"))
    if shape in HIP_SHAPES and area_m2 <= 800.0:
        material = tagged_material or (template.hip.material if template.hip else "roof_tile")
        hip = HipRoof(max(area_m2, 1.0), 30.0, 4.0, material)
        return replace(template, parapet_m=0.0, hip=hip), material, "observed", shape
    if shape in FLAT_SHAPES:
        material = tagged_material or stable_choice(seed, template.flat_roof_materials, building_id, "roof")
        return template, material, "observed", shape
    if (
        template.hip is None
        and template.parapet_m > 0
        and convex
        and area_m2 <= ESTIMATED_HIP_MAX_AREA_M2
        and stable_unit(seed, building_id, "roof_grammar") < 0.55
    ):
        material = "roof_tile"
        hip = HipRoof(ESTIMATED_HIP_MAX_AREA_M2, 28.0, 2.5, material)
        return replace(template, parapet_m=0.0, hip=hip), material, "estimated", None
    material = tagged_material or stable_choice(seed, template.flat_roof_materials, building_id, "roof")
    origin = "observed" if tagged_material else "generated"
    return template, material, origin, shape or None
