"""Roof and wall grammar: OSM tags win; estimates stay marked and stay off the default."""

from __future__ import annotations

import pytest
from shapely import Polygon, affinity, box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import _hip_roof
from onward_generator.detail.grammar import FacadeUse, choose_roof, choose_wall, facade_use, resolve_use
from onward_generator.generation.mesh import upward_area

CFG = load_detail_config("config/detail.default.json")
RESIDENTIAL = CFG.templates["generic_residential"]


def test_osm_roof_shape_forces_hip() -> None:
    template, material, origin, shape = choose_roof(
        {"roof:shape": "gabled", "roof:material": "tiles"}, RESIDENTIAL, CFG.seed, "osm-way-1", 120.0,
    )
    assert origin == "observed" and shape == "gabled"
    assert material == "roof_tile" and template.parapet_m == 0 and template.hip is not None


def test_missing_roof_tag_keeps_large_parapet() -> None:
    template, _material, origin, shape = choose_roof({}, RESIDENTIAL, CFG.seed, "osm-way-311", 180.0)
    assert origin == "generated" and shape is None
    assert template.parapet_m == RESIDENTIAL.parapet_m


def test_osm_brick_is_observed_and_untagged_is_not_all_brick() -> None:
    palette = CFG.facade_palettes["residential"]
    material, origin = choose_wall({"building:material": "brick"}, palette, CFG.seed, "b")
    assert material == "brick_red" and origin == "observed"
    assert choose_wall({"material": "limestone"}, palette, CFG.seed, "b") == ("stone_light", "observed")
    origins = [choose_wall({}, palette, CFG.seed, f"b{i}")[1] for i in range(40)]
    bricks = [choose_wall({}, palette, CFG.seed, f"b{i}")[0] for i in range(40)]
    assert set(origins) == {"generated"}
    assert bricks.count("brick_red") < 40 * 0.25
    assert set(bricks) == set(palette)


def test_wall_ranking_is_seeded_per_building_and_skips_the_neighbours() -> None:
    palette = ("x", "y", "z")
    # sha256(seed, building, "wall", material) ranks seed 1 / "a" as y, z, x and seed 1 / "b" as y, x, z.
    assert choose_wall({}, palette, 1, "a") == ("y", "generated")
    assert choose_wall({}, palette, 2, "a") == ("x", "generated")
    assert choose_wall({}, palette, 1, "a", {"y"}) == ("z", "generated")
    assert choose_wall({}, palette, 1, "b", {"y"}) == ("x", "generated")
    assert choose_wall({}, palette, 1, "a", {"y", "z"}) == ("x", "generated")
    # Every material is taken by a neighbour: the building keeps its first choice.
    assert choose_wall({}, palette, 1, "a", {"x", "y", "z"}) == ("y", "generated")
    # An observed material ignores the neighbours.
    assert choose_wall({"building:material": "brick"}, palette, 1, "a", {"brick_red"}) == ("brick_red", "observed")


EXPECTED_USES = {
    "building": {
        **dict.fromkeys(("apartments", "residential", "house", "detached", "semidetached_house", "terrace",
                         "dormitory", "bungalow"), "residential"),
        **dict.fromkeys(("school", "kindergarten", "university", "college", "public", "civic", "government",
                         "townhall", "hospital", "fire_station", "education"), "institutional"),
        **dict.fromkeys(("office", "commercial", "bank"), "office"),
        **dict.fromkeys(("retail", "supermarket", "kiosk"), "retail"),
        **dict.fromkeys(("industrial", "warehouse", "manufacture"), "industrial"),
    },
    "amenity": {
        **dict.fromkeys(("school", "kindergarten", "college", "university", "library", "townhall", "courthouse",
                         "police", "hospital", "clinic", "fire_station", "community_centre"), "institutional"),
        **dict.fromkeys(("bank", "bureau_de_change"), "office"),
        **dict.fromkeys(("restaurant", "cafe", "bar", "pub", "fast_food", "pharmacy", "ice_cream"), "retail"),
    },
    "office": {"government": "institutional", "company": "office", "insurance": "office"},
    "shop": {"bakery": "retail", "clothes": "retail"},
    "tourism": {"museum": "historic", "gallery": "historic", "hotel": "office"},
    "historic": dict.fromkeys(("building", "castle", "palace", "manor", "monastery"), "historic"),
}


@pytest.mark.parametrize(("key", "value", "use"), [(k, v, u) for k, values in EXPECTED_USES.items() for v, u in values.items()])
def test_each_osm_tag_names_its_facade_use(key: str, value: str, use: str) -> None:
    assert facade_use({key: value}) == (use, f"{key}={value}")


def test_building_use_tag_reads_like_building() -> None:
    assert facade_use({"building": "yes", "building:use": "education"}) == ("institutional", "building:use=education")


@pytest.mark.parametrize(
    "tags",
    [{}, {"building": "yes"}, {"shop": "no"}, {"office": "no"}, {"amenity": "bench"}, {"historic": "memorial"},
     {"tourism": "attraction"}, {"building": "church"}],
)
def test_tags_without_a_use_name_none(tags: dict[str, str]) -> None:
    assert facade_use(tags) is None


def test_the_most_specific_use_wins_and_values_are_read_like_osm() -> None:
    assert facade_use({"building": "apartments", "shop": "bakery"}) == ("retail", "shop=bakery")
    assert facade_use({"office": "company", "amenity": "school"}) == ("institutional", "amenity=school")
    assert facade_use({"historic": "building", "amenity": "school"}) == ("historic", "historic=building")
    assert facade_use({"building": "industrial", "building:use": "residential"}) == ("industrial", "building=industrial")
    assert facade_use({"amenity": "School;cafe"}) == ("institutional", "amenity=school")


def test_resolve_use_prefers_the_building_then_the_closest_evidence() -> None:
    school = ("osm-node-1", {"amenity": "school"}, "poi_inside")
    bank_near = ("osm-node-2", {"amenity": "bank"}, "poi_near")
    cafe = ("osm-node-3", {"amenity": "cafe"}, "poi_inside")
    grounds = ("osm-way-9", {"amenity": "school"}, "enclosing_area")
    own = resolve_use("osm-way-1", {"building": "office"}, [school])
    assert own == FacadeUse("office", "observed", "osm-way-1", "building=office", "building")
    assert resolve_use("osm-way-1", {"building": "yes"}, [bank_near, cafe, school]) == FacadeUse(
        "institutional", "observed", "osm-node-1", "amenity=school", "poi_inside",
    )
    assert resolve_use("osm-way-1", {}, [bank_near, grounds]) == FacadeUse(
        "institutional", "observed", "osm-way-9", "amenity=school", "enclosing_area",
    )
    # A shop inside the footprint beats a school ground around it: the closer match comes first.
    assert resolve_use("osm-way-1", {}, [grounds, cafe]).tag == "amenity=cafe"
    near = resolve_use("osm-way-1", {}, [bank_near, ("osm-node-0", {"natural": "tree"}, "poi_inside")])
    assert near == FacadeUse("office", "estimated", "osm-node-2", "amenity=bank", "poi_near")
    # Two equal matches: the lower id wins.
    tie = resolve_use("osm-way-1", {}, [("osm-node-8", {"shop": "bakery"}, "poi_inside"), ("osm-node-7", {"shop": "books"}, "poi_inside")])
    assert tie.source_id == "osm-node-7"


def test_a_building_without_evidence_is_estimated_residential() -> None:
    use = resolve_use("osm-way-1", {"building": "yes"}, [("osm-node-0", {"natural": "tree"}, "poi_inside")])
    assert use == FacadeUse("residential", "estimated")
    assert use.to_record() == {"use": "residential", "origin": "estimated", "evidence": []}
    seen = FacadeUse("retail", "observed", "osm-node-4", "shop=bakery", "poi_inside").to_record()
    assert seen == {
        "use": "retail", "origin": "observed",
        "evidence": [{"source_id": "osm-node-4", "tag": "shop=bakery", "match": "poi_inside"}],
    }


def test_a_hip_roof_needs_an_apex_that_sees_every_eave() -> None:
    """A near-convex footprint with a 4 cm reflex jog: a fan from the centroid folds one face under the roof."""
    template, *_ = choose_roof({"roof:shape": "hipped"}, RESIDENTIAL, CFG.seed, "osm-way-1", 18.0)
    plain = _hip_roof(box(0, 0, 5, 3.6), 30.0, template)
    assert plain is not None and upward_area(plain[0]) == pytest.approx(18.0)
    jog = Polygon([(0.04, 0), (5, 0), (5, 3.6), (0, 3.6), (0, 3.4), (0.04, 3.4)])  # the west wall steps in under its top
    assert jog.area >= 0.995 * jog.convex_hull.area
    assert _hip_roof(jog, 30.0, template) is None
    assert _hip_roof(affinity.rotate(jog, 45, origin=(0, 0)), 30.0, template) is None  # a slanted step too
    chamfer = Polygon([(0.1, 0), (5, 0), (5, 3.6), (0, 3.6), (0, 0.1)])  # a short eave still faces the apex
    assert _hip_roof(chamfer, 30.0, template) is not None
    # Two 2 cm steps on the centroid's own line: their faces would be edge-on, so the roof stays flat too.
    edge_on = Polygon([(0.02, 0), (4, 0), (4, 1), (3.98, 1), (3.98, 2), (0, 2), (0, 1), (0.02, 1)])
    assert edge_on.centroid.y == 1.0 and edge_on.area >= 0.995 * edge_on.convex_hull.area
    assert _hip_roof(edge_on, 30.0, template) is None
