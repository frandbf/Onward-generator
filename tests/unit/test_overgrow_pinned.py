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

from onward_generator.generation.mesh import box
from onward_generator.geo.projection import frame_for_origin
from onward_generator.overgrow.catalog import ASSETS_BY_ID
from onward_generator.overgrow.library import Prop
from onward_generator.overgrow.pinned import (
    BUILDING_GAP_M,
    SEARCH_DEFAULT_M,
    SEARCH_MAX_M,
    SEARCH_STEP_M,
    Pin,
    PinnedError,
    frame_of,
    keep_out_circles,
    place_pins,
    plan,
    read_pins,
)
from onward_generator.overgrow.zone import square_zone

LAT, LON = 2.047458, 45.326639


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 90, 90)).save(out, format="PNG")
    return out.getvalue()


def _wreck(length: float = 14.0, width: float = 4.0) -> Prop:
    mesh = box(-length / 2, length / 2, -width / 2, width / 2, 0.0, 2.5)
    spec = replace(ASSETS_BY_ID["covered_car"], id="crashed_helicopter")
    return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2)), _png())


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
    ({"props": [{"asset_id": "a", "latitude": 1, "longitude": 1, "yaw_deg": "n"}]}, "yaw_deg must be a number"),
])
def test_a_bad_file_is_refused(tmp_path: Path, document: object, message: str) -> None:
    path = tmp_path / "pinned_props.json"
    path.write_text(document if isinstance(document, str) else json.dumps(document))
    with pytest.raises(PinnedError, match=message):
        read_pins(path)


def test_the_frame_comes_from_the_geo_scene() -> None:
    frame = frame_of({"frame": {"origin_wgs84": [LON, LAT], "crs": FRAME.crs}})
    point = frame.to_local(Point(LON, LAT))
    assert (round(point.x, 6), round(point.y, 6)) == (0.0, 0.0)
    for geo in ({}, {"frame": {"origin_wgs84": [LON], "crs": "EPSG:32638"}}, {"frame": {"origin_wgs84": [LON, LAT]}}):
        with pytest.raises(PinnedError, match="names no frame"):
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
    with pytest.raises(PinnedError, match=r"^pinned prop crashed_helicopter \(no label\) is not in the library$"):
        place_pins(pins, {}, FRAME, EMPTY, square_zone(100))
    walled = STRtree([shapely_box(-50, -50, 50, 50)])
    with pytest.raises(PinnedError, match=r"finds no free spot within 30 m of 2.047458, 45.326639: raise search_m or move the point$"):
        place_pins([Pin("crashed_helicopter", LAT, LON)], {"crashed_helicopter": _wreck()}, FRAME, walled, square_zone(100))
    with pytest.raises(PinnedError, match="finds no free spot"):  # outside the zone
        place_pins([Pin("crashed_helicopter", LAT + 0.01, LON, search_m=5)], {"crashed_helicopter": _wreck()}, FRAME, EMPTY, square_zone(100))


def test_the_plan_and_the_keep_out_circle() -> None:
    footprint = plan(_wreck(10.0, 2.0).mesh)
    assert footprint.area == pytest.approx(20.0)
    ((x, y, r),) = keep_out_circles([footprint])
    assert (x, y) == (0.0, 0.0) and r == pytest.approx(math.hypot(5, 1), abs=1e-3)
    assert keep_out_circles([]) == []
    assert isinstance(Polygon(), Polygon)
