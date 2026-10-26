"""Dress classify + style on the synthetic Murcia block. No real Ollama, no whole-map apply."""

from __future__ import annotations

import json
from pathlib import Path

from onward_generator.cli import main
from onward_generator.dress.geometry import compare_fingerprints, geometry_fingerprint
from onward_generator.generation.gltf import nodes_from_glb, read_glb
from tests.conftest import MURCIA_CENTER


def test_style_keeps_geographic_geometry_and_writes_atlas(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    lon, lat = MURCIA_CENTER
    assert (lon, lat) == MURCIA_CENTER
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    before = json.loads((detail / "scene_detail.json").read_text())["objects"]
    styled = tmp_path / "styled"
    assert main([
        "dress", "style",
        "--detail", str(detail),
        "--scene", str(scene),
        "--heights", str(heights),
        "--base", str(base),
        "--out", str(styled),
    ]) == 0
    after = json.loads((styled / "scene_detail.json").read_text())["objects"]
    keep = {"building", "surface", "curb", "landmark_part"}
    compare = compare_fingerprints(
        geometry_fingerprint([o for o in before if o.get("type") in keep]),
        geometry_fingerprint([o for o in after if o.get("type") in keep]),
    )
    assert compare["identical"], compare
    generated = [o for o in after if o.get("type") == "urban_element" and o.get("attribute_origin") == "generated"
                 and o.get("kind") in {"bench", "waste_basket"}]
    assert generated, "style should add generated sidewalk furniture"
    assert (styled / "atlas.png").read_bytes().startswith(b"\x89PNG")
    doc, blob = read_glb((styled / "scene_visual.glb").read_bytes())
    assert any("TEXCOORD_0" in (p.get("attributes") or {}) for m in doc["meshes"] for p in m["primitives"])
    names = {m["name"] for m in doc["materials"]}
    for required in ("tree_canopy", "tree_trunk", "urban_metal"):
        if required in names:
            pbr = next(m["pbrMetallicRoughness"] for m in doc["materials"] if m["name"] == required)
            assert pbr.get("baseColorTexture"), required
    assert doc.get("images") and doc["images"][0]["mimeType"] == "image/png"
    assert blob[doc["bufferViews"][doc["images"][0]["bufferView"]]["byteOffset"]:].startswith(b"\x89PNG")
    report = json.loads((styled / "detail_report.json").read_text())
    assert report["dress"]["applied_ai_materials"] is False
    assert report["dress"]["regenerated_facades"] is False
    assert (detail / "scene_visual.glb").read_bytes() != (styled / "scene_visual.glb").read_bytes()
    assert json.dumps(geometry_fingerprint([o for o in before if o.get("type") == "building"]), sort_keys=True) == (
        json.dumps(geometry_fingerprint([o for o in after if o.get("type") == "building"]), sort_keys=True)
    )
    before_nodes = {n.name: sum(p.mesh.vertex_count for p in n.primitives)
                    for n in nodes_from_glb((detail / "scene_visual.glb").read_bytes())[0]
                    if n.name.startswith("building/")}
    after_nodes = {n.name: sum(p.mesh.vertex_count for p in n.primitives)
                   for n in nodes_from_glb((styled / "scene_visual.glb").read_bytes())[0]
                   if n.name.startswith("building/")}
    assert before_nodes == after_nodes


def test_classify_on_real_detail_is_dry_run(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    visual = (detail / "scene_visual.glb").read_bytes()
    plan = tmp_path / "plan"
    assert main(["dress", "classify", "--detail", str(detail), "--scene", str(scene),
                 "--out", str(plan), "--provider", "none", "--limit", "8"]) == 0
    report = json.loads((plan / "material_plan.json").read_text())
    assert report["applied"] is False
    assert report["geometry"]["identical"] is True
    assert report["recommendations"]
    assert all(r["recommended_material_id"] for r in report["recommendations"])
    assert (detail / "scene_visual.glb").read_bytes() == visual
