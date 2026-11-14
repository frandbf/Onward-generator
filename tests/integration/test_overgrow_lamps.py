"""``overgrow place`` draws detail's street lamps with one library lamp post (overgrow/lamps.py, 14 November 2026)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.gltf import nodes_from_glb
from onward_generator.overgrow import lamps
from onward_generator.overgrow.place import overgrow
from tests.integration.test_overgrow_furnish import _detail_out, _library


@pytest.fixture(scope="module")
def runs(detail_inputs, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:
    tmp = tmp_path_factory.mktemp("lamps")
    base = _detail_out(detail_inputs, tmp, False)
    library = _library(tmp / "library", "kenney")
    plain = overgrow(base, detail_inputs[0], tmp / "plain", library=library)
    patch = pytest.MonkeyPatch()
    # The Kenney lamps are too coarse (68 and 96 triangles) and short, so no candidate; let them in to swap one.
    patch.setattr(lamps, "prop_flat", lambda prop: False)
    patch.setattr(lamps, "LAMP_MIN_TRIANGLES", 0)
    patch.setattr(lamps, "LAMP_SOURCE_HEIGHT_M", (0.0, 100.0))
    try:
        swapped = overgrow(base, detail_inputs[0], tmp / "swapped", library=library)
    finally:
        patch.undo()
    return {"base": base, "plain": plain, "swapped": swapped, "tmp": tmp}


def _lamp_nodes(directory: Path) -> dict[str, object]:
    nodes, _ = nodes_from_glb((directory / "scene_visual.glb").read_bytes())
    return {node.name: node for node in nodes if node.name.startswith("urban/street_lamp/")}


def test_a_library_without_a_textured_lamp_leaves_the_lamps_procedural(runs) -> None:
    plain = runs["plain"]
    assert plain["street_lamps"] is None
    base, out = _lamp_nodes(runs["base"]), _lamp_nodes(runs["tmp"] / "plain")
    assert base and sorted(base) == sorted(out)
    assert all(out[name].mesh_key == base[name].mesh_key for name in base)


def test_every_street_lamp_takes_the_library_post_and_keeps_its_place_and_collider(runs) -> None:
    report = runs["swapped"]["street_lamps"]
    base, out = _lamp_nodes(runs["base"]), _lamp_nodes(runs["tmp"] / "swapped")
    assert report["count"] == len(base) > 0
    assert report["asset_id"].startswith("kenney_urban_detail_light_") and "traffic" not in report["asset_id"]
    for name, before in base.items():
        after = out[name]
        assert np.allclose(after.matrix, before.matrix)
        assert after.extras["asset_id"] == report["asset_id"] and after.extras["representation"] == "library_lamp"
        assert {k: v for k, v in after.extras.items() if k not in ("asset_id", "representation")} == dict(before.extras)
        assert all(p.material.startswith(f"prop_{report['asset_id']}") for p in after.primitives)
        assert max(float(v[2]) for p in after.primitives for v in p.mesh.vertices) == pytest.approx(report["height_m"], abs=1e-3)
    collision = {n.name for n in nodes_from_glb((runs["tmp"] / "swapped" / "scene_collision.glb").read_bytes())[0]}
    assert all(f"{name}#collider" in collision for name in base)
    # The swap spends the headroom before the props do, and the scene records are the same.
    assert runs["swapped"]["triangle_headroom"] == runs["plain"]["triangle_headroom"] - report["added_triangles"]
    lamps_of = lambda path: [o for o in json.loads(path.read_text(encoding="utf-8"))["objects"] if o.get("kind") == "street_lamp"]  # noqa: E731 - a one-line reader
    assert lamps_of(runs["tmp"] / "swapped" / "scene_detail.json") == lamps_of(runs["base"] / "scene_detail.json") != []
