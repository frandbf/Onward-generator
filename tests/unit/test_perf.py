"""Phase 05 measurement helpers: GLB counts, merge prediction, device CSV, logcat and draw-call traces.

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from onward_generator.dress.atlas import encode_png_rgba
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import box
from onward_generator.perf.__main__ import main as perf_main
from onward_generator.perf.device import drawcall_trace, load_times, summarize_csv
from onward_generator.perf.measure import combine_groups, furniture_building, measure_glb, png_size, texture_bytes, texture_memory

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
    assert m["texture_memory"]["rgba32_mips_bytes"] == 0


def test_combine_groups_merges_per_cell_and_material(small_glb: Path) -> None:
    # glTF ground plane is (x, z) = (east, -north): the instance 60 m north lands in another z cell.
    fine = combine_groups(small_glb, 25.0)
    assert fine["cells"] == 3 and fine["draws"] == 5 and fine["primitives_merged"] == 5
    coarse = combine_groups(small_glb, 1000.0)
    assert coarse["cells"] == 1 and coarse["draws"] == 2  # one draw per material
    assert coarse["max_group_triangles"] == 3 * 12


def test_furniture_is_combined_per_building_and_storey_not_per_cell(tmp_path: Path) -> None:
    """As the adapter: furnish/<building>/<n> nodes group by (building, storey, material), lit by their rooms' probes."""
    piece = box(0, 1, 0, 0.5, 0, 1.5)
    nodes = [
        Node("furnish/osm-way-1/0", (Primitive(piece, "a"),)),
        Node("furnish/osm-way-1/1", (Primitive(box(5, 6, 0, 0.5, 0, 1.5), "a"),)),          # same building, storey, material
        Node("furnish/osm-way-1/2", (Primitive(box(5, 6, 0, 0.5, 2.8, 4.3), "a"),)),        # the floor above
        Node("furnish/osm-way-2/0", (Primitive(box(8, 9, 0, 0.5, 0, 1.5), "a"),)),          # another building, same cell
        Node("furnish/collider/osm-way-2/0", (Primitive(box(8, 9, 0, 0.5, 0, 1.5), "a"),)),  # not a furniture node: the cell
        Node("prop/barrel", (Primitive(box(2, 3, 0, 1, 0, 1), "a"),)),                       # a street prop in the same cell
    ]
    path = tmp_path / "furniture.glb"
    path.write_bytes(build_glb(nodes, MATERIALS))
    groups = combine_groups(path, 100.0)
    assert groups["draws"] == 4 and groups["primitives_merged"] == 6
    assert furniture_building("furnish/osm-way-7/3") == "osm-way-7"
    assert furniture_building("furnish/collider/osm-way-7/3") is None and furniture_building("prop/x") is None


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


@pytest.mark.parametrize(("width", "height", "fmt", "expected"), [
    # The ADR-020 facade atlas: 42.7 MiB as RGBA32, 4.8 MiB as ASTC 6x6.
    (2048, 4096, "rgba32", 44_739_244),
    (2048, 4096, "astc_6x6", 4_988_080),
    (1024, 1024, "rgba32", 5_592_404),
    (1024, 1024, "astc_6x6", 626_288),
    # Levels 7x3, 3x1, 1x1: whole 6x6 blocks per level, so 2 + 1 + 1 blocks.
    (7, 3, "astc_6x6", 64),
    (7, 3, "rgba32", (21 + 3 + 1) * 4),
    (3, 7, "rgba32", (21 + 3 + 1) * 4),
    (1, 1, "astc_6x6", 16),
])
def test_texture_bytes_counts_the_full_mip_chain(width: int, height: int, fmt: str, expected: int) -> None:
    assert texture_bytes(width, height, fmt) == expected


@pytest.mark.parametrize(("width", "height", "fmt", "match"), [
    (0, 4, "rgba32", "must be positive"),
    (4, 0, "rgba32", "must be positive"),
    (4, 4, "dxt1", "unknown texture format"),
])
def test_texture_bytes_rejects_bad_input(width: int, height: int, fmt: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        texture_bytes(width, height, fmt)


def _png(width: int, height: int) -> bytes:
    return encode_png_rgba(np.zeros((height, width, 4), dtype=np.uint8))


def test_png_size_reads_the_header_and_refuses_other_bytes() -> None:
    assert png_size(_png(7, 3)) == (7, 3)
    assert png_size(_png(7, 3)[:24]) == (7, 3)  # signature, IHDR length and type, width, height
    with pytest.raises(ValueError, match="not a PNG"):
        png_size(b"\xff\xd8\xff\xe0" + bytes(40))
    with pytest.raises(ValueError, match="not a PNG"):
        png_size(_png(7, 3)[:23])
    no_header = bytearray(_png(7, 3))
    no_header[12:16] = b"IDAT"
    with pytest.raises(ValueError, match="not a PNG"):
        png_size(bytes(no_header))


def test_measure_glb_estimates_texture_memory_per_format(tmp_path: Path) -> None:
    cube = box(0, 2, 0, 2, 0, 3)
    path = tmp_path / "scene_visual.glb"
    path.write_bytes(build_glb([Node("building/1", (Primitive(cube, "a"), Primitive(cube, "b")))], MATERIALS,
                               repeat_images={"a": _png(1024, 1024), "b": _png(7, 3)}))
    memory = measure_glb(path)["texture_memory"]
    assert memory == {
        "rgba32_mips_bytes": 5_592_404 + 100, "rgba32_mips_mib": 5.3,
        "astc_6x6_mips_bytes": 626_288 + 64, "astc_6x6_mips_mib": 0.6,
    }


def test_texture_memory_names_an_image_that_is_not_a_png() -> None:
    doc = {"images": [{"bufferView": 0}], "bufferViews": [{"byteLength": 24}]}
    with pytest.raises(ValueError, match="image 0: not a PNG"):
        texture_memory(doc, bytes(24))
    # A view with no byteOffset starts at 0 (glTF default).
    assert texture_memory(doc, _png(7, 3))["astc_6x6_mips_bytes"] == 64
    assert texture_memory({}, b"") == {"rgba32_mips_bytes": 0, "rgba32_mips_mib": 0.0,
                                       "astc_6x6_mips_bytes": 0, "astc_6x6_mips_mib": 0.0}
