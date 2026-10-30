"""Interior light anchors (ADR-013) on the synthetic Murcia block: detail → dress style."""

from __future__ import annotations

import json
from pathlib import Path

from onward_generator.cli import main
from onward_generator.generation.gltf import read_glb
from tests.conftest import MURCIA_CENTER


def _anchors(glb: Path) -> dict[str, dict]:
    doc, _ = read_glb(glb.read_bytes())
    return {n["name"]: n["extras"]["light_anchors"] for n in doc["nodes"] if "light_anchors" in (n.get("extras") or {})}


def test_each_furnished_room_gets_an_anchor_in_the_visual_glb_only(
    detail_inputs: tuple[Path, Path, Path], tmp_path: Path,
) -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    manifest_text = (detail / "scene_detail.json").read_text(encoding="utf-8")
    # scene_detail.json does not change: the anchors live only in the GLB node extras.
    assert "light_anchors" not in manifest_text
    interiors = {o["id"]: o for o in json.loads(manifest_text)["objects"] if o.get("type") == "interior"}
    anchors = _anchors(detail / "scene_visual.glb")
    assert interiors and set(anchors) == set(interiors)
    for node, block in anchors.items():
        assert block["attribute_origin"] == "generated"
        rooms = interiors[node]["rooms"]
        assert [a["room_id"] for a in block["rooms"]] == [r["id"] for r in rooms]
        for anchor, room in zip(block["rooms"], rooms, strict=True):
            assert room["floor_z_m"] < anchor["light_m"][2] < room["ceiling_z_m"]
            assert 4.0 <= anchor["range_m"] <= 12.0
            assert len(anchor["probes_m"]) % 3 == 0 and anchor["probes_m"]

    styled = tmp_path / "styled"
    assert main(["dress", "style", "--detail", str(detail), "--scene", str(scene), "--heights", str(heights),
                 "--base", str(base), "--out", str(styled)]) == 0
    # Later steps copy node extras unchanged, as they do for uv_frame.
    assert _anchors(styled / "scene_visual.glb") == anchors

