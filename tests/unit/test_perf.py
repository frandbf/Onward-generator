"""Phase 05 measurement helpers: GLB counts, merge prediction, device CSV, logcat and draw-call traces.

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import box
from onward_generator.perf.__main__ import main as perf_main
from onward_generator.perf.device import drawcall_trace, load_times, summarize_csv
from onward_generator.perf.measure import combine_groups, measure_glb

MATERIALS = {"a": Material((0.5, 0.5, 0.5, 1.0), 0.9), "b": Material((0.8, 0.2, 0.2, 1.0), 0.9)}


def _translate(dx: float, dy: float) -> np.ndarray:
    m = np.eye(4)
    m[0, 3], m[1, 3] = dx, dy
    return m


@pytest.fixture
def small_glb(tmp_path: Path) -> Path:
    """Three nodes around the origin: a two-material box, a one-material box 30 m east, an instance 60 m north."""
    cube = box(0, 2, 0, 2, 0, 3)
    two = (Primitive(cube, "a"), Primitive(cube, "b"))
    nodes = [
        Node("building/1", two, mesh_key="cube2"),
        Node("building/2", (Primitive(box(30, 32, 0, 2, 0, 3), "a"),)),
        Node("building/3", two, matrix=_translate(0, 60), mesh_key="cube2"),
    ]
    path = tmp_path / "scene_visual.glb"
    path.write_bytes(build_glb(nodes, MATERIALS))
    return path


def test_measure_glb_counts_instances_and_primitives(small_glb: Path) -> None:
    m = measure_glb(small_glb)
    assert m["nodes_with_mesh"] == 3
    assert m["unique_meshes"] == 2 and m["instanced_nodes"] == 1
    assert m["primitives_drawn"] == 5
    assert m["triangles"] == 5 * 12 and m["unique_triangles"] == 3 * 12
    assert m["primitives_by_material"] == {"a": 3, "b": 2}
    assert m["textures"] == 0 and m["lights"] == 0 and not m["has_lightmap_uv"]


def test_combine_groups_merges_per_cell_and_material(small_glb: Path) -> None:
    # glTF ground plane is (x, z) = (east, -north): the instance 60 m north lands in another z cell.
    fine = combine_groups(small_glb, 25.0)
    assert fine["cells"] == 3 and fine["draws"] == 5 and fine["primitives_merged"] == 5
    coarse = combine_groups(small_glb, 1000.0)
    assert coarse["cells"] == 1 and coarse["draws"] == 2  # one draw per material
    assert coarse["max_group_triangles"] == 3 * 12


@pytest.mark.parametrize("cell", [0.0, -5.0, float("nan")])
def test_combine_groups_rejects_non_positive_cells(small_glb: Path, cell: float) -> None:
    with pytest.raises(ValueError, match="cell_m"):
        combine_groups(small_glb, cell)


CSV = "Time Stamp,average_frame_rate,app_pss_MB,note\n" + "".join(
    f"{1000 * (i + 1)},{60 + i},{500 + 10 * i},x\n" for i in range(10)
)


def test_summarize_csv_window_and_non_numeric_columns(tmp_path: Path) -> None:
    path = tmp_path / "metrics.csv"
    path.write_text(CSV, encoding="utf-8")
    whole = summarize_csv(path)
    assert whole["rows"] == 10 and whole["non_numeric_columns"] == ["note"]
    assert whole["stats"]["average_frame_rate"]["min"] == 60 and whole["stats"]["average_frame_rate"]["max"] == 69
    window = summarize_csv(path, skip_first_s=2, until_s=5, time_column="Time Stamp")
    assert window["rows"] == 3  # t - t0 = 2, 3, 4 s
    assert window["stats"]["average_frame_rate"]["p50"] == 63


def test_summarize_csv_rejects_bad_windows(tmp_path: Path) -> None:
    path = tmp_path / "metrics.csv"
    path.write_text(CSV, encoding="utf-8")
    with pytest.raises(ValueError, match="time_column"):
        summarize_csv(path, skip_first_s=2)
    with pytest.raises(ValueError, match="greater"):
        summarize_csv(path, skip_first_s=5, until_s=5, time_column="Time Stamp")
    with pytest.raises(ValueError, match="no rows"):
        summarize_csv(path, skip_first_s=100, time_column="Time Stamp")
    with pytest.raises(ValueError, match="not numeric"):
        summarize_csv(path, skip_first_s=1, time_column="note")
    empty = tmp_path / "empty.csv"
    empty.write_text("Time Stamp,average_frame_rate\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no data rows"):
        summarize_csv(empty)


def test_load_times_pairs_markers(tmp_path: Path) -> None:
    log = tmp_path / "logcat.txt"
    log.write_text(
        "--------- beginning of main\n"
        "10-19 00:19:44.083  7653  7693 I Unity   : Loading level: CustomContentLoader\n"
        "10-19 00:19:45.000  7653  7693 I Unity   : something else\n"
        "10-19 00:19:46.800  7653  7693 I Unity   : SendSetIsLoadingMap: False\n"
        "10-19 00:21:00.000  7653  7693 I Unity   : SendSetIsLoadingMap: False\n",  # no start: ignored
        encoding="utf-8",
    )
    loads = load_times(log)
    assert len(loads) == 1 and loads[0]["seconds"] == pytest.approx(2.717)


TRACE = """Tracing for 0.1 seconds...
Process com.downpourinteractive.onward has 3 commandbuffers
\tFrame 1   : Draw 1   Label 0xffffffff
\t\tClocks                                     :        8586.000
\t\tVertices Shaded                            :           0.000
\tFrame 1   : Draw 2   Label 0xffffffff
\t\tVertices Shaded                            :           0.000
\tFrame 1   : Draw 1   Label 0xffffffff
\t\tVertices Shaded                            :         100.000
\tFrame 1   : Draw 2   Label 0xffffffff
\t\tVertices Shaded                            :         250.000
\tFrame 1   : Draw 3   Label 0xffffffff
\t\tVertices Shaded                            :          50.000
\tFrame 1   : Draw 1   Label 0xffffffff
\t\tVertices Shaded                            :           0.000
"""


def test_drawcall_trace_takes_the_busiest_command_buffer(tmp_path: Path) -> None:
    path = tmp_path / "drawcalls_0.txt"
    path.write_text(TRACE, encoding="utf-8")
    t = drawcall_trace(path)
    assert [b["draws"] for b in t["command_buffers"]] == [2, 3, 1]
    assert t["frame_draws"] == 3 and t["frame_vertices_shaded"] == 400.0


def test_drawcall_trace_without_draws_is_an_error(tmp_path: Path) -> None:
    path = tmp_path / "drawcalls_0.txt"
    path.write_text("No metrics to track!\n2026-10-19T03:02:56Z\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no draw calls"):
        drawcall_trace(path)


def test_cli_drawcalls_validates_labels(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "drawcalls_0.txt"
    path.write_text(TRACE, encoding="utf-8")
    out = tmp_path / "out" / "drawcalls.json"
    assert perf_main(["drawcalls", f"v00_A={path}", "--out", str(out)]) == 0
    assert "v00_A: 3 draws" in capsys.readouterr().out
    assert perf_main(["drawcalls", str(path), "--out", str(tmp_path / "x.json")]) == 1
    assert "LABEL=PATH" in capsys.readouterr().err
    assert perf_main(["drawcalls", f"a={path}", f"a={path}", "--out", str(tmp_path / "y.json")]) == 1
    assert perf_main(["drawcalls", f"a={path}", "--out", str(out)]) == 1  # no silent overwrite
