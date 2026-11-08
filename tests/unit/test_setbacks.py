"""Setbacks: the parts a Catastro split made of one parcel look like one building (ADR-016).

A wall between two parts of one parcel is that building's facade above the lower part's roof;
between two parcels it stays a blank party wall. Local coordinates are metres from MURCIA_CENTER
(x east, y north, z up).
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Any

import numpy as np
import pytest
from shapely import Polygon, STRtree
from shapely.geometry import box

from onward_generator.detail import load_detail_config
from onward_generator.detail.config import Balcony, Opening
from onward_generator.detail.facades import (
    Layout,
    Segment,
    Storeys,
    WallAreas,
    build_shell,
    exposure_probe,
    layout_segment,
    party_material,
    refine_exposure,
    setback_floor,
    setback_floors,
    split_blank_walls,
)
from onward_generator.detail.grammar import FacadeUse, appearance_key, choose_roof, choose_wall
from onward_generator.detail.scene import _facade_walls, _roof_plans, _templates
from onward_generator.generation.mesh import Mesh, merge

CFG = load_detail_config()
TEMPLATE = CFG.template_for("residential_multi", False)
WALL = "plaster_ochre"
ROOF = "roof_flat_grey"
# The segment runs east along y = 0; its outward side is y < 0.
SEG = Segment(0, 0, 0, (0.0, 0.0), (10.0, 0.0), False)
BELOW = box(0, -5, 10, 0)  # a footprint just outside SEG


def test_appearance_key_is_the_parcel_then_the_parent_then_the_building() -> None:
    sub = {"parent": {"id": "osm-way-1"}, "subdivision": {"refcat": "9033801VK3793C"}}
    assert appearance_key("osm-way-1:9033801VK3793C_part1", sub) == "parcel:9033801VK3793C"
    assert appearance_key("osm-way-1:x", {"parent": {"id": "osm-way-1"}, "subdivision": {"refcat": ""}}) == "osm-way-1"
    assert appearance_key("osm-way-1:x", {"parent": {"id": "osm-way-1"}}) == "osm-way-1"
    assert appearance_key("osm-way-2", {"tags": {"building": "yes"}}) == "osm-way-2"
    assert appearance_key("osm-way-2", {"parent": None, "subdivision": None}) == "osm-way-2"


def test_exposure_probe_is_the_strip_outside_the_segment_short_of_its_ends() -> None:
    assert exposure_probe(SEG, CFG).bounds == pytest.approx((0.05, -CFG.probe_m, 9.95, 0.0))
    short = Segment(0, 0, 0, (0.0, 0.0), (0.1, 0.0), False)
    assert exposure_probe(short, CFG).bounds == pytest.approx((0.025, -CFG.probe_m, 0.075, 0.0))


def test_setback_floor_is_the_highest_roof_of_the_same_parcel() -> None:
    assert setback_floor(SEG, [(BELOW, 9.0)], CFG) == 9.0
    assert setback_floor(SEG, [(box(0, -5, 4, 0), 9.0), (box(4, -5, 10, 0), 12.0)], CFG) == 12.0
    # Within probe_m outside the wall still counts; beyond it, or inside the building, does not.
    assert setback_floor(SEG, [(box(0, -5, 10, -0.9), 9.0)], CFG) == 9.0
    assert setback_floor(SEG, [(box(0, -5, 10, -1.1), 9.0)], CFG) is None
    assert setback_floor(SEG, [(box(0, 0, 10, 5), 9.0)], CFG) is None
    assert setback_floor(SEG, [], CFG) is None
    # A footprint the probe misses does not end the search.
    assert setback_floor(SEG, [(box(0, 5, 10, 8), 3.0), (BELOW, 9.0)], CFG) == 9.0


def test_another_parcel_keeps_the_wall_a_blank_party_wall() -> None:
    assert setback_floor(SEG, [(BELOW, None)], CFG) is None
    assert setback_floor(SEG, [(box(0, -5, 4, 0), 9.0), (box(4, -5, 10, 0), None)], CFG) is None
    assert setback_floor(SEG, [(box(4, -5, 10, 0), None), (box(0, -5, 4, 0), 9.0)], CFG) is None
    # Another parcel that only touches the end of the wall (inside the probe's inset) does not count.
    assert setback_floor(SEG, [(BELOW, 9.0), (box(-5, -5, 0.04, 0), None)], CFG) == 9.0
    # Nor one that meets the probe over less than 1 cm².
    assert setback_floor(SEG, [(BELOW, 9.0), (box(5, -0.001, 5.09, 0), None)], CFG) == 9.0
    assert setback_floor(SEG, [(BELOW, 9.0), (box(5, -0.0012, 5.1, 0), None)], CFG) is None


def test_setback_floors_lists_the_party_walls_that_open_below_the_roof() -> None:
    exposed = Segment(0, 0, 1, (10.0, 0.0), (10.0, 8.0), True)
    lower = Segment(0, 0, 2, (10.0, 8.0), (0.0, 8.0), False)  # faces north (y > 8)
    neighbours: list[tuple[Any, float | None]] = [(BELOW, 9.0), (box(10, 0, 20, 8), 3.0), (box(0, 8, 10, 20), 20.0)]
    assert setback_floors([SEG, exposed, lower], neighbours, 15.0, CFG) == {0: 9.0}
    # A floor at or above the building's own roof leaves nothing to open.
    assert setback_floors([SEG], [(BELOW, 15.0)], 15.0, CFG) == {}
    assert setback_floors([SEG], [(BELOW, 14.9)], 15.0, CFG) == {0: 14.9}
    assert setback_floors([SEG], [(BELOW, None)], 15.0, CFG) == {}


def _layout(floor_m: float | None, others: Polygon, template: Any = TEMPLATE) -> Layout:
    footprint = box(0, 0, 10, 8)
    layout = Layout()
    args = (SEG, Storeys(4, 3.0, "heights_levels"), template, template.ground, None, "door", CFG.recess_depth_m,
            "b1", footprint, others, CFG, layout)
    if floor_m is None:
        layout_segment(*args)
    else:
        layout_segment(*args, floor_m)
    return layout


ALWAYS = replace(TEMPLATE, balcony=Balcony(1.0, Opening(1.0, 2.2), 1.6, 0.6, 0.15, 1.0))  # a balcony in every bay


def test_a_setback_wall_opens_only_above_the_floor() -> None:
    plain = _layout(None, Polygon())
    assert {h.storey for h in plain.holes} == {0, 1, 2, 3}
    assert min(h.v0 for h in plain.holes) == pytest.approx(0.9)
    floor = 3.0 + 0.9  # exactly the sill of the first-floor windows
    raised = _layout(floor, Polygon())
    assert {h.storey for h in raised.holes} == {1, 2, 3}
    assert min(h.v0 for h in raised.holes) == floor
    assert all(h.v0 >= floor for h in raised.holes)
    # A balcony door would start at the first floor, under the floor: a window takes its bay, and no slab is hung.
    balconies = _layout(floor, Polygon(), ALWAYS)
    assert {h.kind for h in balconies.holes if h.storey == 1} == {"window"}
    assert {h.kind for h in balconies.holes if h.storey > 1} == {"balcony_door"}
    assert {v0 for _, _, v0 in balconies.balconies} == {6.0, 9.0}
    assert {h.kind for h in _layout(None, Polygon(), ALWAYS).holes if h.storey == 1} == {"balcony_door"}


def test_a_storey_under_the_floor_is_skipped_whole() -> None:
    always = ALWAYS
    # The lower part outside the wall stops every balcony slab: each bay of a laid storey counts one.
    bays = len({h.column for h in _layout(None, BELOW, always).holes})
    assert _layout(None, BELOW, always).skipped["balcony_not_placed"] == 3 * bays
    assert _layout(6.0, BELOW, always).skipped["balcony_not_placed"] == 2 * bays  # the first floor ends at 6 m
    assert _layout(6.01, BELOW, always).skipped["balcony_not_placed"] == 2 * bays
    assert _layout(5.99, BELOW, always).skipped["balcony_not_placed"] == 3 * bays


def _setback_shell(neighbour: Polygon, floor_m: float) -> tuple[Any, list[Segment]]:
    footprint = box(0, 0, 10, 8)
    refined, segments = refine_exposure(footprint, neighbour, CFG)
    layout = Layout()
    storeys = Storeys(3, 3.0, "heights_levels")
    for seg in segments:
        if not seg.exposed:
            layout_segment(seg, storeys, TEMPLATE, TEMPLATE.ground, None, "door", CFG.recess_depth_m,
                           "b1", refined, neighbour, CFG, layout, floor_m)
    return build_shell(refined, segments, layout, 9.0, TEMPLATE, WALL, ROOF, "b1", CFG), segments


def _east_faces(mesh: Mesh) -> np.ndarray:
    """The triangles of the wall at x = 10 that face east."""
    tri = mesh.vertices[mesh.triangles]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return tri[np.all(np.isclose(tri[:, :, 0], 10.0), axis=1) & (normal[:, 0] > 0)]


def _area(tri: np.ndarray) -> float:
    return float(np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1).sum() / 2)


def test_build_shell_lists_the_party_walls_with_openings() -> None:
    shell, segments = _setback_shell(box(10, 0, 20, 8), 4.0)
    (party,) = [s for s in segments if not s.exposed]
    assert shell.blank_walls == []
    assert [(seg, rects) for seg, _, rects in shell.opened_walls] == [
        (party, [(h.s0, h.v0, h.s0 + h.width, h.v0 + h.height) for h in _layout_holes(shell, party)]),
    ]
    assert all(min(v0 for _, v0, _, _ in rects) == pytest.approx(6.9) for _, _, rects in shell.opened_walls)
    assert any(mesh is face for _, face, _ in shell.opened_walls for mesh in shell.parts[WALL])
    # Street walls with openings are not party walls.
    street, _ = _setback_shell(Polygon(), 4.0)
    assert street.opened_walls == [] and street.blank_walls == []


def _layout_holes(shell: Any, seg: Segment) -> list[Any]:
    footprint = box(0, 0, 10, 8)
    layout = Layout()
    layout_segment(seg, Storeys(3, 3.0, "heights_levels"), TEMPLATE, TEMPLATE.ground, None, "door", CFG.recess_depth_m,
                   "b1", footprint, box(10, 0, 20, 8), CFG, layout, 4.0)
    return layout.holes


@pytest.mark.parametrize(("roof", "cut"), [(4.0, 4.0), (8.0, 6.9)])
def test_split_drops_the_band_a_lower_part_hides_and_keeps_the_facade_above(roof: float, cut: float) -> None:
    neighbour = box(10, 0, 20, 8)
    shell, _ = _setback_shell(neighbour, 4.0)
    z_top = shell.z_top
    before = _area(_east_faces(merge(shell.parts[WALL])))
    areas = split_blank_walls(shell, [(neighbour, roof)])
    assert areas == WallAreas(0.0, 0.0, pytest.approx(8 * cut))
    after = _east_faces(merge(shell.parts[WALL]))
    assert after[:, :, 2].min() == pytest.approx(cut) and after[:, :, 2].max() == pytest.approx(z_top)
    assert before - _area(after) == pytest.approx(8 * cut)
    assert party_material(WALL) not in shell.parts


def test_split_sums_blank_and_opened_party_walls() -> None:
    east, west = box(10, 0, 20, 8), box(-10, 0, 0, 8)
    refined, segments = refine_exposure(box(0, 0, 10, 8), east.union(west), CFG)
    layout = Layout()
    for seg in segments:
        if seg.a[0] == 10.0 and seg.b[0] == 10.0:
            layout_segment(seg, Storeys(3, 3.0, "heights_levels"), TEMPLATE, TEMPLATE.ground, None, "door", CFG.recess_depth_m,
                           "b1", refined, east.union(west), CFG, layout, 4.0)
    shell = build_shell(refined, segments, layout, 9.0, TEMPLATE, WALL, ROOF, "b1", CFG)
    assert len(shell.blank_walls) == 1 and len(shell.opened_walls) == 1
    # West: blank, hidden up to 5 m. East: openings from 6.9 m, hidden up to 4 m.
    areas = split_blank_walls(shell, [(east, 4.0), (west, 5.0)])
    assert areas == WallAreas(0.0, pytest.approx(8 * (shell.z_top - 5.0)), pytest.approx(8 * 5.0 + 8 * 4.0))


def test_a_wall_only_partly_covered_keeps_its_whole_face() -> None:
    neighbour = box(10, 0, 20, 8)
    shell, _ = _setback_shell(neighbour, 4.0)
    before = _area(_east_faces(merge(shell.parts[WALL])))
    assert split_blank_walls(shell, [(box(10, 2, 20, 8), 4.0)]) == WallAreas(0.0, 0.0, 0.0)
    after = _east_faces(merge(shell.parts[WALL]))
    assert after[:, :, 2].min() == pytest.approx(0.0) and _area(after) == pytest.approx(before)


# Parcel P is two parts side by side; Q touches the second part only; R is far away.
PARTS = {"p1": box(0, 0, 10, 10), "p2": box(10, 0, 20, 10), "q": box(20, 0, 30, 10), "r": box(60, 0, 70, 10)}


def _feature(bid: str) -> dict[str, Any]:
    if bid.startswith("p"):
        return {"tags": {"building": "yes"}, "parent": {"id": "osm-way-1"}, "subdivision": {"refcat": "P"}}
    return {"tags": {"building": "yes"}}


def _walls(cfg: Any, features: list[dict[str, Any]]) -> dict[str, tuple[FacadeUse, str, str]]:
    ids = sorted(PARTS)
    tree = STRtree([PARTS[i] for i in ids])
    height_of = {bid: {"category": "unknown", "landmark": False} for bid in ids}
    return _facade_walls(ids, PARTS, tree, {bid: _feature(bid) for bid in ids}, height_of, features, cfg)


def test_the_parts_of_one_parcel_share_their_use_and_wall() -> None:
    two = replace(CFG, facade_palettes=MappingProxyType({**CFG.facade_palettes, "residential": ("plaster_ochre", "plaster_cream"),
                                                          "retail": ("plaster_ochre", "plaster_cream")}))
    shop = {"id": "poi-shop", "kind": "poi", "tags": {"shop": "bakery"}, "geometry": {"type": "Point", "coordinates": [15, 5]}}
    walls = _walls(two, [shop])
    # The bakery is inside p2; the parcel's first part, p1, resolves the use from both.
    assert walls["p1"] == walls["p2"]
    assert walls["p1"][0] == FacadeUse("retail", "observed", "poi-shop", "shop=bakery", "poi_inside")
    # The wall is drawn for the parcel, not for p1: with nothing to avoid it is the parcel's first choice.
    assert walls["p1"][1] == choose_wall({"building": "yes"}, two.facade_palettes["retail"], two.seed, "parcel:P")[0]
    # q touches p2 only, and avoids the parcel's wall.
    assert walls["q"][1] != walls["p1"][1]
    assert walls["q"][0] == FacadeUse("residential", "estimated")


def test_a_parcel_avoids_the_walls_around_any_of_its_parts() -> None:
    # q (drawn first) touches only the second part of parcel z: z must still avoid it.
    footprints = {"q": box(20, 0, 30, 10), "z1": box(0, 0, 10, 10), "z2": box(10, 0, 20, 10)}
    ids = sorted(footprints)
    features = {"q": {"tags": {}}, **{z: {"tags": {}, "subdivision": {"refcat": "Z"}} for z in ("z1", "z2")}}
    height_of = {bid: {"landmark": False} for bid in ids}
    two = replace(CFG, facade_palettes=MappingProxyType({**CFG.facade_palettes, "residential": ("plaster_ochre", "plaster_cream")}))
    for seed in range(8):
        cfg = replace(two, seed=seed)
        walls = _facade_walls(ids, footprints, STRtree([footprints[i] for i in ids]), features, height_of, [], cfg)
        assert walls["z1"][1] == walls["z2"][1] != walls["q"][1]


def _plans(footprints: dict[str, Polygon], keys: dict[str, str], heights: dict[str, float], seed: int) -> dict[str, Any]:
    ids = sorted(footprints)
    cfg = replace(CFG, seed=seed)
    return _roof_plans(ids, footprints, STRtree([footprints[i] for i in ids]), keys, {i: {} for i in ids},
                       dict.fromkeys(ids, TEMPLATE), heights, cfg, dict.fromkeys(ids, 3.0))


def _hip_seed(key: str) -> int:
    """A seed whose estimated grammar gives ``key`` a hip roof."""
    return next(s for s in range(100) if choose_roof({}, TEMPLATE, s, key, 100.0)[0].hip is not None)


def test_the_terrace_under_a_setback_keeps_a_flat_roof() -> None:
    lower, upper = box(0, 0, 10, 10), box(10, 0, 20, 10)  # 100 m² each: a hip is possible
    seed = _hip_seed("parcel:P")
    same = _plans({"a": lower, "b": upper}, {"a": "parcel:P", "b": "parcel:P"}, {"a": 9.0, "b": 15.0}, seed)
    assert same["a"][0].hip is None and same["a"][0].parapet_m == TEMPLATE.parapet_m
    assert same["b"][0].hip is not None  # the top of the building may still take a hip
    # As tall as its neighbour, or next to a taller building of another parcel, the hip stays possible.
    level = _plans({"a": lower, "b": upper}, {"a": "parcel:P", "b": "parcel:P"}, {"a": 15.0, "b": 15.0}, seed)
    assert level["a"][0].hip is not None
    other = _plans({"a": lower, "b": upper}, {"a": "parcel:P", "b": "parcel:Q"}, {"a": 9.0, "b": 15.0}, seed)
    assert other["a"][0].hip is not None
    # A taller part beyond probe_m is not over the terrace.
    apart = _plans({"a": lower, "b": box(11.5, 0, 20, 10)}, {"a": "parcel:P", "b": "parcel:P"}, {"a": 9.0, "b": 15.0}, seed)
    assert apart["a"][0].hip is not None
    # Within probe_m, across a gap, it is.
    gap = _plans({"a": lower, "b": box(10.5, 0, 20, 10)}, {"a": "parcel:P", "b": "parcel:P"}, {"a": 9.0, "b": 15.0}, seed)
    assert gap["a"][0].hip is None


def test_a_concave_footprint_gets_no_estimated_hip() -> None:
    seed = _hip_seed("ell")
    ell = box(0, 0, 10, 10).difference(box(5, 5, 10, 10))
    assert _plans({"ell": ell}, {"ell": "ell"}, {"ell": 9.0}, seed)["ell"][0].hip is None
    # A hull exactly 2 % larger than the footprint still counts as convex: 10.2 x 10 m less a 2 x 1 m bite.
    bitten = box(0, 0, 10.2, 10).difference(box(4, 9, 6, 10))
    assert bitten.convex_hull.area == bitten.area * 1.02
    assert _plans({"ell": bitten}, {"ell": "ell"}, {"ell": 9.0}, seed)["ell"][0].hip is not None
    square = box(0, 0, 8, 8)
    assert _plans({"ell": square}, {"ell": "ell"}, {"ell": 9.0}, seed)["ell"][0].hip is not None


def test_the_parts_of_one_parcel_share_a_roof_material() -> None:
    big = {f"p{i}": box(30 * i, 0, 30 * i + 20, 20) for i in range(6)}  # 400 m²: no estimated hip
    plans = _plans(big, dict.fromkeys(big, "parcel:P"), dict.fromkeys(big, 9.0), 7)
    assert len({plan[1] for plan in plans.values()}) == 1
    assert plans["p0"] == choose_roof({}, TEMPLATE, 7, "parcel:P", 400.0)


def test_templates_follow_the_category_and_a_landmark_takes_its_own() -> None:
    height_of: dict[str, dict[str, Any]] = {"a": {"category": "commercial", "landmark": True}, "b": {"category": "commercial"}, "c": {}}
    assert _templates(["a", "b", "c"], height_of, CFG) == {
        "a": CFG.templates[CFG.landmark_template],
        "b": CFG.template_for("commercial", False),
        "c": CFG.template_for("None", False),
    }
    assert CFG.template_for("commercial", False) != CFG.templates[CFG.landmark_template]


def _lend(colour: str) -> dict[str, Any]:
    return {"colour": colour, "dataset": "Esri Community Maps", "license": "CC-BY-4.0", "origin": "observed", "attribution": {}}


def test_a_colour_lent_to_one_part_is_the_parcels() -> None:
    from onward_generator.detail.scene import _lent_by_key

    ids = sorted(PARTS)
    key_of = {bid: appearance_key(bid, _feature(bid)) for bid in ids}
    tints = [
        {"building_id": "p2", "gers_id": "g2", "iou": 0.8, "facade": _lend("#c88c3c"), "roof": _lend("#b43c28")},
        {"building_id": "q", "gers_id": "gq", "iou": 0.7, "roof": _lend("#323237")},
        {"building_id": "elsewhere", "gers_id": "gx", "iou": 0.9, "facade": _lend("#ffffff")},
    ]
    walls, roofs = _lent_by_key(ids, key_of, tints)
    assert walls == {"parcel:P": {"gers_id": "g2", "iou": 0.8, **_lend("#c88c3c")}}
    assert roofs == {"parcel:P": {"gers_id": "g2", "iou": 0.8, **_lend("#b43c28")}, "q": {"gers_id": "gq", "iou": 0.7, **_lend("#323237")}}
    # The first part in id order lends first; a second part's colour does not replace it.
    first = [{"building_id": "p1", "gers_id": "g1", "iou": 0.6, "facade": _lend("#5078c8")}, *tints]
    walls, roofs = _lent_by_key(ids, key_of, first)
    assert walls["parcel:P"]["gers_id"] == "g1" and roofs["parcel:P"]["gers_id"] == "g2"
    # Both parts draw that tint, wall and roof, though only p2 was matched.
    tree = STRtree([PARTS[i] for i in ids])
    height_of = {bid: {"category": "unknown", "landmark": False} for bid in ids}
    drawn = _facade_walls(ids, PARTS, tree, {bid: _feature(bid) for bid in ids}, height_of, [], CFG, lent=_lent_by_key(ids, key_of, tints)[0])
    assert drawn["p1"][1:] == drawn["p2"][1:] == ("plaster_white__tint_ochre", "observed")
    assert drawn["q"][2] == "generated" and drawn["r"][2] == "generated"
    flat = replace(TEMPLATE, parapet_m=0.0)  # no estimated hip on these 100 m² squares
    plans = _roof_plans(ids, PARTS, tree, key_of, {i: {} for i in ids}, dict.fromkeys(ids, flat), dict.fromkeys(ids, 9.0),
                        CFG, dict.fromkeys(ids, 3.0), _lent_by_key(ids, key_of, tints)[1])
    assert plans["p1"][1] == plans["p2"][1] == "roof_flat_grey__tint_red"
    assert plans["q"][1] == "roof_flat_grey__tint_dark" and "__tint_" not in plans["r"][1]


def test_a_landmark_is_lent_no_wall_colour() -> None:
    ids = sorted(PARTS)
    tree = STRtree([PARTS[i] for i in ids])
    height_of = {bid: {"category": "unknown", "landmark": bid == "r"} for bid in ids}
    features = {bid: _feature(bid) for bid in ids}
    lent = {"r": {"gers_id": "g", "iou": 1.0, **_lend("#c88c3c")}, "q": {"gers_id": "g", "iou": 1.0, **_lend("#c88c3c")}}
    walls = _facade_walls(ids, PARTS, tree, features, height_of, [], CFG, lent=lent)
    assert walls["r"] == _facade_walls(ids, PARTS, tree, features, height_of, [], CFG)["r"]
    assert walls["q"][1:] == ("plaster_white__tint_ochre", "observed")
