"""Edge cases of the device-measurement parsers (OVR Metrics CSV, GPU trace, logcat).

The parsers read files already pulled from the headset; nothing here talks to a device.
test_perf.py holds the main paths.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from onward_generator.perf.device import drawcall_trace, load_times, summarize_csv


def test_summarize_csv_single_row_and_byte_order_mark(tmp_path: Path) -> None:
    raw = "﻿Time Stamp,FPS\n1000,72\n".encode()
    path = tmp_path / "metrics.csv"
    path.write_bytes(raw)
    s = summarize_csv(path)
    assert s["columns"] == ["Time Stamp", "FPS"]
    assert s["sha256"] == hashlib.sha256(raw).hexdigest()
    assert s["stats"]["FPS"] == {"n": 1, "mean": 72.0, "min": 72.0, "p05": 72.0, "p50": 72.0, "p95": 72.0, "max": 72.0}


def test_summarize_csv_interpolates_percentiles_and_drops_non_finite_values(tmp_path: Path) -> None:
    path = tmp_path / "metrics.csv"
    path.write_text("FPS,GPU\n10,nan\n20,inf\n30,\n40,x\n50,5\n", encoding="utf-8")
    s = summarize_csv(path)
    fps = s["stats"]["FPS"]
    assert (fps["n"], fps["mean"], fps["min"], fps["max"]) == (5, 30.0, 10.0, 50.0)
    assert (fps["p05"], fps["p50"], fps["p95"]) == (12.0, 30.0, 48.0)
    assert s["stats"]["GPU"]["n"] == 1
    assert s["non_numeric_columns"] == []


def test_summarize_csv_until_without_skip_keeps_the_head(tmp_path: Path) -> None:
    path = tmp_path / "metrics.csv"
    path.write_text("Time Stamp,FPS\n5000,60\n6000,70\n7000,80\n", encoding="utf-8")
    s = summarize_csv(path, until_s=2, time_column="Time Stamp")
    assert s["rows"] == 2 and s["stats"]["FPS"]["max"] == 70.0
    with pytest.raises(ValueError, match="needs time_column"):
        summarize_csv(path, until_s=2)


def test_drawcall_trace_counts_a_buffer_that_does_not_start_at_draw_one(tmp_path: Path) -> None:
    path = tmp_path / "drawcalls_0.txt"
    path.write_text(
        "\tFrame 4 : Draw 7 Label 0x0\n"
        "\t\tVertices Shaded : 12.5\n"
        "\tFrame 4 : Draw 8 Label 0x0\n"
        "Vertices Shaded without a colon is ignored\n",
        encoding="utf-8",
    )
    t = drawcall_trace(path)
    assert t["command_buffers"] == [{"draws": 2, "vertices_shaded": 12.5}]
    assert t["frame_draws"] == 2 and t["frame_vertices_shaded"] == 12.5


def test_drawcall_trace_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        drawcall_trace(tmp_path / "absent.txt")


def test_load_times_restarted_load_uses_the_last_start(tmp_path: Path) -> None:
    log = tmp_path / "logcat.txt"
    log.write_text(
        "10-19 00:00:00.000  1  1 I Unity   : Loading level: CustomContentLoader\n"
        "Loading level: CustomContentLoader without a timestamp is ignored\n"
        "10-19 00:00:10.000  1  1 I Unity   : Loading level: CustomContentLoader\n"
        "10-19 00:00:12.500  1  1 I Unity   : SendSetIsLoadingMap: False\n"
        "10-19 23:59:59.000  1  1 I Unity   : Loading level: CustomContentLoader\n"
        "10-20 00:00:01.250  1  1 I Unity   : SendSetIsLoadingMap: False\n"
        "10-20 00:00:02.000  1  1 I Unity   : Loading level: CustomContentLoader\n",  # never finishes
        encoding="utf-8",
    )
    assert load_times(log) == [
        {"start": "10-19 00:00:10.000", "end": "10-19 00:00:12.500", "seconds": 2.5},
        {"start": "10-19 23:59:59.000", "end": "10-20 00:00:01.250", "seconds": 2.25},
    ]


def test_load_times_accepts_leap_day_and_empty_logs(tmp_path: Path) -> None:
    log = tmp_path / "logcat.txt"
    log.write_text(
        "02-29 10:00:00.000  1  1 I Unity   : Loading level: CustomContentLoader\n"
        "02-29 10:00:03.000  1  1 I Unity   : SendSetIsLoadingMap: False\n",
        encoding="utf-8",
    )
    assert [x["seconds"] for x in load_times(log)] == [3.0]
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    assert load_times(empty) == []
