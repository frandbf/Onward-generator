"""Wall texture coordinates on the synthetic Murcia block (detail → dress style)."""

from __future__ import annotations

from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.generation.gltf import read_glb
from tests.conftest import MURCIA_CENTER


@pytest.fixture(scope="module")
def detail_dir(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> Path:
    scene, heights, base = detail_inputs
    out = tmp_path_factory.mktemp("wall_uvs") / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(out), "--no-preview"]) == 0
    return out


def test_detail_stage_stores_no_uvs_for_dress_style_to_replace(detail_dir: Path) -> None:
    """``dress style`` repeats a tile on every material before it looks at stored UVs.

    That order is safe only while the detail GLB it copies carries no ``TEXCOORD_0``:
    interiors, openings and glass get their coordinates in ``dress style`` itself.
    """
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    doc, _ = read_glb((detail_dir / "scene_visual.glb").read_bytes())
    kinds = {str((n.get("extras") or {}).get("type")) for n in doc["nodes"]}
    assert {"building", "interior"} <= kinds
    stored = [m["name"] for m in doc["meshes"] for p in m["primitives"] if "TEXCOORD_0" in p["attributes"]]
    assert stored == []
