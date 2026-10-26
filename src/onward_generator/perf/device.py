"""Device measurements (phase 05): OVR Metrics Tool CSV and Onward logcat.

Column names are taken from the CSV header as recorded; nothing is assumed
about which columns exist. Load time comes from the two Onward log lines
observed while loading a custom map (``docs/09-perf.md``).
"""

from __future__ import annotations

import csv
import hashlib
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

LOAD_START = "Loading level: CustomContentLoader"
LOAD_END = "SendSetIsLoadingMap: False"
_LOGCAT_TIME = re.compile(r"^(\d\d-\d\d) (\d\d:\d\d:\d\d\.\d{3})")


def _percentile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def summarize_csv(path: str | Path, *, skip_first_s: float = 0.0, until_s: float | None = None,
                  time_column: str | None = None) -> dict[str, Any]:
    """Per-column statistics of an OVR Metrics Tool CSV. Non-numeric columns are listed, not summarised.

    With ``time_column``, only rows with ``skip_first_s <= t - t0 < until_s`` are kept,
    t0 being the first row's timestamp.
    """
    p = Path(path).expanduser()
    raw = p.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    if not rows:
        raise ValueError(f"{p}: no data rows")
    columns = list(rows[0].keys())

    def number(v: str | None) -> float | None:
        try:
            x = float(v) if v not in (None, "") else None
        except ValueError:
            return None
        return x if x is not None and math.isfinite(x) else None

    if (skip_first_s > 0 or until_s is not None) and not time_column:
        raise ValueError("a time window needs time_column")
    if until_s is not None and until_s <= skip_first_s:
        raise ValueError(f"until_s ({until_s}) must be greater than skip_first_s ({skip_first_s})")
    if time_column:
        t0 = number(rows[0].get(time_column))
        if t0 is None:
            raise ValueError(f"{p}: time column {time_column!r} is not numeric")
        lo = skip_first_s * 1000.0
        hi = math.inf if until_s is None else until_s * 1000.0
        # OVR Metrics timestamps are in milliseconds.
        rows = [r for r in rows if lo <= (number(r.get(time_column)) or t0) - t0 < hi]
        if not rows:
            raise ValueError(f"{p}: no rows in the window [{skip_first_s}, {until_s}) s")
    stats: dict[str, Any] = {}
    text: list[str] = []
    for col in columns:
        values = [v for v in (number(r.get(col)) for r in rows) if v is not None]
        if not values:
            text.append(col)
            continue
        values.sort()
        stats[col] = {
            "n": len(values),
            "mean": round(sum(values) / len(values), 4),
            "min": values[0],
            "p05": round(_percentile(values, 0.05), 4),
            "p50": round(_percentile(values, 0.50), 4),
            "p95": round(_percentile(values, 0.95), 4),
            "max": values[-1],
        }
    return {"file": str(p), "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(rows), "columns": columns,
            "non_numeric_columns": text, "stats": stats}


_DRAW = re.compile(r"Frame\s+\d+\s*:\s*Draw\s+(\d+)\b")


def drawcall_trace(path: str | Path) -> dict[str, Any]:
    """Draws per command buffer in an ``ovrgpuprofiler -t -x=...`` trace.

    Draw numbering restarts at 1 for each command buffer. The eye-buffer pass is
    the command buffer with the most draws (the others hold one or two draws of
    compositor work); ``frame_draws`` is that maximum.
    """
    p = Path(path).expanduser()
    buffers: list[dict[str, float]] = []
    current: dict[str, float] | None = None
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _DRAW.search(line)
        if m:
            if int(m.group(1)) == 1 or current is None:
                current = {"draws": 0, "vertices_shaded": 0.0}
                buffers.append(current)
            current["draws"] += 1
        elif current is not None and ":" in line and line.strip().startswith("Vertices Shaded"):
            current["vertices_shaded"] += float(line.rsplit(":", 1)[1])
    if not buffers:
        raise ValueError(f"{p}: no draw calls in the trace (was detailed profiling enabled before the app started?)")
    main = max(buffers, key=lambda b: b["draws"])
    return {"file": str(p), "command_buffers": [{"draws": int(b["draws"]), "vertices_shaded": b["vertices_shaded"]} for b in buffers],
            "frame_draws": int(main["draws"]), "frame_vertices_shaded": main["vertices_shaded"]}


def load_times(logcat_path: str | Path) -> list[dict[str, Any]]:
    """Every (start, end) map-load pair in a ``logcat -v threadtime`` dump, in seconds."""
    loads: list[dict[str, Any]] = []
    start: tuple[str, datetime] | None = None
    for line in Path(logcat_path).expanduser().read_text(encoding="utf-8", errors="replace").splitlines():
        m = _LOGCAT_TIME.match(line)
        if not m:
            continue
        # logcat has no year; a leap year accepts 02-29. A load spanning New Year is not handled.
        stamp = datetime.strptime(f"2000-{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S.%f")
        if LOAD_START in line:
            start = (m.group(0), stamp)
        elif LOAD_END in line and start is not None:
            loads.append({"start": start[0], "end": m.group(0), "seconds": round((stamp - start[1]).total_seconds(), 3)})
            start = None
    return loads
