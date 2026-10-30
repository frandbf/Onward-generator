"""Console lines for ``onward-generator run``.

Each stage gets one line: index, name, duration and its key numbers. The numbers
come from the report JSON the stage already wrote under its ``--out``, not from
the text it printed. A breakdown (openings, urban elements, skipped materials)
goes on indented lines under it, and a non-zero warning goes on its own line.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

WIDTH = 100
"""Breakdown lines wrap at about this many columns."""
INDENT = " " * 9
"""Lines under a stage start below its name."""
LABEL = 10
"""Width of the breakdown label column."""
SETUP_STEPS = frozenset({"map", "osm"})
"""Map setup belongs to the header. Its time goes in the final line, not a numbered row."""

Level = Literal["warning", "error"]
_SEVERITY: tuple[tuple[str, Level], ...] = (("critical", "error"), ("warning", "warning"))


class _Stream(Protocol):
    def isatty(self) -> bool: ...


def use_color(stream: _Stream, env: Mapping[str, str] | None = None) -> bool:
    """Colour only on a terminal, and never when ``NO_COLOR`` is set to a non-empty value."""
    environ = os.environ if env is None else env
    if environ.get("NO_COLOR"):
        return False
    return stream.isatty()


@dataclass(frozen=True, slots=True)
class Palette:
    """ANSI colour when ``enabled``; otherwise the text unchanged."""

    enabled: bool = False

    def paint(self, text: str, code: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{code}m{text}\033[0m"

    def ok(self, text: str) -> str:
        return self.paint(text, "32")

    def warning(self, text: str) -> str:
        return self.paint(text, "33")

    def error(self, text: str) -> str:
        return self.paint(text, "31")

    def level(self, level: Level, text: str) -> str:
        return self.error(text) if level == "error" else self.warning(text)


@dataclass(frozen=True, slots=True)
class Alert:
    """A non-zero warning or error count from a stage report."""

    level: Level
    text: str
    count: int


@dataclass(frozen=True, slots=True)
class StageReport:
    """What ``run`` prints for one finished stage."""

    summary: str = ""
    details: tuple[tuple[str, tuple[str, ...]], ...] = ()
    alerts: tuple[Alert, ...] = ()

    @property
    def warnings(self) -> int:
        return sum(alert.count for alert in self.alerts)


@dataclass(slots=True)
class RunLog:
    """Console state for one ``run``: colour, verbosity, step times and warnings."""

    palette: Palette = field(default_factory=Palette)
    verbose: bool = False
    times: list[tuple[str, float]] = field(default_factory=list)
    warnings: int = 0


def group(number: int) -> str:
    """``21194`` → ``21 194``."""
    return f"{number:,}".replace(",", " ")


def count_items(counts: Mapping[str, int], rename: Callable[[str], str] = str) -> tuple[str, ...]:
    """``key count`` items, largest count first, then by key. Zero counts are left out."""
    rows = sorted((-int(value), key) for key, value in counts.items() if int(value) > 0)
    return tuple(rename(f"{key} {group(-negative)}") for negative, key in rows)


def wrap(label: str, items: Sequence[str], width: int = WIDTH) -> list[str]:
    """``label`` and ``items`` joined with commas, wrapped under the first item."""
    lead = f"{INDENT}{label:<{LABEL}} "
    lines: list[str] = []
    current = ""
    for index, item in enumerate(items):
        piece = item if index == len(items) - 1 else f"{item},"
        if current and len(lead) + len(current) + 1 + len(piece) > width:
            lines.append(current)
            current = piece
        else:
            current = f"{current} {piece}" if current else piece
    lines.append(current)
    pad = " " * len(lead)
    return [lead + lines[0], *(pad + line for line in lines[1:])]


def stage_line(index: int, total: int, name: str, duration: str, summary: str) -> str:
    """`` [ 4/13] detail     0.9 s  21 194 tris``."""
    head = f" [{index:>{len(str(total))}}/{total}] {name:<9} {duration:>6}"
    return f"{head}  {summary}" if summary else head


def render(report: StageReport, palette: Palette) -> list[str]:
    """The breakdown and alert lines under a stage line."""
    lines: list[str] = []
    for label, items in report.details:
        if items:
            lines.extend(wrap(label, items))
    for alert in report.alerts:
        lines.append(INDENT + palette.level(alert.level, f"! {alert.text}"))
    return lines


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _read(path: Path) -> Any:
    """A report document. Something other than an object fails in ``summarize`` (TypeError)."""
    return json.loads(path.read_bytes())


def _severity(report: Mapping[str, Any]) -> tuple[Alert, ...]:
    """Non-zero ``critical`` and ``warning`` counts, each with its issue codes."""
    alerts: list[Alert] = []
    for key, level in _SEVERITY:
        count = int(report["severity"][key])
        if not count:
            continue
        codes = Counter(str(issue["code"]) for issue in report["issues"] if issue["severity"] == key)
        listed = ", ".join(count_items(codes))
        tail = f" ({listed})" if listed else ""
        alerts.append(Alert(level, f"{key} {count}{tail}", count))
    return tuple(alerts)


def _geo(out: Path) -> StageReport:
    report = _read(out / "geo_report.json")
    counts = report["counts"]
    parts = [str(report["frame"]["crs"]), f"kept {group(counts['kept'])}", f"dropped {group(counts.get('dropped_outside', 0))}"]
    rejected = int(counts.get("rejected_invalid", 0))
    if rejected:
        parts.append(f"rejected {group(rejected)}")
    alerts: list[Alert] = []
    if counts["errors"]:
        alerts.append(Alert("error", plural(int(counts["errors"]), "error"), int(counts["errors"])))
    if counts["warnings"]:
        alerts.append(Alert("warning", plural(int(counts["warnings"]), "warning"), int(counts["warnings"])))
    return StageReport("  ".join(parts), alerts=tuple(alerts))


def _catastro(out: Path) -> StageReport:
    record = _read(out.with_name(out.name + ".source.json"))
    return StageReport(f"{group(int(record['size_bytes']) // 1024)} KiB  {record['license']}")


def _cadastre(out: Path) -> StageReport:
    report = _read(out / "cadastre_match_report.json")
    counts = report["split"]["counts"]
    parts = [f"parts {group(int(report['counts']['cadastre_parts']))}"]
    parts.append(f"split {group(int(counts['osm_buildings_split']))} → {group(int(counts['sub_buildings']))}")
    levels = counts["levels_range"]
    if levels:
        parts.append(f"storeys {levels[0]}–{levels[1]}")
    quarantined = int(counts.get("status_quarantined", 0))
    if quarantined:
        parts.append(f"kept whole {group(quarantined)} ({group(round(counts['quarantined_m2']))} m²)")
    return StageReport("  ".join(parts))


def _heights(out: Path) -> StageReport:
    report = _read(out / "heights_report.json")
    counts = report["counts"]
    parts = [plural(int(counts["buildings"]), "building")]
    parts.extend(f"{name} {value:g}%" for name, value in report["percent_by_class"].items() if value)
    review = int(counts["needs_manual_review"])
    if review:
        parts.append(f"review {review}")
    return StageReport("  ".join(parts))


def _generate(out: Path) -> StageReport:
    report = _read(out / "generation_report.json")
    counts = report["counts"]
    summary = (
        f"{group(counts['visual_triangles'])} tris  {group(counts['collision_triangles'])} collision  "
        f"{counts['materials']} mat"
    )
    return StageReport(summary, alerts=_severity(report))


def _urban(item: str) -> str:
    """``tree:observed 24`` → ``tree 24 obs``; ``bench:generated 9`` → ``bench 9``."""
    key, _, count = item.rpartition(" ")
    kind, _, origin = key.partition(":")
    return f"{kind} {count} obs" if origin == "observed" else f"{kind} {count}"


def _detail(out: Path) -> StageReport:
    report = _read(out / "detail_report.json")
    counts = report["counts"]
    summary = f"{group(counts['visual_triangles'])} tris  {counts['materials']} mat  {group(counts['visual_nodes'])} nodes"
    details = (
        ("openings", count_items(counts["openings"])),
        ("urban", count_items(counts["urban_elements"], rename=_urban)),
        ("tiers", _tiers(counts.get("tiers") or {})),
    )
    return StageReport(summary, details=details, alerts=_severity(report))


def _tiers(tiers: Mapping[str, Any]) -> tuple[str, ...]:
    """``core 120 bldg 180 000 tris``, then the scenic ring (ADR-017). Empty without a core."""
    return tuple(
        f"{name} {group(tiers[name]['buildings'])} bldg {group(tiers[name]['visual_triangles'])} tris"
        for name in ("core", "scenic") if name in tiers
    )


def _dress(out: Path) -> StageReport:
    from onward_generator.dress.style import TREE_VARIANTS

    dress = _read(out / "detail_report.json")["dress"]
    atlas = "on" if dress["atlas"] else "off"
    return StageReport(f"trees {dress['tree_sides']}-side x{TREE_VARIANTS}  atlas {atlas}")


def _materials(out: Path) -> StageReport:
    report = _read(out / "materials_report.json")
    skipped = report["skipped"]
    summary = f"applied {len(report['applied'])}  skipped {len(skipped)}"
    reasons: dict[str, list[str]] = {}
    for row in skipped:
        reasons.setdefault(str(row["reason"]), []).append(str(row.get("catalog_id") or row.get("id")))
    details = tuple(("skipped", (f"{reason}: {', '.join(names)}",)) for reason, names in sorted(reasons.items()))
    return StageReport(summary, details=details)


def _panoramax(out: Path) -> StageReport:
    report = _read(out / "panoramax_report.json")
    return StageReport(f"{len(report['assignments'])} facades")


def _signs(out: Path) -> StageReport:
    report = _read(out / "signs_report.json")
    shops = int((report.get("shop_signs") or {}).get("count", 0))
    return StageReport(f"{plural(int(report['count']), 'plate')}  {plural(shops, 'shop sign')}")


def _overgrow(out: Path) -> StageReport:
    report = _read(out / "overgrow_report.json")
    growth = {key: int(report[key]) for key in ("moss", "grass", "vines")}
    summary = (
        f"{group(report['added_triangles'])} tris  props {report['props']}  wrecks {report['wrecks']}  "
        f"trees {report['trees']} ({report['trees_observed']} obs)"
    )
    return StageReport(summary, details=(("growth", count_items(growth)),))


def _fit(out: Path) -> StageReport:
    return StageReport(f"checked {_read(out)['checked']}")


def _bounds(out: Path) -> StageReport:
    wall = _read(out / "bounds_report.json")["wall"]
    core = f"  core {wall['core_half_side_m']:g} m" if wall.get("core_half_side_m") else ""
    return StageReport(f"wall {wall['height_m']:g} m  {group(wall['triangles'])} tris{core}")


def _layout(out: Path) -> StageReport:
    layout = _read(out)
    parts: list[str] = []
    if "routes" in layout:
        parts.append(f"routes {len(layout['routes'])}")
    if "ai_m" in layout:
        parts.append(f"hunt {len(layout['ai_m'])}")
    for team in ("attackers", "defenders"):
        points = layout.get(team)
        if isinstance(points, dict) and "points_m" in points:
            parts.append(f"{team} {len(points['points_m'])}")
    return StageReport("  ".join(parts))


def _export(out: Path) -> StageReport:
    manifest = _read(out / "onward_export.json")
    # Every package carries the unverified-adapter warning until the headset checklist passes.
    warnings = [text for text in manifest.get("warnings") or [] if "[export_adapter_unverified]" not in text]
    alerts = tuple(Alert("warning", str(text), 1) for text in warnings)
    tiers = manifest.get("tiers")
    draws = f"  draws {group(tiers['draws'])} at {tiers['cell_m']:g} m" if tiers else ""
    return StageReport(f"{manifest['package_id']}{draws}", alerts=alerts)


_STAGES: dict[str, Callable[[Path], StageReport]] = {
    "geo": _geo,
    "catastro": _catastro,
    "cadastre": _cadastre,
    "heights": _heights,
    "generate": _generate,
    "detail": _detail,
    "dress": _dress,
    "materials": _materials,
    "panoramax": _panoramax,
    "signs": _signs,
    "overgrow": _overgrow,
    "fit": _fit,
    "bounds": _bounds,
    "layout": _layout,
    "export": _export,
}


def summarize(name: str, argv: Sequence[str]) -> StageReport:
    """The report a finished stage wrote, read from its ``--out``. Empty when it cannot be read."""
    reader = _STAGES.get(name)
    if reader is None or "--out" not in argv:
        return StageReport()
    position = list(argv).index("--out") + 1
    if position >= len(argv):
        return StageReport()
    try:
        return reader(Path(argv[position]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return StageReport()
