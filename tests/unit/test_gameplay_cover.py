"""Cover on long open sightlines, the pure placement rules (plan C7, ADR-032 C7 amendment).

Local metres on synthetic ground; the integration test runs the stage around MURCIA_CENTER.
"""

from __future__ import annotations

import dataclasses
import math
from typing import Any

import numpy as np
import pytest
from shapely import LineString, Point, STRtree, box, union_all
from shapely.geometry.base import BaseGeometry

from onward_generator.gameplay import cover as module
from onward_generator.gameplay.cover import (
    COVER_MAX_ADDED_DRAWS,
    COVER_MAX_ADDED_TRIS,
    MAX_COVER_PROPS,
    MAX_NEW_COVER_ASSETS,
    NO_PROP,
    NO_SPOT,
    NO_TARGET,
    Caps,
    CoverPlan,
    CoverProp,
    CoverSite,
    Placement,
    Spot,
    cap_reason,
    choose_cover,
    cover_props,
    footprint,
    joined,
    keep_out,
    keeps_passage,
    note,
    pose,
    prop_reason,
    split_reason,
    spots,
    target_reason,
    triangle_cap,
)
from onward_generator.gameplay.report import OpenPoint
from onward_generator.generation.mesh import Mesh
from onward_generator.generation.mesh import box as mesh_box
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.library import Prop, PropPart
from onward_generator.overgrow.zone import Zone
from tests.conftest import MURCIA_CENTER

SQUARE = box(-50, -50, 50, 50)


def _prop(asset_id: str = "barrier", role: str = "barrier", size: tuple[float, float, float] = (1.0, 0.5, 1.0), *,
          category: str = "barriers", anchor: str = "ground", parts: int = 1) -> Prop:
    length, width, height = size
    mesh = mesh_box(-length / 2, length / 2, -width / 2, width / 2, 0.0, height)
    spec = AssetSpec(asset_id, category, role, ("sidewalk",), anchor, "test")
    pieces = tuple(PropPart(mesh.triangle_count * (i + 1) // parts, b"png%d" % i) for i in range(parts)) if parts > 1 else ()
    return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2), dtype=np.float32), b"png", parts=pieces)


def _cover(asset_id: str = "barrier", size: tuple[float, float, float] = (1.0, 0.5, 1.0), *, in_scene: bool = True,
           role: str = "barrier", parts: int = 1) -> CoverProp:
    props, refused = cover_props([_prop(asset_id, role, size, parts=parts)], {asset_id: "CC0-1.0"}, frozenset({asset_id} if in_scene else ()))
    assert refused == {} and len(props) == 1
    return props[0]


def _site(ground: BaseGeometry = SQUARE, **values: Any) -> CoverSite:
    buildings = values.pop("buildings", [])
    lanes = values.pop("lanes", [])
    site = CoverSite(
        zone=Zone("test", box(-500, -500, 500, 500)),
        buildings=STRtree(buildings or [Point(1.0e9, 1.0e9)]),
        lanes=lanes, lane_tree=STRtree(lanes) if lanes else None,
        spawns=values.pop("spawns", []),
        free=values.pop("free", ground), ground=ground, islands=values.pop("islands", ground),
    )
    return dataclasses.replace(site, **values)


def _target(x: float = 0.0, y: float = 0.0, *, to: tuple[float, float] | None = None, length: float = 100.0) -> OpenPoint:
    return OpenPoint((x, y), to or (x + length, y), length)


def _at(prop: CoverProp, x: float, y: float, yaw: float = 0.0) -> np.ndarray:
    return pose(prop, Spot(x, y, yaw))


# ------------------------------------------------------------------------------------------------ props


def test_the_cover_rule_keeps_solid_street_props_and_names_why_others_are_left_out() -> None:
    props = [
        _prop("barrel", "barrel", (0.6, 0.6, 0.9), category="waste"),
        _prop("barrier", "barrier", (2.0, 0.6, 1.0)),
        _prop("low", "barrier", (1.0, 0.6, 0.49)),
        _prop("thin", "barrier", (1.0, 0.39, 1.0)),
        _prop("long", "barrier", (3.01, 0.6, 1.0)),
        _prop("plant", "plant", (1.0, 1.0, 1.0), category="vegetation"),
        _prop("wall_cam", "barrier", (1.0, 1.0, 1.0), anchor="facade"),
        _prop("house", "building", (2.0, 2.0, 2.0), category="structures"),
        _prop("bench", "bench", (1.5, 0.6, 0.9), category="furniture"),
        _prop("nolicence", "barrel", (0.6, 0.6, 0.9), category="waste"),
        _prop("debris", "debris", (0.4, 0.4, 0.5), category="waste"),
        _prop("long_ok", "barrier", (3.0, 0.6, 1.0)),
    ]
    licences = {p.spec.id: "CC0-1.0" for p in props if p.spec.id != "nolicence"}
    kept, refused = cover_props(props, licences, frozenset())
    # All four are under 1.2 m and have 12 triangles: the id decides.
    assert [p.asset_id for p in kept] == ["barrel", "barrier", "debris", "long_ok"]
    assert refused == {
        "bench": "not_a_cover_role", "house": "not_a_cover_role", "long": "too_long", "low": "too_low",
        "nolicence": "no_licence", "plant": "not_a_cover_role", "thin": "too_thin", "wall_cam": "not_on_the_ground",
    }
    assert list(refused) == sorted(refused)


def test_a_vegetation_debris_row_is_not_solid() -> None:
    assert prop_reason(_prop("moss", "debris", (1.0, 1.0, 1.0), category="vegetation"), "CC0-1.0") == "not_solid"
    empty = dataclasses.replace(_prop(), mesh=Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)))
    assert prop_reason(empty, "CC0-1.0") == "no_mesh"
    assert prop_reason(_prop(), "CC0-1.0") is None


def test_props_rank_in_scene_then_sight_blockers_then_fewer_triangles_then_id() -> None:
    tall = _prop("b_tall", size=(1.0, 0.5, 1.2))
    short = _prop("a_short", size=(1.0, 0.5, 1.19))
    many = dataclasses.replace(_prop("c_many", size=(1.0, 0.5, 1.3)), mesh=_prop(size=(1.0, 0.5, 1.3)).mesh)
    licences = {name: "CC0-1.0" for name in ("b_tall", "a_short", "c_many", "d_scene", "e_same")}
    scene = _prop("d_scene", size=(1.0, 0.5, 0.6))
    same = _prop("e_same", size=(1.0, 0.5, 1.2))
    kept, _ = cover_props([short, many, tall, scene, same], licences, frozenset({"d_scene"}))
    assert [p.asset_id for p in kept] == ["d_scene", "b_tall", "c_many", "e_same", "a_short"]
    assert kept[0].key() == (False, True, 12, "d_scene")


def test_a_prop_deeper_than_long_turns_a_quarter_and_rests_centred_on_the_ground() -> None:
    prop = _prop(size=(0.5, 2.0, 1.0))
    moved = dataclasses.replace(prop, mesh=Mesh(prop.mesh.vertices + np.array([3.0, -2.0, 0.4]), prop.mesh.triangles))
    item = _cover_of(moved)
    assert (item.length_m, item.depth_m, item.height_m) == pytest.approx((2.0, 0.5, 1.0))
    posed = (item.local @ np.c_[moved.mesh.vertices, np.ones(len(moved.mesh.vertices))].T).T[:, :3]
    assert np.allclose(posed.min(axis=0), [-1.0, -0.25, 0.0]) and np.allclose(posed.max(axis=0), [1.0, 0.25, 1.0])
    print_ = footprint(item, _at(item, 10.0, 5.0))
    assert print_.bounds == pytest.approx((9.0, 4.75, 11.0, 5.25))
    turned = footprint(item, _at(item, 10.0, 5.0, math.pi / 2))
    assert turned.bounds == pytest.approx((9.75, 4.0, 10.25, 6.0))
    raised = pose(item, Spot(1.0, 2.0, 0.0), 0.3)
    assert (raised @ np.c_[moved.mesh.vertices, np.ones(len(moved.mesh.vertices))].T)[2].min() == pytest.approx(0.3)


def _cover_of(prop: Prop) -> CoverProp:
    kept, refused = cover_props([prop], {prop.spec.id: "CC0-1.0"}, frozenset())
    assert refused == {}
    return kept[0]


def test_parts_and_triangles_are_counted_per_copy() -> None:
    item = _cover(parts=3)
    assert item.parts == 3 and item.triangles == 12 and item.licence == "CC0-1.0" and item.asset_id == "barrier"


# ------------------------------------------------------------------------------------------------ spots and targets


def test_spots_have_a_fixed_order_and_a_quarter_turn_across_the_ray() -> None:
    east = spots(_target(10.0, 20.0))
    assert [(s.x, s.y) for s in east] == [(11.0, 20.0), (11.0, 21.0), (11.0, 19.0), (10.0, 20.0), (10.0, 21.0), (10.0, 19.0)]
    assert {s.yaw for s in east} == {math.pi / 2}
    north = spots(_target(0.0, 0.0, to=(0.0, 80.0)))
    assert [(s.x, s.y) for s in north] == [(0.0, 1.0), (-1.0, 1.0), (1.0, 1.0), (0.0, 0.0), (-1.0, 0.0), (1.0, 0.0)]
    assert {s.yaw for s in north} == {0.0}
    # A diagonal a little closer to east lays the long side north-south.
    assert {s.yaw for s in spots(_target(0.0, 0.0, to=(10.0, 9.9)))} == {math.pi / 2}
    assert {s.yaw for s in spots(_target(0.0, 0.0, to=(9.9, 10.0)))} == {0.0}
    assert all(math.dist(_target(10.0, 20.0).point, (s.x, s.y)) <= math.sqrt(2) + 1e-9 for s in east)


def _placed(x: float, y: float, prop: CoverProp | None = None) -> Placement:
    item = prop or _cover()
    matrix = _at(item, x, y)
    return Placement(item, Spot(x, y, 0.0), matrix, footprint(item, matrix), _target(x, y))


def test_a_target_near_a_placed_prop_is_skipped_by_spacing_or_reach() -> None:
    placed = [_placed(0.0, 0.0)]
    assert target_reason(_target(11.9, 0.0), placed) == "spacing"
    assert target_reason(_target(12.0, 0.0), placed) is None
    assert target_reason(_target(12.1, 0.0), placed) is None
    # The 1 m by 0.5 m box ends at x = 0.5: reach is measured to its box.
    assert target_reason(_target(2.0, 0.0), placed) == "covered"
    assert target_reason(_target(2.01, 0.0), placed) == "spacing"
    assert target_reason(_target(2.0, 0.0), []) is None


# ------------------------------------------------------------------------------------------------ passage


def test_the_passage_test_keeps_every_eroded_place_joined() -> None:
    corridor = box(0, 0, 20, 3)
    assert joined(corridor, [box(9.5, 0, 10.5, 1.0)])
    assert joined(corridor, [box(9.5, 0, 10.5, 1.8)])
    assert not joined(corridor, [box(9.5, 0, 10.5, 1.81)])
    assert joined(corridor, [])
    # A piece that fills a whole eroded place loses it; one that leaves a part of it keeps it.
    rooms = union_all([box(0, 0, 3, 3), box(10, 0, 13, 3)])
    assert not joined(rooms, [box(9.5, -0.5, 13.5, 3.5)])
    assert joined(rooms, [box(9.5, -0.5, 11.0, 3.5)])
    assert joined(box(0, 0, 0.5, 0.5), [box(0, 0, 0.1, 0.1)])


@pytest.mark.parametrize(("top", "kept"), [(1.0, True), (1.8, True), (1.81, False)])
def test_a_prop_leaves_at_least_1_2_m_of_a_corridor(top: float, kept: bool) -> None:
    site = _site(box(0, 0, 20, 3))
    assert keeps_passage(site, box(9.5, 0, 10.5, top), (10.0, top / 2)) is kept


def test_a_prop_in_a_courtyard_neck_is_refused_and_in_an_open_square_kept() -> None:
    courtyards = union_all([box(0, 0, 10, 10), box(10, 4, 14, 6), box(14, 0, 24, 10)])
    site = _site(courtyards)
    assert not keeps_passage(site, box(11.5, 4, 12.5, 5.0), (12.0, 4.5))
    assert keeps_passage(site, box(11.5, 4, 12.5, 4.5), (12.0, 4.25))
    assert keeps_passage(site, box(4.5, 4.5, 5.5, 5.5), (5.0, 5.0))


def test_an_existing_solid_or_placed_prop_narrows_the_passage() -> None:
    corridor = box(0, 0, 20, 3)
    prop = box(9.5, 0, 10.5, 1.0)
    assert keeps_passage(_site(corridor), prop, (10.0, 0.5))
    assert keeps_passage(_site(corridor, solids=[box(9, 2.2, 11, 3)]), prop, (10.0, 0.5))
    assert not keeps_passage(_site(corridor, solids=[box(9, 2.19, 11, 3)]), prop, (10.0, 0.5))
    assert not keeps_passage(_site(corridor), prop, (10.0, 0.5), [box(9, 2.19, 11, 3)])
    # A solid beyond the 6 m disc is left out.
    assert keeps_passage(_site(corridor, solids=[box(17, 0, 18, 3)]), prop, (10.0, 0.5))


# ------------------------------------------------------------------------------------------------ keep-outs


def test_a_spot_in_the_open_passes_every_keep_out() -> None:
    item = _cover()
    assert keep_out(_site(), item, _at(item, 0.0, 0.0)) is None


@pytest.mark.parametrize(("key", "radius", "reason"), [
    ("spawns", 5.0, "spawn_point"),
    ("ammo", 3.0, "ammo_box"),
    ("climbs", 3.0, "climb_foot"),
    ("doors", 3.0, "door"),
])
def test_each_point_keep_out_refuses_inside_its_radius(key: str, radius: float, reason: str) -> None:
    item = _cover(size=(0.5, 0.5, 1.0))
    site = _site(**{key: [(0.0, 0.0)]})
    assert keep_out(site, item, _at(item, radius - 0.1, 0.0)) == reason
    assert keep_out(site, item, _at(item, radius + 0.1, 0.0)) is None


def test_load_spawns_keys_are_spawn_points() -> None:
    from onward_generator.overgrow.dressing import SPAWN_CLEAR_M

    assert SPAWN_CLEAR_M == 5.0


def test_a_hunt_route_keeps_its_line_and_its_points_clear() -> None:
    item = _cover(size=(0.5, 0.5, 1.0))
    route = LineString([(-20.0, 10.0), (20.0, 10.0)])
    site = _site(routes=[route])
    # The box's edge is 0.34 m, then 0.36 m, from the line.
    assert keep_out(site, item, _at(item, 0.0, 9.41)) == "hunt_route"
    assert keep_out(site, item, _at(item, 0.0, 9.39)) is None
    assert keep_out(site, item, _at(item, -20.0, 8.6)) == "hunt_route"
    assert keep_out(site, item, _at(item, -21.6, 8.4)) is None


def test_ground_keep_outs_name_their_reason() -> None:
    item = _cover(size=(1.0, 1.0, 1.0))
    carriageway = box(-50, -5, 50, 5)
    site = _site(
        free=SQUARE.difference(carriageway).difference(box(20, 20, 30, 30)), buildings=[box(20, 20, 30, 30)],
        playable=box(-40, -40, 40, 40), islands=SQUARE.difference(box(-30, -30, -20, -20)),
        urban=[Point(0.0, 20.0).buffer(1.0)], solids=[box(10.0, 20.0, 11.0, 21.0)],
    )
    assert keep_out(site, item, _at(item, 0.0, 0.0)) == "off_walkable"
    assert keep_out(site, item, _at(item, 0.0, 5.4)) == "off_walkable"
    assert keep_out(site, item, _at(item, 0.0, 5.6)) is None
    assert keep_out(site, item, _at(item, 25.0, 25.0)) == "inside_building"
    assert keep_out(site, item, _at(item, 20.0, 15.0)) is None
    assert keep_out(site, item, _at(item, 39.6, 15.0)) == "outside_playable"
    assert keep_out(site, item, _at(item, -25.0, -25.0)) == "off_navmesh"
    assert keep_out(site, item, _at(item, 0.0, 21.4)) == "on_urban_element"
    assert keep_out(site, item, _at(item, 0.0, 21.6)) is None
    # The solid box shares 0.25 m² at most before it overlaps.
    assert keep_out(site, item, _at(item, 10.0, 20.0)) is None
    assert keep_out(site, item, _at(item, 10.01, 20.01)) == "overlaps_solid"
    assert keep_out(site, item, _at(item, 10.0, 20.0), [box(9.0, 20.0, 10.2, 22.0)]) == "overlaps_solid"


def test_the_drive_lane_is_not_a_keep_out_only_the_carriageway_is() -> None:
    item = _cover(size=(1.0, 1.0, 1.0))
    lane = LineString([(-50.0, 0.0), (50.0, 0.0)])
    site = _site(free=SQUARE.difference(box(-50, -3, 50, 3)), lanes=[lane])
    assert keep_out(site, item, _at(item, 0.0, 3.6)) is None
    assert keep_out(site, item, _at(item, 0.0, 3.4)) == "off_walkable"


def test_a_fit_reason_is_named() -> None:
    wreck = _cover("car", size=(2.0, 1.0, 1.0), role="wreck")
    # A wreck must be a car: 2 m is short of the 2.8 m band, so fit's size check refuses it.
    assert keep_out(_site(), wreck, _at(wreck, 0.0, 0.0)) == "wrong_size"


def test_a_prop_that_closes_a_passage_is_refused() -> None:
    item = _cover(size=(1.0, 1.0, 1.0))
    site = _site(box(0, 0, 20, 2))
    assert keep_out(site, item, _at(item, 10.0, 1.0)) == "blocks_passage"


# ------------------------------------------------------------------------------------------------ whole map


def test_two_props_that_together_close_a_link_split_the_navmesh() -> None:
    rooms = union_all([box(0, 0, 10, 10), box(10, 3.6, 14, 6.0), box(14, 0, 24, 10)])
    site = _site(rooms)
    upper, lower = box(11, 4.8, 12, 6.0), box(12, 3.6, 13, 4.8)
    assert split_reason(site, [upper]) is None
    assert split_reason(site, [lower]) is None
    assert split_reason(site, [upper, lower]) == "navmesh_split"
    assert split_reason(site, []) is None
    assert split_reason(_site(rooms, solids=[box(11, 3.6, 12, 4.8)]), [upper]) == "navmesh_split"


def test_a_spawn_the_props_take_off_the_islands_is_named() -> None:
    site = _site(anchors=[(5.0, 4.5), (100.0, 100.0)])
    assert split_reason(site, [box(4.0, 4.0, 4.7, 4.7)]) == "anchor_off_navmesh"
    assert split_reason(site, [box(4.0, 4.0, 4.6, 4.6)]) is None


def test_choose_cover_drops_the_last_prop_while_the_map_splits(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = [_target(0.0, 0.0, length=90.0), _target(0.0, 30.0, length=80.0)]
    monkeypatch.setattr(module, "split_reason", lambda _site, prints: "navmesh_split" if len(prints) > 1 else None)
    plan = choose_cover(targets, [_cover()], _site())
    assert [item.target.length_m for item in plan.placements] == [90.0]
    assert plan.rejects["navmesh_split"] == 1 and plan.reason is None
    monkeypatch.setattr(module, "split_reason", lambda _site, prints: "anchor_off_navmesh")
    plan = choose_cover(targets, [_cover()], _site())
    assert plan.placements == () and plan.reason == NO_SPOT and plan.rejects["anchor_off_navmesh"] == 2


# ------------------------------------------------------------------------------------------------ caps and plan


def _grid(count: int) -> list[OpenPoint]:
    return [_target(-45.0 + 15.0 * (i % 7), -45.0 + 15.0 * (i // 7), length=100.0 + i) for i in range(count)]


def test_the_triangle_cap_is_what_overgrow_left_never_more_than_12000() -> None:
    assert (COVER_MAX_ADDED_TRIS, MAX_COVER_PROPS, COVER_MAX_ADDED_DRAWS, MAX_NEW_COVER_ASSETS) == (12_000, 24, 24, 2)
    assert triangle_cap(0) == 12_000
    assert triangle_cap(68_000) == 12_000
    assert triangle_cap(68_001) == 11_999
    assert triangle_cap(80_000) == 0
    assert triangle_cap(90_000) == 0


def test_at_most_max_cover_props_are_placed_whatever_the_order() -> None:
    targets = _grid(30)
    plan = choose_cover(targets, [_cover()], _site())
    assert len(plan.placements) == 24 and plan.targets == 30
    assert plan.rejects == {"cap_props": 6}
    assert [item.target.length_m for item in plan.placements] == [129.0 - i for i in range(24)]
    shuffled = choose_cover(list(reversed(targets)), [_cover("other", in_scene=False), _cover()], _site())
    assert [(i.spot, i.prop.asset_id) for i in shuffled.placements] == [(i.spot, i.prop.asset_id) for i in plan.placements]
    assert (plan.triangles, plan.draws, plan.new_assets) == (24 * 12, 24, [])


@pytest.mark.parametrize(("caps", "count", "reason"), [
    (Caps(triangles=36), 3, {"cap_triangles": 1, "no_valid_spot": 1}),
    (Caps(triangles=35), 2, {"cap_triangles": 2, "no_valid_spot": 2}),
    (Caps(draws=3), 3, {"cap_draws": 1, "no_valid_spot": 1}),
    (Caps(draws=2), 2, {"cap_draws": 2, "no_valid_spot": 2}),
    (Caps(props=3), 3, {"cap_props": 1}),
])
def test_each_cap_is_met_at_its_limit_and_refuses_one_more(caps: Caps, count: int, reason: dict[str, int]) -> None:
    plan = choose_cover(_grid(4), [_cover()], _site(), caps)
    assert len(plan.placements) == count
    assert plan.rejects == reason


def test_the_new_asset_cap_counts_distinct_props_not_in_the_scene() -> None:
    first, second, third = (_cover(name, in_scene=False) for name in ("a_new", "b_new", "c_new"))
    placed = [_placed(0.0, 0.0, first), _placed(20.0, 0.0, second)]
    caps = Caps(new_assets=2)
    assert cap_reason(third, placed, caps) == "cap_new_assets"
    assert cap_reason(first, placed, caps) is None
    assert cap_reason(_cover("old"), placed, caps) is None
    assert cap_reason(third, placed[:1], caps) is None
    assert cap_reason(third, placed, Caps(new_assets=3)) is None
    plan = choose_cover(_grid(3), [third], _site(), Caps(new_assets=0))
    assert plan.placements == () and plan.rejects == {"cap_new_assets": 3, "no_valid_spot": 3}
    assert choose_cover(_grid(3), [third], _site()).new_assets == ["c_new"]


def test_nothing_placed_has_a_reason_and_never_raises() -> None:
    assert choose_cover([], [_cover()], _site()).reason == NO_TARGET == "no open sightline over 60 m"
    plan = choose_cover([_target()], [], _site())
    assert (plan.reason, plan.targets) == (NO_PROP, 1) and NO_PROP == "no solid catalogue prop meets the cover rule"
    blocked = choose_cover([_target()], [_cover()], _site(spawns=[(0.0, 0.0)]))
    assert blocked.reason == NO_SPOT == "no valid spot"
    assert blocked.rejects == {"no_valid_spot": 1, "on_route_sample": 1, "spawn_point": 5}
    assert note(blocked) == "cover: nothing placed (no valid spot)"
    assert note(choose_cover([], [], _site())) == "cover: nothing placed (no open sightline over 60 m)"


def test_the_note_names_what_was_placed() -> None:
    plan = choose_cover(_grid(2), [_cover("b_new", in_scene=False), _cover("a_old")], _site())
    assert note(plan) == (
        "cover: placed 2 of 2 targets over 60 m (a_old), +24 triangles, +2 primitives (estimated from the 2D navmesh)"
    )


def test_a_spot_out_of_reach_or_on_the_sample_is_skipped() -> None:
    big = _cover(size=(3.0, 2.9, 1.0))
    plan = choose_cover([_target()], [big], _site())
    # 2.9 m deep: at 1 m along the box covers the sample; at 0 m across it does too; across 1 m it still does.
    assert plan.placements == () and plan.rejects == {"no_valid_spot": 1, "on_route_sample": 6}
    small = _cover(size=(0.6, 0.6, 1.0))
    plan = choose_cover([_target()], [small], _site())
    assert plan.placements[0].spot == Spot(1.0, 0.0, math.pi / 2)


def test_the_road_floor_sets_the_height(monkeypatch: pytest.MonkeyPatch) -> None:
    class Floor:
        def z(self, x: float, y: float) -> float:
            return 0.25

    plan = choose_cover([_target()], [_cover(size=(0.6, 0.6, 1.0))], _site(), floor=Floor())  # type: ignore[arg-type]
    assert plan.placements[0].matrix[2, 3] == pytest.approx(0.25)
    assert MURCIA_CENTER == (-1.12863, 37.98407)


def test_a_plan_is_a_frozen_record() -> None:
    plan = CoverPlan(0, (), {}, Caps())
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.targets = 1  # type: ignore[misc]


# ------------------------------------------------------------------------------------------------ edges the mutation run found


def test_only_a_prop_deeper_than_long_turns_and_the_turn_is_a_rotation() -> None:
    tall = _cover_of(_prop("tall", size=(1.0, 0.5, 2.0)))
    assert (tall.length_m, tall.depth_m) == pytest.approx((1.0, 0.5)) and tall.local[0, 0] == 1.0
    square = _cover_of(_prop("square", size=(1.0, 1.0, 1.0)))
    assert np.array_equal(square.local[:3, :3], np.eye(3))
    deep = _cover_of(_prop("deep", size=(0.5, 2.0, 1.0)))
    assert np.linalg.det(deep.local[:3, :3]) == pytest.approx(1.0)
    # A quarter turn counter-clockwise: +y goes to -x.
    assert (deep.local[:3, :3] @ np.array([0.0, 1.0, 0.0])) == pytest.approx([-1.0, 0.0, 0.0])


def test_pose_rotates_about_z_rests_on_z_and_keeps_a_rigid_matrix() -> None:
    item = _cover(size=(2.0, 0.5, 1.0))
    matrix = pose(item, Spot(3.0, 4.0, math.pi / 2))
    assert matrix[3].tolist() == [0.0, 0.0, 0.0, 1.0]
    assert (matrix @ np.array([1.0, 0.0, 0.0, 1.0]))[:3] == pytest.approx([3.0, 5.0, 0.0])
    assert np.linalg.det(matrix[:3, :3]) == pytest.approx(1.0)
    assert (matrix @ np.array([0.0, 0.0, 1.0, 1.0]))[2] == pytest.approx(1.0)
    assert pose(item, Spot(0.0, 0.0, 0.0))[2, 3] == 0.0


def test_spots_follow_the_ray_from_the_sample_and_round_to_the_centimetre() -> None:
    north = spots(_target(10.0, 20.0, to=(10.0, 100.0)))
    assert [(s.x, s.y) for s in north][:3] == [(10.0, 21.0), (9.0, 21.0), (11.0, 21.0)]
    diagonal = spots(_target(0.0, 0.0, to=(10.0, 10.0)))
    assert {s.yaw for s in diagonal} == {0.0}
    odd = spots(_target(0.123, 0.456))
    assert (odd[0].x, odd[0].y) == (1.12, 0.46)
    assert (odd[1].x, odd[1].y) == (1.12, 1.46)


@pytest.mark.parametrize(("key", "radius"), [("ammo", 3.0), ("climbs", 3.0), ("doors", 3.0)])
def test_a_spot_exactly_at_a_keep_out_radius_is_clear(key: str, radius: float) -> None:
    item = _cover(size=(0.5, 0.5, 1.0))
    assert keep_out(_site(**{key: [(0.0, 0.0)]}), item, _at(item, radius, 0.0)) is None


def test_keep_out_counts_the_props_already_placed_in_the_passage() -> None:
    item = _cover(size=(1.0, 1.0, 1.0))
    site = _site(box(0, 0, 20, 3))
    matrix = _at(item, 10.0, 0.5)
    assert keep_out(site, item, matrix) is None
    assert keep_out(site, item, matrix, [box(9, 2.19, 11, 3)]) == "blocks_passage"


def test_a_capped_prop_gives_way_to_the_next_one() -> None:
    heavy = _cover("a_heavy", parts=3)
    light = _cover("b_light")
    plan = choose_cover([_target()], [heavy, light], _site(), Caps(draws=1))
    assert [item.prop.asset_id for item in plan.placements] == ["b_light"]
    assert plan.rejects == {"cap_draws": 1}


def test_the_note_lists_every_asset_placed() -> None:
    plan = choose_cover(_grid(2), [_cover("b_new", size=(1.0, 0.5, 1.2)), _cover("a_old")], _site())
    assert note(plan) == (
        "cover: placed 2 of 2 targets over 60 m (b_new), +24 triangles, +2 primitives (estimated from the 2D navmesh)"
    )
    mixed = CoverPlan(2, (_placed(0.0, 0.0, _cover("x")), _placed(20.0, 0.0, _cover("y"))), {}, Caps())
    assert note(mixed).startswith("cover: placed 2 of 2 targets over 60 m (x, y), ")


def test_the_early_plans_are_empty_and_name_their_reason() -> None:
    caps = Caps(props=5)
    assert choose_cover([], [_cover()], _site(), caps) == CoverPlan(0, (), {}, caps, NO_TARGET)
    assert choose_cover(_grid(3), [], _site(), caps) == CoverPlan(3, (), {}, caps, NO_PROP)


def test_skipped_targets_are_counted_and_the_next_ones_still_placed() -> None:
    targets = [_target(0.0, 0.0, length=100.0), _target(5.0, 0.0, length=90.0), _target(30.0, 0.0, length=80.0),
               _target(33.0, 0.0, length=70.0), _target(2.0, 0.0, length=60.5)]
    plan = choose_cover(targets, [_cover()], _site())
    assert [item.target.length_m for item in plan.placements] == [100.0, 80.0]
    assert plan.rejects == {"covered": 1, "spacing": 2}
