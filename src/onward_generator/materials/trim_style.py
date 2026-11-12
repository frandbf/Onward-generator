"""Regional facade styles and the trim-sheet atlas layout (ADR-020).

The style table (``config/facade_styles.json``) names one style per region and,
for a style that has one, the recipe of its atlas. The layout puts horizontal
strips across the whole width of the atlas, so U repeats along a facade while V
stays inside one strip, and a row of cells at the top for the openings, which
fit 0–1.

UVs follow the rest of the pipeline: V = 0 is the bottom row of the PNG.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match

from onward_generator.detail.typology import LATE_PERIOD, PERIODS

REPO_ROOT = Path(__file__).resolve().parents[3]
STYLE_TABLE_PATH = REPO_ROOT / "config" / "facade_styles.json"
STYLE_SCHEMA_PATH = REPO_ROOT / "config" / "facade_styles.schema.json"
# Opt-in recipes for the styles whose atlas is null in the main table (plan D1).
REGIONAL_TABLE_PATH = REPO_ROOT / "config" / "facade_styles.regional.json"
# The era periods of detail (ADR-024), which key the era bands of an atlas.
ERA_PERIODS = (*(name for name, _ in PERIODS), LATE_PERIOD)
FACADE_MODES = ("trim", "legacy")
AUTO = "auto"
# The cells of the fit row, left to right. A window family has two shutter positions.
DOOR_CELLS = ("door_0", "door_1")
SHOP_CELL = "shop_glass"
BOARDED_CELL = "boarded"


class StyleError(ValueError):
    """The style table is invalid, or names a style it does not define."""


@dataclass(frozen=True, slots=True)
class Style:
    name: str
    note: str
    atlas: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class StyleTable:
    path: Path
    sha256: str
    default: str
    rules: tuple[tuple[str, tuple[tuple[float, float, float, float], ...]], ...]
    styles: Mapping[str, Style]


def load_style_table(path: str | Path | None = None) -> StyleTable:
    """Read and validate the style table. Every style named by a rule or the default must exist."""
    source = Path(path) if path is not None else STYLE_TABLE_PATH
    raw = source.read_bytes()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StyleError(f"{source}: not JSON: {exc}") from exc
    schema = json.loads(STYLE_SCHEMA_PATH.read_bytes())
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        where = "/".join(str(p) for p in first.absolute_path) or "(root)"
        raise StyleError(f"{source}: {where}: {first.message}")
    styles = {name: Style(name, str(body["note"]), body["atlas"]) for name, body in doc["styles"].items()}
    rules = tuple((str(r["style"]), tuple(_box(box) for box in r["boxes"])) for r in doc["selection"])
    for name in [doc["default_style"], *(style for style, _ in rules)]:
        if name not in styles:
            raise StyleError(f"{source}: style {name!r} is used but not defined")
    for style in styles.values():
        if style.atlas is not None:
            _check_atlas(style.name, style.atlas)
    return StyleTable(source, hashlib.sha256(raw).hexdigest(), str(doc["default_style"]), rules, styles)


@dataclass(frozen=True, slots=True)
class RegionalTable:
    """The opt-in atlas recipes of ``config/facade_styles.regional.json``, by style name."""

    path: Path
    sha256: str
    atlases: Mapping[str, Mapping[str, Any]]


def load_regional_table(table: StyleTable, path: str | Path | None = None) -> RegionalTable:
    """Read and validate the regional recipes against ``table``.

    Each recipe must name a style of ``table`` whose atlas is null there: a regional
    recipe never replaces an atlas the main table already has (the mediterranean one).
    """
    source = Path(path) if path is not None else REGIONAL_TABLE_PATH
    raw = source.read_bytes()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StyleError(f"{source}: not JSON: {exc}") from exc
    schema = json.loads(STYLE_SCHEMA_PATH.read_bytes())
    regional = {"$defs": schema["$defs"], "$ref": "#/$defs/regional_table"}
    first = best_match(Draft202012Validator(regional).iter_errors(doc))
    if first is not None:
        where = "/".join(str(p) for p in first.absolute_path) or "(root)"
        raise StyleError(f"{source}: {where}: {first.message}")
    atlases: dict[str, Mapping[str, Any]] = doc["atlases"]
    for name, atlas in atlases.items():
        if name not in table.styles:
            raise StyleError(f"{source}: style {name!r} is not in the style table")
        if table.styles[name].atlas is not None:
            raise StyleError(f"{source}: style {name} already has an atlas in the style table")
        _check_atlas(name, atlas)
    return RegionalTable(source, hashlib.sha256(raw).hexdigest(), atlases)


def style_atlas(table: StyleTable, name: str, regional: RegionalTable | None) -> tuple[Mapping[str, Any] | None, str | None]:
    """The atlas of a style and where it comes from: ``table``, ``regional`` or none."""
    atlas = table.styles[name].atlas
    if atlas is not None:
        return atlas, "table"
    if regional is not None and name in regional.atlases:
        return regional.atlases[name], "regional"
    return None, None


def _box(raw: Sequence[float]) -> tuple[float, float, float, float]:
    lat0, lat1, lon0, lon1 = (float(v) for v in raw)
    return lat0, lat1, lon0, lon1


def _check_atlas(name: str, atlas: Mapping[str, Any]) -> None:
    cells = len(window_cells(atlas)) + len(DOOR_CELLS) + 2
    if cells * int(atlas["fit_cell_px"]) > int(atlas["size_px"]):
        raise StyleError(f"style {name}: {cells} cells of {atlas['fit_cell_px']} px do not fit the atlas width")
    for wall, target in atlas["scenic_walls"].items():
        if target not in atlas["walls"]:
            raise StyleError(f"style {name}: scenic wall {wall} maps to {target}, which has no strip")
    for wall in atlas.get("wall_tints") or {}:
        if wall not in atlas["walls"]:
            raise StyleError(f"style {name}: wall tint {wall} has no strip")
    for wall, target in (atlas.get("estimated_walls") or {}).items():
        if wall not in atlas["walls"] or target not in atlas["walls"]:
            raise StyleError(f"style {name}: estimated wall {wall} -> {target} needs a strip for both")
    if wall_strip_px(atlas) < 4 * int(atlas["gutter_px"]):
        strips = len(atlas["walls"]) * len(atlas["tones"])
        raise StyleError(f"style {name}: {strips} wall strips do not fit the atlas height")
    for period, band in (atlas.get("eras") or {}).items():
        for tone in band["tones"]:
            if tone >= len(atlas["tones"]):
                raise StyleError(f"style {name}: era {period} tone {tone} has no strip")
        for family in band["window_families"]:
            if family >= len(atlas["window_families"]):
                raise StyleError(f"style {name}: era {period} window family {family} does not exist")


def select_style(table: StyleTable, latitude: float, longitude: float) -> str:
    """The first style whose box holds the point, else the default. Boxes include their edges."""
    for style, boxes in table.rules:
        for lat0, lat1, lon0, lon1 in boxes:
            if lat0 <= latitude <= lat1 and lon0 <= longitude <= lon1:
                return style
    return table.default


def resolve_style(table: StyleTable, requested: str, origin: Sequence[float] | None) -> tuple[str, str]:
    """The style name and how it was chosen: ``config`` (named), ``map_centre`` or ``default`` (no centre)."""
    if requested != AUTO:
        if requested not in table.styles:
            raise StyleError(f"unknown facade style {requested!r}; known: {', '.join(sorted(table.styles))}")
        return requested, "config"
    if origin is None:
        return table.default, "default"
    longitude, latitude = float(origin[0]), float(origin[1])
    return select_style(table, latitude, longitude), "map_centre"


def window_cells(atlas: Mapping[str, Any]) -> list[str]:
    """``window_<family>_<position>`` for every family and shutter position."""
    return [f"window_{f}_{p}" for f, family in enumerate(atlas["window_families"]) for p in range(len(family["closed"]))]


def fit_cells(atlas: Mapping[str, Any]) -> list[str]:
    return [*window_cells(atlas), *DOOR_CELLS, SHOP_CELL, BOARDED_CELL]


def wall_strip_px(atlas: Mapping[str, Any]) -> int:
    """Rows of each wall strip (one per wall colour and tone): what the shared rows leave, split evenly, rounded down to 8 px."""
    shared = sum(int(atlas[key]) for key in ("fit_row_px", "cornice_px", "zocalo_px", "stone_px"))
    return (int(atlas["height_px"]) - shared) // (len(atlas["walls"]) * len(atlas["tones"])) // 8 * 8


@dataclass(frozen=True, slots=True)
class Strip:
    """A band of the atlas: pixel rows ``row0 … row1`` from the top, and its UV range inside the gutters."""

    row0: int
    row1: int
    v0: float
    v1: float
    repeat_m: float


@dataclass(frozen=True, slots=True)
class Cell:
    col0: int
    col1: int
    row0: int
    row1: int
    u0: float
    v0: float
    u1: float
    v1: float


@dataclass(frozen=True, slots=True)
class TrimLayout:
    """``size`` is the width and ``height`` the height in pixels. ``walls`` holds the first tone of each
    wall colour, ``tones`` every tone of it, the first one included."""

    size: int
    height: int
    gutter: int
    walls: Mapping[str, Strip]
    tones: Mapping[str, tuple[Strip, ...]]
    zocalo: Strip
    cornice: Strip
    stone: Strip
    cells: Mapping[str, Cell]


def trim_layout(atlas: Mapping[str, Any]) -> TrimLayout:
    """Rows from the top: the fit row, the cornice, the zocalo, the stone trim, then one strip per wall colour and tone."""
    size, height, gutter = int(atlas["size_px"]), int(atlas["height_px"]), int(atlas["gutter_px"])

    def strip(row0: int, rows: int, repeat_m: float) -> Strip:
        row1 = row0 + rows
        return Strip(row0, row1, 1.0 - (row1 - gutter) / height, 1.0 - (row0 + gutter) / height, float(repeat_m))

    fit = int(atlas["fit_row_px"])
    cornice = strip(fit, int(atlas["cornice_px"]), atlas["cornice_repeat_m"])
    zocalo = strip(cornice.row1, int(atlas["zocalo_px"]), atlas["zocalo_repeat_m"])
    stone = strip(zocalo.row1, int(atlas["stone_px"]), atlas["stone_repeat_m"])
    rows, count = wall_strip_px(atlas), len(atlas["tones"])
    tones = {
        name: tuple(strip(stone.row1 + (index * count + tone) * rows, rows, atlas["wall_repeat_m"]) for tone in range(count))
        for index, name in enumerate(sorted(atlas["walls"]))
    }
    width = int(atlas["fit_cell_px"])
    cells = {}
    for index, name in enumerate(fit_cells(atlas)):
        col0, col1 = index * width, (index + 1) * width
        cells[name] = Cell(
            col0, col1, 0, fit,
            (col0 + gutter) / size, 1.0 - (fit - gutter) / height, (col1 - gutter) / size, 1.0 - gutter / height,
        )
    walls = {name: strips[0] for name, strips in tones.items()}
    return TrimLayout(size, height, gutter, walls, tones, zocalo, cornice, stone, cells)
