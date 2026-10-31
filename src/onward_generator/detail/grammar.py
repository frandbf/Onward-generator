"""Roof and wall choices from OSM tags, with an estimated grammar when the tag is absent.

Observed tags win. Estimated choices are marked ``estimated`` and are not written
back into the OSM extract. Without a material tag, the wall comes from the palette
of the building's facade use, and differs from its neighbours where the palette allows.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from onward_generator.detail.config import FACADE_USES, HipRoof, Template
from onward_generator.detail.facades import stable_choice, stable_unit
from onward_generator.detail.roofs import APPROXIMATIONS, PITCHED_SHAPES, pitched_spec
from onward_generator.detail.s3db import ROOF_PALETTE, ROOF_TINT_BASES, WALL_PALETTE, WALL_TINT_BASES, Tint, quantize_colour, tint_material
from onward_generator.generation.config import Material

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

_BUILDING_USES = {
    **dict.fromkeys(("apartments", "residential", "house", "detached", "semidetached_house", "terrace",
                     "dormitory", "bungalow"), "residential"),
    **dict.fromkeys(("school", "kindergarten", "university", "college", "public", "civic", "government",
                     "townhall", "hospital", "fire_station", "education"), "institutional"),
    **dict.fromkeys(("office", "commercial", "bank"), "office"),
    **dict.fromkeys(("retail", "supermarket", "kiosk"), "retail"),
    **dict.fromkeys(("industrial", "warehouse", "manufacture"), "industrial"),
}
# OSM key → value → facade use. ``*`` matches any value but ``no``.
FACADE_USE_TAGS: dict[str, dict[str, str]] = {
    "building": _BUILDING_USES,
    "building:use": _BUILDING_USES,
    "amenity": {
        **dict.fromkeys(("school", "kindergarten", "college", "university", "library", "townhall", "courthouse",
                         "police", "hospital", "clinic", "fire_station", "community_centre"), "institutional"),
        **dict.fromkeys(("bank", "bureau_de_change"), "office"),
        **dict.fromkeys(("restaurant", "cafe", "bar", "pub", "fast_food", "pharmacy", "ice_cream"), "retail"),
    },
    "office": {"government": "institutional", "*": "office"},
    "shop": {"*": "retail"},
    "tourism": {"museum": "historic", "gallery": "historic", "hotel": "office"},
    "historic": dict.fromkeys(("building", "castle", "palace", "manor", "monastery"), "historic"),
}
# How a tag reached the building, most direct first. A POI only near the footprint is an estimate.
USE_MATCHES = ("building", "poi_inside", "enclosing_area", "poi_near")


def facade_use(tags: Mapping[str, str]) -> tuple[str, str] | None:
    """``(use, "key=value")`` for the most specific use these tags name, or ``None``."""
    found: list[tuple[int, str, str]] = []
    for key, values in FACADE_USE_TAGS.items():
        raw = _first(dict(tags), key)
        use = values.get(raw) or (values.get("*") if raw not in ("", "no") else None)
        if use is not None:
            found.append((FACADE_USES.index(use), use, f"{key}={raw}"))
    return min(found)[1:] if found else None


@dataclass(frozen=True, slots=True)
class FacadeUse:
    use: str
    origin: str  # observed | estimated
    source_id: str | None = None
    tag: str | None = None
    match: str | None = None

    def to_record(self) -> dict[str, Any]:
        evidence = [] if self.source_id is None else [{"source_id": self.source_id, "tag": self.tag, "match": self.match}]
        return {"use": self.use, "origin": self.origin, "evidence": evidence}


def resolve_use(building_id: str, tags: Mapping[str, str], related: Sequence[tuple[str, Mapping[str, str], str]]) -> FacadeUse:
    """The building's facade use from its own tags, then from ``related`` ``(source id, tags, match)``.

    The most direct match wins, then the most specific use, then the lowest id. Without
    any evidence the use is ``residential``, marked ``estimated``.
    """
    own = facade_use(tags)
    if own is not None:
        return FacadeUse(own[0], "observed", building_id, own[1], "building")
    ranked: list[tuple[int, int, str, str, str]] = []
    for source_id, source_tags, match in related:
        found = facade_use(source_tags)
        if found is not None:
            ranked.append((USE_MATCHES.index(match), FACADE_USES.index(found[0]), source_id, found[1], match))
    if not ranked:
        return FacadeUse("residential", "estimated")
    _, rank, source_id, tag, match = min(ranked)
    return FacadeUse(FACADE_USES[rank], "estimated" if match == "poi_near" else "observed", source_id, tag, match)


def appearance_key(building_id: str, feature: Mapping[str, Any]) -> str:
    """The identity a building's wall, use and roof are drawn for (ADR-016).

    The sub-buildings a Catastro split made of one parcel are one building: ``parcel:<refcat>``.
    A sub-building without a parcel reference takes its OSM parent's id; any other building its own id.
    """
    refcat = (feature.get("subdivision") or {}).get("refcat")
    if refcat:
        return f"parcel:{refcat}"
    return str((feature.get("parent") or {}).get("id") or building_id)


def _first(tags: dict[str, str], key: str) -> str:
    raw = tags.get(key) or ""
    return raw.split(";")[0].strip().lower()


def _tinted(colours: Mapping[str, Material] | None, base: str, raw: str, palette: tuple[Tint, ...]) -> str | None:
    """``base`` under the palette entry nearest ``raw``, when ``raw`` is a colour and ``colours`` defines that tint."""
    tint = quantize_colour(raw, palette)
    if tint is None or not colours:
        return None
    name = tint_material(base, tint)
    return name if name in colours else None


def tagged_wall(tags: Mapping[str, str], colours: Mapping[str, Material] | None = None) -> tuple[str, str] | None:
    """``(material, tag)`` when an OSM tag names the wall. ``None`` otherwise.

    ``building:colour`` wins: the nearest ``WALL_PALETTE`` tint of the texture ``building:material`` (or
    ``material``) names when it is one of ``WALL_TINT_BASES``, else of plain plaster. Without a colour the
    material tag alone picks the catalogue material. ``colours`` holds the defined materials; with none,
    colours are not read.
    """
    material: tuple[str, str] | None = None
    for key in ("building:material", "material"):
        raw = _first(dict(tags), key)
        if raw:
            material = (WALL_MATERIALS[raw], key) if raw in WALL_MATERIALS else None
            break
    base = material[0] if material and material[0] in WALL_TINT_BASES else WALL_TINT_BASES[0]
    tinted = _tinted(colours, base, _first(dict(tags), "building:colour"), WALL_PALETTE)
    return (tinted, "building:colour") if tinted else material


def tagged_roof(tags: Mapping[str, str], colours: Mapping[str, Material] | None = None) -> tuple[str, str] | None:
    """``(material, tag)`` when an OSM tag names the roof. ``None`` otherwise.

    ``roof:colour`` wins: the nearest ``ROOF_PALETTE`` tint of the material ``roof:material`` names, else
    of tile under a pitched ``roof:shape`` and of a flat roof otherwise. Then ``roof:material`` alone.
    """
    material = ROOF_MATERIALS.get(_first(dict(tags), "roof:material"))
    shape = _first(dict(tags), "roof:shape")
    pitched = APPROXIMATIONS.get(shape, shape) in PITCHED_SHAPES
    base = material or ROOF_TINT_BASES[0 if pitched else 1]
    tinted = _tinted(colours, base, _first(dict(tags), "roof:colour"), ROOF_PALETTE)
    if tinted:
        return tinted, "roof:colour"
    return (material, "roof:material") if material else None


def choose_wall(
    tags: dict[str, str],
    palette: tuple[str, ...],
    seed: int,
    building_id: str,
    avoid: Collection[str] = (),
    colours: Mapping[str, Material] | None = None,
) -> tuple[str, str]:
    """Return ``(material, origin)``. Origin is ``observed`` or ``generated``.

    A wall tag wins (``tagged_wall``: the colour, then the material). Without one the palette is
    ranked by ``sha256(seed, building, material)``, and the first material not in ``avoid`` (the
    neighbours' walls) wins. When every palette material is in ``avoid``, the first one is used.
    """
    tagged = tagged_wall(tags, colours)
    if tagged is not None:
        return tagged[0], "observed"
    ranked = sorted(palette, key=lambda material: stable_unit(seed, building_id, "wall", material))
    return next((m for m in ranked if m not in avoid), ranked[0]), "generated"


def choose_roof(
    tags: dict[str, str],
    template: Template,
    seed: int,
    building_id: str,
    area_m2: float,
    *,
    convex: bool = True,
    storey_m: float = 3.0,
    colours: Mapping[str, Material] | None = None,
) -> tuple[Template, str, str, str | None]:
    """Return ``(template, roof_material, origin, roof:shape or None)``.

    An OSM ``roof:shape`` the roof builder draws (``detail/roofs.py``) replaces the template roof on
    any footprint; ``build_shell`` falls back to a flat roof when the shape does not fit it. ``flat``
    keeps the template. Without a shape tag, convex footprints of 160 m² or less may get an estimated
    hip. The roof material is ``tagged_roof``, else the template's.
    """
    shape = _first(tags, "roof:shape")
    tagged = tagged_roof(tags, colours)
    tagged_material = tagged[0] if tagged else None
    material = tagged_material or (template.hip.material if template.hip else "roof_tile")
    pitched = pitched_spec(tags, material, storey_m)
    if pitched is not None:
        return replace(template, parapet_m=0.0, hip=None, pitched=pitched), material, "observed", shape
    if shape in FLAT_SHAPES:
        material = tagged_material or stable_choice(seed, template.flat_roof_materials, building_id, "roof")
        return template, material, "observed", shape
    if (
        not shape
        and template.hip is None
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
