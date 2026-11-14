"""``overgrow place --pinned`` stands a library prop near a WGS84 point before anything else (overgrow/pinned.py, 14 November 2026).

One test runs overgrow and checks everything: mutation testing re-runs only the tests that call the code.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from shapely.affinity import affine_transform
from shapely.geometry import Point

from onward_generator.generation.gltf import nodes_from_glb, read_glb
from onward_generator.overgrow import place
from onward_generator.overgrow.library import read_library
from onward_generator.overgrow.pinned import frame_of, plan
from onward_generator.overgrow.place import OvergrowError, overgrow
from tests.integration.test_overgrow_furnish import _detail_out, _library

TRUCK = "kenney_urban_truck_grey"
SPOT = (20.0, -12.0)  # open ground south of the fixture's street


def test_a_pinned_prop_stands_at_its_point_with_its_collider_and_spends_the_headroom_first(
    detail_inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    scene = detail_inputs[0]
    base = _detail_out(detail_inputs, tmp_path, False)
    library = _library(tmp_path / "library", "kenney")
    headrooms: list[int] = []
    real = place.swap_lamps

    def spy(nodes, props, seed, headroom):  # type: ignore[no-untyped-def]
        headrooms.append(headroom)
        return real(nodes, props, seed, headroom)

    monkeypatch.setattr(place, "swap_lamps", spy)
    frame = frame_of(json.loads(scene.read_text(encoding="utf-8")))
    point = frame.to_wgs84(Point(*SPOT))
    pins = tmp_path / "pinned_props.json"
    pins.write_text(json.dumps({"props": [{"asset_id": "nope", "latitude": point.y, "longitude": point.x}]}), encoding="utf-8")
    with pytest.raises(OvergrowError, match=r"^pinned prop nope \(no label\) is not in the library$"):
        overgrow(base, scene, tmp_path / "lost", library=library, seed=4242, pinned=pins)

    plain = overgrow(base, scene, tmp_path / "plain", library=library, seed=4242)
    assert plain["pinned"] == []
    pins.write_text(json.dumps({"props": [{"asset_id": TRUCK, "latitude": point.y, "longitude": point.x, "yaw_deg": 30, "label": "Truck"}]}),
                    encoding="utf-8")
    report = overgrow(base, scene, tmp_path / "pinned", library=library, seed=4242, pinned=pins)
    truck = next(prop for prop in read_library(library)[0] if prop.spec.id == TRUCK)
    (row,) = report["pinned"]
    assert row == {"asset_id": TRUCK, "label": "Truck", "target_m": [SPOT[0], SPOT[1]], "position_m": [SPOT[0], SPOT[1]], "offset_m": 0.0,
                   "yaw_deg": 30.0, "triangles": truck.triangles}
    # The pin spends the headroom before the lamps, the trees and the props.
    assert truck.triangles > 0 and headrooms[-1] == headrooms[-2] - truck.triangles

    out = tmp_path / "pinned"
    nodes, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    (node,) = [n for n in nodes if n.name == f"overgrow/pinned/{TRUCK}/0"]
    yaw = math.radians(30.0)
    assert np.allclose(node.matrix[:3, 3], [*SPOT, 0.0], atol=1e-3)
    assert math.degrees(math.atan2(node.matrix[1, 0], node.matrix[0, 0])) == pytest.approx(30.0)
    assert node.extras["origin"] == "pinned" and node.extras["asset_id"] == TRUCK and node.extras["label"] == "Truck"
    # Its own materials, outside the atlas, each in the file.
    doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    names = {m["name"] for m in doc["materials"]}
    used = {p.material for p in node.primitives}
    assert used and used <= names and all(name.startswith(f"prop_{TRUCK}") for name in used)
    collision, _ = nodes_from_glb((out / "scene_collision.glb").read_bytes())
    (collider,) = [n for n in collision if n.name == f"overgrow/collider/pinned/{TRUCK}/0"]
    assert np.allclose(collider.matrix, node.matrix)
    # No other prop or wreck stands in the pin's plan.
    footprint = affine_transform(plan(truck.mesh), [math.cos(yaw), -math.sin(yaw), math.sin(yaw), math.cos(yaw), *SPOT])
    objects = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))["objects"]
    placed = [o for o in objects if o.get("type") == "overgrowth" and o.get("kind") not in ("grass", "moss", "vines")]
    assert placed and not [o for o in placed if footprint.buffer(0.5).contains(Point(o["position_m"][:2]))]
