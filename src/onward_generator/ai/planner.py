"""Validated intent → configuration changes (phase 06).

The mapping is deterministic code; the model only fills the intent. Every
change is checked against an allowlist of configuration fields, so area,
coordinates, sources, performance limits and the export adapter cannot change
even if the mapping code had a bug.
"""

from __future__ import annotations

import copy
import hashlib
import math
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from onward_generator.ai.intent import INTENT_VERSION, Intent
from onward_generator.config.diagnostics import Diagnostic, has_errors
from onward_generator.config.loader import ConfigLoadError, read_json_document
from onward_generator.config.models import GeneratorConfig
from onward_generator.config.validation import schema_diagnostics, semantic_diagnostics, validate_config_file
from onward_generator.detail.config import DEFAULT_CONFIG_PATH as DETAIL_DEFAULT
from onward_generator.detail.config import DetailConfigError
from onward_generator.detail.config import config_from_document as detail_from_document
from onward_generator.heights.config import DEFAULT_CONFIG_PATH as HEIGHTS_DEFAULT
from onward_generator.heights.config import HeightConfigError
from onward_generator.heights.config import config_from_document as heights_from_document

PROMPT_VERSION = "0.1.0"
DocName = Literal["project", "heights", "detail"]

# The only configuration fields an intent can reach. Anything else that differs is refused.
ALLOWED_CHANGES: dict[DocName, frozenset[str]] = {
    "project": frozenset({"/quality/detail_level", "/generation/interiors"}),
    "heights": frozenset({"/typical_levels/residential_multi"}),
    "detail": frozenset({"/interiors/max_buildings", "/interiors/min_buildings", "/interiors/enabled"}),
}

# Documented for people and checked by tests; the allowlist above is what enforces it.
PROTECTED_FIELDS: dict[DocName, tuple[str, ...]] = {
    "project": ("/area", "/source", "/project/target", "/optimization", "/export", "/quality/deterministic_seed"),
    "heights": ("/bounds_m", "/overrides_file", "/storey_height_m"),
    "detail": ("/budget", "/seed"),
}

_PROTECTED_TERMS = (
    (re.compile(r"-?\d{1,3}[.,]\d{3,}\s*[,;/]\s*-?\d{1,3}[.,]\d{3,}"), "coordinates"),
    (re.compile(r"\b(coordenadas?|latitud|longitud|coordinates?|latitude|longitude)\b", re.I), "coordinates"),
    (re.compile(r"\b(fuentes?|sources?|openstreetmap|osm|catastro|cadastre)\b", re.I), "data sources"),
    (re.compile(r"\b(exporta\w*|export\w*|adaptador|adapter)\b", re.I), "export adapter"),
    (re.compile(r"\b(presupuestos?|budgets?|l[ií]mites?|limits?|draw ?calls?|tri[aá]ngulos|triangles|fps)\b", re.I),
     "performance limits"),
)

# Wishes no intent field can express; flagged even when the model forgets to list them.
_UNSUPPORTED_TERMS = (
    (re.compile(r"\b(hist[oó]ric\w*|antigu\w*|medieval\w*|barroc\w*|g[oó]tic\w*|modernis\w*|estilo\w*|old town|"
                r"style\w*|baroque|gothic)\b", re.I), "architectural style or era"),
    (re.compile(r"\b(noche|nocturn\w*|amanecer|atardecer|lluvi\w*|niebla|clima|iluminaci[oó]n|night\w*|sunset|sunrise|"
                r"rain\w*|fog\w*|weather|lighting)\b", re.I), "lighting or weather"),
)

SYSTEM_PROMPT = f"""You convert a map-generation instruction into ONE JSON object for a procedural generator.
Answer with the JSON object only. No prose, no markdown, no code.

Fields (use null for anything the instruction does not ask for):
- intent_version: always "{INTENT_VERSION}".
- parameters.facade_detail: "low" | "medium" | "high" | null. "detailed facades" = "high".
- parameters.residential_levels: integer 1-50 | null. Storeys of ordinary multi-storey buildings ("four storeys" = 4).
- parameters.interior_buildings: integer 0-50 | null. How many buildings get an interior ("interiors in only two buildings" = 2; "no interiors" = 0).
- parameters.performance_priority: "max" | "balanced" | null. "maximum priority to performance" = "max".
- unsupported_requests: list of {{"request": short quote, "reason": "protected_field" | "not_supported" | "ambiguous"}}.
  Use "protected_field" for anything about the geographic area, place names, coordinates, data sources, performance limits or export.
  Use "not_supported" for EVERY other wish with no field above, for example an architectural style or era
  ("historic", "baroque"), lighting, weather or vegetation. Never drop a wish silently.
- summary: one short sentence listing the fields you set. Do not claim that anything was generated.

You cannot change the area, coordinates, sources, performance limits or export. Never invent values the instruction does not state."""


def system_prompt_sha256() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


class PlanError(ValueError):
    def __init__(self, diagnostics: list[Diagnostic]) -> None:
        super().__init__("\n".join(str(d) for d in diagnostics))
        self.diagnostics = diagnostics


@dataclass(frozen=True, slots=True)
class BaseDocument:
    name: DocName
    path: Path
    sha256: str
    document: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BaseConfigs:
    project: BaseDocument
    heights: BaseDocument
    detail: BaseDocument

    def by_name(self) -> dict[DocName, BaseDocument]:
        return {"project": self.project, "heights": self.heights, "detail": self.detail}

    def provenance(self) -> dict[str, Any]:
        return {n: {"path": str(b.path), "sha256": b.sha256} for n, b in self.by_name().items()}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_base(project: str | Path, heights: str | Path | None = None, detail: str | Path | None = None) -> BaseConfigs:
    """Read and validate the three base configurations; the intent only edits copies of them."""
    problems: list[Diagnostic] = []
    result = validate_config_file(project)
    if not result.valid:
        raise PlanError([Diagnostic(d.severity, d.code, f"project:{d.path}", d.message) for d in result.diagnostics
                         if d.severity == "error"])
    docs: dict[str, tuple[dict[str, Any], Path]] = {}
    for name, path in (("heights", heights or HEIGHTS_DEFAULT), ("detail", detail or DETAIL_DEFAULT)):
        try:
            docs[name] = read_json_document(path)
        except ConfigLoadError as exc:
            problems.append(Diagnostic("error", exc.diagnostic.code, f"{name}:", exc.diagnostic.message))
    if problems:
        raise PlanError(problems)
    try:
        heights_from_document(docs["heights"][0], base_dir=docs["heights"][1].parent)
        detail_from_document(docs["detail"][0])
    except (HeightConfigError, DetailConfigError) as exc:
        raise PlanError([Diagnostic("error", "base_config_invalid", "", str(exc))]) from exc
    project_doc, _ = read_json_document(project)
    return BaseConfigs(
        project=BaseDocument("project", result.path, _sha256(result.path), project_doc),
        heights=BaseDocument("heights", docs["heights"][1], _sha256(docs["heights"][1]), docs["heights"][0]),
        detail=BaseDocument("detail", docs["detail"][1], _sha256(docs["detail"][1]), docs["detail"][0]),
    )


@dataclass(frozen=True, slots=True)
class Change:
    document: DocName
    path: str
    before: Any
    after: Any
    effect: Literal["geometry", "recorded_only"]
    cost: Literal["increase", "decrease", "neutral"]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"document": self.document, "path": self.path, "before": self.before, "after": self.after,
                "effect": self.effect, "cost": self.cost, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class Plan:
    changes: tuple[Change, ...]
    diagnostics: tuple[Diagnostic, ...]
    documents: dict[DocName, dict[str, Any]]

    @property
    def valid(self) -> bool:
        return not has_errors(self.diagnostics)

    @property
    def needs_confirmation(self) -> bool:
        """Relevant = changes generated geometry, or anything the user should read first (a warning)."""
        return any(c.effect == "geometry" for c in self.changes) or any(d.severity == "warning" for d in self.diagnostics)


def _get(doc: Mapping[str, Any], pointer: str) -> Any:
    node: Any = doc
    for part in pointer.strip("/").split("/"):
        node = node[part]
    return node


def _set(doc: dict[str, Any], pointer: str, value: Any) -> None:
    *parents, last = pointer.strip("/").split("/")
    node = doc
    for part in parents:
        node = node[part]
    node[last] = value


def changed_paths(before: Any, after: Any, prefix: str = "") -> set[str]:
    """Leaf JSON pointers whose value differs (added and removed keys included)."""
    if isinstance(before, dict) and isinstance(after, dict):
        out: set[str] = set()
        for key in set(before) | set(after):
            if key not in before or key not in after:
                out.add(f"{prefix}/{key}")
            else:
                out |= changed_paths(before[key], after[key], f"{prefix}/{key}")
        return out
    return set() if before == after and type(before) is type(after) else {prefix or "/"}


def guard(base: BaseConfigs, documents: Mapping[DocName, dict[str, Any]]) -> list[Diagnostic]:
    """Refuse any difference outside ``ALLOWED_CHANGES``."""
    out = []
    for name, b in base.by_name().items():
        for path in sorted(changed_paths(b.document, documents[name]) - ALLOWED_CHANGES[name]):
            out.append(Diagnostic("error", "protected_change", f"{name}:{path}",
                                  f"{name} field {path} is not changeable from an instruction; nothing was written"))
    return out


def instruction_warnings(instruction: str) -> list[Diagnostic]:
    seen: dict[str, str] = {}
    for pattern, topic in _PROTECTED_TERMS:
        m = pattern.search(instruction)
        if m and topic not in seen:
            seen[topic] = m.group(0)
    out = [Diagnostic("warning", "instruction_mentions_protected", "",
                      f"the instruction mentions {topic} ({term!r}); these cannot be changed from an instruction and were not")
           for topic, term in seen.items()]
    for pattern, topic in _UNSUPPORTED_TERMS:
        m = pattern.search(instruction)
        if m:
            out.append(Diagnostic("warning", "instruction_mentions_unsupported", "",
                                  f"the instruction asks for {topic} ({m.group(0)!r}); the generator has no parameter for it, "
                                  "so it was not applied"))
    return out


def allowed_residential_levels(heights: Mapping[str, Any]) -> tuple[int, int]:
    """Storeys whose type height survives the estimator's clamp (``fallback_m`` ∩ ``bounds_m``)."""
    storey = float(heights["storey_height_m"]["residential_multi"])
    r = heights["fallback_m"]["residential_multi"]
    lo_m = max(float(r["min"]), float(heights["bounds_m"]["min"]))
    hi_m = min(float(r["max"]), float(heights["bounds_m"]["max"]))
    return max(1, math.ceil(lo_m / storey - 1e-9)), math.floor(hi_m / storey + 1e-9)


def build_plan(intent: Intent, base: BaseConfigs, instruction: str = "") -> Plan:
    docs: dict[DocName, dict[str, Any]] = {n: copy.deepcopy(b.document) for n, b in base.by_name().items()}
    changes: list[Change] = []
    diags: list[Diagnostic] = instruction_warnings(instruction)
    priority = intent.performance_priority

    def change(doc: DocName, path: str, after: Any, effect: Literal["geometry", "recorded_only"], reason: str,
               more_is_costlier: bool = False) -> None:
        before = _get(docs[doc], path)
        if before == after and type(before) is type(after):
            return
        cost: Literal["increase", "decrease", "neutral"] = "neutral"
        if effect == "geometry":
            grows = (after > before) if more_is_costlier else bool(after) and not bool(before)
            cost = "increase" if grows else "decrease"
        _set(docs[doc], path, after)
        changes.append(Change(doc, path, before, after, effect, cost, reason))

    if intent.facade_detail is not None:
        change("project", "/quality/detail_level", intent.facade_detail, "recorded_only",
               "facade_detail; no generation stage reads quality.detail_level yet")
    if intent.residential_levels is not None:
        levels = intent.residential_levels
        lo, hi = allowed_residential_levels(docs["heights"])
        if not lo <= levels <= hi:
            diags.append(Diagnostic("error", "levels_outside_allowed_range", "/parameters/residential_levels",
                                    f"{levels} storeys is outside the allowed {lo}–{hi}: the heights configuration clamps "
                                    "residential_multi type heights to fallback_m.residential_multi, so other values would "
                                    "be silently ignored"))
        else:
            change("heights", "/typical_levels/residential_multi", levels, "geometry",
                   "residential_levels; only buildings without observed height or levels use it", more_is_costlier=True)
    if intent.interior_buildings is not None:
        n = intent.interior_buildings
        change("detail", "/interiors/max_buildings", n, "geometry", "interior_buildings", more_is_costlier=True)
        if n > 0:  # a ceiling of 0 turns interiors off and leaves the floor alone
            change("detail", "/interiors/min_buildings", min(n, _get(docs["detail"], "/interiors/min_buildings")), "geometry",
                   "interior_buildings; the floor of the draw may not exceed the ceiling", more_is_costlier=True)
        change("detail", "/interiors/enabled", n > 0, "geometry", "interior_buildings")
        change("project", "/generation/interiors", n > 0, "recorded_only",
               "interior_buildings; the detail stage reads its own interiors settings")

    for c in changes:
        if c.cost != "increase":
            continue
        where = f"{c.document}:{c.path}"
        if priority == "max":
            diags.append(Diagnostic("error", "exceeds_measured_configuration", where,
                                    f"{c.path} {c.before!r} → {c.after!r} adds cost beyond the configuration measured on the "
                                    "Quest (phase 05), which performance_priority 'max' forbids"))
        else:
            diags.append(Diagnostic("warning", "not_measured_on_device", where,
                                    f"{c.path} {c.before!r} → {c.after!r} adds cost; re-measure on the Quest "
                                    "(docs/09-perf.md) before relying on it"))
    for c in changes:
        if c.effect == "recorded_only":
            diags.append(Diagnostic("info", "recorded_only", f"{c.document}:{c.path}", c.reason))
    for request, reason in intent.unsupported_requests:
        code = "protected_request_ignored" if reason == "protected_field" else "unsupported_request"
        diags.append(Diagnostic("warning", code, "/unsupported_requests", f"not applied ({reason}): {request!r}"))
    if not intent.requested:
        diags.append(Diagnostic("info", "no_parameters", "/parameters", "the instruction did not map to any parameter"))

    diags.extend(guard(base, docs))
    return Plan(tuple(changes), tuple(diags), docs)


def _rebase(value: str | None, from_dir: Path, to_dir: Path) -> str | None:
    if value is None or Path(value).is_absolute():
        return value
    return os.path.relpath((from_dir / value).resolve(), to_dir.resolve())


def output_documents(plan: Plan, base: BaseConfigs, out_dir: Path) -> dict[DocName, dict[str, Any]]:
    """Copies for ``out_dir``: relative paths are rewritten to point at the same files, then re-validated."""
    out_dir = out_dir.expanduser().resolve()
    docs = copy.deepcopy(plan.documents)
    project = docs["project"]
    for key in ("$schema",):
        if isinstance(project.get(key), str) and not project[key].startswith(("http://", "https://")):
            project[key] = _rebase(project[key], base.project.path.parent, out_dir)
    project["area"]["boundary_file"] = _rebase(project["area"]["boundary_file"], base.project.path.parent, out_dir)
    docs["heights"]["overrides_file"] = _rebase(docs["heights"]["overrides_file"], base.heights.path.parent, out_dir)

    problems = [Diagnostic(d.severity, d.code, f"project:{d.path}", d.message) for d in schema_diagnostics(project)]
    if not problems:
        cfg = GeneratorConfig.from_document(project, out_dir / "project.json")
        original = GeneratorConfig.from_document(base.project.document, base.project.path)
        if (cfg.area.center, cfg.area.boundary_file, cfg.area.margin_m) != (original.area.center, original.area.boundary_file,
                                                                             original.area.margin_m):
            problems.append(Diagnostic("error", "protected_change", "project:/area", "the area would change when written"))
        problems += [d for d in semantic_diagnostics(cfg) if d.severity == "error"]
    try:
        heights_from_document(docs["heights"], base_dir=out_dir)
        detail_from_document(docs["detail"])
    except (HeightConfigError, DetailConfigError) as exc:
        problems.append(Diagnostic("error", "output_config_invalid", "", str(exc)))
    if problems:
        raise PlanError(problems)
    return docs
