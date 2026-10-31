"""Building typology and construction era (ADR-024): computed, recorded and counted, never drawn.

The era is observed (an OSM date tag, or the Catastro ``dateOfConstruction`` of the parcel) or unknown;
it is never estimated. The morphology is measured on the footprint in the scene's metric CRS. The typology
is a rule over both and is always ``estimated``. The parts of one parcel (``appearance_key``) are one
building here, as for the facade use.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from shapely import STRtree, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.detail.config import DetailConfig
from onward_generator.detail.facades import refine_exposure, storeys_for
from onward_generator.detail.grammar import FacadeUse
from onward_generator.generation.scene import Issue
from onward_generator.sources.es.catastro import PLUGIN_ID as CATASTRO_ID
from onward_generator.sources.registry import OSM_EVIDENCE

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "typology.default.json"
SCHEMA_PATH = REPO_ROOT / "config" / "typology.schema.json"

PERIODS = (("pre_1900", 1899), ("1900_1939", 1939), ("1940_1959", 1959), ("1960_1979", 1979), ("1980_1999", 1999))
"""Each period and its last year; a later year is ``2000_plus``."""
LATE_PERIOD = "2000_plus"
MIN_YEAR, MAX_YEAR = 1000, 2100
SHARE_MIN_RUN_M = 0.1
"""An exposed run shorter than this is the end of a probe, not a gap between two neighbours."""
_LEADING_YEAR = re.compile(r"~?(\d{4})(?!\d)")


class TypologyConfigError(ValueError):
    def __init__(self, path: Path | str, problems: list[str]) -> None:
        super().__init__(f"{path}: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class UseRule:
    uses: frozenset[str]
    catastro_uses: frozenset[str]
    categories: frozenset[str]

    def matches(self, use: str, catastro_use: str | None, category: str) -> bool:
        return use in self.uses or catastro_use in self.catastro_uses or category in self.categories


@dataclass(frozen=True, slots=True)
class TypologyConfig:
    version: str
    era_tags: tuple[str, ...]
    minor_categories: frozenset[str]
    civic: UseRule
    industrial: UseRule
    shed_max_storeys: int
    shed_min_area_m2: float
    tower_min_storeys: int
    attached_min_share: float
    historic_core_periods: frozenset[str]
    house_max_storeys: int
    house_max_area_m2: float
    closed_min_share: float
    closed_min_storeys: int
    closed_max_storeys: int
    open_min_storeys: int
    open_max_storeys: int
    open_min_area_m2: float
    slab_min_elongation: float
    point_min_compactness: float
    path: str | None = None
    sha256: str | None = None


@cache
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _use_rule(d: Mapping[str, Any]) -> UseRule:
    return UseRule(frozenset(d["uses"]), frozenset(d["catastro_uses"]), frozenset(d["categories"]))


def typology_config_from_document(doc: Mapping[str, Any], *, path: str | None = None, sha256: str | None = None) -> TypologyConfig:
    problems = [f"/{'/'.join(map(str, e.absolute_path))}: {e.message}"
                for e in sorted(_validator().iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))]
    if problems:
        raise TypologyConfigError(path or "<document>", problems)
    ind, house, closed, open_ = doc["industrial"], doc["house"], doc["closed_block"], doc["open_block"]
    return TypologyConfig(
        version=doc["version"], era_tags=tuple(doc["era_tags"]), minor_categories=frozenset(doc["minor_categories"]),
        civic=_use_rule(doc["civic"]), industrial=_use_rule(ind),
        shed_max_storeys=int(ind["shed_max_storeys"]), shed_min_area_m2=float(ind["shed_min_area_m2"]),
        tower_min_storeys=int(doc["tower_min_storeys"]), attached_min_share=float(doc["attached_min_share"]),
        historic_core_periods=frozenset(doc["historic_core_periods"]),
        house_max_storeys=int(house["max_storeys"]), house_max_area_m2=float(house["max_area_m2"]),
        closed_min_share=float(closed["min_share"]), closed_min_storeys=int(closed["min_storeys"]), closed_max_storeys=int(closed["max_storeys"]),
        open_min_storeys=int(open_["min_storeys"]), open_max_storeys=int(open_["max_storeys"]), open_min_area_m2=float(open_["min_area_m2"]),
        slab_min_elongation=float(open_["slab_min_elongation"]), point_min_compactness=float(open_["point_min_compactness"]),
        path=path, sha256=sha256,
    )


def load_typology_config(path: str | Path | None = None) -> TypologyConfig:
    try:
        doc, resolved = read_json_document(path or DEFAULT_CONFIG_PATH)
    except ConfigLoadError as exc:
        raise TypologyConfigError(path or DEFAULT_CONFIG_PATH, [exc.diagnostic.message]) from exc
    return typology_config_from_document(doc, path=str(resolved), sha256=hashlib.sha256(resolved.read_bytes()).hexdigest())


# --- era -------------------------------------------------------------------------------------------


def parse_year(value: str) -> int | None:
    """The leading four-digit year of an OSM date (``1890``, ``1890-05-01``, ``~1890``, ``1960s``), else ``None``.

    A century (``C19``), a phrase (``before 1900``), five digits or a year outside 1000–2100 is not a year.
    """
    match = _LEADING_YEAR.match(value.strip())
    if match is None:
        return None
    year = int(match.group(1))
    return year if MIN_YEAR <= year <= MAX_YEAR else None


def era_period(year: int) -> str:
    return next((name for name, last in PERIODS if year <= last), LATE_PERIOD)


@dataclass(frozen=True, slots=True)
class Era:
    year: int
    source: str  # a config/sources.json evidence id
    evidence: Mapping[str, Any]

    def to_record(self) -> dict[str, Any]:
        return {"year": self.year, "period": era_period(self.year), "origin": "observed", "source": self.source, "evidence": dict(self.evidence)}


def _osm_era(members: Sequence[str], feature_of: Mapping[str, Mapping[str, Any]], tags: Sequence[str],
             unparsable: dict[tuple[str, str], str]) -> Era | None:
    for bid in members:
        own = feature_of[bid].get("tags") or {}
        for key in tags:
            value = own.get(key)
            if not value:
                continue
            year = parse_year(str(value))
            if year is not None:
                return Era(year, OSM_EVIDENCE, {"source_id": bid, "tag": key, "value": str(value)})
            unparsable.setdefault((key, str(value)), bid)
    return None


def _catastro_era(members: Sequence[str], feature_of: Mapping[str, Mapping[str, Any]]) -> Era | None:
    for bid in members:
        use = feature_of[bid].get("catastro_use") or {}
        if use.get("construction_year") is not None:
            return Era(int(use["construction_year"]), CATASTRO_ID, {
                "refcat": use["refcat"], "provider": use["provider"], "license": use["license"], "attribution": use["attribution"]})
    return None


def building_era(members: Sequence[str], feature_of: Mapping[str, Mapping[str, Any]], evidence: Sequence[str],
                 cfg: TypologyConfig, issues: list[Issue]) -> Era | None:
    """The construction year of a building (the parts of one parcel together) from the first source in
    ``evidence`` that has one: OSM ``era_tags``, then the Catastro ``construction_year``. Overture has none.

    An OSM date with no leading year is an ``era_unparsable`` issue (info) and gives no era.
    """
    unparsable: dict[tuple[str, str], str] = {}
    era = None
    for source in evidence:
        if source == OSM_EVIDENCE:
            era = _osm_era(members, feature_of, cfg.era_tags, unparsable)
        elif source == CATASTRO_ID:
            era = _catastro_era(members, feature_of)
        if era is not None:
            break
    issues.extend(Issue("era_unparsable", "info", bid, f"{key}={value!r} has no leading year") for (key, value), bid in unparsable.items())
    return era


# --- morphology ------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Morphology:
    area_m2: float
    perimeter_m: float
    compactness: float  # 4πA/P², 1 for a circle
    elongation: float  # long / short side of the minimum rotated rectangle
    shared_wall_share: float  # share of the outer walls whose probe meets another building


def shared_wall_share(geometry: BaseGeometry, others: BaseGeometry, cfg: DetailConfig) -> float:
    """The share of the outer ring length that ``refine_exposure`` marks as against a neighbour.

    The probe is the facade one (``probe_m`` outside each edge), with a minimum run of ``SHARE_MIN_RUN_M``
    instead of the facade's: a short exposed run between two neighbours stays exposed, and the few
    centimetres the probe leaves at each end of a wall against a neighbour do not count as exposed.
    """
    _, segments = refine_exposure(geometry, others, replace(cfg, min_facade_length_m=SHARE_MIN_RUN_M))
    outer = [s for s in segments if s.ring == 0]
    total = sum(s.length for s in outer)
    return sum(s.length for s in outer if not s.exposed) / total


def morphology(geometry: BaseGeometry, others: BaseGeometry, cfg: DetailConfig) -> Morphology:
    """Footprint measures in the scene CRS (metres): ``others`` is every footprint of another building nearby."""
    area, perimeter = geometry.area, geometry.length
    rect = list(geometry.minimum_rotated_rectangle.exterior.coords)
    sides = sorted(math.dist(rect[k], rect[k + 1]) for k in range(2))
    return Morphology(area, perimeter, 4 * math.pi * area / perimeter**2, sides[1] / sides[0], shared_wall_share(geometry, others, cfg))


# --- classification --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TypologyInputs:
    morphology: Morphology
    height_m: float
    storeys: int
    category: str
    use: str
    catastro_use: str | None
    landmark: bool
    period: str | None


def classify(x: TypologyInputs, c: TypologyConfig) -> tuple[str, str]:
    """``(typology, rule)``: the first rule that matches, in the order of ``docs/07-detail.md`` *Typology*."""
    m = x.morphology
    if x.landmark:
        return "landmark", "landmark:heights_landmark"
    if x.category in c.minor_categories:
        return "other", "other:minor_category"
    if c.civic.matches(x.use, x.catastro_use, x.category):
        return "civic", "civic:use"
    if c.industrial.matches(x.use, x.catastro_use, x.category):
        return "industrial_shed", "industrial_shed:use"
    if x.storeys <= c.shed_max_storeys and m.area_m2 >= c.shed_min_area_m2:
        return "industrial_shed", "industrial_shed:large_low"
    if x.storeys >= c.tower_min_storeys:
        return "tower", "tower:storeys"
    attached = m.shared_wall_share >= c.attached_min_share
    if attached and x.period in c.historic_core_periods:
        return "historic_core", "historic_core:attached_early_era"
    if x.storeys <= c.house_max_storeys and m.area_m2 <= c.house_max_area_m2:
        return ("row_house", "row_house:attached_small_low") if attached else ("detached_house", "detached_house:detached_small_low")
    if m.shared_wall_share >= c.closed_min_share:
        if c.closed_min_storeys <= x.storeys <= c.closed_max_storeys:
            return "closed_block", "closed_block:attached_mid_rise"
    elif c.open_min_storeys <= x.storeys <= c.open_max_storeys and m.area_m2 >= c.open_min_area_m2:
        if m.elongation >= c.slab_min_elongation:
            return "open_block", "open_block:slab"
        if m.compactness >= c.point_min_compactness:
            return "open_block", "open_block:point"
    return "other", "other:no_rule"


def typology_record(x: TypologyInputs, era: Era | None, use: FacadeUse, key: str, parts: int, storeys_origin: str, c: TypologyConfig) -> dict[str, Any]:
    typology, rule = classify(x, c)
    m = x.morphology
    return {
        "typology": typology, "origin": "estimated", "rule": rule,
        "inputs": {
            "key": key, "parts": parts, "area_m2": round(m.area_m2, 2), "perimeter_m": round(m.perimeter_m, 2),
            "compactness": round(m.compactness, 4), "elongation": round(m.elongation, 3), "shared_wall_share": round(m.shared_wall_share, 4),
            "height_m": round(x.height_m, 2), "storeys": x.storeys, "storeys_origin": storeys_origin, "category": x.category,
            "use": x.use, "use_origin": use.origin, "catastro_use": x.catastro_use, "landmark": x.landmark, "period": x.period,
        },
        "era": era.to_record() if era is not None else None,
    }


def building_typologies(
    ids: Sequence[str],
    footprints: Mapping[str, BaseGeometry],
    tree: STRtree,
    key_of: Mapping[str, str],
    feature_of: Mapping[str, Mapping[str, Any]],
    height_of: Mapping[str, Mapping[str, Any]],
    heights_m: Mapping[str, float],
    uses: Mapping[str, FacadeUse],
    evidence: Sequence[str],
    cfg: DetailConfig,
    tcfg: TypologyConfig,
    issues: list[Issue],
) -> dict[str, dict[str, Any]]:
    """The typology record of every building; the parts of one parcel (same ``key_of``) share one.

    The parcel is measured as the union of its parts, against the footprints of other keys within
    ``probe_m``; its height and storeys are those of its tallest part. ``tree`` indexes ``footprints`` in ``ids`` order.
    """
    members: dict[str, list[str]] = {}
    for bid in ids:
        members.setdefault(key_of[bid], []).append(bid)
    out: dict[str, dict[str, Any]] = {}
    for key, parts in members.items():
        geometry = union_all([footprints[b] for b in parts])
        near = {ids[k] for b in parts for k in tree.query(footprints[b], predicate="dwithin", distance=cfg.probe_m) if key_of[ids[k]] != key}
        others = union_all([footprints[n] for n in sorted(near)])
        tallest = max(parts, key=lambda b: heights_m[b])
        storeys = storeys_for(heights_m[tallest], height_of[tallest].get("estimated_levels"), False, cfg)  # pragma: no mutate: the third argument is only read for its truth
        first = height_of[parts[0]]
        catastro = (feature_of[parts[0]].get("catastro_use") or {}).get("use")
        era = building_era(parts, feature_of, evidence, tcfg, issues)
        x = TypologyInputs(morphology(geometry, others, cfg), heights_m[tallest], storeys.count, str(first.get("category")),
                           uses[parts[0]].use, catastro, any(bool(height_of[b].get("landmark")) for b in parts),
                           era_period(era.year) if era is not None else None)
        record = typology_record(x, era, uses[parts[0]], key, len(parts), storeys.origin, tcfg)
        out.update(dict.fromkeys(parts, record))
    return out
