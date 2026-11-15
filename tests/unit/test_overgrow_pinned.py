"""Props pinned near a WGS84 point (overgrow/pinned.py, 14 November 2026)."""

from __future__ import annotations

import io
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from shapely import STRtree
from shapely.geometry import Point, Polygon
from shapely.geometry import box as shapely_box

from onward_generator.generation.mesh import Mesh, box
from onward_generator.geo.projection import frame_for_origin
from onward_generator.overgrow import pinned
from onward_generator.overgrow.catalog import ASSETS_BY_ID
from onward_generator.overgrow.fit import collider_mesh
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.pinned import (
    BUILDING_GAP_M,
    SEARCH_DEFAULT_M,
    SEARCH_MAX_M,
    SEARCH_STEP_M,
    Pin,
    PinnedError,
    _offsets,
    frame_of,
    keep_out_circles,
    place_pins,
    plan,
    read_pins,
)
from onward_generator.overgrow.zone import square_zone, zone_from_polygon

LAT, LON = 2.047458, 45.326639


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 90, 90)).save(out, format="PNG")
    return out.getvalue()


def _prop(mesh: Mesh) -> Prop:
    spec = replace(ASSETS_BY_ID["covered_car"], id="crashed_helicopter")
    return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2)), _png())


def _wreck(length: float = 14.0, width: float = 4.0) -> Prop:
    return _prop(box(-length / 2, length / 2, -width / 2, width / 2, 0.0, 2.5))


FRAME = frame_for_origin(LON, LAT)
EMPTY = STRtree([Point(1e9, 1e9)])


def test_the_settings() -> None:
    assert (SEARCH_DEFAULT_M, SEARCH_MAX_M, SEARCH_STEP_M, BUILDING_GAP_M) == (30.0, 200.0, 1.0, 0.3)


def test_pins_are_read_whole_with_defaults(tmp_path: Path) -> None:
    path = tmp_path / "pinned_props.json"
    path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1.5, "longitude": 2},
                                          {"asset_id": "b", "latitude": -1, "longitude": -2, "yaw_deg": 45, "search_m": 10, "label": "B"}]}))
    assert read_pins(path) == [Pin("a", 1.5, 2.0), Pin("b", -1.0, -2.0, 45.0, 10.0, "B")]


@pytest.mark.parametrize(("document", "message"), [
    ("{", "cannot be read"),
    ({"props": []}, r'needs \{"props": \[...\]\} with at least one prop$'),
    ([], r"needs \{"),
    ({"props": [{"latitude": 1, "longitude": 1}]}, r"props\[0\]: needs an asset_id$"),
    ({"props": [{"asset_id": "", "latitude": 1, "longitude": 1}]}, "needs an asset_id"),
    ({"props": [{"asset_id": "a", "latitude": "1", "longitude": 1}]}, "latitude must be a number, got '1'$"),
    ({"props": [{"asset_id": "a", "latitude": True, "longitude": 1}]}, "latitude must be a number, got True$"),
    ({"props": [{"asset_id": "a", "latitude": 91, "longitude": 1}]}, "latitude 91.0 / longitude 1.0 is not a WGS84 point$"),
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": -181}]}, "is not a WGS84 point$"),
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": 1, "search_m": 201}]}, "search_m must lie between 0 and 200, got 201$"),
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": 1, "search_m": -1}]}, "got -1$"),
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": 1, "yaw_deg": "n"}]}, r"json props\[0\]: yaw_deg must be a number, got 'n'$"),
    ({"props": [{"asset_id": "a", "latitude": "1", "longitude": 1}]}, r"json props\[0\]: latitude must be a number, got '1'$"),
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": "x"}]}, r"json props\[0\]: longitude must be a number, got 'x'$"),
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": 1, "search_m": "s"}]}, r"json props\[0\]: search_m must be a number, got 's'$"),
    ({"props": [{"asset_id": "a", "latitude": -90.5, "longitude": 1}]}, "latitude -90.5 / longitude 1.0 is not a WGS84 point$"),
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": 180.5}]}, "latitude 1.0 / longitude 180.5 is not a WGS84 point$"),
])
def test_a_bad_file_is_refused(tmp_path: Path, document: object, message: str) -> None:
    path = tmp_path / "pinned_props.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    with pytest.raises(PinnedError, match=message):
        read_pins(path)


def test_the_bounds_of_a_point_and_of_search_m_are_kept(tmp_path: Path) -> None:
    path = tmp_path / "pinned_props.json"
    rows = [{"asset_id": "a", "latitude": lat, "longitude": lon, "search_m": search}
            for lat, lon, search in ((-90, -180, 0), (90, 180, 0.5), (0, 0, 200))]
    path.write_text(json.dumps({"props": rows}), encoding="utf-8")
    assert read_pins(path) == [Pin("a", -90.0, -180.0, search_m=0.0), Pin("a", 90.0, 180.0, search_m=0.5), Pin("a", 0.0, 0.0, search_m=200.0)]


def test_the_search_grid_is_the_whole_disc_nearest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    ring_1 = [(-1.0, 0.0), (0.0, -1.0), (0.0, 1.0), (1.0, 0.0)]
    diagonal = [(-1.0, -1.0), (-1.0, 1.0), (1.0, -1.0), (1.0, 1.0)]
    ring_2 = [(-2.0, 0.0), (0.0, -2.0), (0.0, 2.0), (2.0, 0.0)]
    assert _offsets(0.0) == [(0.0, 0.0)]
    assert _offsets(2.0) == [(0.0, 0.0), *ring_1, *diagonal, *ring_2]
    wide = _offsets(2.5)
    assert len(wide) == 21 and set(wide) == {(float(i), float(j)) for i in range(-2, 3) for j in range(-2, 3)} - {
        (-2.0, -2.0), (-2.0, 2.0), (2.0, -2.0), (2.0, 2.0)}
    monkeypatch.setattr(pinned, "SEARCH_STEP_M", 0.5)
    assert _offsets(1.0) == [(0.0, 0.0), *[(x / 2, y / 2) for x, y in (*ring_1, *diagonal, *ring_2)]]


def test_the_frame_comes_from_the_geo_scene() -> None:
    frame = frame_of({"frame": {"origin_wgs84": [LON, LAT], "crs": FRAME.crs}})
    point = frame.to_local(Point(LON, LAT))
    assert (round(point.x, 6), round(point.y, 6)) == (0.0, 0.0)
    for geo in ({}, {"frame": {"origin_wgs84": [LON], "crs": "EPSG:32638"}}, {"frame": {"origin_wgs84": [LON, LAT]}}):
        with pytest.raises(PinnedError, match=r"^the geo scene names no frame \(origin_wgs84 and crs\): pinned props need it$"):
            frame_of(geo)


def test_a_pin_stands_on_its_point_when_it_is_free() -> None:
    result = place_pins([Pin("crashed_helicopter", LAT, LON, 30.0, label="Super Six-Four")], {"crashed_helicopter": _wreck()}, FRAME, EMPTY, square_zone(100))
    (node,) = result.nodes
    assert node.name == "overgrow/pinned/crashed_helicopter/0" and node.mesh_key == "pinned:crashed_helicopter"
    assert node.extras == {"type": "overgrowth", "origin": "pinned", "kind": "vehicles", "role": "wreck", "asset_id": "crashed_helicopter",
                           "label": "Super Six-Four"}
    assert np.allclose(node.matrix[:3, 3], [0.0, 0.0, 0.0], atol=1e-6)
    assert math.degrees(math.atan2(node.matrix[1, 0], node.matrix[0, 0])) == pytest.approx(30.0)
    assert result.colliders[0].name == "overgrow/collider/pinned/crashed_helicopter/0"
    assert result.triangles == 12 and result.records[0]["offset_m"] == 0.0 and result.records[0]["yaw_deg"] == 30.0
    assert result.records[0]["label"] == "Super Six-Four" and result.footprints[0].area == pytest.approx(14 * 4)
    prop = _wreck()
    assert [(p.material, p.mesh.vertices.tolist()) for p in node.primitives] == [(p.material, p.mesh.vertices.tolist()) for p in prop.primitives()]
    ((collider,),) = [c.primitives for c in result.colliders]
    fence, whole = collider_mesh(prop.mesh, "fence"), collider_mesh(prop.mesh, "box")
    assert collider.material == "collider" and collider.mesh.vertices.tolist() == fence.vertices.tolist() != whole.vertices.tolist()
    assert result.colliders[0].extras == {"type": "collision", "origin": "pinned", "asset_id": "crashed_helicopter"}
    assert np.array_equal(result.colliders[0].matrix, node.matrix)
    assert list(result.images) == list(prop.images())


def test_a_pin_moves_to_the_nearest_free_spot_and_turns_when_it_must() -> None:
    # A building east of the point: the 14 m wreck lying east-west hits it, so it turns or moves west.
    buildings = STRtree([shapely_box(3.0, -20.0, 30.0, 20.0)])
    result = place_pins([Pin("crashed_helicopter", LAT, LON, 0.0)], {"crashed_helicopter": _wreck()}, FRAME, buildings, square_zone(100))
    footprint = result.footprints[0]
    assert footprint.buffer(BUILDING_GAP_M).intersects(shapely_box(3.0, -20.0, 30.0, 20.0)) is False
    assert result.records[0]["offset_m"] <= 6.0


def test_two_pins_do_not_overlap_and_a_lost_pin_stops_the_stage() -> None:
    pins = [Pin("crashed_helicopter", LAT, LON), Pin("crashed_helicopter", LAT, LON)]
    result = place_pins(pins, {"crashed_helicopter": _wreck()}, FRAME, EMPTY, square_zone(100))
    assert not result.footprints[0].intersects(result.footprints[1]) and len(result.nodes) == 2
    assert result.triangles == 24 and [n.name for n in result.nodes] == ["overgrow/pinned/crashed_helicopter/0", "overgrow/pinned/crashed_helicopter/1"]
    with pytest.raises(PinnedError, match=r"^pinned prop crashed_helicopter \(no label\) is not in the library$"):
        place_pins(pins, {}, FRAME, EMPTY, square_zone(100))
    walled = STRtree([shapely_box(-50, -50, 50, 50)])
    with pytest.raises(PinnedError, match=r"^pinned prop crashed_helicopter \(no label\) finds no free spot within 30 m of 2.047458, 45.326639: "
                                          r"raise search_m or move the point$"):
        place_pins([Pin("crashed_helicopter", LAT, LON)], {"crashed_helicopter": _wreck()}, FRAME, walled, square_zone(100))
    with pytest.raises(PinnedError, match="finds no free spot"):  # outside the zone
        place_pins([Pin("crashed_helicopter", LAT + 0.01, LON, search_m=5)], {"crashed_helicopter": _wreck()}, FRAME, EMPTY, square_zone(100))


def test_the_first_free_spot_of_the_nearest_ring_is_taken_and_recorded() -> None:
    # A cross of streets-wide blocks through the target: the 1 m square stands at the first corner, (-2, -2).
    lat, lon = LAT + 0.0001, LON + 0.0001
    target = FRAME.to_local(Point(lon, lat))
    tx, ty = target.x, target.y
    assert all(round(v, 3) != round(v, 4) for v in (tx, ty, tx - 2.0, ty - 2.0))  # the rounding shows
    cross = STRtree([shapely_box(tx - 50, ty - 1, tx + 50, ty + 1), shapely_box(tx - 1, ty - 50, tx + 1, ty + 50)])
    result = place_pins([Pin("crashed_helicopter", lat, lon, 30.123, label="corner")], {"crashed_helicopter": _wreck(1.0, 1.0)}, FRAME, cross,
                        square_zone(100))
    assert result.records == [{"asset_id": "crashed_helicopter", "label": "corner", "target_m": [round(tx, 3), round(ty, 3)],
                               "position_m": [round(tx - 2.0, 3), round(ty - 2.0, 3)], "offset_m": 2.828, "yaw_deg": 30.12, "triangles": 12}]


ARMS = {"+x": shapely_box(5, -2, 8, 2), "+y": shapely_box(-2, 5, 2, 8), "-x": shapely_box(-8, -2, -5, 2)}


@pytest.mark.parametrize(("blocked", "yaw"), [((), 0.0), (("+x",), 90.0), (("+x", "+y"), 180.0), (("+x", "+y", "-x"), 270.0)])
def test_a_pin_tries_its_yaw_then_each_quarter_turn_on_the_spot(blocked: tuple[str, ...], yaw: float) -> None:
    # A 10 m arm east of its origin: each block stops one heading, so the pin turns where it stands.
    arm = _prop(box(0.0, 10.0, -0.5, 0.5, 0.0, 1.0))
    buildings = STRtree([ARMS[name] for name in blocked]) if blocked else EMPTY
    result = place_pins([Pin("crashed_helicopter", LAT, LON)], {"crashed_helicopter": arm}, FRAME, buildings, square_zone(100))
    (record,) = result.records
    assert (record["yaw_deg"], record["offset_m"], record["position_m"]) == (yaw, 0.0, [0.0, 0.0])


def test_the_recorded_yaw_is_a_heading_and_a_spot_outside_the_zone_is_passed_by() -> None:
    result = place_pins([Pin("crashed_helicopter", LAT, LON, 450.0)], {"crashed_helicopter": _wreck(1.0, 1.0)}, FRAME, EMPTY, square_zone(100))
    assert result.records[0]["yaw_deg"] == 90.0
    # The zone starts 1.5 m east of the point: the nearest spots are out of it, the first one in is (2, 0).
    east = zone_from_polygon(shapely_box(1.5, -50, 50, 50))
    result = place_pins([Pin("crashed_helicopter", LAT, LON, search_m=5)], {"crashed_helicopter": _wreck(1.0, 1.0)}, FRAME, EMPTY, east)
    assert result.records[0]["position_m"] == [2.0, 0.0] and result.records[0]["offset_m"] == 2.0


def test_the_plan_and_the_keep_out_circle() -> None:
    footprint = plan(_wreck(10.0, 2.0).mesh)
    assert footprint.area == pytest.approx(20.0)
    ((x, y, r),) = keep_out_circles([footprint])
    assert (x, y) == (0.0, 0.0) and r == pytest.approx(math.hypot(5, 1), abs=1e-3)
    assert keep_out_circles([]) == []
    # Off the origin, with digits past the third: the centre and the radius are rounded to the millimetre.
    assert keep_out_circles([shapely_box(10.2468, 20.1357, 13.2468, 21.1357)]) == [(11.747, 20.636, 1.581)]
    assert isinstance(Polygon(), Polygon)


def test_a_pin_may_lay_its_model_on_its_side_and_tip_its_nose(tmp_path: Path) -> None:
    """14 November 2026: an intact Black Hawk laid on its side reads as a crash."""
    from onward_generator.overgrow.pinned import posed

    path = tmp_path / "pinned_props.json"
    path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2, "roll_deg": 80, "pitch_deg": -12}]}))
    assert read_pins(path) == [Pin("a", 1.0, 2.0, roll_deg=80.0, pitch_deg=-12.0)]
    for bad in ({"roll_deg": 180.5}, {"roll_deg": -181}, {"pitch_deg": 90.5}, {"pitch_deg": -91}):
        path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2, **bad}]}))
        with pytest.raises(PinnedError, match=r"props\[0\]: roll_deg must lie between -180 and 180 and pitch_deg between -90 and 90"):
            read_pins(path)
    path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2, "roll_deg": 180, "pitch_deg": -90}]}))
    assert read_pins(path)[0].roll_deg == 180.0
    wreck = _wreck(14.0, 4.0)  # 14 m long on X, 4 m wide, 2.5 m tall
    assert posed(wreck, 0.0, 0.0) is wreck
    side = posed(wreck, 90.0, 0.0)
    span = side.mesh.vertices.max(axis=0) - side.mesh.vertices.min(axis=0)
    assert span == pytest.approx([14.0, 2.5, 4.0], abs=1e-9) and side.mesh.vertices[:, 2].min() == pytest.approx(0.0, abs=1e-12)
    nose = posed(wreck, 0.0, 90.0)
    assert (nose.mesh.vertices.max(axis=0) - nose.mesh.vertices.min(axis=0)) == pytest.approx([2.5, 4.0, 14.0], abs=1e-9)
    tipped = posed(wreck, 0.0, 30.0)  # +Y turn: the +X end goes down
    low = tipped.mesh.vertices[np.argmin(tipped.mesh.vertices[:, 2])]
    assert low[0] > 0 and tipped.mesh.vertices[:, 2].min() == pytest.approx(0.0, abs=1e-12)
    result = place_pins([Pin("crashed_helicopter", LAT, LON, 0.0, roll_deg=90.0)], {"crashed_helicopter": wreck}, FRAME, EMPTY,
                        square_zone(100))
    assert result.records[0]["roll_deg"] == 90.0 and result.records[0]["pitch_deg"] == 0.0
    assert result.footprints[0].area == pytest.approx(14.0 * 2.5, rel=1e-6)  # its plan on its side
    collider = result.colliders[0].primitives[0].mesh
    assert collider.vertices[:, 2].max() == pytest.approx(4.0, abs=1e-6)  # its side stands 4 m
    flat = place_pins([Pin("crashed_helicopter", LAT, LON, 0.0)], {"crashed_helicopter": wreck}, FRAME, EMPTY, square_zone(100))
    assert "roll_deg" not in flat.records[0] and "pitch_deg" not in flat.records[0]


def test_a_pin_may_sink_its_model_into_the_ground(tmp_path: Path) -> None:
    """14 November 2026: on its side, the Black Hawk rests on a rotor tip with its body metres up; sink_m buries the
    blades so the body lies on the ground."""
    from onward_generator.overgrow.pinned import SINK_MAX_M

    assert SINK_MAX_M == 10.0
    path = tmp_path / "pinned_props.json"
    path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2, "sink_m": 4.5}]}))
    assert read_pins(path) == [Pin("a", 1.0, 2.0, sink_m=4.5)]
    path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2}]}))
    assert read_pins(path)[0].sink_m == 0.0
    for edge in (0, 10):
        path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2, "sink_m": edge}]}))
        assert read_pins(path)[0].sink_m == float(edge)
    for bad in (-0.1, 10.1):
        path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2, "sink_m": bad}]}))
        with pytest.raises(PinnedError, match=rf"props\[0\]: sink_m must lie between 0 and 10, got {bad:g}"):
            read_pins(path)
    path.write_text(json.dumps({"props": [{"asset_id": "a", "latitude": 1, "longitude": 2, "sink_m": "4"}]}))
    with pytest.raises(PinnedError, match=r"sink_m must be a number"):
        read_pins(path)
    wreck = _wreck(14.0, 4.0)
    sunk = place_pins([Pin("crashed_helicopter", LAT, LON, 0.0, sink_m=1.5)], {"crashed_helicopter": wreck}, FRAME, EMPTY, square_zone(100))
    assert sunk.nodes[0].matrix[2, 3] == pytest.approx(-1.5) and sunk.colliders[0].matrix[2, 3] == pytest.approx(-1.5)
    assert sunk.records[0]["sink_m"] == 1.5 and "roll_deg" not in sunk.records[0]
    flat = place_pins([Pin("crashed_helicopter", LAT, LON, 0.0)], {"crashed_helicopter": wreck}, FRAME, EMPTY, square_zone(100))
    assert flat.nodes[0].matrix[2, 3] == 0.0 and "sink_m" not in flat.records[0]


def test_pins_of_one_model_share_its_mesh_only_in_the_same_pose() -> None:
    """14 November 2026: one mesh key for every pin of a model drew Super Six-One with Super Six-Four's pose."""
    wreck = _wreck(4.0, 2.0)
    pins = [Pin("crashed_helicopter", LAT, LON, 0.0, roll_deg=35.0, pitch_deg=-10.0),
            Pin("crashed_helicopter", LAT + 0.0005, LON, 0.0, roll_deg=-80.0),
            Pin("crashed_helicopter", LAT - 0.0005, LON, 0.0, roll_deg=-80.0),
            Pin("crashed_helicopter", LAT, LON + 0.0005, 0.0)]
    result = place_pins(pins, {"crashed_helicopter": wreck}, FRAME, EMPTY, square_zone(200))
    assert [n.mesh_key for n in result.nodes] == ["pinned:crashed_helicopter:35:-10", "pinned:crashed_helicopter:-80:0",
                                                  "pinned:crashed_helicopter:-80:0", "pinned:crashed_helicopter"]
    heights = [round(float(np.vstack([p.mesh.vertices for p in n.primitives])[:, 2].max()), 6) for n in result.nodes]
    assert heights[1] == heights[2] != heights[0] and heights[3] == pytest.approx(2.5)
