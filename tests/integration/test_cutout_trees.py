"""Cut-out trees end to end (ADR-040): an OBJ with leaves, through the converter, the library and overgrow, into the
export package's adapter options."""

from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from onward_generator.assetstore.objglb import obj_to_glb
from onward_generator.export.cutouts import scene_cutouts
from onward_generator.generation.gltf import nodes_from_glb, read_glb
from onward_generator.overgrow.place import overgrow
from tests.integration.test_overgrow_furnish import _detail_out


def _tree_obj(height: float = 5.0, crown: float = 2.0) -> str:
    """A square trunk and two crossed leaf cards, Y up as the exporter writes them."""
    t, top, low = 0.15, height, height * 0.4
    lines = [f"v {x} {y} {z}" for x, y, z in (
        (-t, 0, -t), (t, 0, -t), (t, top * 0.7, -t), (-t, top * 0.7, -t),  # trunk face
        (-crown, low, 0), (crown, low, 0), (crown, top, 0), (-crown, top, 0),  # card along x
        (0, low, -crown), (0, low, crown), (0, top, crown), (0, top, -crown),  # card along z
    )]
    lines += ["vt 0 0", "vt 1 0", "vt 1 1", "vt 0 1", "vn 0 0 1"]
    lines += ["usemtl Bark", "f 1/1/1 2/2/1 3/3/1", "f 1/1/1 3/3/1 4/4/1",
              "usemtl Leaves", "f 5/1/1 6/2/1 7/3/1", "f 5/1/1 7/3/1 8/4/1", "f 9/1/1 10/2/1 11/3/1", "f 9/1/1 11/3/1 12/4/1"]
    return "\n".join(lines) + "\n"


@pytest.fixture(scope="module")
def runs(detail_inputs, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("cutout_trees")
    base = _detail_out(detail_inputs, tmp, False)
    catalogue = tmp / "library" / "test_trees"
    catalogue.mkdir(parents=True)
    leaves = Image.new("RGBA", (8, 8), (40, 120, 50, 255))
    leaves.putpixel((0, 0), (40, 120, 50, 0))
    leaves.save(catalogue / "leaves.png")
    Image.new("RGB", (8, 8), (90, 60, 30)).save(catalogue / "bark.png")
    mtl = "newmtl Bark\nKd 1 1 1\nmap_Kd bark.png\nnewmtl Leaves\nKd 1 1 1\nmap_Kd leaves.png\nonward_cutoff 0.4\n"
    (catalogue / "holly.glb").write_bytes(obj_to_glb(_tree_obj(), mtl, "holly", catalogue))
    row = {"id": "test_holly", "file": "holly.glb", "category": "vegetation", "role": "tree", "anchor": "ground", "lane": "forbid",
           "surfaces": ["park", "plaza", "sidewalk"], "clearance_m": 0.0, "use": "A test tree with cut-out leaves."}
    (catalogue / "manifest.json").write_text(json.dumps({"schema_version": "0.1.0", "assets": [row]}), encoding="utf-8")
    out = tmp / "out"
    report = overgrow(base, detail_inputs[0], out, library=tmp / "library")
    return {"base": base, "out": out, "report": report}


def test_the_street_trees_take_the_cut_out_tree_and_the_scene_lists_its_leaves(runs) -> None:
    report = runs["report"]["street_trees"]
    base_nodes, _ = nodes_from_glb((runs["base"] / "scene_visual.glb").read_bytes())
    trees = {n.name: n for n in base_nodes if n.name.startswith("urban/tree") and n.extras.get("type") == "urban_element"}
    assert trees and report["trees"] == len(trees)
    assert report["count"] + report["out_of_scale"] + report["over_budget"] == len(trees) and report["count"] > 0
    out_nodes, _ = nodes_from_glb((runs["out"] / "scene_visual.glb").read_bytes())
    swapped = [n for n in out_nodes if n.extras.get("representation") == "library_tree"]
    assert len(swapped) == report["count"]
    for node in swapped:
        assert node.name in trees and node.extras["asset_id"] == "test_holly"
        assert sorted(p.material for p in node.primitives) == ["prop_test_holly", "prop_test_holly__1"]
        assert np.allclose(node.matrix[:3, 3], trees[node.name].matrix[:3, 3])
    visual = (runs["out"] / "scene_visual.glb").read_bytes()
    listed = scene_cutouts(visual)
    assert list(listed.values()) == [0.4] and next(iter(listed)).startswith("prop_test_holly")
    doc, _ = read_glb(visual)
    assert set(listed) <= {m["name"] for m in doc["materials"]}
    # The trunk colliders of detail stay where they were.
    collision = {n.name for n in nodes_from_glb((runs["out"] / "scene_collision.glb").read_bytes())[0]}
    assert all(f"{name}#collider" in collision for name in trees)
