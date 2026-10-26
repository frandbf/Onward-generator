"""Recommend catalogue materials for surface groups. Does not apply them."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from onward_generator.ai.providers import Provider, ProviderError, ProviderResult
from onward_generator.config.diagnostics import Diagnostic
from onward_generator.config.loader import loads_strict
from onward_generator.dress.catalog import MaterialCatalog
from onward_generator.dress.surfaces import SurfaceCollection, SurfaceGroup

REPO_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_SCHEMA_PATH = REPO_ROOT / "config" / "dress" / "classify.output.schema.json"
INPUT_SCHEMA_PATH = REPO_ROOT / "config" / "dress" / "surfaces.input.schema.json"
PROMPT_VERSION = "dress-classify-0.1.0"
MAX_RESPONSE_BYTES = 256 * 1024
LOW_CONFIDENCE = 0.4

SYSTEM_PROMPT = """You assign materials to grouped 3D map surfaces.

Rules:
- Choose recommended_material_id only from the catalogue ids in the user message.
- Do not invent materials, file paths, textures, colours or properties.
- OSM tags already applied as osm_override must be kept: copy that id.
- Do not infer building use from shape or size. Missing context is unknown.
- Orientation distinguishes wall vs roof vs ground; it does not pick the material by itself.
- Historic walls: historic_plaster_cream or limestone_warm according to tags and context.
- Brick: brick_red_wall only if OSM building:material=brick. Never the default facade.
- Roofs: terracotta_roof only if geometry or OSM roof tags justify tiles; otherwise a flat roof id.
- Streets and plazas: asphalt_road or cobblestone_paving according to surface tags and role.
- If unsure, pick the role's safe material, lower confidence and set needs_review true.
- Return one recommendation per group. surface_id must be that group's group_id.
- Respond with JSON only that matches the supplied schema.
"""


class ClassifyError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        super().__init__("\n".join(str(d) for d in diagnostics))
        self.diagnostics = diagnostics


@dataclass(frozen=True, slots=True)
class Recommendation:
    surface_id: str
    recommended_material_id: str
    confidence: float
    reason: str
    alternative_material_ids: tuple[str, ...]
    needs_review: bool
    source: str
    rejected: tuple[str, ...] = ()

    def to_document(self) -> dict[str, Any]:
        return {
            "surface_id": self.surface_id,
            "recommended_material_id": self.recommended_material_id,
            "confidence": self.confidence,
            "reason": self.reason,
            "alternative_material_ids": list(self.alternative_material_ids),
            "needs_review": self.needs_review,
            "source": self.source,
            "rejected": list(self.rejected),
        }


@dataclass(frozen=True, slots=True)
class ClassifyResult:
    groups: SurfaceCollection
    recommendations: tuple[Recommendation, ...]
    expanded: tuple[dict[str, Any], ...]
    raw_response: str
    provenance: dict[str, Any]
    diagnostics: tuple[Diagnostic, ...]
    valid: bool

    def report(self, *, geometry_compare: Mapping[str, Any], applied: bool = False) -> dict[str, Any]:
        return {
            "schema": "onward_generator.dress_classify_report",
            "schema_version": "0.1.0",
            "applied": applied,
            "note": "Recommendations only. Geometry was not modified. Apply is disabled until this report is reviewed.",
            "prompt_version": PROMPT_VERSION,
            "catalog_sha256": self.provenance.get("catalog_sha256"),
            "provider": self.provenance,
            "groups": [g.to_document() for g in self.groups.groups],
            "recommendations": [r.to_document() for r in self.recommendations],
            "expanded": list(self.expanded),
            "geometry": dict(geometry_compare),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "valid": self.valid,
        }


@cache
def output_schema() -> dict[str, Any]:
    schema = json.loads(OUTPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


@cache
def input_schema() -> dict[str, Any]:
    schema = json.loads(INPUT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def recommendation_schema(catalog_ids: Sequence[str]) -> dict[str, Any]:
    """Output schema with the live catalogue ids injected as enums (Ollama structured output)."""
    schema = copy.deepcopy(output_schema())
    ids = sorted(catalog_ids)
    rec = schema["$defs"]["recommendation"]["properties"]
    rec["recommended_material_id"] = {"type": "string", "enum": ids}
    rec["alternative_material_ids"] = {
        "type": "array",
        "maxItems": 4,
        "items": {"type": "string", "enum": ids},
    }
    return schema


def groups_document(groups: Sequence[SurfaceGroup], catalog: MaterialCatalog) -> dict[str, Any]:
    return {
        "schema": "onward_generator.surface_groups",
        "schema_version": "0.1.0",
        "catalog_sha256": catalog.sha256,
        "groups": [g.to_document() for g in groups],
    }


def _pointer(parts: Any) -> str:
    return "".join(f"/{p}" for p in parts)


def parse_recommendations(text: str) -> dict[str, Any]:
    raw = text.encode("utf-8")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ClassifyError([Diagnostic("error", "response_too_large", "",
                                        f"response is {len(raw)} bytes; limit is {MAX_RESPONSE_BYTES}")])
    if not text.strip():
        raise ClassifyError([Diagnostic("error", "empty_response", "", "the response is empty")])
    try:
        doc = loads_strict(text.strip())
    except json.JSONDecodeError as exc:
        raise ClassifyError([Diagnostic("error", "invalid_json", "",
                                        f"the response is not JSON (line {exc.lineno}, column {exc.colno}: {exc.msg})")]) from exc
    except ValueError as exc:
        raise ClassifyError([Diagnostic("error", "invalid_json", "", f"the response is not strict JSON: {exc}")]) from exc
    if not isinstance(doc, dict):
        raise ClassifyError([Diagnostic("error", "invalid_root", "", "the response must be a JSON object")])
    errors = sorted(Draft202012Validator(output_schema()).iter_errors(doc), key=lambda e: (list(e.absolute_path), e.message))
    if errors:
        raise ClassifyError([
            Diagnostic("error", f"recommend_{e.validator}", _pointer(e.absolute_path), e.message) for e in errors
        ])
    return doc


def _sanitize_alternatives(ids: Sequence[str], catalog: MaterialCatalog, role: str, recommended: str) -> tuple[str, ...]:
    out: list[str] = []
    for mid in ids:
        if mid == recommended or mid in out:
            continue
        if catalog.compatible(mid, role):
            out.append(mid)
    return tuple(out)


def validate_recommendation(
    raw: Mapping[str, Any],
    group: SurfaceGroup,
    catalog: MaterialCatalog,
    *,
    source: str,
) -> Recommendation:
    rejected: list[str] = []
    mid = raw["recommended_material_id"]
    confidence = float(raw["confidence"])
    reason = str(raw["reason"])
    needs_review = bool(raw["needs_review"])
    alternatives = [str(a) for a in raw.get("alternative_material_ids") or []]

    if group.osm_override:
        if mid != group.osm_override:
            rejected.append(f"osm_tag_overrides_model:{mid}->{group.osm_override}")
            mid = group.osm_override
            source = "osm_tag"
            reason = f"OSM tag takes precedence over the model ({reason})"
            confidence = max(confidence, 0.9)
            needs_review = False
        else:
            source = "osm_tag"
            confidence = max(confidence, 0.9)

    if mid not in catalog.ids:
        rejected.append(f"unknown_material:{mid}")
        mid = catalog.safe_id(group.role)
        source = "safe_fallback"
        confidence = min(confidence, 0.2)
        needs_review = True
        reason = f"unknown material rejected; using safe {mid}"
    elif not catalog.compatible(mid, group.role):
        rejected.append(f"incompatible_role:{mid}:{group.role}")
        mid = catalog.safe_id(group.role)
        source = "safe_fallback"
        confidence = min(confidence, 0.2)
        needs_review = True
        reason = f"material not compatible with {group.role}; using safe {mid}"

    if confidence < LOW_CONFIDENCE and source != "osm_tag":
        safe = catalog.safe_id(group.role)
        if mid != safe:
            rejected.append(f"low_confidence:{mid}")
            mid = safe
            source = "safe_fallback"
            reason = f"confidence {confidence:.2f} below {LOW_CONFIDENCE}; using safe {mid}"
        needs_review = True

    alts = _sanitize_alternatives(alternatives, catalog, group.role, mid)
    dropped = [a for a in alternatives if a not in alts and a != mid]
    rejected.extend(f"dropped_alternative:{a}" for a in dropped)
    return Recommendation(group.group_id, mid, confidence, reason[:240], alts, needs_review, source, tuple(rejected))


def deterministic_recommendations(groups: Sequence[SurfaceGroup], catalog: MaterialCatalog) -> list[dict[str, Any]]:
    """Rule fallback used when no model is asked, and to fill missing groups."""
    out: list[dict[str, Any]] = []
    for group in groups:
        if group.osm_override and catalog.compatible(group.osm_override, group.role):
            mid = group.osm_override
            reason = "explicit OSM material or surface tag"
            confidence = 0.95
            needs_review = False
            source_note = "osm"
        else:
            mid = _rule_material(group, catalog)
            reason = _rule_reason(group, mid)
            confidence = min(0.55, group.confidence_hint + 0.15)
            needs_review = confidence < 0.5 or group.building_context == "unknown"
            source_note = "rules"
        out.append({
            "surface_id": group.group_id,
            "recommended_material_id": mid,
            "confidence": confidence,
            "reason": f"{reason} [{source_note}]",
            "alternative_material_ids": _rule_alternatives(group, catalog, mid),
            "needs_review": needs_review,
        })
    return out


def _rule_material(group: SurfaceGroup, catalog: MaterialCatalog) -> str:
    if group.role == "wall":
        if group.building_context == "religious":
            return "limestone_warm"
        if group.building_context == "historic":
            return "historic_plaster_cream"
        if group.building_context == "residential":
            return "plaster_ochre_wall"
        if group.building_context == "commercial":
            return "historic_plaster_cream"
        if group.building_context == "modern":
            return "plaster_white_wall"
        return catalog.safe_id("wall")
    if group.role == "roof":
        inclined = (group.inclination_deg or 0) > 8
        if inclined or group.osm_tags.get("roof:shape") in {"hipped", "gabled", "pyramidal"}:
            return "terracotta_roof"
        if group.building_context in {"historic", "religious"}:
            return "terracotta_roof"
        if group.building_context == "residential":
            return "flat_roof_warm"
        return catalog.safe_id("roof")
    if group.role == "street":
        return "asphalt_road"
    if group.role == "plaza":
        return "cobblestone_paving"
    if group.role == "opening" and group.current_material == "door_wood":
        return "wood_door"
    if group.role == "opening" and group.current_material == "glass_dark":
        return "glass_opening"
    if group.role == "opening" and group.current_material == "railing_metal":
        return "wrought_iron"
    if group.role == "accessory" and group.current_material == "tree_trunk":
        return "tree_bark"
    if group.role == "accessory" and group.current_material == "tree_canopy":
        return "tree_canopy_green"
    if group.role == "accessory" and group.current_material == "railing_metal":
        return "wrought_iron"
    return catalog.safe_id(group.role)


def _rule_reason(group: SurfaceGroup, mid: str) -> str:
    return f"{group.role} / {group.building_context} → {mid}"


def _rule_alternatives(group: SurfaceGroup, catalog: MaterialCatalog, mid: str) -> list[str]:
    options = [e.id for e in catalog.entries if group.role in e.compatible_surfaces and e.id != mid]
    return options[:2]


def build_user_message(groups: Sequence[SurfaceGroup], catalog: MaterialCatalog) -> str:
    payload = {
        "instruction": "Recommend one catalogue material per group. surface_id is group_id.",
        "catalog": catalog.model_payload(),
        "groups": [g.to_document() | ({"osm_override": g.osm_override} if g.osm_override else {}) for g in groups],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def expand_recommendations(
    recommendations: Sequence[Recommendation],
    groups: Sequence[SurfaceGroup],
    catalog: MaterialCatalog,
) -> list[dict[str, Any]]:
    by_id = {g.group_id: g for g in groups}
    expanded: list[dict[str, Any]] = []
    for rec in recommendations:
        group = by_id[rec.surface_id]
        entry = catalog.get(rec.recommended_material_id)
        for sid in group.surface_ids:
            expanded.append({
                "surface_id": sid,
                "group_id": group.group_id,
                "role": group.role,
                "recommended_material_id": rec.recommended_material_id,
                "color_id": entry.color_id if entry else None,
                "confidence": rec.confidence,
                "reason": rec.reason,
                "alternative_material_ids": list(rec.alternative_material_ids),
                "needs_review": rec.needs_review,
                "source": rec.source,
            })
    return expanded


def classify_groups(
    groups: SurfaceCollection,
    catalog: MaterialCatalog,
    provider: Provider | None,
    *,
    raw_override: str | None = None,
) -> ClassifyResult:
    """Validate a model (or rule) response. OSM overrides and id checks run in code."""
    diagnostics: list[Diagnostic] = []
    schema = recommendation_schema(sorted(catalog.ids))
    Draft202012Validator(input_schema()).validate(groups_document(groups.groups, catalog))

    provenance: dict[str, Any] = {
        "catalog_path": str(catalog.path),
        "catalog_sha256": catalog.sha256,
        "prompt_version": PROMPT_VERSION,
        "system_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
    }
    raw = ""
    parsed: dict[str, Any] | None = None
    if raw_override is not None:
        raw = raw_override
        provenance.update({"provider": "static", "model": None})
        parsed = parse_recommendations(raw)
    elif provider is None:
        rules = deterministic_recommendations(groups.groups, catalog)
        parsed = {"schema": "onward_generator.material_recommendations", "schema_version": "0.1.0",
                  "recommendations": rules}
        raw = json.dumps(parsed, ensure_ascii=False)
        provenance.update({"provider": "none", "model": None, "mode": "deterministic_rules"})
    else:
        user = build_user_message(groups.groups, catalog)
        try:
            result: ProviderResult = provider.complete(SYSTEM_PROMPT, user, schema)
            raw = result.text
            provenance.update(result.provenance)
            parsed = parse_recommendations(raw)
        except ProviderError as exc:
            diagnostics.append(Diagnostic("error", "provider_error", "", str(exc)))
            parsed = None
        except ClassifyError as exc:
            diagnostics.extend(exc.diagnostics)
            parsed = None

    by_id = {g.group_id: g for g in groups.groups}
    recommendations: list[Recommendation] = []
    seen: set[str] = set()
    if parsed is not None:
        for i, raw_rec in enumerate(parsed["recommendations"]):
            sid = raw_rec.get("surface_id")
            if sid not in by_id:
                diagnostics.append(Diagnostic("error", "unknown_surface", f"/recommendations/{i}/surface_id",
                                              f"{sid!r} is not a group_id from the input"))
                continue
            if sid in seen:
                diagnostics.append(Diagnostic("warning", "duplicate_surface", f"/recommendations/{i}/surface_id",
                                              f"{sid} repeated; first recommendation is kept"))
                continue
            seen.add(sid)
            rec = validate_recommendation(raw_rec, by_id[sid], catalog, source="model")
            if rec.source == "safe_fallback":
                diagnostics.append(Diagnostic("warning", "recommendation_adjusted", sid, "; ".join(rec.rejected) or rec.reason))
            recommendations.append(rec)
        missing = [g.group_id for g in groups.groups if g.group_id not in seen]
        if missing:
            fill = {r["surface_id"]: r for r in deterministic_recommendations(
                [g for g in groups.groups if g.group_id in missing], catalog
            )}
            for gid in missing:
                diagnostics.append(Diagnostic("warning", "missing_recommendation", gid, "model omitted this group; rules filled it"))
                recommendations.append(validate_recommendation(fill[gid], by_id[gid], catalog, source="rules"))

    errors = any(d.severity == "error" for d in diagnostics)
    if errors:
        recommendations = []
    expanded = expand_recommendations(recommendations, groups.groups, catalog) if recommendations else []
    return ClassifyResult(
        groups, tuple(recommendations), tuple(expanded), raw, provenance, tuple(diagnostics), not errors,
    )
