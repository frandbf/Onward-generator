"""Print the next pipeline command as one line that can be pasted in a shell.

Run it from the repository root. Paths are relative to the current directory
when they sit inside it. Each command is kept as its argument list, so
``run`` executes it without parsing the printed line back.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from onward_generator.cadastre.acquire import buildings_path

_PREFIX = ".venv/bin/python -m onward_generator"
# The directory under data/processed that each pipeline stage writes. `run` reads it to find a stage's output.
STAGE_DIRS: Mapping[str, str] = MappingProxyType({
    "geo": "geo",
    "heights": "heights",
    "generate": "scene3d",
    "detail": "scene_detail",
    "dress": "scene_dress",
    "materials": "scene_materials",
    "panoramax": "scene_panoramax",
    "signs": "scene_signs",
    "overgrow": "scene_overgrow",
    "fit": "scene_overgrow",
    "bounds": "scene_playable",
    "layout": "layout",
})


@dataclass(frozen=True)
class StageCommand:
    """One CLI call after ``python -m onward_generator``. ``argv`` is what runs."""

    argv: tuple[str, ...]

    def display(self) -> str:
        """The shell line printed after ``next:``."""
        return _PREFIX + " " + shlex.join(self.argv)


def emit(command: StageCommand) -> None:
    print(f"next: {command.display()}")


def invoke(*parts: str) -> StageCommand:
    return StageCommand(tuple(parts))


def show(path: Path) -> str:
    resolved = path.expanduser().resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(resolved)


def map_root(start: Path) -> Path | None:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent
    for parent in (current, *current.parents):
        if (parent / "map.json").is_file():
            return parent
    return None


def stage_path(start: Path, *relative: str) -> Path:
    """A file under ``data/processed`` of the map that contains ``start``."""
    root = map_root(start)
    if root is not None:
        return root / "data" / "processed" / Path(*relative)
    current = start.expanduser().resolve()
    stage_dir = current.parent if current.is_file() else current
    return stage_dir.parent.joinpath(*relative)


def geo_command(directory: str | Path, retrieved_at: str | None = None) -> StageCommand:
    from onward_generator.maps.workspace import load_map

    root = Path(directory).expanduser().resolve()
    document = load_map(root)
    center = document["center"]
    paths = document["paths"]
    source = root / paths["raw"] / f"{document['id']}.osm"
    when = retrieved_at if retrieved_at is not None else _retrieved_at(source)
    parts = [
        "geo",
        "--source", show(source),
        "--aoi", show(root / paths["aoi"]),
        "--origin", str(center["longitude"]), str(center["latitude"]),
        "--crs", str(document["crs"]),
        "--out", show(root / paths["processed"] / "geo"),
    ]
    if when:
        parts.extend(["--retrieved-at", when])
    return invoke(*parts)


def heights_command(scene: Path, overrides: Path | None = None, secondary: Path | None = None) -> StageCommand:
    extra = ("--overrides", show(overrides)) if overrides is not None else ()
    third = ("--secondary-overrides", show(secondary)) if secondary is not None else ()
    return invoke("heights", "--scene", show(scene), *extra, *third, "--out", show(stage_path(scene, STAGE_DIRS["heights"])))


def overture_fetch_command(root: Path) -> StageCommand:
    """``overture fetch``: Overture places and buildings for the map bbox into ``data/raw`` (network, ADR-021)."""
    return invoke("overture", "fetch", "--map", show(root), "--out", show(root / "data" / "raw"))


def overture_match_command(scene: Path, root: Path) -> StageCommand:
    """``overture match``: third-party height overrides and POIs for the scene (offline, ADR-021)."""
    return invoke("overture", "match", "--scene", show(scene), "--map", show(root), "--out", show(stage_path(scene, "overture")))


def catastro_command(scene: Path, gml: Path) -> StageCommand:
    """``cadastre download``: the one Catastro request (network)."""
    return invoke("cadastre", "download", "--scene", show(scene), "--out", show(gml))


def cadastre_split_command(scene: Path, gml: Path) -> StageCommand:
    """``cadastre split``: sub-buildings and storey overrides (ADR-016)."""
    return invoke(
        "cadastre", "split",
        "--scene", show(scene),
        "--gml", show(gml),
        "--buildings", show(buildings_path(gml)),
        "--out", show(stage_path(scene, "cadastre")),
    )


def generate_command(scene: Path, heights: Path) -> StageCommand:
    return invoke(
        "generate",
        "--scene", show(scene),
        "--heights", show(heights),
        "--out", show(stage_path(scene, STAGE_DIRS["generate"])),
    )


def core_radius(start: Path) -> float | None:
    """``core_radius_m`` of the map that contains ``start`` (ADR-017), or None without a core or a map."""
    root = map_root(start)
    if root is None:
        return None
    value = json.loads((root / "map.json").read_bytes()).get("core_radius_m")
    return float(value) if value else None


def detail_command(scene: Path, heights: Path, base: Path, places: Path | None = None) -> StageCommand:
    """``detail``, with the map's playable core when ``map.json`` has one, and Overture places when given."""
    core = core_radius(scene)
    return invoke(
        "detail",
        "--scene", show(scene),
        "--heights", show(heights),
        "--base", show(base),
        *(("--places", show(places)) if places is not None else ()),
        "--out", show(stage_path(scene, STAGE_DIRS["detail"])),
        *(("--core-radius-m", f"{core:g}") if core is not None else ()),
    )


def materials_command(base: Path) -> StageCommand:
    """``materials apply`` with the map's own plan. ``--download`` is the explicit network step."""
    root = map_root(base)
    plan = (root / "config" / "texture_plan.json") if root is not None else base.parent / "texture_plan.json"
    from onward_generator.maps.workspace import facade_options

    return invoke(
        "materials", "apply",
        "--base", show(base),
        "--plan", show(plan),
        "--download",
        *(facade_options(root) if root is not None else []),
        "--out", show(stage_path(base, STAGE_DIRS["materials"])),
    )


def dress_command(detail: Path, scene: Path | None = None, heights: Path | None = None) -> StageCommand:
    parts = ["dress", "style", "--detail", show(detail)]
    if scene is not None:
        parts.extend(["--scene", show(scene)])
    if heights is not None:
        parts.extend(["--heights", show(heights)])
    parts.extend(["--out", show(stage_path(detail, STAGE_DIRS["dress"]))])
    return invoke(*parts)


def _geo_for(base: Path) -> Path:
    """Scene path for this map. It does not have to exist yet: the runner plans before ``geo`` writes it."""
    root = map_root(base)
    if root is not None and "compact" in Path(base).name:
        compact = root / "data" / "processed" / "geo_compact" / "geo_scene.json"
        if compact.is_file():
            return compact
    if root is not None:
        return root / "data" / "processed" / "geo" / "geo_scene.json"
    return _existing(_inputs(base), "geo_scene", "path") or Path("geo_scene.json")


def after_materials(base: Path) -> StageCommand:
    """The command printed after ``materials apply``.

    A map that did not ask for street photos continues at the plates.
    """
    from onward_generator.maps.workspace import panoramax_enabled

    root = map_root(base)
    if root is not None and panoramax_enabled(root):
        return panoramax_command(base)
    return signs_command(base)


def panoramax_command(base: Path) -> StageCommand:
    """Copy materials forward and project 360 photos. Runs only when the map asks for it."""
    return invoke(
        "panoramax", "apply",
        "--base", show(base),
        "--out", show(stage_path(base, STAGE_DIRS["panoramax"])),
    )


def signs_command(base: Path) -> StageCommand:
    scene = _geo_for(base)
    out_name = STAGE_DIRS["signs"] + ("_compact" if "compact" in Path(base).name else "")
    return invoke(
        "signs", "place",
        "--base", show(base),
        "--scene", show(scene),
        "--out", show(stage_path(base, out_name)),
    )


def fit_command(scene: Path, base: Path) -> StageCommand:
    return invoke(
        "overgrow", "fit",
        "--scene", show(scene),
        "--base", show(base),
        "--out", show(Path(base) / "fit.json"),
    )


def overgrow_command(base: Path) -> StageCommand:
    root = map_root(base)
    compact = "compact" in Path(base).name
    scene = _geo_for(base)
    parts = [
        "overgrow", "place",
        "--base", show(base),
        "--scene", show(scene),
        "--profile", "100",
        "--seed", "48152",
    ]
    if compact and root is not None:
        zone = root / "config" / "zone_compact.json"
        layout = root / "data" / "processed" / "layout" / "layout.json"
        if zone.is_file():
            parts.extend(["--zone-polygon", show(zone)])
        if layout.is_file():
            parts.extend(["--layout", show(layout)])
    library = Path("assets")
    if library.is_dir() and any(
        child.is_dir() and (child / "manifest.json").is_file() for child in library.iterdir()
    ):
        parts.extend(["--library", show(library)])
    if root is not None:
        selection = root / "config" / "prop_selection.json"
        if selection.is_file():
            parts.extend(["--selection", show(selection)])
    out_name = "scene_phase3" if compact else STAGE_DIRS["overgrow"]
    parts.extend(["--out", show(stage_path(base, out_name))])
    return invoke(*parts)


def bounds_command(base: Path) -> StageCommand:
    playable = STAGE_DIRS["bounds"]
    out = stage_path(base, playable)
    if out.exists():
        compact = stage_path(base, f"{playable}_compact")
        out = compact if not compact.exists() else stage_path(base, f"{playable}_next")
    return invoke("bounds", "--base", show(base), "--out", show(out))


def layout_command(playable: Path) -> StageCommand | None:
    inputs = _inputs(playable)
    scene = _existing(inputs, "geo_scene", "path")
    heights = _existing(inputs, "heights", "path")
    base = _existing(inputs, "phase03_base", "dir")
    root = map_root(playable)
    if root is not None:
        scene = scene or _if_file(root / "data" / "processed" / "geo" / "geo_scene.json")
        heights = heights or _if_file(root / "data" / "processed" / "heights" / "heights.json")
        base = base or _if_dir(root / "data" / "processed" / "scene3d")
    if scene is None or heights is None or base is None:
        return None
    return invoke(
        "perf", "layout",
        "--scene", show(scene),
        "--heights", show(heights),
        "--base", show(base),
        "--detail", show(playable),
        "--playable",
        "--out", show(stage_path(playable, STAGE_DIRS["layout"], "layout.json")),
    )


def export_command(detail: Path, layout: Path) -> StageCommand | None:
    root = map_root(detail) or map_root(layout)
    if root is None:
        return None
    if not (root / "config" / "project.json").is_file():
        return None
    return export_invocation(root, detail, layout, stage_path(detail, "export", "pkg_01"))


def export_invocation(root: Path, detail: Path, layout: Path, out: Path) -> StageCommand:
    """``export package`` for the map at ``root``.

    ``--config`` is passed only when ``config/export.json`` exists. Without it
    the exporter uses its default configuration.
    """
    parts = [
        "export", "package",
        "--project", show(root / "config" / "project.json"),
        "--detail", show(detail),
    ]
    config = root / "config" / "export.json"
    if config.is_file():
        parts.extend(["--config", show(config)])
    parts.extend(["--layout", show(layout), "--out", show(out)])
    return invoke(*parts)


def _retrieved_at(source: Path) -> str | None:
    side = source.with_name(source.name + ".source.json")
    if not side.is_file():
        return None
    try:
        value = json.loads(side.read_text(encoding="utf-8")).get("retrieved_at")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return str(value) if value else None


def _inputs(directory: Path) -> dict:
    for name in ("scene_detail.json", "scene3d.json"):
        path = directory.expanduser() / name
        if path.is_file():
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {}
            inputs = document.get("inputs")
            return inputs if isinstance(inputs, dict) else {}
    return {}


def _existing(inputs: dict, group: str, key: str) -> Path | None:
    value = (inputs.get(group) or {}).get(key)
    if not value:
        return None
    path = Path(value)
    if key == "dir" and path.is_dir():
        return path
    if key == "path" and path.is_file():
        return path
    return None


def _if_file(path: Path) -> Path | None:
    return path if path.is_file() else None


def _if_dir(path: Path) -> Path | None:
    return path if path.is_dir() else None
