"""The asset-store command route, the background wall bands of the trim, and furniture storeys in the draw estimate."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from onward_generator.cli import main
from onward_generator.detail.facades import face_with_openings
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh, box, cap, transform
from onward_generator.materials.trim import TrimStats, trim_building, trim_layout, window_families
from onward_generator.materials.trim_style import load_style_table
from onward_generator.perf.measure import combine_groups

# ---- cli -----------------------------------------------------------------------------------------------------------------


def test_asset_store_routes_its_own_arguments(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cache = tmp_path / "no_cache"
    assert main(["asset-store", "scan", "--cache", str(cache)]) == 0
    assert capsys.readouterr().out.startswith(f"asset-store: no .unitypackage under {cache}")


# ---- trim: the scenic and mid rings take one band per wall ---------------------------------------------------------------

SHIPPED = load_style_table().styles["mediterranean"].atlas or {}
ATLAS = {**SHIPPED, "tones": [[1.0, 1.0, 1.0]], "dress": {"zocalo_share": 1.0, "cornice_share": 1.0, "course_share": 1.0}}
LAYOUT = trim_layout(ATLAS)
WINDOWS = window_families(ATLAS)
RECORD = {"facade": {"storeys": {"count": 3, "height_m": 3.0}}, "roof": {"type": "flat"}}


def _facade() -> Mesh:
    holes = [(0.9 + 3.0 * c, 0.9 + 3.0 * r, 2.1 + 3.0 * c, 2.3 + 3.0 * r) for c in range(3) for r in range(3)]
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2] = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0)
    return transform(cap(face_with_openings(9.0, 0.0, 9.0, holes), 0.0), m)


def _wall_triangles(tier: str | None) -> int:
    node = Node("building/s1", (Primitive(_facade(), "scenic_wall_warm"),), {"type": "building", "object_id": "building/s1"})
    stats = TrimStats()
    trim_building(node, RECORD if tier is None else {**RECORD, "tier": tier}, "mediterranean", ATLAS, LAYOUT, WINDOWS, stats)
    return stats.wall_triangles_after


def test_a_background_wall_is_not_cut_into_storeys() -> None:
    core = _wall_triangles("core")
    assert _wall_triangles("scenic") == _wall_triangles("mid") < core
    assert _wall_triangles(None) == core


# ---- perf: furniture storeys ---------------------------------------------------------------------------------------------

MATERIALS = {"a": Material((0.5, 0.5, 0.5, 1.0), 0.9)}


def _groups(tmp_path: Path, nodes: list[Node], cell_m: float) -> dict[str, object]:
    path = tmp_path / "f.glb"
    path.write_bytes(build_glb(nodes, MATERIALS))
    return combine_groups(path, cell_m)


def test_furniture_below_the_ground_floor_is_its_own_storey(tmp_path: Path) -> None:
    nodes = [
        Node("furnish/b1/0", (Primitive(box(0, 1, 0, 0.5, 0.0, 1.5), "a"),)),
        Node("furnish/b1/1", (Primitive(box(0, 1, 0, 0.5, -2.0, -0.5), "a"),)),
    ]
    assert _groups(tmp_path, nodes, 100.0)["draws"] == 2


def test_the_furniture_of_a_second_building_never_shares_a_ground_cell(tmp_path: Path) -> None:
    # The second building's furniture group sits one cell below the first one's, far from any ground cell near it.
    nodes = [
        Node("furnish/b1/0", (Primitive(box(0, 1, 0, 0.5, 0.0, 1.5), "a"),)),
        Node("furnish/b2/0", (Primitive(box(0, 1, 0, 0.5, 0.0, 1.5), "a"),)),
        Node("prop/far", (Primitive(box(-999998.8, -999998.2, -0.8, -0.2, 0.0, 1.0), "a"),)),
    ]
    assert _groups(tmp_path, nodes, 1.0)["draws"] == 3
