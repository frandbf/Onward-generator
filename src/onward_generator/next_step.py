"""Print the next pipeline command as one line that can be pasted in a shell.

Run it from the repository root. Paths are relative to the current directory
when they sit inside it.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path

_PREFIX = "PYTHONPATH=src .venv/bin/python -m onward_generator"


def emit(command: str) -> None:
    print(f"next: {command}")


def invoke(*parts: str) -> str:
    return _PREFIX + " " + " ".join(shlex.quote(part) for part in parts)


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


def geo_command(directory: str | Path, retrieved_at: str | None = None) -> str:
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


def heights_command(scene: Path) -> str:
    return invoke("heights", "--scene", show(scene), "--out", show(stage_path(scene, "heights")))


def generate_command(scene: Path, heights: Path) -> str:
    return invoke(
        "generate",
        "--scene", show(scene),
        "--heights", show(heights),
        "--out", show(stage_path(scene, "scene3d")),
    )


def detail_command(scene: Path, heights: Path, base: Path) -> str:
    return invoke(
        "detail",
        "--scene", show(scene),
        "--heights", show(heights),
        "--base", show(base),
        "--out", show(stage_path(scene, "scene_detail")),
    )


def materials_command(base: Path) -> str:
    """``materials apply`` with the map's own plan. ``--download`` is the explicit network step."""
    root = map_root(base)
    plan = (root / "config" / "texture_plan.json") if root is not None else base.parent / "texture_plan.json"
    return invoke(
        "materials", "apply",
        "--base", show(base),
        "--plan", show(plan),
        "--download",
        "--out", show(stage_path(base, "scene_materials")),
    )


def dress_command(detail: Path, scene: Path | None = None, heights: Path | None = None) -> str:
    parts = ["dress", "style", "--detail", show(detail)]
    if scene is not None:
        parts.extend(["--scene", show(scene)])
    if heights is not None:
        parts.extend(["--heights", show(heights)])
    parts.extend(["--out", show(stage_path(detail, "scene_dress"))])
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


def after_materials(base: Path) -> str:
    """The command printed after ``materials apply``.

    A map that did not ask for street photos continues at the plates.
    """
    from onward_generator.maps.workspace import panoramax_enabled

    root = map_root(base)
    if root is not None and panoramax_enabled(root):
        return panoramax_command(base)
    return signs_command(base)


def panoramax_command(base: Path) -> str:
    """Copy materials forward and project 360 photos. Runs only when the map asks for it."""
    return invoke(
        "panoramax", "apply",
        "--base", show(base),
        "--out", show(stage_path(base, "scene_panoramax")),
    )


def signs_command(base: Path) -> str:
    scene = _geo_for(base)
    out_name = "scene_signs_compact" if "compact" in Path(base).name else "scene_signs"
    return invoke(
        "signs", "place",
        "--base", show(base),
        "--scene", show(scene),
        "--out", show(stage_path(base, out_name)),
    )


def fit_command(scene: Path, base: Path) -> str:
    return invoke(
        "overgrow", "fit",
        "--scene", show(scene),
        "--base", show(base),
        "--out", show(Path(base) / "fit.json"),
    )


def overgrow_command(base: Path) -> str:
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
    out_name = "scene_phase3" if compact else "scene_overgrow"
    parts.extend(["--out", show(stage_path(base, out_name))])
    return invoke(*parts)


def bounds_command(base: Path) -> str:
    out = stage_path(base, "scene_playable")
    if out.exists():
        compact = stage_path(base, "scene_playable_compact")
        out = compact if not compact.exists() else stage_path(base, "scene_playable_next")
    return invoke("bounds", "--base", show(base), "--out", show(out))


def layout_command(playable: Path) -> str | None:
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
        "--out", show(stage_path(playable, "layout", "layout.json")),
    )


def export_command(detail: Path, layout: Path) -> str | None:
    root = map_root(detail) or map_root(layout)
    if root is None:
        return None
    project = root / "config" / "project.json"
    if not project.is_file():
        return None
    parts = [
        "export", "package",
        "--project", show(project),
        "--detail", show(detail),
        "--layout", show(layout),
        "--out", show(stage_path(detail, "export", "pkg_01")),
    ]
    config = root / "config" / "export.json"
    if config.is_file():
        parts[6:6] = ["--config", show(config)]
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
