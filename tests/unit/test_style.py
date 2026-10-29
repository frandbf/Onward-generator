"""``dress style`` writes one repeating tile per material and keeps atlas.png as a contact sheet."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from onward_generator.dress.atlas import MIN_TILE_PX, _tile_pixels, encode_png_rgba
from onward_generator.dress.catalog import load_catalog
from onward_generator.dress.style import write_styled
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, images_by_material, nodes_from_glb, read_glb
from onward_generator.generation.mesh import Mesh, quad

SEED = 48152
MATERIALS = {
    "plaster_ochre": Material((0.8, 0.6, 0.3, 1.0), 0.9),
    "roof_tile": Material((0.6, 0.3, 0.2, 1.0), 0.8),
    "tree_canopy": Material((0.2, 0.5, 0.2, 1.0), 0.9),
}


def _nodes() -> list[Node]:
    # Far from the origin: the old atlas path wrapped each vertex here into one tile.
    wall = quad((900.0, 400.0, 0.0), (912.0, 400.0, 0.0), (912.0, 400.0, 9.0), (900.0, 400.0, 9.0))
    rise = math.tan(math.radians(22.0)) * 4.0
    roof = quad((900.0, 400.0, 9.0), (912.0, 400.0, 9.0), (912.0, 404.0, 9.0 + rise), (900.0, 404.0, 9.0 + rise))
    canopy = quad((880.0, 380.0, 3.0), (884.0, 380.0, 3.0), (884.0, 384.0, 3.0), (880.0, 384.0, 3.0))
    return [
        Node("building/1", (Primitive(wall, "plaster_ochre"), Primitive(roof, "roof_tile")), {"type": "building"}),
        Node("tree/1", (Primitive(canopy, "tree_canopy"),), {"type": "urban_element"}),
    ]


def _style(out: Path, *, atlas: bool) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    targets = {
        "visual": out / "scene_visual.glb",
        "collision": out / "scene_collision.glb",
        "manifest": out / "scene_detail.json",
        "report": out / "detail_report.json",
        "atlas": out / "atlas.png",
    }
    return write_styled(
        _nodes(), _nodes(), [], [], MATERIALS, load_catalog(), targets,
        atlas=atlas, attribution="© OpenStreetMap contributors", seed=SEED,
        source_manifest={}, source_detail_sha256=None, generated_accessories={},
    )


def _uvs(path: Path) -> dict[str, np.ndarray]:
    nodes, _ = nodes_from_glb(path.read_bytes())
    return {p.material: np.asarray(p.uvs, dtype=np.float64) for n in nodes for p in n.primitives}


def _stretch(mesh: Mesh, uvs: np.ndarray, tile_m: float) -> float:
    worst = 0.0
    for tri in mesh.triangles:
        for a, b in ((0, 1), (1, 2), (2, 0)):
            metres = float(np.linalg.norm(mesh.vertices[tri[b]] - mesh.vertices[tri[a]]))
            worst = max(worst, abs(float(np.linalg.norm(uvs[tri[b]] - uvs[tri[a]])) * tile_m / metres - 1.0))
    return worst


def test_style_embeds_one_repeating_tile_per_material(tmp_path: Path) -> None:
    report = _style(tmp_path, atlas=True)
    data = (tmp_path / "scene_visual.glb").read_bytes()
    doc, _ = read_glb(data)
    assert len(doc["images"]) == len(MATERIALS)
    assert all(s["wrapS"] == 10497 and s["wrapT"] == 10497 for s in doc["samplers"])
    indices = [m["pbrMetallicRoughness"]["baseColorTexture"]["index"] for m in doc["materials"]]
    assert sorted(indices) == list(range(len(MATERIALS)))
    images = images_by_material(data)
    for index, color_id in enumerate(sorted(MATERIALS)):
        assert images[color_id] == encode_png_rgba(_tile_pixels(color_id, MIN_TILE_PX, SEED + index))
    assert doc["extras"]["atlas"]["role"] == "contact_sheet"
    assert report["dress"]["atlas"] is True


def test_style_keeps_atlas_png_as_a_contact_sheet(tmp_path: Path) -> None:
    _style(tmp_path, atlas=True)
    assert (tmp_path / "atlas.png").read_bytes().startswith(b"\x89PNG")


def test_style_uvs_repeat_in_metres_instead_of_wrapping_into_a_slot(tmp_path: Path) -> None:
    _style(tmp_path, atlas=True)
    catalog = {e.color_id: float(e.technical["tile_m"]) for e in load_catalog().entries}
    uvs = _uvs(tmp_path / "scene_visual.glb")
    wall, roof = _nodes()[0].primitives
    wall_uv = uvs["plaster_ochre"]
    # A 12 m × 9 m wall spans 12/tile_m by 9/tile_m repeats, near zero, not inside one atlas slot.
    span = wall_uv.max(axis=0) - wall_uv.min(axis=0)
    assert np.allclose(span, [12.0 / catalog["plaster_ochre"], 9.0 / catalog["plaster_ochre"]], rtol=1e-4)
    assert float(wall_uv.min()) >= 0.0
    assert float(wall_uv.max()) <= float(span.max()) + 1.0
    assert _stretch(roof.mesh, uvs["roof_tile"], catalog["roof_tile"]) < 0.01


def test_style_without_atlas_has_no_images_and_no_contact_sheet(tmp_path: Path) -> None:
    report = _style(tmp_path, atlas=False)
    doc, _ = read_glb((tmp_path / "scene_visual.glb").read_bytes())
    assert "images" not in doc
    assert not (tmp_path / "atlas.png").exists()
    assert report["dress"]["atlas"] is False


def test_style_output_is_deterministic(tmp_path: Path) -> None:
    _style(tmp_path / "a", atlas=True)
    _style(tmp_path / "b", atlas=True)
    for name in ("scene_visual.glb", "scene_collision.glb", "atlas.png"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()
