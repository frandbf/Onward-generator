"""One-storey shop interiors on the synthetic street block around MURCIA_CENTER.

Building 312 holds a bakery POI; 311 and 313 match the stair-interior rule. On the row
block, 606 is a shop with a measured 8 m height over 3 storeys (2.67 m), too low for the door.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from shapely import Point
from shapely.geometry import shape

from onward_generator.detail import config_from_document, detail_scene, load_base, load_detail_config
from onward_generator.detail.config import DEFAULT_CONFIG_PATH
from onward_generator.detail.interiors import LIGHT_BELOW_CEILING_M
from onward_generator.detail.shops import VINYL_INSET_M
from onward_generator.generation import load_generation_config
from tests.integration.test_detail_pipeline import _inside

GEN = load_generation_config()
CFG = load_detail_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
SHOP = "osm-way-312"


def _cfg(**changes: object):
    doc = copy.deepcopy(DOC)
    for path, value in changes.items():
        target = doc
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return config_from_document(doc)


def _detail(inputs: tuple[Path, Path, Path], cfg=CFG):
    scene, heights, base = inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, cfg)


def _obj(d, oid: str):
    return next(o for o in d.objects if o.id == oid)


def _footprint(inputs: tuple[Path, Path, Path], bid: str):
    scene = json.loads(inputs[0].read_text(encoding="utf-8"))
    return shape(next(f for f in scene["features"] if f["id"] == bid)["geometry"])


@pytest.fixture
def shops(detail_inputs: tuple[Path, Path, Path]):
    return _detail(detail_inputs)


def test_a_shop_gets_one_storey_room_with_a_walkable_door_and_no_stair(shops) -> None:
    assert not shops.critical
    assert shops.stats["shop_interiors"] == [SHOP]
    assert SHOP not in shops.stats["interiors"] and sorted(shops.stats["interiors"]) == ["osm-way-311", "osm-way-313"]
    room = _obj(shops, f"interior/{SHOP}").record
    assert room["kind"] == "shop" and room["representative_of_real_interior"] is False
    assert room["accessible_floors"] == 1 and room["roof_access"] is False
    assert [r["id"] for r in room["rooms"]] == [f"{SHOP}/L0"]
    assert [c["type"] for c in room["connections"]] == ["door"]
    assert room["collider_parts"][-1] == "upper_solid"
    assert room["generation"] == {"method": "generic_simplified_interior", "attribute_origin": "generated"}
    assert all(_obj(shops, f"interior/{b}").record["kind"] == "residential" for b in shops.stats["interiors"])
    building = _obj(shops, f"building/{SHOP}")
    openings = building.record["facade"]["openings"]
    assert openings["interior_door"] == 1 and "shop_door" not in openings and openings["shopfront"] > 0
    assert building.record["interior"] is True and building.record["collider"]["source"] == "interior_shell"
    # Walk in through the door; the storey above the ceiling is solid.
    door = room["connections"][0]
    px, py = door["position_m"]
    inside = np.array([px, py + CFG.interiors.wall_thickness_m + 1.0])
    ceiling = room["rooms"][0]["ceiling_z_m"]
    assert ceiling == building.record["facade"]["storeys"]["height_m"]
    assert not any(_inside(m, np.array([px, py + CFG.interiors.wall_thickness_m / 2, 1.0])) for m in building.colliders)
    assert not any(_inside(m, np.array([*inside, 1.0])) for m in building.colliders)
    assert any(_inside(m, np.array([*inside, ceiling + 0.5])) for m in building.colliders)


def test_shop_display_windows_are_see_through_and_carry_their_vinyls(detail_inputs: tuple[Path, Path, Path], shops) -> None:
    room = _obj(shops, f"interior/{SHOP}").record
    building = _obj(shops, f"building/{SHOP}")
    ceiling = room["rooms"][0]["ceiling_z_m"]
    view = [p for p in building.visual if p.material == "glass_view"]
    assert len(view) == 1 and view[0].mesh.triangle_count == 2 * room["through_windows"] == 2 * room["glass_panes"]
    assert room["through_windows"] >= building.record["facade"]["openings"]["shopfront"]
    for prim in building.visual:
        if prim.material == "glass_dark":
            assert prim.mesh.vertices[:, 2].min() >= ceiling - 1e-3  # only the upper floors keep the opaque glass
    vinyl = _detail(detail_inputs, _cfg(shops__vinyl_share=1.0))
    [shop] = _obj(vinyl, f"building/{SHOP}").record["facade"]["shops"]
    fronts = _obj(vinyl, f"building/{SHOP}").record["facade"]["openings"]["shopfront"]
    assert len(shop["vinyls"]) == fronts > 0
    # Each strip is 1 cm in front of the pane at the inner face: a step along its outward normal
    # reaches the outer wall face, and the strip itself is inside the footprint.
    behind = CFG.interiors.wall_thickness_m - VINYL_INSET_M
    footprint = _footprint(detail_inputs, SHOP)
    for strip in shop["vinyls"]:
        x, y, _ = strip["centre_m"]
        nx, ny = strip["normal"]
        assert footprint.exterior.distance(Point(x + nx * behind, y + ny * behind)) < 1e-6
        assert footprint.contains(Point(x, y))
        assert strip["height_m"] == CFG.shops.vinyl_height_m < CFG.shopfront.min_height_m


def test_shop_interior_takes_its_category_colours_and_reports_its_triangles(shops) -> None:
    interior = _obj(shops, f"interior/{SHOP}")
    assert interior.record["shop"] == {"category": "bakery", "wall_material": "interior_shop_bakery",
                                       "floor_material": "interior_shop_tile", "attribute_origin": "generated"}
    assert sorted(p.material for p in interior.visual) == ["interior_shop_bakery", "interior_shop_tile", "interior_wall"]
    cost = sum(p.mesh.triangle_count for p in interior.visual)
    assert interior.record["visual_triangles"] == cost == shops.stats["shop_interior_visual_triangles"]
    assert 0 < cost < 200
    residential = [o for o in shops.objects if o.type == "interior" and o.record["kind"] == "residential"]
    assert shops.stats["interior_visual_triangles"] == sum(o.record["visual_triangles"] for o in residential)
    for o in residential:
        assert {p.material for p in o.visual} == {"interior_wall", "interior_floor", "stair"}
    node = next(n for n in shops.visual_nodes() if n.name == f"interior/{SHOP}")
    assert sorted(p.material for p in node.primitives) == ["interior_shop_bakery", "interior_shop_tile", "interior_wall"]


def test_every_interior_room_has_one_light_within_the_shared_cap(shops) -> None:
    rooms = {o.id: o.light_anchors["rooms"] for o in shops.objects if o.type == "interior"}
    [shop_room] = rooms[f"interior/{SHOP}"]
    ceiling = _obj(shops, f"interior/{SHOP}").record["rooms"][0]["ceiling_z_m"]
    assert shop_room["light_m"][2] == pytest.approx(ceiling - LIGHT_BELOW_CEILING_M)
    total = sum(len(r) for r in rooms.values())
    assert shops.stats["interior_lights"] == {"max": 24, "placed": total, "dropped": []}
    assert not [i for i in shops.issues if i.code == "interior_light_dropped"]
    node = next(n for n in shops.visual_nodes() if n.name == f"interior/{SHOP}")
    assert node.extras["light_anchors"]["rooms"] == [shop_room]


def test_the_light_cap_keeps_the_shops_first_and_records_the_dropped_rooms(detail_inputs: tuple[Path, Path, Path], shops) -> None:
    total = shops.stats["interior_lights"]["placed"]
    one = _detail(detail_inputs, _cfg(interiors__lights_max=1))
    rooms = {o.record["source_ids"][0]: o.light_anchors["rooms"] for o in one.objects if o.type == "interior"}
    assert "light_m" in rooms[SHOP][0]
    dropped = sorted(r["room_id"] for b, rs in rooms.items() if b != SHOP for r in rs)
    assert one.stats["interior_lights"] == {"max": 1, "placed": 1, "dropped": dropped} and len(dropped) == total - 1
    for b, rs in rooms.items():
        for r in rs:
            assert r["probes_m"]
            if b != SHOP:
                assert r["light"] == "over_cap" and "light_m" not in r and "range_m" not in r
    issues = [(i.object_id, i.severity, i.message) for i in one.issues if i.code == "interior_light_dropped"]
    assert issues == [(room, "info", "over interiors.lights_max 1") for room in dropped]
    none = _detail(detail_inputs, _cfg(interiors__lights_max=0))
    assert none.stats["interior_lights"]["placed"] == 0 and len(none.stats["interior_lights"]["dropped"]) == total


def test_shop_interiors_have_their_own_triangle_budget(detail_inputs: tuple[Path, Path, Path], shops) -> None:
    cost = shops.stats["shop_interior_visual_triangles"]
    exact = _detail(detail_inputs, _cfg(interiors__shops__max_triangles=cost, interiors__max_triangles=0))
    assert exact.stats["shop_interiors"] == [SHOP] and exact.stats["interiors"] == []
    short = _detail(detail_inputs, _cfg(interiors__shops__max_triangles=cost - 1))
    assert short.stats["shop_interiors"] == [] and short.stats["shop_interior_visual_triangles"] == 0
    assert sorted(short.stats["interiors"]) == sorted(shops.stats["interiors"])
    skipped = [(i.object_id, i.message) for i in short.issues if i.code == "interior_not_generated"]
    assert skipped == [(SHOP, f"triangle_budget: needs {cost} triangles, {cost - 1} left of shops.max_triangles {cost - 1}")]
    building = _obj(short, f"building/{SHOP}").record
    assert building["interior"] is False and building["facade"]["openings"]["shop_door"] == 1


def test_a_shop_too_large_or_with_shop_interiors_off_gets_no_interior(detail_inputs: tuple[Path, Path, Path]) -> None:
    area = _footprint(detail_inputs, SHOP).area
    assert area == pytest.approx(240.0, abs=0.5)
    edge = _detail(detail_inputs, _cfg(interiors__shops__max_area_m2=area))
    assert edge.stats["shop_interiors"] == [SHOP]
    large = _detail(detail_inputs, _cfg(interiors__shops__max_area_m2=area - 0.5))
    assert large.stats["shop_interiors"] == []
    assert [(i.object_id, i.severity, i.message) for i in large.issues if i.code == "interior_not_generated"] == [
        (SHOP, "info", f"shop_too_large: {area:.1f} m² > max_area_m2 {area - 0.5}")]
    assert _obj(large, f"building/{SHOP}").record["facade"]["openings"]["shop_door"] == 1
    for cfg in (_cfg(interiors__shops__enabled=False), _cfg(interiors__enabled=False)):
        off = _detail(detail_inputs, cfg)
        assert off.stats["shop_interiors"] == [] and SHOP not in off.stats["interiors"]
        assert not [i for i in off.issues if i.object_id == SHOP and i.code == "interior_not_generated"]
        assert _obj(off, f"building/{SHOP}").record["facade"]["openings"]["shop_door"] == 1


def test_a_shop_whose_storey_is_too_low_for_the_door_keeps_its_shop_door(row_inputs: tuple[Path, Path, Path]) -> None:
    row = _detail(row_inputs)
    assert "osm-way-606" not in row.stats["shop_interiors"]
    assert ("osm-way-606", "no_door_position") in [(i.object_id, i.message) for i in row.issues if i.code == "interior_not_generated"]
    assert _obj(row, "building/osm-way-606").record["facade"]["openings"]["shop_door"] == 1


def test_the_shop_budget_goes_to_the_shop_nearest_the_map_centre(row_inputs: tuple[Path, Path, Path]) -> None:
    # 602 (centre x = -25) and 603 (x = -15) are shops of the same size; 603 is nearer the origin.
    row = _detail(row_inputs)
    assert row.stats["shop_interiors"] == ["osm-way-602", "osm-way-603"]
    costs = {b: _obj(row, f"interior/{b}").record["visual_triangles"] for b in row.stats["shop_interiors"]}
    assert costs["osm-way-602"] == costs["osm-way-603"]
    assert row.stats["shop_interior_visual_triangles"] == sum(costs.values())
    one = _detail(row_inputs, _cfg(interiors__shops__max_triangles=costs["osm-way-603"]))
    assert one.stats["shop_interiors"] == ["osm-way-603"]
    assert [i.object_id for i in one.issues if i.code == "interior_not_generated" and "triangle_budget" in i.message] == ["osm-way-602"]
