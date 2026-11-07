"""Run the map pipeline from the mandatory inputs, one stage at a time.

Each stage is the existing command. The runner waits for it to finish, then
starts the next. After the Python stages, a terminal asks whether to copy the
built map to the Quest. Only an explicit yes runs ``push`` with
``CONFIRM_PUSH=yes``. That replaces the headset's single local map and backs
up the previous one first.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from onward_generator.maps.workspace import (
    MapError,
    core_budget,
    gameplay_report_enabled,
    load_map,
    map_directory,
    map_storm,
    map_time_of_day,
    panoramax_enabled,
    store_brief,
    store_panoramax,
    store_sources,
    store_storm,
    store_time_of_day,
)
from onward_generator.next_step import (
    STAGE_DIRS,
    detail_command,
    dress_command,
    export_invocation,
    fit_command,
    generate_command,
    geo_command,
    heights_command,
    invoke,
    materials_command,
    overgrow_command,
    panoramax_command,
    show,
    signs_command,
)
from onward_generator.osm.acquire import osm_target
from onward_generator.platform import HostProfile, PlatformError, resolve_host
from onward_generator.run_log import (
    SETUP_STEPS,
    Palette,
    RunLog,
    StageReport,
    plural,
    render,
    stage_line,
    summarize,
    use_color,
)
from onward_generator.sources.merge import MERGED_FILE
from onward_generator.sources.plugin import SourceError, SourceStage, SourceSteps
from onward_generator.sources.registry import check_licence, check_secondary_rank, choices, describe, load_source_config

Execute = Callable[[Sequence[str]], int]
Ask = Callable[[], str]
Push = Callable[[Path], int]
Build = Callable[[Path, Sequence[str]], int]
UnityOpen = Callable[[], bool]
Summarize = Callable[[str, Sequence[str]], StageReport]
_YES = {"s", "si", "sí", "y", "yes"}


class PipelineError(ValueError):
    """The runner cannot build or continue the pipeline."""


def map_create_argv(
    *,
    name: str,
    latitude: float,
    longitude: float,
    radius_m: float,
    margin_m: float,
    seed: int | None = None,
    description: str | None = None,
    brief: str | None = None,
    ambient_ai: bool = False,
    panoramax: bool = False,
    time_of_day: str | None = None,
    storm: bool = False,
    lightmap_texels_per_m: float = 10.0,
    core_radius_m: float | None = None,
    core_target_triangles: int | None = None,
    overwrite: bool = False,
) -> list[str]:
    argv = [
        "map", "create",
        "--name", name,
        "--lat", str(latitude),
        "--lon", str(longitude),
        "--radius-m", str(radius_m),
        "--margin-m", str(margin_m),
        "--lightmap-texels-per-m", str(lightmap_texels_per_m),
        *(("--core-radius-m", str(core_radius_m)) if core_radius_m is not None else ()),
        *(("--core-target-triangles", str(core_target_triangles)) if core_target_triangles is not None else ()),
    ]
    if seed is not None:
        argv.extend(["--seed", str(seed)])
    if description:
        argv.extend(["--description", description])
    if brief:
        argv.extend(["--brief", brief])
    if ambient_ai:
        argv.append("--ai")
    if panoramax:
        argv.append("--panoramax")
    if time_of_day:
        argv.extend(["--time-of-day", time_of_day])
    if storm:
        argv.append("--storm")
    if overwrite:
        argv.append("--overwrite")
    return argv


def plan_steps(directory: str | Path, *, preview: bool = False) -> list[tuple[str, list[str]]]:
    """Stages after ``map create``, in pipeline order. Stops before Unity.

    ``generate`` and ``detail`` skip their diagnostic PNGs unless ``preview``
    is set. The export thumbnail is drawn either way.
    """
    root = Path(directory)
    no_preview = () if preview else ("--no-preview",)
    geo_argv = list(geo_command(root).argv)
    geo_scene = _flag(geo_argv, "--out") / "geo_scene.json"
    sources, scene, overrides = source_plan(root, geo_scene)
    secondary, places = third_party_plan(root, geo_scene)
    source_steps = [(stage.name, list(stage.argv)) for stage in sources]
    heights_argv = list(heights_command(scene, overrides, secondary).argv)
    heights = _flag(heights_argv, "--out") / "heights.json"
    generate_argv = [*generate_command(scene, heights).argv, *no_preview]
    scene3d = _flag(generate_argv, "--out")
    detail_argv = [*detail_command(scene, heights, scene3d, places).argv, *no_preview]
    detail = _flag(detail_argv, "--out")
    dress_argv = list(dress_command(detail, scene, heights).argv)
    dress = _flag(dress_argv, "--out")
    materials_argv = list(materials_command(dress).argv)
    materials = _flag(materials_argv, "--out")
    panoramax_argv: list[str] | None = None
    signs_base = materials
    if panoramax_enabled(root):
        panoramax_argv = list(panoramax_command(materials).argv)
        signs_base = _flag(panoramax_argv, "--out")
    signs_argv = list(signs_command(signs_base, scene).argv)
    signs = _flag(signs_argv, "--out")
    overgrow_argv = list(overgrow_command(signs, scene).argv)
    overgrow = _flag(overgrow_argv, "--out")
    fit_argv = list(fit_command(scene, overgrow).argv)
    # bounds, layout and export are planned before their inputs exist, so the
    # ``next:`` helpers that look for those inputs are not used here.
    playable = root / "data" / "processed" / "scene_playable"
    # The haze ring past the wall (ADR-014): on the headset the ground ended at the site edge on an empty horizon,
    # as if the player could fall off the map.
    bounds_argv = list(invoke("bounds", "--base", show(overgrow), "--out", show(playable), "--backdrop").argv)
    layout_path = root / "data" / "processed" / "layout" / "layout.json"
    layout_argv = list(invoke(
        "perf", "layout",
        "--scene", show(scene),
        "--heights", show(heights),
        "--base", show(scene3d),
        "--detail", show(playable),
        "--playable",
        "--time-of-day", map_time_of_day(root),
        *(["--storm"] if map_storm(root) else []),
        *(["--gameplay-report"] if gameplay_report_enabled(root) else []),
        "--out", show(layout_path),
    ).argv)
    export_argv = list(export_invocation(
        root, playable, layout_path, root / "data" / "processed" / "export" / "pkg_01",
    ).argv)
    picture_steps: list[tuple[str, list[str]]] = []
    if panoramax_argv is not None:
        picture_steps.append(("panoramax", panoramax_argv))
    return [
        ("geo", geo_argv),
        *source_steps,
        ("heights", heights_argv),
        ("generate", generate_argv),
        ("detail", detail_argv),
        ("dress", dress_argv),
        ("materials", materials_argv),
        *picture_steps,
        ("signs", signs_argv),
        ("overgrow", overgrow_argv),
        ("fit", fit_argv),
        ("bounds", bounds_argv),
        ("layout", layout_argv),
        ("export", export_argv),
    ]


def source_plan(root: Path, scene: Path) -> tuple[list[SourceStage], Path, Path | None]:
    """The stages of the source plugins this map uses, the scene ``heights`` reads, and its overrides (ADR-018).

    Plugins run in registry order; one that writes a scene hands it to the
    next. With overrides from more than one plugin, ``overrides`` merges them
    by the priority in ``config/sources.json``. A plugin whose licence is not
    on the allowlist stops the plan before anything is fetched.
    """
    stages: list[SourceStage] = []
    overrides: list[tuple[str, Path]] = []
    for plugin_id, steps in _plugin_steps(root, scene):
        stages.extend(steps.stages)
        scene = steps.scene or scene
        if steps.overrides is not None:
            overrides.append((plugin_id, steps.overrides))
    if len(overrides) < 2:
        return stages, scene, overrides[0][1] if overrides else None
    out = root / "data" / "processed" / "sources"
    pairs = [part for plugin_id, path in overrides for part in ("--overrides", f"{plugin_id}={show(path)}")]
    argv = invoke("source", "merge", *pairs, "--out", show(out)).argv
    stages.append(SourceStage("overrides", argv, out / MERGED_FILE, tuple(path for _id, path in overrides)))
    return stages, scene, out / MERGED_FILE


def _plugin_steps(root: Path, scene: Path) -> list[tuple[str, SourceSteps]]:
    """The steps of every plugin this map runs, in registry order, each reading the scene of the ones before it."""
    config = load_source_config()
    found: list[tuple[str, SourceSteps]] = []
    for choice in choices(load_map(root)):
        if not choice.runs:
            continue
        try:
            check_licence(choice.plugin, config)
        except SourceError as exc:
            raise PipelineError(str(exc)) from exc
        steps = choice.plugin.steps(root, scene)
        scene = steps.scene or scene
        found.append((choice.plugin.id, steps))
    primary = [plugin_id for plugin_id, steps in found if steps.overrides is not None]
    secondary = [plugin_id for plugin_id, steps in found if steps.secondary_overrides is not None]
    try:
        check_secondary_rank(primary, secondary, config)
    except SourceError as exc:
        raise PipelineError(str(exc)) from exc
    return found


def third_party_plan(root: Path, scene: Path) -> tuple[Path | None, Path | None]:
    """The secondary overrides ``heights`` reads and the places ``detail`` reads, from the last plugin that writes each (ADR-021)."""
    secondary: Path | None = None
    places: Path | None = None
    for _plugin_id, steps in _plugin_steps(root, scene):
        secondary = steps.secondary_overrides or secondary
        places = steps.places or places
    return secondary, places


def _source_stages(root: Path) -> list[SourceStage]:
    try:
        return source_plan(root, root / "data" / "processed" / "geo" / "geo_scene.json")[0]
    except (MapError, PipelineError):
        return []


def format_duration(seconds: float) -> str:
    """``12.3 s``, ``1 min 05 s`` or ``1 h 02 min``."""
    whole = max(0, int(round(seconds)))
    if whole < 60:
        return f"{max(0.0, seconds):.1f} s"
    minutes, rest = divmod(whole, 60)
    if minutes < 60:
        return f"{minutes} min {rest:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


def say(text: str = "") -> None:
    """One console line, flushed so progress shows through a pipe."""
    print(text, flush=True)  # pragma: no mutate: flush only changes when a pipe sees the line, not its text


def print_header(
    name: str,
    root: Path,
    *,
    latitude: float,
    longitude: float,
    radius_m: float,
    margin_m: float,
    lightmap_texels_per_m: float,
) -> None:
    """The map and its area. Map setup has no numbered row; its time is in the final line."""
    say(f"{name}  {show(root)}")
    say(f"{latitude}, {longitude}  r {radius_m:g} m  margin {margin_m:g} m  lightmap {lightmap_texels_per_m:g} t/m")
    say()


def print_total(log: RunLog, *, failed: bool) -> None:
    """``✓ Python 9.6 s  (map setup 56.4 s)  0 warnings``. Nothing when no step ran."""
    if not log.times:
        return
    setup = [seconds for name, seconds in log.times if name in SETUP_STEPS]
    python = sum(seconds for name, seconds in log.times if name not in SETUP_STEPS)
    mark = log.palette.error("✗") if failed else log.palette.ok("✓")
    parts = [f"{mark} Python {format_duration(python)}"]
    if setup:
        parts.append(f"(map setup {format_duration(sum(setup))})")
    warnings = plural(log.warnings, "warning")
    parts.append(log.palette.warning(warnings) if log.warnings else warnings)
    say()
    say("  ".join(parts))


def run_steps(
    steps: Sequence[tuple[str, Sequence[str]]],
    *,
    execute: Execute,
    log: RunLog | None = None,
    summarize_stage: Summarize = summarize,
) -> int:
    """Run ``steps`` in order. On the first failure, that report is the next console text.

    A finished stage prints one line from the report it wrote. ``map`` and ``osm``
    are map setup: they print nothing unless they fail or ``log.verbose`` is set.
    """
    state = log if log is not None else RunLog()
    total = sum(1 for name, _argv in steps if name not in SETUP_STEPS)
    index = 0
    for name, argv in steps:
        setup = name in SETUP_STEPS
        if not setup:
            index += 1
        started = time.perf_counter()
        captured_out, captured_err = io.StringIO(), io.StringIO()
        with redirect_stdout(captured_out), redirect_stderr(captured_err):
            code = execute(list(argv))
        elapsed = time.perf_counter() - started
        state.times.append((name, elapsed))
        duration = format_duration(elapsed)
        if code != 0:
            step = "" if setup else f" Step {index}/{total}."
            where = "" if setup else f" (step {index}/{total})"
            print(
                f"{state.palette.error('FAILED')} {name} (exit {code}, {duration}).{step} The following steps were not started.\n"
                f"Next: run again from {name}{where}. The steps before it do not need to run again.",
                flush=True,
            )
            _emit_captured(captured_err.getvalue(), captured_out.getvalue())
            return code
        if not setup:
            report = summarize_stage(name, argv)
            state.warnings += report.warnings
            say(stage_line(index, total, name, duration, report.summary))
            for line in render(report, state.palette):
                say(line)
        if state.verbose:
            _emit_verbose(captured_out.getvalue(), captured_err.getvalue())
    return 0


def _emit_verbose(out: str, err: str) -> None:
    for text in (out, err):
        if text.strip():
            say(text.rstrip())


def _stage_summary(text: str) -> str:
    """One result per Python stage. Paths, file lists and the layout JSON stay out of ``run``."""
    kept: list[str] = []
    skips: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith(("wrote ", "next:", "phase ", "{", "source:", "written:", "texture plan ")):
            continue
        if " sha256 " in stripped:
            continue
        if line.startswith(" ") and not stripped.startswith(("WARNING", "ERROR", "adapter ")):
            if stripped.startswith("skip "):
                skips.append(stripped.removeprefix("skip "))
            continue
        if stripped.startswith("WARNING ") and "acceptance test" in stripped:
            continue
        if stripped.startswith("package ") and " written to " in stripped:
            stripped = stripped.split(" written to ", 1)[0]
        if stripped.startswith("osm already at "):
            stripped = "osm already present"
        kept.append(stripped)
    if skips:
        shown = ", ".join(item.split(":", 1)[0] for item in skips[:6])
        more = f" (+{len(skips) - 6})" if len(skips) > 6 else ""
        kept.append(f"skipped {len(skips)}: {shown}{more}")
    return "\n".join(f"  {line}" for line in kept)


def _unity_summary(text: str, *, failed: bool, content_md5: str | None = None) -> str:
    """The Unity and Quest lines that say what happened. The full editor log stays on disk.

    ``content_md5`` is read from ``build/content3.md5``, not from the log.
    """
    kept: list[str] = []
    dropped_routes = 0
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith(("total ", "-rw", "drw")):
            continue
        if "already applied" in stripped or stripped.startswith("[export] copied "):
            continue
        if stripped.startswith("[export] step ") and "->" in stripped:
            if failed:
                kept.append(stripped.removeprefix("[export] "))
            continue
        if stripped.startswith("[export] step ") and " OK in " in stripped:
            kept.append(stripped.removeprefix("[export] step ").replace("'", ""))
            continue
        if stripped.startswith("[export] step ") and stripped.endswith(" OK"):
            continue
        if stripped in {"[OnwardAdapter] OK"} or "files verified" in stripped or stripped.startswith("[OnwardAdapter] Combined "):
            continue
        if stripped.startswith("[OnwardAdapter] Scene saved") or stripped.startswith("[OnwardAdapter] Launcher MapName="):
            continue
        if " dropped:" in stripped and "Route_" in stripped and " WP-" not in stripped:
            dropped_routes += 1
            continue
        if " dropped:" in stripped:
            continue
        if stripped.startswith(("[OnwardAdapter] Navigation ", "[OnwardAdapter] Hunt ", "[OnwardAdapter] Timings: ", "[OnwardAdapter] Lightmapper: ")):
            kept.append(stripped.removeprefix("[OnwardAdapter] "))
            continue
        if "Launcher output OK in " in stripped:
            kept.append(_launcher_line(stripped))
            continue
        if stripped.startswith("info3 "):
            kept.append(stripped)
            continue
        if stripped.startswith("[export] content3 md5 "):
            continue
        if stripped.startswith(("[export] pushed ", "[export] lightmapper: ")):
            kept.append(stripped.removeprefix("[export] "))
            continue
        if stripped.startswith("[export] device backup:"):
            kept.append("device backup saved")
            continue
        if failed and any(token in stripped for token in ("FAILED", "Exception", "error CS", "Crash", "mismatch")):
            kept.append(stripped.removeprefix("[export] "))
    if content_md5:
        kept.append(f"content3 md5 {content_md5[:12]}")
    if dropped_routes:
        kept.append(f"routes dropped: {dropped_routes}")
    return "\n".join(f"  {line}" for line in kept)


def _launcher_line(line: str) -> str:
    marker = "Launcher output OK in "
    start = line.find(marker)
    rest = line[start + len(marker):]
    seconds, _, tail = rest.partition(":")
    size = ""
    if "(" in tail and " bytes)" in tail:
        raw = tail.rsplit("(", 1)[-1].split(" ", 1)[0]
        if raw.isdigit():
            size = f", {int(raw) / (1024 * 1024):.0f} MB"
    return f"launcher OK in {seconds.strip()}{size}"


def _emit_captured(err: str, out: str) -> None:
    if err.strip():
        print(err.rstrip(), flush=True)
    summary = _stage_summary(out)
    if summary:
        print(summary, flush=True)


def output_ready(name: str, root: Path) -> bool:
    """True when this stage's file exists and is at least as new as the file it reads.

    A repeated earlier stage makes every later file stale, so the next run
    continues there instead of trusting a package or scene left from before.
    """
    if name == "map":
        return (root / "map.json").is_file()
    if name == "osm":
        path = osm_target(root)
        return path.is_file() and path.stat().st_size > 0
    if name == "export":
        return _export_is_fresh(root)
    marker = _canon(root, name)
    if marker is None:
        return False
    return _fresh(marker, _input_markers(root, name))


# The file in a stage's directory whose age decides whether the stage is fresh. Scene stages use scene_visual.glb.
_MARKER_FILES = {"geo": "geo_scene.json", "heights": "heights.json", "fit": "fit.json", "layout": "layout.json"}


def _canon(root: Path, name: str) -> Path | None:
    if name in STAGE_DIRS:
        return root / "data" / "processed" / STAGE_DIRS[name] / _MARKER_FILES.get(name, "scene_visual.glb")
    return next((stage.marker for stage in _source_stages(root) if stage.name == name), None)


def _input_markers(root: Path, name: str) -> list[Path]:
    sources = _source_stages(root)
    for stage in sources:
        if stage.name == name:
            return list(stage.inputs)
    previous_of = {
        "geo": "osm",
        "heights": sources[-1].name if sources else "geo",
        "generate": "heights",
        "detail": "generate",
        "dress": "detail",
        "materials": "dress",
        "signs": "panoramax" if panoramax_enabled(root) else "materials",
        "panoramax": "materials",
        "overgrow": "signs",
        "fit": "overgrow",
        "bounds": "fit",
        "layout": "bounds",
    }
    previous = previous_of.get(name)
    if previous == "osm":
        return [osm_target(root)]
    if previous is None:
        return []
    marker = _canon(root, previous)
    return [marker] if marker is not None else []


def _fresh(marker: Path, inputs: Sequence[Path]) -> bool:
    if not marker.is_file() or marker.stat().st_size == 0:
        return False
    stamps: list[float] = []
    for path in inputs:
        if not path.is_file() or path.stat().st_size == 0:
            return False
        stamps.append(path.stat().st_mtime)
    if not stamps:
        return True
    return marker.stat().st_mtime >= max(stamps)


def _export_is_fresh(root: Path) -> bool:
    layout = _canon(root, "layout")
    playable = _canon(root, "bounds")
    if layout is None or playable is None or not _fresh(layout, [playable]):
        return False
    export_root = root / "data" / "processed" / "export"
    if not export_root.is_dir():
        return False
    need = max(layout.stat().st_mtime, playable.stat().st_mtime)
    return any(
        manifest.is_file() and manifest.stat().st_mtime >= need
        for manifest in export_root.glob("pkg_*/onward_export.json")
    )


def _occupied(name: str, argv: Sequence[str], root: Path) -> bool:
    if name == "osm" or any(stage.download for stage in _source_stages(root) if stage.name == name):
        target = osm_target(root) if name == "osm" else _flag(argv, "--out")
        return target.exists() or target.with_name(target.name + ".source.json").exists()
    return _flag(argv, "--out").exists()


def next_package(root: Path) -> Path:
    """First ``export/pkg_NN`` directory that is not already on disk."""
    base = root / "data" / "processed" / "export"
    for number in range(1, 100):
        candidate = base / f"pkg_{number:02d}"
        if not candidate.exists():
            return candidate
    raise PipelineError(f"{base} has no free package directory")


def package_for_quest(root: Path, ran: Sequence[tuple[str, Sequence[str]]]) -> Path:
    """The package this run exported, or the newest one already on disk."""
    for name, argv in reversed(list(ran)):
        if name == "export":
            return _flag(argv, "--out")
    export_root = root / "data" / "processed" / "export"
    manifests = list(export_root.glob("pkg_*/onward_export.json")) if export_root.is_dir() else []
    if manifests:
        return max(manifests, key=lambda path: path.stat().st_mtime).parent
    return export_root / "pkg_01"


def resume_steps(directory: str | Path, *, preview: bool = False) -> list[tuple[str, list[str]]]:
    """``osm download`` plus every stage after ``map create``."""
    root = Path(directory)
    return [("osm", ["osm", "download", "--map", str(root)]), *plan_steps(root, preview=preview)]


def choose_start(
    names: Sequence[str],
    done: Sequence[str],
    pending: Sequence[str],
    *,
    start: str | None,
    interactive: bool,
    ask: Ask | None,
) -> str:
    """Pick the stage to run. Several finished stages ask; otherwise the next pending one."""
    if start:
        if start not in names:
            raise PipelineError(f"unknown step {start!r}. Choices: {', '.join(names)}")
        return start
    if not pending:
        return ""
    if len(done) < 2 or not interactive:
        return pending[0]
    print("Pasos ya hechos:", flush=True)
    for index, name in enumerate(done, start=1):
        print(f"  {index} {name}", flush=True)
    print(f"Siguiente pendiente: {pending[0]}", flush=True)
    print("Pendientes: " + ", ".join(pending), flush=True)
    prompt = f"Desde dónde continuar [{pending[0]}]: "
    answer = (ask() if ask is not None else input(prompt)).strip()
    if not answer:
        return pending[0]
    if answer.isdigit():
        chosen = int(answer) - 1
        if 0 <= chosen < len(names):
            return names[chosen]
    if answer in names:
        return answer
    raise PipelineError(f"unknown step {answer!r}. Choices: {', '.join(names)}")


def prepare_from(steps: Sequence[tuple[str, list[str]]], start: str, root: Path) -> list[tuple[str, list[str]]]:
    """Keep ``start`` and the stages after it.

    A directory left by a failed or finished stage is written again with
    ``--overwrite``. An export package is never overwritten: the next free
    ``pkg_NN`` is used instead.
    """
    names = [name for name, _argv in steps]
    if start == "map":
        raise PipelineError("map.json already exists. Continue from osm, or from a later step.")
    index = names.index(start)
    prepared: list[tuple[str, list[str]]] = []
    for name, argv in steps[index:]:
        argv = list(argv)
        if name == "export":
            out = _flag(argv, "--out")
            if out.exists():
                argv = _set_flag(argv, "--out", show(next_package(root)))
        elif _occupied(name, argv, root) and "--overwrite" not in argv:
            argv.append("--overwrite")
        prepared.append((name, argv))
    return prepared


def unity_project() -> Path:
    """The stage-0 project ``run_export.sh`` opens. ``PROJECT`` overrides the default."""
    return Path(os.environ.get("PROJECT", Path.home() / "Unity" / "OnwardPhase0Project"))


def unity_editor_open(project: Path | None = None) -> bool:
    """True when an Editor already has this project open.

    A running Editor holds an exclusive lock on ``Temp/UnityLockfile``. If that
    file is absent, ``Library/EditorInstance.json`` is used when its process is
    still alive. A leftover file from a crash does not count.
    """
    root = project if project is not None else unity_project()
    lock = root / "Temp" / "UnityLockfile"
    if lock.is_file():
        return _unity_lock_held(lock)
    return _editor_instance_alive(root)


def confirm_unity_closed(
    *,
    interactive: bool,
    ask: Ask | None,
    unity_open: UnityOpen,
) -> int:
    """Return 0 when the Editor is closed. 1 when the run must not start."""
    if not unity_open():
        return 0
    project = unity_project()
    if not interactive:
        print(
            f"FAILED unity. Unity tiene abierto {project}. "
            "Ciérralo y vuelve a ejecutar el comando en un terminal.",
            flush=True,
        )
        return 1
    print(
        f"Unity tiene abierto {project}.\n"
        "Ciérralo: el pipeline abre ese mismo proyecto y Unity no admite dos instancias.",
        flush=True,
    )
    while True:
        print("Cuando lo hayas cerrado, confirma para iniciar el pipeline [s]:", flush=True)
        answer = (ask() if ask is not None else input()).strip().lower()
        if answer not in _YES:
            print("run: cancelled. El pipeline no ha empezado.", flush=True)
            return 1
        if not unity_open():
            print("run: Unity está cerrado. El pipeline empieza.", flush=True)
            return 0
        print(f"Sigue abierto {project}. Ciérralo y confirma otra vez.", flush=True)


def _editor_instance_alive(root: Path) -> bool:
    path = root / "Library" / "EditorInstance.json"
    try:
        pid = int(json.loads(path.read_text(encoding="utf-8"))["process_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return _pid_alive(pid)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _unity_lock_held(path: Path) -> bool:
    """True when another process holds the exclusive lock Unity takes on open."""
    try:
        import fcntl
    except ImportError:
        return False
    try:
        handle = path.open("rb")
    except OSError:
        return True
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
    finally:
        handle.close()


def run_map(
    *,
    map_id: str,
    name: str,
    latitude: float,
    longitude: float,
    radius_m: float,
    margin_m: float,
    out: str,
    seed: int | None = None,
    description: str | None = None,
    brief: str | None = None,
    ambient_ai: bool = False,
    panoramax: bool = False,
    time_of_day: str | None = None,
    storm: bool | None = None,
    sources: Mapping[str, bool] | None = None,
    lightmap_texels_per_m: float = 10.0,
    core_radius_m: float | None = None,
    core_target_triangles: int | None = None,
    overwrite: bool = False,
    start: str | None = None,
    execute: Execute | None = None,
    ask: Ask | None = None,
    interactive: bool | None = None,
    push: Push | None = None,
    build: Build | None = None,
    unity_open: UnityOpen | None = None,
    host: HostProfile | None = None,
    verbose: bool = False,
    no_unity: bool = False,
    preview: bool = False,
    palette: Palette | None = None,
) -> int:
    """Create the map folder, then run every Python stage through ``export package``.

    A second run skips stages that already wrote their output. With two or more
    of those stages present, a terminal asks where to continue. ``--from`` skips
    that question. When the Python stages are done, Unity builds the Quest
    files (``tool scene launcher``) and the terminal then asks whether to upload.

    If the Editor already has the stage-0 project open, nothing starts until
    the user closes it and confirms. Any other answer cancels the run.

    ``no_unity`` stops after ``export`` and prints the command for the Unity
    steps, so the Python stages can run where Unity is not installed. It does
    not check the Editor either. ``verbose`` adds each stage's own output.
    ``preview`` keeps the diagnostic PNGs of ``generate`` and ``detail``.
    """
    runner = execute if execute is not None else _execute_cli
    root = Path(out)
    prompting = sys.stdin.isatty() if interactive is None else interactive
    if not no_unity:
        gate = confirm_unity_closed(
            interactive=prompting,
            ask=ask,
            unity_open=unity_open if unity_open is not None else unity_editor_open,
        )
        if gate != 0:
            return gate
    log = RunLog(palette=palette if palette is not None else Palette(use_color(sys.stdout)), verbose=verbose)

    def header(directory: Path) -> None:
        print_header(
            name, directory,
            latitude=latitude, longitude=longitude, radius_m=radius_m, margin_m=margin_m,
            lightmap_texels_per_m=lightmap_texels_per_m,
        )

    ran: list[tuple[str, list[str]]] = []
    if not (root / "map.json").is_file():
        try:
            root = map_directory(name)
        except MapError as exc:
            print(f"FAILED map (exit 1). The following steps were not started.\n{exc}", flush=True)
            return 1
        out = str(root)
        map_id = root.name
        if start not in (None, "map"):
            print(
                f"FAILED plan (exit 1). The following steps were not started.\n"
                f"{out} has no map.json, so the run has to start at map.",
                flush=True,
            )
            return 1
        header(root)
        code = run_steps(
            [(
                "map",
                map_create_argv(
                    name=name,
                    latitude=latitude,
                    longitude=longitude,
                    radius_m=radius_m,
                    margin_m=margin_m,
                    seed=seed,
                    description=description,
                    brief=brief,
                    ambient_ai=ambient_ai,
                    panoramax=panoramax,
                    time_of_day=time_of_day,
                    storm=bool(storm),
                    lightmap_texels_per_m=lightmap_texels_per_m,
                    core_radius_m=core_radius_m,
                    core_target_triangles=core_target_triangles,
                    overwrite=overwrite,
                ),
            )],
            execute=runner,
            log=log,
        )
        if code != 0:
            print_total(log, failed=True)
            return code
        if sources:
            store_sources(root, sources)
        try:
            say(describe(choices(load_map(root))))
            steps = plan_steps(out, preview=preview)
        except PipelineError as exc:
            print(f"FAILED plan (exit 1). The following steps were not started.\n{exc}", flush=True)
            return 1
        code = run_steps(steps, execute=runner, log=log)
        ran = steps
    else:
        header(root)
        try:
            _same_request(root, map_id, latitude, longitude, radius_m, margin_m)
            if core_target_triangles is not None:
                _same_core_target(root, core_target_triangles)
            if brief is not None or ambient_ai:
                store_brief(root, brief, ambient_ai=True if ambient_ai else None)
            if panoramax:
                store_panoramax(root, True)
            if sources:
                store_sources(root, sources)
            # Off first and on last: --time-of-day day --no-storm and --time-of-day night --storm are both valid.
            if storm is False and store_storm(root, False):
                say("no storm now: run again with --from layout, then bake a new package")
            if time_of_day and store_time_of_day(root, time_of_day):
                say(f"time of day is now {time_of_day}: run again with --from layout, then bake a new package")
            if storm and store_storm(root, True):
                say("storm is on: run again with --from layout, then bake a new package")
            say(describe(choices(load_map(root))))
            steps = resume_steps(root, preview=preview)
            done = [step for step, _argv in steps if output_ready(step, root)]
            done.insert(0, "map")
            pending = [step for step, _argv in steps if step not in done]
            chosen = choose_start(
                ["map", *[step for step, _argv in steps]],
                done,
                pending,
                start=start,
                interactive=prompting,
                ask=ask,
            )
        except (MapError, PipelineError) as exc:
            print(f"FAILED plan (exit 1). The following steps were not started.\n{exc}", flush=True)
            return 1
        if not chosen:
            print("run: every Python stage is already present.", flush=True)
            code = 0
        else:
            try:
                steps = prepare_from(steps, chosen, root)
            except PipelineError as exc:
                print(f"FAILED plan (exit 1). The following steps were not started.\n{exc}", flush=True)
                return 1
            say(f"continue from {chosen}")
            code = run_steps(steps, execute=runner, log=log)
            ran = steps
    print_total(log, failed=code != 0)
    if code != 0:
        return code
    package = package_for_quest(root, ran)
    if no_unity:
        return print_unity_command(package)
    return finish_device(
        package,
        interactive=prompting,
        ask=ask,
        push=push if push is not None else lambda package: _push_package(package, host=host),
        build=build if build is not None else lambda package, steps: _build_package(package, steps, host=host),
    )


def print_unity_command(package: Path) -> int:
    """What ``--no-unity`` leaves for the host: the Unity steps for this package."""
    if not (package / "onward_export.json").is_file():
        say("→ Unity: skipped. The export package is not on disk.")
        return 0
    steps = unity_steps(package)
    if not steps:
        say(f"→ Unity: the Quest build is already in {show(package)}")
        return 0
    say(f"→ Unity: {', '.join(steps)}. On the host, run:")
    say(f"  task unity -- {show(package)}")
    return 0


def unity_steps(package: Path) -> list[str]:
    """Unity substeps still missing for this package. Empty when the Quest pair exists."""
    if launcher_ready(package):
        return []
    steps = ["tool"]
    if not (package / "build" / "scene_report.json").is_file():
        steps.append("scene")
    steps.append("launcher")
    return steps


def launcher_ready(package: Path) -> bool:
    launcher = package / "build" / "launcher"
    return (launcher / "unity-export.local.content3").is_file() and (
        launcher / "unity-export.local.info3"
    ).is_file()


def finish_device(
    package: Path,
    *,
    interactive: bool,
    ask: Ask | None,
    push: Push,
    build: Build,
) -> int:
    """Build the Quest files, then ask before copying them to the headset."""
    if not (package / "onward_export.json").is_file():
        say("→ Unity: skipped. The export package is not on disk.")
        return 0
    steps = unity_steps(package)
    if steps:
        say(f"→ Unity: {', '.join(steps)}")
        started = time.perf_counter()
        code = build(package, steps)
        elapsed = time.perf_counter() - started
        if code != 0:
            print(f"FAILED unity (exit {code}). Quest upload was not asked.", flush=True)
            return code
        if not launcher_ready(package):
            print(
                "FAILED unity (exit 1). tool scene launcher did not write the Quest files.",
                flush=True,
            )
            return 1
        print(f"OK unity  {format_duration(elapsed)}", flush=True)
    else:
        say("→ Unity: already has the Quest build.")
    return offer_quest_upload(package, interactive=interactive, ask=ask, push=push)


def offer_quest_upload(
    package: Path,
    *,
    interactive: bool,
    ask: Ask | None,
    push: Push,
) -> int:
    """Ask before replacing the headset's local map. Empty or any other answer skips it."""
    if not interactive:
        print("run: Quest upload skipped. Run this in a terminal to answer the question.", flush=True)
        return 0
    print(
        "Subir al Quest sustituye el mapa local del visor (unity-export.local). "
        "Antes se guarda una copia del que haya.",
        flush=True,
    )
    print("¿Subir al Quest? [s/N]", flush=True)
    answer = (ask() if ask is not None else input()).strip().lower()
    if answer not in _YES:
        print("run: Quest upload skipped.", flush=True)
        return 0
    content = package / "build" / "launcher" / "unity-export.local.content3"
    info = package / "build" / "launcher" / "unity-export.local.info3"
    if not content.is_file() or not info.is_file():
        script = _quest_script()
        print(
            "FAILED quest (exit 1). The package has no Quest build yet.\n"
            f"Build it first: {script} {package} tool scene launcher",
            flush=True,
        )
        return 1
    print("run: quest", flush=True)
    started = time.perf_counter()
    code = push(package)
    elapsed = time.perf_counter() - started
    duration = format_duration(elapsed)
    if code != 0:
        print(f"FAILED quest (exit {code}, {duration}). The headset map was not replaced.", flush=True)
        return code
    print(f"OK quest  {duration}", flush=True)
    return 0


def _quest_script() -> Path:
    return Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "run_export.sh"


def _run_export(args: Sequence[str], env: dict[str, str]) -> tuple[int, str]:
    completed = subprocess.run(list(args), env=env, check=False, capture_output=True, text=True)
    text = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    return int(completed.returncode), text


def _script_env(host: HostProfile | None) -> dict[str, str]:
    """The current environment plus the host paths ``tools/unity/lib.sh`` reads."""
    profile = host if host is not None else resolve_host()
    return os.environ | profile.script_env()


def _build_package(package: Path, steps: Sequence[str], host: HostProfile | None = None) -> int:
    """Run ``tool``, ``scene`` and ``launcher`` for the substeps that are still missing."""
    try:
        env = _script_env(host)
    except PlatformError as exc:
        print(f"unity: {exc}", flush=True)
        return 2
    code, text = _run_export([str(_quest_script()), str(package), *steps], env=env)
    summary = _unity_summary(text, failed=code != 0, content_md5=_content_md5(package))
    if summary:
        print(summary, flush=True)
    return code


def _content_md5(package: Path) -> str | None:
    """The digest the launcher step wrote. ``None`` when that file is missing or empty."""
    try:
        digest = (package / "build" / "content3.md5").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return digest.split()[0] if digest else None


def _push_package(package: Path, host: HostProfile | None = None) -> int:
    """Run the existing push. ``CONFIRM_PUSH`` is set only after the user said yes."""
    try:
        env = _script_env(host)
    except PlatformError as exc:
        print(f"push: {exc}", flush=True)
        return 2
    env["CONFIRM_PUSH"] = "yes"
    code, text = _run_export([str(_quest_script()), str(package), "push"], env=env)
    summary = _unity_summary(text, failed=code != 0)
    if summary:
        print(summary, flush=True)
    return code


def _same_request(root: Path, map_id: str, latitude: float, longitude: float, radius_m: float, margin_m: float) -> None:
    try:
        document = load_map(root)
    except MapError as exc:
        raise PipelineError(str(exc)) from exc
    mismatches: list[str] = []
    if str(document.get("id")) != map_id:
        mismatches.append(f"id {document.get('id')}")
    center = document.get("center") or {}
    if abs(float(center.get("latitude", 0.0)) - latitude) > 1e-6:
        mismatches.append(f"lat {center.get('latitude')}")
    if abs(float(center.get("longitude", 0.0)) - longitude) > 1e-6:
        mismatches.append(f"lon {center.get('longitude')}")
    if abs(float(document.get("radius_m", 0.0)) - radius_m) > 1e-6:
        mismatches.append(f"radius {document.get('radius_m')}")
    if abs(float(document.get("margin_m", 0.0)) - margin_m) > 1e-6:
        mismatches.append(f"margin {document.get('margin_m')}")
    if mismatches:
        raise PipelineError(
            f"{root} already records {', '.join(mismatches)}. "
            "Use the same centre, radius and margin, or another name."
        )


def _same_core_target(root: Path, target: int) -> None:
    """Refuse ``--core-target-triangles`` on a map that records another one: the budget core is set at map create.

    Called after ``_same_request``, which has already read and checked ``map.json``.
    """
    recorded = core_budget(load_map(root))
    if recorded is None or recorded[1] != target:
        has = f"core target {recorded[1]}" if recorded else "no core target"
        raise PipelineError(
            f"{root} already records {has}. The budget core is set at map create: "
            "drop --core-target-triangles, or use another name."
        )


def _set_flag(argv: Sequence[str], name: str, value: str) -> list[str]:
    updated = list(argv)
    updated[list(argv).index(name) + 1] = value
    return updated


def _flag(argv: Sequence[str], name: str) -> Path:
    try:
        return Path(argv[list(argv).index(name) + 1])
    except (ValueError, IndexError) as exc:
        raise PipelineError(f"{name} is missing from {' '.join(argv)}") from exc


def _execute_cli(argv: Sequence[str]) -> int:
    from onward_generator.cli import main

    return main(list(argv))
