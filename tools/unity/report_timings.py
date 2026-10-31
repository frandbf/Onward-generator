"""Print the Unity phase timings of one or more export packages as a Markdown table.

    python tools/unity/report_timings.py PATH...

PATH is a ``scene_report.json``, the ``build/`` folder holding it, or the export
package folder. The scene step writes ``seconds`` in ``scene_report.json``; the
launcher step writes its own ``seconds`` in ``launcher_report.json`` beside it,
read when present. One column per package, one row per phase, in the order the
adapter wrote them. A report from before the per-phase timings has only
``scene`` and ``bake``; one from before the bake profiles has no ``bake profile``
row (it baked as ``final``). Editor timings only, not device measurements.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

MISSING = "-"


def report_path(path: Path) -> Path:
    """The scene report a command-line argument names."""
    for candidate in (path, path / "scene_report.json", path / "build" / "scene_report.json"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no scene_report.json at {path}")


def _read(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _seconds(report: dict[str, Any], prefix: str) -> dict[str, float]:
    seconds = report.get("seconds")
    if not isinstance(seconds, dict):
        return {}
    return {f"{prefix}.{name}": float(value) for name, value in seconds.items() if isinstance(value, int | float)}


def _bake_rows(report: dict[str, Any]) -> dict[str, str]:
    bake = report.get("lightmap_bake")
    if not isinstance(bake, dict) or not bake.get("lightmapper"):
        return {}
    used = str(bake["lightmapper"])
    if bake.get("fallback"):
        used += f" (fallback from {bake.get('lightmapper_requested') or '?'})"
    sizes = [str(size) for size in bake.get("sizes") or []]
    counts: dict[str, int] = {}
    for size in sizes:
        counts[size] = counts.get(size, 0) + 1
    pages = ", ".join(f"{size} x{count}" if count > 1 else size for size, count in counts.items())
    rows = {"lightmapper": used, "lightmaps": f"{len(sizes)} ({pages or 'none'})"}
    if bake.get("profile"):
        rows["bake profile"] = str(bake["profile"])
    return rows


def column(scene_report: Path) -> tuple[str, dict[str, str]]:
    """The column title and its cells (row name to text) for one scene report."""
    report = _read(scene_report)
    values = _seconds(report, "scene")
    launcher = scene_report.with_name("launcher_report.json")
    if launcher.is_file():
        values |= _seconds(_read(launcher), "launcher")
    cells = {name: f"{value:.1f}" for name, value in values.items()}
    cells |= _bake_rows(report)
    return str(report.get("package_id") or scene_report.parent.parent.name), cells


def table(columns: list[tuple[str, dict[str, str]]]) -> str:
    """A Markdown table: rows in first-seen order, a dash where a package has no value."""
    rows: list[str] = []
    for _, cells in columns:
        rows.extend(name for name in cells if name not in rows)
    lines = [
        "| phase | " + " | ".join(title for title, _ in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    lines.extend(f"| {row} | " + " | ".join(cells.get(row, MISSING) for _, cells in columns) + " |" for row in rows)
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        columns = [column(report_path(Path(arg))) for arg in argv]
    except (OSError, ValueError) as error:
        print(f"report_timings: {error}", file=sys.stderr)
        return 1
    print(table(columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
