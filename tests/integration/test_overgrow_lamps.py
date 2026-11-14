"""``overgrow place`` draws detail's street lamps with one library lamp post (overgrow/lamps.py, 14 November 2026).

One test runs overgrow and checks everything: mutation testing re-runs only the tests that call the code.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.gltf import nodes_from_glb, read_glb
from onward_generator.overgrow import lamps, place
from onward_generator.overgrow.place import overgrow
from tests.integration.test_overgrow_furnish import _detail_out, _library


def _lamp_nodes(directory: Path) -> dict[str, object]:
    nodes, _ = nodes_from_glb((directory / "scene_visual.glb").read_bytes())
    return {node.name: node for node in nodes if node.name.startswith("urban/street_lamp/")}


def _lamps_of(path: Path) -> list[dict]:
    return [o for o in json.loads(path.read_text(encoding="utf-8"))["objects"] if o.get("kind") == "street_lamp"]


def test_detail_street_lamps_take_one_library_post_only_when_the_library_has_one(
    detail_inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _detail_out(detail_inputs, tmp_path, False)
    library = _library(tmp_path / "library", "kenney")
    calls: list[tuple[int, int]] = []
    real = place.swap_lamps

    def spy(nodes, props, seed, headroom):  # type: ignore[no-untyped-def]
        calls.append((seed, headroom))
        return real(nodes, props, seed, headroom)

    monkeypatch.setattr(place, "swap_lamps", spy)
    plain = overgrow(base, detail_inputs[0], tmp_path / "plain", library=library, seed=4242)
    # The Kenney lamps are too coarse (68 and 96 triangles) and short, so no candidate: nothing changes.
    assert plain["street_lamps"] is None
    procedural, kept = _lamp_nodes(base), _lamp_nodes(tmp_path / "plain")
    assert procedural and sorted(procedural) == sorted(kept)
    assert all(kept[name].mesh_key == procedural[name].mesh_key for name in procedural)

    # Let them in to swap one.
    monkeypatch.setattr(lamps, "prop_flat", lambda prop: False)
    monkeypatch.setattr(lamps, "LAMP_MIN_TRIANGLES", 0)
    monkeypatch.setattr(lamps, "LAMP_SOURCE_HEIGHT_M", (0.0, 100.0))
    swapped = overgrow(base, detail_inputs[0], tmp_path / "swapped", library=library, seed=4242)
    report = swapped["street_lamps"]
    assert [seed for seed, _ in calls] == [4242, 4242]
    assert calls[0][1] == calls[1][1] == plain["triangle_headroom"]  # the headroom before the lamps
    assert report["count"] == len(procedural) > 0
    assert report["asset_id"].startswith("kenney_urban_detail_light_") and "traffic" not in report["asset_id"]
    out = _lamp_nodes(tmp_path / "swapped")
    for name, before in procedural.items():
        after = out[name]
        assert np.allclose(after.matrix, before.matrix)
        assert after.extras["asset_id"] == report["asset_id"] and after.extras["representation"] == "library_lamp"
        assert {k: v for k, v in after.extras.items() if k not in ("asset_id", "representation")} == dict(before.extras)
        assert all(p.material.startswith(f"prop_{report['asset_id']}") for p in after.primitives)
        assert max(float(v[2]) for p in after.primitives for v in p.mesh.vertices) == pytest.approx(report["height_m"], abs=1e-3)
    # Each lamp material is a white, rough albedo material, as the other library props.
    doc, _ = read_glb((tmp_path / "swapped" / "scene_visual.glb").read_bytes())
    lamp_materials = [m for m in doc["materials"] if m["name"].startswith(f"prop_{report['asset_id']}")]
    assert lamp_materials and all(m["pbrMetallicRoughness"]["baseColorFactor"] == [1, 1, 1, 1] for m in lamp_materials)
    assert all(m["pbrMetallicRoughness"]["roughnessFactor"] == pytest.approx(0.9) for m in lamp_materials)
    collision = {n.name for n in nodes_from_glb((tmp_path / "swapped" / "scene_collision.glb").read_bytes())[0]}
    assert all(f"{name}#collider" in collision for name in procedural)
    # The swap spends the headroom before the props do, and the scene records are the same.
    assert swapped["triangle_headroom"] == plain["triangle_headroom"] - report["added_triangles"]
    assert _lamps_of(tmp_path / "swapped" / "scene_detail.json") == _lamps_of(base / "scene_detail.json") != []
