"""Street profile (plan D2): the local model picks enumerated ids, code turns them into stage flags.

The reply is data. It is parsed as strict JSON and validated against
``config/street_profile.schema.json``: five fields, each one id of a fixed list,
and an optional summary that no code reads. Each id stands for one row of
``EFFECTS``, an existing and validated stage flag. Nothing in the reply is
executed, and only the literal values of that table reach a command line.

The profile is resolved once per map, before ``run`` plans its stages, and
stored in ``config/street_profile.json`` with the raw reply and the hashes of the
schema, the prompt and the inputs. A later resolution with the same hashes
replays that reply without calling the model. A provider error or a reply that
does not validate is tried once more, quoting the failure (ADR-010 §5); after
that the neutral profile (every field ``map_default``) is used and the reason
recorded. A missing Ollama never fails the run.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator

from onward_generator.ai.planner import changed_paths
from onward_generator.ai.providers import Provider
from onward_generator.config.loader import loads_strict
from onward_generator.io import atomic_write_bytes, atomic_write_json

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = REPO_ROOT / "config" / "street_profile.schema.json"
PROFILE_VERSION = "0.1.0"
PROMPT_VERSION = "0.1.0"
RECORD_SCHEMA = "onward_generator.street_profile_record"
RECORD_VERSION = "0.1.0"
RECORD_FILE = "street_profile.json"
SELECTION_FILE = "street_profile_selection.json"
MAX_RESPONSE_BYTES = 4096
MAX_RECORD_BYTES = 64 * 1024
PROFILE_ATTEMPTS = 2
NEUTRAL = "map_default"
FIELDS = ("dominant_typology", "palette", "props_profile", "abandonment", "cover")

Origin = Literal["ai_generated", "replay", "rules"]
ORIGINS: tuple[Origin, ...] = ("ai_generated", "replay", "rules")
REPLAYABLE = frozenset({"ai_generated", "replay"})

# Library ``condition`` values each props profile may place. A row without a condition (vegetation) is always kept.
CONDITION_GROUPS: Mapping[str, tuple[str, ...]] = {
    "intact": ("intact",),
    "worn": ("damaged", "worn"),
    "wrecked": ("burned", "damaged", "wrecked"),
}
PROFILE_YEARS: Mapping[str, int] = {"years_10": 10, "years_30": 30, "years_100": 100}
COVER_PROPS: Mapping[str, int] = {"off": 0, "light": 8, "full": 24}
STYLE_IDS = ("anglo", "central_european", "generic", "latin_american", "mediterranean")

# The only settings a profile can reach. Anything else that differs is refused.
ALLOWED_POINTERS = frozenset({
    "/materials/facade_style", "/materials/repetition", "/overgrow/selection", "/overgrow/profile_years", "/cover/max_props",
})
# Documented for people and checked by tests; the allowlist above is what enforces it.
PROTECTED_POINTERS = ("/area", "/center", "/sources", "/export", "/budgets", "/core", "/buildings_sha256", "/sightline_cover")


@dataclass(frozen=True, slots=True)
class Effect:
    """What one id does: the setting it writes, the stage flag it becomes, and the ADR it cites."""

    field: str
    id: str
    pointer: str
    value: str | int | bool
    stage: Literal["materials", "overgrow", "cover"]
    flag: str
    effect: str
    adr: str


def _effects() -> dict[tuple[str, str], Effect]:
    rows: list[Effect] = []
    for style in STYLE_IDS:
        rows.append(Effect("dominant_typology", style, "/materials/facade_style", style, "materials", "--facade-style",
                           f"the map-wide facade atlas style is {style}; no building is reclassified", "ADR-020, ADR-027"))
    rows.append(Effect("palette", "extended_pools", "/materials/repetition", True, "materials", "--repetition",
                       "the calm roof and paving pools and the per-cell ground phase", "ADR-009"))
    for group, conditions in CONDITION_GROUPS.items():
        rows.append(Effect("props_profile", group, "/overgrow/selection", f"config/{SELECTION_FILE}", "overgrow", "--selection",
                           f"overgrow places only library props whose condition is {', '.join(conditions)}, or that have none",
                           "ADR-010"))
    for name, years in PROFILE_YEARS.items():
        rows.append(Effect("abandonment", name, "/overgrow/profile_years", years, "overgrow", "--profile",
                           f"grass, vines and trees follow the {years}-year profile", "ADR-010"))
    for name, count in COVER_PROPS.items():
        rows.append(Effect("cover", name, "/cover/max_props", count, "cover", "--max-props",
                           f"sightline cover places at most {count} props", "ADR-032"))
    return {(row.field, row.id): row for row in rows}


EFFECTS: Mapping[tuple[str, str], Effect] = _effects()


@dataclass(frozen=True, slots=True)
class StreetProfile:
    dominant_typology: str
    palette: str
    props_profile: str
    abandonment: str
    cover: str
    summary: str | None = None

    def ids(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in FIELDS}


RULES_PROFILE = StreetProfile(NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL)


class StreetProfileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{message} [{code}]")
        self.code = code
        self.message = message


@cache
def profile_schema() -> dict[str, Any]:
    schema: dict[str, Any] = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def schema_sha256() -> str:
    return hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()


def field_ids(field: str) -> tuple[str, ...]:
    return tuple(profile_schema()["properties"][field]["enum"])


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_street_profile(text: str) -> StreetProfile:
    """Strict JSON → schema → typed profile. Raises ``StreetProfileError`` with an ``AI_PROFILE_*`` code."""
    if not text.strip():
        raise StreetProfileError("AI_PROFILE_JSON", "the reply is empty")
    size = len(text.encode("utf-8"))
    if size > MAX_RESPONSE_BYTES:
        raise StreetProfileError("AI_PROFILE_SIZE", f"the reply is {size} bytes; the limit is {MAX_RESPONSE_BYTES}")
    try:
        document = loads_strict(text.strip())
    except ValueError as exc:  # JSONDecodeError, a duplicate key, NaN
        raise StreetProfileError("AI_PROFILE_JSON", f"the reply is not strict JSON ({exc}); only one JSON object is accepted") from exc
    if not isinstance(document, dict):
        raise StreetProfileError("AI_PROFILE_SCHEMA", f"the reply must be a JSON object, got {type(document).__name__}")
    errors = sorted(Draft202012Validator(profile_schema()).iter_errors(document), key=lambda e: (list(map(str, e.absolute_path)), e.message))
    if errors:
        problems = "; ".join(f"{''.join(f'/{p}' for p in e.absolute_path) or '/'}: {e.message}" for e in errors)
        raise StreetProfileError("AI_PROFILE_SCHEMA", problems)
    return StreetProfile(
        dominant_typology=str(document["dominant_typology"]),
        palette=str(document["palette"]),
        props_profile=str(document["props_profile"]),
        abandonment=str(document["abandonment"]),
        cover=str(document["cover"]),
        summary=document.get("summary"),  # the schema allows only a string here
    )


SYSTEM_PROMPT = f"""You choose a street profile for a procedural 3D map. Reply with one JSON object and nothing else. No prose, no markdown, no code.

Every field takes exactly one id from its list. map_default keeps what the map already does. Do not invent ids, numbers or names.
- profile_version: always "{PROFILE_VERSION}".
- dominant_typology: the facade style of the whole map: map_default | {" | ".join(STYLE_IDS)}. It never changes a building.
- palette: map_default | extended_pools (calmer roof and paving textures).
- props_profile: which street props may appear: map_default | intact | worn | wrecked.
- abandonment: how long the city has been abandoned: map_default | years_10 | years_30 | years_100.
- cover: cover props on long open sightlines: map_default | off | light | full.
- summary: optional, one short sentence. It is not read by the generator.

You cannot change the area, the coordinates, the data sources, the export, the budgets or any building. Choose map_default when the brief does not ask for a field."""


def instruction(brief: str, center: Mapping[str, Any], library: Sequence[tuple[str, str]]) -> str:
    groups = {group: sum(1 for _ident, condition in library if condition in conditions) for group, conditions in CONDITION_GROUPS.items()}
    return (
        f"Brief (quoted exactly):\n{json.dumps(brief, ensure_ascii=False)}\n\n"
        f"Map centre (WGS84): {_round(center.get('latitude'))}, {_round(center.get('longitude'))}\n\n"
        f"Library props per props_profile: {json.dumps(groups)}\n"
    )


def _round(value: Any) -> float | None:
    return round(float(value), 5) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def prompt_sha256(text: str) -> str:
    return _sha(SYSTEM_PROMPT + "\n" + PROMPT_VERSION + "\n" + text)


def inputs_sha256(brief: str, center: Mapping[str, Any], library: Sequence[tuple[str, str]], sightline_cover: bool) -> str:
    payload = {
        "brief": brief,
        "center": [_round(center.get("latitude")), _round(center.get("longitude"))],
        "styles": sorted(STYLE_IDS),
        "library": sorted([ident, condition] for ident, condition in library),
        "sightline_cover": sightline_cover,
    }
    return _sha(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")))


def library_conditions(library: str | Path | None) -> list[tuple[str, str]]:
    """``(id, condition)`` of every prop overgrow would load from ``library``; ``""`` when a row has no condition."""
    if library is None or not Path(library).is_dir():
        return []
    from onward_generator.overgrow.library import read_library

    props, _rejects = read_library(library)
    return sorted((prop.spec.id, str(prop.spec.condition or "")) for prop in props)


def selection_for(props_profile: str, library: Sequence[tuple[str, str]]) -> list[str]:
    """Sorted ids whose condition is in the group, plus the rows without one. Empty when no row matches the group."""
    conditions = CONDITION_GROUPS.get(props_profile)
    if conditions is None or not any(condition in conditions for _ident, condition in library):
        return []
    return sorted({ident for ident, condition in library if not condition or condition in conditions})


# -------------------------------------------------------------------------------------------------- translation


def effective_settings(root: Path) -> dict[str, Any]:
    """The settings a profile is checked against: the five it may reach, and the protected ones around them."""
    from onward_generator.maps.workspace import load_map, repetition_enabled, sightline_cover_enabled
    from onward_generator.osm.acquire import osm_target
    from onward_generator.overgrow.profiles import PROFILES

    document = load_map(root)
    card = root / document["paths"]["export"]
    facades = document.get("facades")
    style = facades.get("style", "auto") if isinstance(facades, dict) else "auto"
    osm = osm_target(root)
    user_selection = root / "config" / "prop_selection.json"
    return {
        "area": {key: document.get(key) for key in ("radius_m", "margin_m", "crs")},
        "center": document["center"],
        "sources": document.get("sources", {}),
        "export": {"sha256": _file_sha(card)},
        "budgets": {"export_card_sha256": _file_sha(card)},
        "core": {key: document.get(key) for key in ("core_radius_m", "core")},
        "buildings_sha256": _file_sha(osm),
        "sightline_cover": sightline_cover_enabled(root),
        "materials": {"facade_style": style, "repetition": repetition_enabled(root)},
        "overgrow": {"profile_years": max(PROFILES), "selection": "config/prop_selection.json" if user_selection.is_file() else None},
        "cover": {"max_props": max(COVER_PROPS.values())},
    }


def _file_sha(path: Path) -> str | None:
    if not path.is_file():
        return None
    from onward_generator.io import sha256_file

    return sha256_file(path)


@dataclass(frozen=True, slots=True)
class Context:
    """Facts a translation reads but may not change: whether the texture plan is calm, and the library conditions."""

    calm_plan: bool
    library: tuple[tuple[str, str], ...]


def _get(document: Mapping[str, Any], pointer: str) -> Any:
    node: Any = document
    for part in pointer[1:].split("/"):
        node = node[part]
    return node


def _set(document: dict[str, Any], pointer: str, value: Any) -> None:
    *parents, last = pointer[1:].split("/")
    node = document
    for part in parents:
        node = node[part]
    node[last] = value


def _skip_reason(effect: Effect, before: Mapping[str, Any], context: Context) -> str | None:
    if effect.field == "dominant_typology" and before["materials"]["facade_style"] != "auto":
        return "user_setting"
    if effect.field == "palette":
        if before["materials"]["repetition"]:
            return "user_setting"
        if not context.calm_plan:
            return "texture_plan_not_calm"
    if effect.field == "props_profile":
        if before["overgrow"]["selection"] is not None:
            return "user_setting"
        if not selection_for(effect.id, context.library):
            return "props_profile_empty"
    if effect.field == "cover" and not before["sightline_cover"]:
        return "profile_inactive"
    return None


def translate(profile: StreetProfile, before: Mapping[str, Any], context: Context) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A copy of ``before`` with each applied id written, and one row per id that is not ``map_default``."""
    after = copy.deepcopy(dict(before))
    rows: list[dict[str, Any]] = []
    for field, ident in profile.ids().items():
        if ident == NEUTRAL:
            continue
        effect = EFFECTS[(field, ident)]
        previous = _get(before, effect.pointer)
        reason = _skip_reason(effect, before, context)
        if reason is None:
            _set(after, effect.pointer, effect.value)
        rows.append({
            "field": field, "id": ident, "pointer": effect.pointer, "stage": effect.stage, "flag": effect.flag,
            "before": previous, "after": previous if reason else effect.value,
            "status": "skipped" if reason else "applied", "reason": reason, "effect": effect.effect, "adr": effect.adr,
        })
    return after, rows


def guard(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """Pointers that differ outside ``ALLOWED_POINTERS``; any one refuses the profile."""
    return sorted(changed_paths(dict(before), dict(after)) - ALLOWED_POINTERS)


def changes(rows: Sequence[Mapping[str, Any]], origin: Origin) -> list[dict[str, Any]]:
    return [{**row, "origin": origin} for row in rows]


def provenance_for(origin: str) -> Literal["ai_generated", "estimated"]:
    """``estimated`` for the rules, ``ai_generated`` for a model reply or its replay. Never ``observed``."""
    return "estimated" if origin == "rules" else "ai_generated"


# -------------------------------------------------------------------------------------------------- resolution


@dataclass(frozen=True, slots=True)
class ProfileResult:
    record: dict[str, Any]
    path: Path

    @property
    def origin(self) -> str:
        return str(self.record["origin"])


def record_path(root: Path) -> Path:
    return root / "config" / RECORD_FILE


def selection_path(root: Path) -> Path:
    return root / "config" / SELECTION_FILE


def _read_stored(path: Path) -> dict[str, Any] | None:
    if not path.is_file() or path.stat().st_size > MAX_RECORD_BYTES:
        return None
    try:
        document = loads_strict(path.read_bytes().decode())
    except (OSError, ValueError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        return None
    return document if isinstance(document, dict) and document.get("schema") == RECORD_SCHEMA else None


def _replay(stored: Mapping[str, Any] | None, hashes: Mapping[str, str]) -> tuple[str, StreetProfile] | None:
    """The reply of a stored record whose hashes all match, as ``realize._stored_reply``; rules are never replayed."""
    if stored is None or any(stored.get(key) != value for key, value in hashes.items()):
        return None
    if stored.get("origin") not in REPLAYABLE:
        return None
    raw = stored.get("raw")
    if not isinstance(raw, str) or _sha(raw) != stored.get("raw_sha256"):
        return None
    try:
        return raw, parse_street_profile(raw)
    except StreetProfileError:
        return None


def _ask(provider: Provider | None, message: str) -> tuple[str, dict[str, Any] | None]:
    if provider is None:
        from onward_generator.ai.providers import OllamaProvider

        provider = OllamaProvider()
    result = provider.complete(SYSTEM_PROMPT, message, profile_schema())
    return result.text, dict(result.provenance)


def _calm_plan(root: Path) -> bool:
    """True when the map's texture plan passes the calm check ``materials --repetition`` makes; a missing plan is not calm."""
    path = root / "config" / "texture_plan.json"
    if not path.is_file():
        return False
    from onward_generator.materials.library_plan import PlanError, assert_library_entries

    try:
        document = json.loads(path.read_bytes())
        if document.get("provider") == "library":
            assert_library_entries(document, extended=True)
    except (OSError, ValueError, KeyError, TypeError, PlanError):
        return False
    return True


def resolve_street_profile(
    root: str | Path, *, provider: Provider | None = None, library: str | Path | None = "assets",
) -> ProfileResult:
    """Write ``config/street_profile.json`` (and the selection when a props profile applies) for the map at ``root``."""
    from onward_generator.maps.workspace import load_map

    base = Path(root).expanduser().resolve()
    document = load_map(base)
    brief = str(document.get("brief", ""))
    center: Mapping[str, Any] = document["center"]
    rows = tuple(library_conditions(library))
    before = effective_settings(base)
    message = instruction(brief, center, rows)
    hashes = {
        "schema_sha256": schema_sha256(),
        "prompt_sha256": prompt_sha256(message),
        "inputs_sha256": inputs_sha256(brief, center, rows, bool(before["sightline_cover"])),
    }
    path = record_path(base)
    stored = _read_stored(path)
    diagnostics: list[dict[str, str]] = []
    replayed = _replay(stored, hashes)
    origin: Origin
    if replayed is not None and stored is not None:
        (raw, profile), attempts, reason, provider_info = replayed, 0, None, stored.get("provider")
        origin = "replay"
    else:
        if stored is not None and stored.get("origin") in REPLAYABLE:
            _archive(path, stored)
            diagnostics.append({"code": "AI_PROFILE_STALE", "message": "the stored reply was made for other inputs; it was archived"})
        raw, answer, attempts, reason, provider_info = _ask_twice(provider, message, diagnostics)
        origin = "rules" if answer is None else "ai_generated"
        profile = answer or RULES_PROFILE
    after, change_rows = translate(profile, before, Context(_calm_plan(base), rows))
    protected = guard(before, after)
    if protected:
        reason = f"AI_PROFILE_PROTECTED: the profile would change {', '.join(protected)}"
        diagnostics.append({"code": "AI_PROFILE_PROTECTED", "message": reason})
        # The neutral profile writes nothing, so there is nothing to translate.
        profile, origin, change_rows = RULES_PROFILE, "rules", []
    record = {
        "schema": RECORD_SCHEMA,
        "schema_version": RECORD_VERSION,
        "profile_version": PROFILE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "origin": origin,
        "provenance": provenance_for(origin),
        "attempts": attempts,
        "reason": reason,
        "diagnostics": diagnostics,
        "raw": raw,
        "raw_sha256": _sha(raw),
        **hashes,
        "ids": profile.ids(),
        "summary": profile.summary,
        "summary_status": "unverified",
        "provider": provider_info,
        "changes": changes(change_rows, origin),
    }
    _write_selection(base, profile, change_rows, rows)
    atomic_write_json(path, record)  # map create always writes config/
    return ProfileResult(record, path)


def _ask_twice(
    provider: Provider | None, message: str, diagnostics: list[dict[str, str]],
) -> tuple[str, StreetProfile | None, int, str | None, Any]:
    """At most ``PROFILE_ATTEMPTS`` calls; the second quotes the first failure (ADR-010 §5)."""
    last_error: StreetProfileError | None = None
    last_text = ""
    for attempt in range(1, PROFILE_ATTEMPTS + 1):
        text = message
        if last_error is not None:
            text += (
                "\nThe previous reply was rejected:\n"
                f"{last_error}\n"
                "Reply again with one JSON object. Use only the ids listed in the system message.\n"
            )
        try:
            last_text, info = _ask(provider, text)
        except (RuntimeError, OSError, ValueError, TypeError) as exc:  # ProviderError is a RuntimeError
            last_error = StreetProfileError("AI_PROFILE_PROVIDER", str(exc))
            diagnostics.append({"code": last_error.code, "message": last_error.message})
            continue
        try:
            return last_text, parse_street_profile(last_text), attempt, None, info
        except StreetProfileError as exc:
            last_error = exc
            diagnostics.append({"code": exc.code, "message": exc.message})
    return last_text, None, PROFILE_ATTEMPTS, str(last_error), None


def _archive(path: Path, stored: Mapping[str, Any]) -> None:
    """Keep a stale reply as ``street_profile.<raw_sha256[:12]>.json``; one already archived is left as it is."""
    target = path.with_name(f"street_profile.{str(stored.get('raw_sha256'))[:12]}.json")
    if not target.exists():
        atomic_write_bytes(target, path.read_bytes())


def _write_selection(root: Path, profile: StreetProfile, rows: Sequence[Mapping[str, Any]], library: Sequence[tuple[str, str]]) -> None:
    from onward_generator.overgrow.library_catalog import SELECTION_SCHEMA, SELECTION_VERSION

    target = selection_path(root)
    applied = any(row["field"] == "props_profile" and row["status"] == "applied" for row in rows)
    if not applied:
        if target.is_file():
            target.unlink()
        return
    document = {"schema": SELECTION_SCHEMA, "schema_version": SELECTION_VERSION, "ids": selection_for(profile.props_profile, library)}
    atomic_write_bytes(target, (json.dumps(document, indent=2) + "\n").encode("utf-8"))


# -------------------------------------------------------------------------------------------------- reading a record


def load_record(path: str | Path) -> dict[str, Any]:
    """A stored record, checked: its schema, every id in its enum, a known origin and changes that match ``EFFECTS``."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise StreetProfileError("AI_PROFILE_RECORD", f"{source}: street profile record not found")
    if source.stat().st_size > MAX_RECORD_BYTES:
        raise StreetProfileError("AI_PROFILE_RECORD", f"{source}: the record is over {MAX_RECORD_BYTES} bytes")
    try:
        record = loads_strict(source.read_bytes().decode())
    except (OSError, ValueError) as exc:
        raise StreetProfileError("AI_PROFILE_RECORD", f"{source}: not strict JSON ({exc})") from exc
    if not isinstance(record, dict) or record.get("schema") != RECORD_SCHEMA or record.get("schema_version") != RECORD_VERSION:
        raise StreetProfileError("AI_PROFILE_RECORD", f"{source}: not a {RECORD_SCHEMA} {RECORD_VERSION} record")
    ids = record.get("ids")
    if not isinstance(ids, dict) or set(ids) != set(FIELDS) or any(ids[name] not in field_ids(name) for name in FIELDS):
        raise StreetProfileError("AI_PROFILE_RECORD", f"{source}: ids must name one allowed id per field")
    if record.get("origin") not in ORIGINS or record.get("provenance") != provenance_for(str(record.get("origin"))):
        raise StreetProfileError("AI_PROFILE_RECORD", f"{source}: origin and provenance do not match")
    rows = record.get("changes")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise StreetProfileError("AI_PROFILE_RECORD", f"{source}: changes must be a list of objects")
    for row in rows:
        effect = EFFECTS.get((str(row.get("field")), str(row.get("id"))))
        if effect is None or ids.get(effect.field) != effect.id or row.get("pointer") != effect.pointer:
            raise StreetProfileError("AI_PROFILE_PROTECTED", f"{source}: change {row.get('pointer')!r} is not the effect of its id")
    return record


def report_block(path: str | Path) -> dict[str, Any]:
    """The ``street_profile`` block a stage adds to its report. The same for a reply and its replay."""
    record = load_record(path)
    return {
        "schema_version": RECORD_VERSION,
        "raw_sha256": record.get("raw_sha256"),
        "ids": dict(record["ids"]),
        "provenance": record["provenance"],
    }


def _stage_argv(effect: Effect, root: Path) -> list[str] | None:
    from onward_generator.maps.workspace import combine_cell_m, sightline_cover_enabled
    from onward_generator.next_step import show

    if effect.field == "palette":
        return [effect.flag, "--cell-m", str(combine_cell_m(root))]
    if effect.field == "props_profile":
        selection = selection_path(root)
        return [effect.flag, show(selection)] if selection.is_file() else None
    if effect.field == "cover" and not sightline_cover_enabled(root):
        return None
    return [effect.flag, str(effect.value)]


def profile_flags(root: str | Path | None) -> dict[str, list[str]]:
    """The extra flags per stage (``materials``, ``overgrow``, ``cover``) from the map's stored record.

    Empty when the switch is off. A missing or invalid record gives no flags and a warning on stderr; the model is
    never called here. Each flag and value is a literal of ``EFFECTS``: nothing of the reply reaches a command line.
    """
    if root is None:
        return {}
    from onward_generator.maps.workspace import street_profile_enabled
    from onward_generator.next_step import show

    base = Path(root).expanduser().resolve()
    if not street_profile_enabled(base):
        return {}
    path = record_path(base)
    try:
        record = load_record(path)
    except StreetProfileError as exc:
        print(f"warning: street profile not applied: {exc}", file=sys.stderr)
        return {}
    flags: dict[str, list[str]] = {}
    for row in record["changes"]:
        if row.get("status") != "applied":
            continue
        effect = EFFECTS[(str(row["field"]), str(row["id"]))]
        argv = _stage_argv(effect, base)
        if argv is not None:
            flags.setdefault(effect.stage, []).extend(argv)
    return {stage: [*argv, "--street-profile", show(path)] for stage, argv in flags.items()}


def describe(result: ProfileResult) -> str:
    """The line ``run`` prints after resolving."""
    record = result.record
    chosen = ", ".join(f"{name}={record['ids'][name]}" for name in FIELDS if record["ids"][name] != NEUTRAL) or "all map_default"
    tail = f" ({record['reason']})" if record.get("reason") else ""
    return f"street profile: {record['origin']}, {chosen}{tail}"
