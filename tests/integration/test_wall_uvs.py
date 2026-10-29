"""Wall texture coordinates on the synthetic Murcia block (detail → dress style)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from onward_generator.cli import main
from onward_generator.generation.gltf import _accessor, read_glb
from tests.conftest import MURCIA_CENTER

CATALOG = Path(__file__).resolve().parents[2] / "config" / "dress" / "materials.catalog.json"
WALL_PREFIXES = ("plaster", "stone", "brick", "building_wall")


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


Row = tuple[str, np.ndarray, "np.ndarray | None", np.ndarray]


def _primitives(data: bytes) -> dict[str, list[Row]]:
    """Per node: material, local positions, UVs and triangles of each primitive."""
    doc, blob = read_glb(data)
    names = [m["name"] for m in doc["materials"]]
    out: dict[str, list[Row]] = {}
    for node in doc["nodes"]:
        rows: list[Row] = []
        for prim in doc["meshes"][node["mesh"]]["primitives"]:
            attrs = prim["attributes"]
            p = _accessor(doc, blob, attrs["POSITION"]).astype(np.float64)
            uvs = _accessor(doc, blob, attrs["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in attrs else None
            rows.append((names[prim["material"]], np.stack([p[:, 0], -p[:, 2], p[:, 1]], axis=1), uvs,
                         _accessor(doc, blob, prim["indices"]).reshape(-1, 3)))
        out[node["name"]] = rows
    return out


def test_dress_style_aligns_murcia_walls_to_runs_and_storeys(
    detail_inputs: tuple[Path, Path, Path], detail_dir: Path, tmp_path: Path,
) -> None:
    scene, heights, base = detail_inputs
    styled = tmp_path / "styled"
    assert main(["dress", "style", "--detail", str(detail_dir), "--scene", str(scene), "--heights", str(heights),
                 "--base", str(base), "--out", str(styled)]) == 0
    doc, _ = read_glb((styled / "scene_visual.glb").read_bytes())
    frames = {n["name"]: n["extras"]["uv_frame"] for n in doc["nodes"] if "uv_frame" in (n.get("extras") or {})}
    assert frames and all(name.startswith("building/") for name in frames)
    manifest = json.loads((styled / "scene_detail.json").read_text(encoding="utf-8"))
    assert all("uv_frame" not in json.dumps(o) for o in manifest["objects"])
    origin = {o["id"]: o["facade"]["storeys"]["origin"] for o in manifest["objects"] if o.get("type") == "building"}
    tile_m = {e["color_id"]: float(e["technical"]["tile_m"])
              for e in json.loads(CATALOG.read_text(encoding="utf-8"))["materials"]}
    detail = _primitives((detail_dir / "scene_visual.glb").read_bytes())
    styled_prims = _primitives((styled / "scene_visual.glb").read_bytes())
    aligned = 0
    for name, frame in frames.items():
        # No new vertex or triangle: the frame only changes TEXCOORD_0.
        assert [t.shape for *_, t in detail[name]] == [t.shape for *_, t in styled_prims[name]]
        if frame["storey_h"] is None:
            continue
        assert frame["storeys_origin"] == origin[name]
        material, points, uvs, tris = next(row for row in styled_prims[name] if row[0].startswith(WALL_PREFIXES))
        assert uvs is not None
        storey_h = float(frame["storey_h"])
        k = max(1, round(storey_h / tile_m.get(material, 2.0)))
        corners = points[tris]
        normals = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
        normals /= np.linalg.norm(normals, axis=1, keepdims=True)
        for run in frame["runs"]:
            ax, ay, bx, by = run[0]
            length = float(np.hypot(bx - ax, by - ay))
            along = np.array([bx - ax, by - ay]) / length
            out = np.array([along[1], -along[0]])
            rel = corners[:, :, :2] - (ax, ay)
            on = (np.abs(rel @ out) < 1e-3).all(axis=1) & (normals[:, :2] @ out > 0.999)
            on &= (rel @ along > -1e-3).all(axis=1) & (rel @ along < length + 1e-3).all(axis=1)
            vertex = np.unique(tris[on])
            assert len(vertex)
            # k repeats per storey from the ground line: every storey line is a whole number.
            assert np.allclose(uvs[vertex, 1], points[vertex, 2] * k / storey_h, atol=1e-3)
            aligned += 1
    assert aligned
