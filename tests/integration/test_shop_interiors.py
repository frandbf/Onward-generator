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
    # Every display window is frameless shop glass with no pane collider; any other window keeps glass_view and its pane.
    fronts = building.record["facade"]["openings"]["shopfront"]
    [shop_glass] = [p for p in building.visual if p.material == "shop_glass"]
    view = [p.mesh.triangle_count for p in building.visual if p.material == "glass_view"]
    assert shop_glass.mesh.triangle_count == 2 * room["open_shopfronts"] == 2 * fronts > 0
    assert sum(view) == 2 * room["glass_panes"] == 2 * len(building.glass_colliders)
    assert room["through_windows"] == room["open_shopfronts"] + room["glass_panes"]
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


def test_a_shop_too_large_gets_a_band_and_with_shop_interiors_off_no_interior(detail_inputs: tuple[Path, Path, Path]) -> None:
    area = _footprint(detail_inputs, SHOP).area
    assert area == pytest.approx(240.0, abs=0.5)
    edge = _detail(detail_inputs, _cfg(interiors__shops__max_area_m2=area, interiors__shops__band_depth_m=3.0))
    assert edge.stats["shop_interiors"] == [SHOP] and "room_depth_m" not in _obj(edge, f"interior/{SHOP}").record
    large = _detail(detail_inputs, _cfg(interiors__shops__max_area_m2=area - 0.5, interiors__shops__band_depth_m=3.0))
    assert large.stats["shop_interiors"] == [SHOP]
    assert not [i for i in large.issues if i.code == "interior_not_generated" and i.object_id == SHOP]
    banded = _obj(large, f"interior/{SHOP}").record
    whole = _obj(edge, f"interior/{SHOP}").record
    assert banded["room_depth_m"] == 3.0 and banded["rooms"][0]["area_m2"] < whole["rooms"][0]["area_m2"]
    assert banded["checks"]["collider_defects"] == 0
    # The band is for shops: stair interiors above max_area_m2 keep their whole floors.
    small = _detail(detail_inputs, _cfg(interiors__shops__max_area_m2=100.0, interiors__shops__band_depth_m=3.0))
    assert sorted(small.stats["interiors"]) == ["osm-way-311", "osm-way-313"]
    for bid in small.stats["interiors"]:
        record = _obj(small, f"interior/{bid}").record
        assert "room_depth_m" not in record and _footprint(detail_inputs, bid).area > 100.0
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


# A one-storey bakery with a flat roof and a one-storey pharmacy with a hipped roof, apart on one street.
ONE_STOREY = {
    801: ([(-22, 6), (-8, 6), (-8, 19), (-22, 19)], {"building": "retail", "building:levels": "1"}),
    802: ([(4, 6), (12, 6), (12, 14), (4, 14)], {"building": "yes", "building:levels": "1", "roof:shape": "hipped"}),
}
ONE_STOREY_POIS = {901: ((-14, 7), {"shop": "bakery"}), 902: ((8, 7), {"amenity": "pharmacy"})}


@pytest.fixture(scope="module")
def one_storey_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    from tests.conftest import _detail_inputs

    return _detail_inputs(tmp_path_factory.mktemp("one_storey"), ONE_STOREY, ONE_STOREY_POIS)


@pytest.mark.parametrize(("bid", "category", "roof"), [("osm-way-801", "bakery", "flat_parapet"), ("osm-way-802", "pharmacy", "hipped")])
def test_a_one_storey_shop_has_its_room_under_a_closed_roof(one_storey_inputs: tuple[Path, Path, Path], bid: str,
                                                           category: str, roof: str) -> None:
    d = _detail(one_storey_inputs)
    assert not d.critical
    assert sorted(d.stats["shop_interiors"]) == ["osm-way-801", "osm-way-802"]
    building = _obj(d, f"building/{bid}")
    interior = _obj(d, f"interior/{bid}").record
    height = building.record["height"]["height_m"]
    assert building.record["facade"]["storeys"]["count"] == 1 and building.record["roof"]["type"] == roof
    assert interior["kind"] == "shop" and interior["shop"]["category"] == category and interior["accessible_floors"] == 1
    assert interior["collider_parts"][-1] == "roof_slab" and "upper_solid" not in interior["collider_parts"]
    [room] = interior["rooms"]
    assert room["ceiling_z_m"] == pytest.approx(height - CFG.interiors.slab_m)
    # The roof stays closed and watertight; every collider is closed.
    assert building.record["checks"]["facade_open_edges"] == 0 and interior["checks"]["collider_defects"] == 0
    # Walk in through the door to the middle of the room; the roof slab above it is solid.
    [anchor] = _obj(d, f"interior/{bid}").light_anchors["rooms"]
    door = np.array(interior["connections"][0]["position_m"])
    centre = np.array(anchor["light_m"][:2])
    inward = (centre - door) / np.linalg.norm(centre - door)
    t = CFG.interiors.wall_thickness_m
    for step in (t / 2, t + 1.0):
        assert not any(_inside(m, np.array([*(door + inward * step), 1.0])) for m in building.colliders)
    assert not any(_inside(m, np.array([*centre, 1.0])) for m in building.colliders)
    assert any(_inside(m, np.array([*centre, height - 0.1])) for m in building.colliders)
    # See-through shop windows, and a light under the roof slab.
    assert interior["through_windows"] > 0
    assert [p.mesh.triangle_count for p in building.visual if p.material == "shop_glass"] == [2 * interior["through_windows"]]
    assert interior["open_shopfronts"] == interior["through_windows"] and not building.glass_colliders
    assert anchor["light_m"][2] == pytest.approx(room["ceiling_z_m"] - LIGHT_BELOW_CEILING_M)


# Apart on one street: a shop round a courtyard (820); a shop of three parts (830), the third 3.4 m square, too
# narrow for a shopfront; a building with two shops (840); a block of flats in two parts (850); and a bakery
# tagged on the building itself, with no POI (860).
COMPLEX = {
    840: ([(30, 6), (48, 6), (48, 18), (30, 18)], {"building": "retail", "building:levels": "2"}),
    860: ([(-70, 6), (-56, 6), (-56, 16), (-70, 16)], {"building": "retail", "building:levels": "2", "shop": "bakery"}),
}
COMPLEX_RELATIONS = {
    820: ([([(-40, 6), (-16, 6), (-16, 30), (-40, 30)], "outer"), ([(-32, 14), (-24, 14), (-24, 22), (-32, 22)], "inner")],
          {"building": "retail", "building:levels": "2"}),
    830: ([([(0, 6), (12, 6), (12, 16), (0, 16)], "outer"), ([(16, 6), (26, 6), (26, 14), (16, 14)], "outer"),
           ([(-10, 6), (-6.6, 6), (-6.6, 9.4), (-10, 9.4)], "outer")],
          {"building": "retail", "building:levels": "2"}),
    850: ([([(56, 6), (70, 6), (70, 18), (56, 18)], "outer"), ([(74, 6), (86, 6), (86, 18), (74, 18)], "outer")],
          {"building": "apartments", "building:levels": "4"}),
}
COMPLEX_POIS = {
    921: ((-28, 7), {"shop": "bakery"}),
    931: ((6, 7), {"amenity": "cafe"}),
    941: ((32.7, 7), {"shop": "bakery"}),
    942: ((45.3, 7), {"amenity": "pharmacy"}),
}


@pytest.fixture(scope="module")
def complex_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    from tests.conftest import _detail_inputs

    return _detail_inputs(tmp_path_factory.mktemp("complex"), COMPLEX, COMPLEX_POIS, COMPLEX_RELATIONS)


@pytest.fixture(scope="module")
def complex_shops(complex_inputs: tuple[Path, Path, Path]):
    return _detail(complex_inputs)


def _free(building, x: float, y: float, z: float = 1.0) -> bool:
    return not any(_inside(m, np.array([x, y, z])) for m in building.colliders)


def test_shops_round_a_courtyard_of_two_parts_and_of_two_shops_all_get_rooms(complex_shops) -> None:
    d = complex_shops
    assert not d.critical and not [i for i in d.issues if i.code in ("interior_not_generated", "interior_window_omitted")]
    assert d.stats["shop_interiors"] == ["osm-relation-820", "osm-relation-830", "osm-way-840", "osm-way-860"]
    costs = {b: _obj(d, f"interior/{b}").record["visual_triangles"] for b in d.stats["shop_interiors"]}
    assert d.stats["shop_interior_visual_triangles"] == sum(costs.values()) < CFG.interiors.shops.max_triangles
    for bid in d.stats["shop_interiors"]:
        interior = _obj(d, f"interior/{bid}").record
        building = _obj(d, f"building/{bid}").record
        assert interior["checks"]["collider_defects"] == 0 and building["checks"]["facade_open_edges"] == 0
        assert interior["through_windows"] == building["facade"]["openings"]["shopfront"] > 0


def test_a_courtyard_shop_room_goes_round_the_courtyard(complex_inputs: tuple[Path, Path, Path], complex_shops) -> None:
    bid = "osm-relation-820"
    interior = _obj(complex_shops, f"interior/{bid}")
    building = _obj(complex_shops, f"building/{bid}")
    [room] = interior.record["rooms"]
    t = CFG.interiors.wall_thickness_m
    assert room["area_m2"] == pytest.approx((24 - 2 * t) ** 2 - (8 + 2 * t) ** 2, abs=0.01) and room["reachable"] is True
    assert room["shop"]["category"] == "bakery"
    # The courtyard is open air, the band round it is solid, and the room beyond the courtyard is free.
    assert _free(building, -28.0, 18.0) and not _free(building, -24.0 + t / 2, 18.0) and _free(building, -20.0, 18.0)
    door = interior.record["connections"][0]["position_m"]
    assert _free(building, door[0], door[1] + t / 2) and _free(building, door[0], door[1] + t + 1.0)
    [anchor] = interior.light_anchors["rooms"]
    footprint = _footprint(complex_inputs, bid)
    assert not footprint.contains(Point(-28.0, 18.0)) and footprint.contains(Point(anchor["light_m"][:2]))  # not in the courtyard
    # With courtyard openings on, the courtyard windows are see-through like the street ones.
    open_yard = _detail(complex_inputs, _cfg(exposure__courtyard_openings=True))
    yard = _obj(open_yard, f"interior/{bid}").record
    assert yard["checks"]["collider_defects"] == 0 and not open_yard.critical
    assert yard["through_windows"] > interior.record["through_windows"] and yard["omitted_windows"] == 0
    # Panes sit on the room side of the wall, so the courtyard's are within t of its ring (x -32…-24, y 14…22).
    # The courtyard's openings are display windows too: shop glass, with no pane collider.
    [panes] = [p.mesh.vertices for p in _obj(open_yard, f"building/{bid}").visual if p.material == "shop_glass"]
    yard_panes = (np.abs(panes[:, 0] + 28.0) <= 4.0 + t + 0.01) & (np.abs(panes[:, 1] - 18.0) <= 4.0 + t + 0.01)
    assert yard_panes.sum() == 4 * (yard["through_windows"] - interior.record["through_windows"])
    assert yard["open_shopfronts"] == yard["through_windows"] and yard["glass_panes"] == 0


def test_a_shop_of_two_parts_has_a_room_in_each_and_one_door(complex_shops) -> None:
    bid = "osm-relation-830"
    interior = _obj(complex_shops, f"interior/{bid}")
    building = _obj(complex_shops, f"building/{bid}")
    rooms = interior.record["rooms"]
    assert [(r["id"], r["reachable"], r["shops"]) for r in rooms] == [(f"{bid}/L0", True, [0]), (f"{bid}/L0-1", False, [])]
    assert [r["shop"]["category"] for r in rooms] == ["bar", "bar"]
    assert [c["to"] for c in interior.record["connections"]] == [f"{bid}/L0"]
    assert [a["room_id"] for a in interior.light_anchors["rooms"]] == [f"{bid}/L0", f"{bid}/L0-1"]
    # The second part (x 16-26) is hollow behind its see-through shopfronts, and closed at the street.
    assert _free(building, 21.0, 10.0) and _free(building, 6.0, 11.0)
    panes = [p for p in building.visual if p.material == "shop_glass"][0].mesh.vertices
    assert ((panes[:, 0] > 16.0) & (panes[:, 0] < 26.0)).any() and ((panes[:, 0] > 0.0) & (panes[:, 0] < 12.0)).any()
    assert building.record["facade"]["openings"]["interior_door"] == 1
    # The third part has windows only: it has no room, stays solid, and its ground-floor glass stays dark.
    assert not _free(building, -8.3, 7.7)
    dark = [p.mesh.vertices for p in building.visual if p.material == "glass_dark"][0]
    third = (dark[:, 0] > -10.0 - 1e-3) & (dark[:, 0] < -6.6 + 1e-3)
    assert third.any() and dark[third, 2].min() < 1.0


def test_a_shop_tagged_on_its_building_takes_its_colours(complex_shops) -> None:
    interior = _obj(complex_shops, "interior/osm-way-860").record
    assert [(r["shops"], r["shop"]["category"]) for r in interior["rooms"]] == [([0], "bakery")]
    assert _obj(complex_shops, "building/osm-way-860").record["facade"]["shops"][0]["poi_id"] is None


def test_two_shops_in_one_building_get_two_rooms_colours_lights_and_doors(complex_inputs: tuple[Path, Path, Path], complex_shops) -> None:
    bid = "osm-way-840"
    interior = _obj(complex_shops, f"interior/{bid}")
    building = _obj(complex_shops, f"building/{bid}")
    rooms = interior.record["rooms"]
    # Bays of 4.2 m: the bakery has bay 0 (x = 32.7), the main door bay 2 (x = 41.1), the pharmacy bay 3 (x = 45.3).
    assert [(r["id"], r["shop"]["category"], r["shops"], r["reachable"]) for r in rooms] == [
        (f"{bid}/L0", "pharmacy", [1], True), (f"{bid}/L0-1", "bakery", [0], True)]
    assert interior.record["shop"]["category"] == "pharmacy"
    assert sorted(p.material for p in interior.visual) == [
        "interior_shop_bakery", "interior_shop_pharmacy", "interior_shop_tile", "interior_wall"]
    doors = [(c["to"], c["position_m"]) for c in interior.record["connections"]]
    assert doors == [(f"{bid}/L0", [41.1, 6.0]), (f"{bid}/L0-1", [32.7, 6.0])]
    assert [a["room_id"] for a in interior.light_anchors["rooms"]] == [r["id"] for r in rooms]
    assert all("light_m" in a for a in interior.light_anchors["rooms"])
    openings = building.record["facade"]["openings"]
    assert openings["interior_door"] == 2 and "shop_door" not in openings
    t = CFG.interiors.wall_thickness_m
    for x, _ in (d[1] for d in doors):
        assert _free(building, x, 6.0 + t / 2) and _free(building, x, 6.0 + t + 1.0)
    # The partition sits on the bay boundary at x = 39, between the two rooms.
    assert not _free(building, 39.0, 12.0) and _free(building, 38.8, 12.0) and _free(building, 39.2, 12.0)
    # Each shop keeps its sign on its own bay: the bakery's over its door.
    bakery, pharmacy = (s["sign"]["centre_m"][0] for s in building.record["facade"]["shops"])
    assert abs(bakery - 32.7) < 2.1 and abs(pharmacy - 45.3) < 2.1  # within half a bay
    vinyl = _detail(complex_inputs, _cfg(shops__vinyl_share=1.0))
    shops = _obj(vinyl, f"building/{bid}").record["facade"]["shops"]
    assert sum(len(s["vinyls"]) for s in shops) == _obj(vinyl, f"building/{bid}").record["facade"]["openings"]["shopfront"]


def test_a_stair_interior_still_refuses_a_footprint_of_several_parts(complex_inputs: tuple[Path, Path, Path]) -> None:
    flats = _detail(complex_inputs, _cfg(interiors__include_ids=["osm-relation-850"]))
    assert "osm-relation-850" not in flats.stats["interiors"]
    assert [(i.object_id, i.message) for i in flats.issues if i.code == "interior_not_generated"] == [
        ("osm-relation-850", "footprint_not_a_simple_polygon")]


def test_shop_rooms_share_the_light_cap_nearest_the_centre_first(complex_inputs: tuple[Path, Path, Path], complex_shops) -> None:
    assert complex_shops.stats["interior_lights"] == {"max": 24, "placed": 6, "dropped": []}
    capped = _detail(complex_inputs, _cfg(interiors__lights_max=3))
    # Lights near (-36, 18), (6, 11), (21, 10), (43.4, 12), (34.6, 12) and (-63, 11): the three farthest go dark.
    assert capped.stats["interior_lights"] == {
        "max": 3, "placed": 3, "dropped": ["osm-relation-820/L0", "osm-way-840/L0", "osm-way-860/L0"]}


# Apart on one street: a kiosk whose room (4.4 x 3.4 m) is under the stair interior's 20 m²; a shop whose
# back half hangs on a 0.4 m neck; a 30 x 25 m shop, above max_area_m2; and a block of flats as small as the kiosk.
TIGHT = {
    870: ([(-60, 6), (-55, 6), (-55, 10), (-60, 10)], {"building": "retail", "building:levels": "1"}),
    871: ([(-40, 6), (-20, 6), (-20, 14), (-29.8, 14), (-29.8, 16), (-20, 16), (-20, 24), (-40, 24), (-40, 16),
           (-30.2, 16), (-30.2, 14), (-40, 14)], {"building": "retail", "building:levels": "1"}),
    872: ([(0, 6), (30, 6), (30, 31), (0, 31)], {"building": "retail", "building:levels": "1"}),
    873: ([(40, 6), (45, 6), (45, 10), (40, 10)], {"building": "apartments", "building:levels": "4"}),
}
TIGHT_POIS = {970: ((-57.5, 7), {"shop": "kiosk"}), 971: ((-35, 7), {"shop": "books"}), 972: ((15, 7), {"shop": "supermarket"})}


@pytest.fixture(scope="module")
def tight_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    from tests.conftest import _detail_inputs

    return _detail_inputs(tmp_path_factory.mktemp("tight"), TIGHT, TIGHT_POIS)


def test_a_kiosk_a_necked_shop_and_a_large_shop_all_get_rooms(tight_inputs: tuple[Path, Path, Path]) -> None:
    d = _detail(tight_inputs)
    assert not d.critical and not [i for i in d.issues if i.code == "interior_not_generated"]
    assert d.stats["shop_interiors"] == ["osm-way-870", "osm-way-871", "osm-way-872"]
    t = CFG.interiors.wall_thickness_m
    rooms = {b: _obj(d, f"interior/{b}").record for b in d.stats["shop_interiors"]}
    assert all(r["checks"]["collider_defects"] == 0 for r in rooms.values())
    assert rooms["osm-way-870"]["rooms"][0]["area_m2"] == pytest.approx((5 - 2 * t) * (4 - 2 * t), abs=0.01)
    assert "room_depth_m" not in rooms["osm-way-870"] and "room_depth_m" not in rooms["osm-way-871"]
    # The necked shop's room is the half behind its door; the other half stays solid.
    assert rooms["osm-way-871"]["rooms"][0]["area_m2"] == pytest.approx((20 - 2 * t) * (8 - 2 * t), abs=0.01)
    necked = _obj(d, "building/osm-way-871")
    assert _free(necked, -35, 10) and not _free(necked, -35, 20)
    # The large shop's room is the 12 m band behind its frontage, closed by a back wall.
    depth = CFG.interiors.shops.band_depth_m
    assert rooms["osm-way-872"]["room_depth_m"] == depth
    assert rooms["osm-way-872"]["rooms"][0]["area_m2"] == pytest.approx((30 - 2 * t) * (depth - t), abs=0.05)
    large = _obj(d, "building/osm-way-872")
    assert _free(large, 15, 6 + depth - 0.5) and not _free(large, 15, 6 + depth + 0.5)
    # With the stair interior's 20 m² floor the kiosk gets no room.
    strict = _detail(tight_inputs, _cfg(interiors__shops__min_room_area_m2=20.0))
    refused = [(i.object_id, i.message) for i in strict.issues if i.code == "interior_not_generated"]
    assert ("osm-way-870", "inner_space_too_small") in refused
    assert strict.stats["shop_interiors"] == ["osm-way-871", "osm-way-872"]
    # The stair interior keeps its 20 m² floor: the flats the size of the kiosk get no room.
    flats = _detail(tight_inputs, _cfg(interiors__include_ids=["osm-way-873"]))
    assert ("osm-way-873", "inner_space_too_small") in [(i.object_id, i.message) for i in flats.issues if i.code == "interior_not_generated"]
