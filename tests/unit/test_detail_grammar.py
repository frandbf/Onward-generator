"""Roof and wall grammar: OSM tags win; estimates stay marked and stay off the default."""

from __future__ import annotations

from dataclasses import replace

import pytest
from shapely import Polygon, affinity, box

from onward_generator.detail.config import HipRoof, load_detail_config
from onward_generator.detail.facades import _hip_roof
from onward_generator.detail.grammar import (
    OVERTURE_FACADE,
    OVERTURE_ROOF,
    FacadeUse,
    choose_roof,
    choose_wall,
    facade_use,
    resolve_use,
    tagged_roof,
    tagged_wall,
)
from onward_generator.detail.lidar_roofs import RoofSpread
from onward_generator.generation.mesh import upward_area

CFG = load_detail_config("config/detail.default.json")
RESIDENTIAL = CFG.templates["generic_residential"]


def test_osm_roof_shape_replaces_the_template_roof_on_any_area() -> None:
    for area in (120.0, 5000.0):
        template, material, origin, shape = choose_roof(
            {"roof:shape": "gabled", "roof:material": "tiles"}, RESIDENTIAL, CFG.seed, "osm-way-1", area,
        )
        assert origin == "observed" and shape == "gabled"
        assert material == "roof_tile" and template.parapet_m == 0 and template.hip is None
        assert template.pitched is not None and template.pitched.shape == "gabled" and template.pitched.material == "roof_tile"


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
    template = replace(RESIDENTIAL, parapet_m=0.0, hip=HipRoof(18.0, 30.0, 4.0, "roof_tile"))
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


def test_a_colour_tag_tints_the_wall_texture_a_material_tag_names() -> None:
    colours = CFG.materials
    assert tagged_wall({"building:colour": "#faf0e6", "building:material": "brick"}, colours) == ("brick_red__tint_white", "building:colour")
    assert tagged_wall({"building:colour": "blue", "material": "limestone"}, colours) == ("stone_light__tint_blue", "building:colour")
    # Plaster (or a material that is not a tint base) is tinted as plain plaster.
    assert tagged_wall({"building:colour": "yellow", "building:material": "plaster"}, colours) == ("plaster_white__tint_yellow", "building:colour")
    assert tagged_wall({"building:colour": "red", "building:material": "glass"}, colours) == ("plaster_white__tint_red", "building:colour")
    assert tagged_wall({"building:colour": "#faf0e6", "building:material": "brick"}) == ("brick_red", "building:material")
    assert tagged_wall({"building:colour": "nope", "material": "limestone"}, colours) == ("stone_light", "material")
    assert tagged_wall({"building:material": "glass", "material": "brick"}, colours) is None
    assert tagged_wall({"roof:colour": "red"}, colours) is None
    assert tagged_wall({}, {}) is None
    # A configuration without the tint keeps the material tag.
    assert tagged_wall({"building:colour": "red", "material": "brick"}, {"brick_red": colours["brick_red"]}) == ("brick_red", "material")
    assert choose_wall({"building:colour": "red"}, ("plaster_white",), CFG.seed, "b", colours=colours) == ("plaster_white__tint_red", "observed")
    assert choose_wall({"building:colour": "red"}, ("plaster_white",), CFG.seed, "b") == ("plaster_white", "generated")


def test_a_colour_tag_tints_the_roof_before_a_material_tag() -> None:
    colours = CFG.materials
    assert tagged_roof({"roof:colour": "grey", "roof:material": "tiles"}, colours) == ("roof_tile__tint_grey", "roof:colour")
    assert tagged_roof({"roof:colour": "green", "roof:material": "metal"}, colours) == ("roof_flat_grey__tint_green", "roof:colour")
    # Without roof:material a pitched shape tints tile, any other roof a flat roof.
    assert tagged_roof({"roof:colour": "green", "roof:shape": "gabled"}, colours) == ("roof_tile__tint_green", "roof:colour")
    assert tagged_roof({"roof:colour": "green", "roof:shape": "gambrel"}, colours) == ("roof_tile__tint_green", "roof:colour")
    assert tagged_roof({"roof:colour": "green", "roof:shape": "flat"}, colours) == ("roof_flat_grey__tint_green", "roof:colour")
    assert tagged_roof({"roof:colour": "green"}, colours) == ("roof_flat_grey__tint_green", "roof:colour")
    assert tagged_roof({"roof:colour": "grey", "roof:material": "tiles"}) == ("roof_tile", "roof:material")
    assert tagged_roof({"roof:colour": "nope"}, colours) is None
    assert tagged_roof({"building:colour": "grey"}, colours) is None
    template, material, origin, shape = choose_roof({"roof:shape": "gabled", "roof:colour": "grey", "roof:height": "2"},
                                                    RESIDENTIAL, CFG.seed, "b", 100.0, storey_m=3.5, colours=colours)
    assert material == "roof_tile__tint_grey" and template.pitched is not None and template.pitched.material == material
    assert template.pitched.rise_m == 2.0 and origin == "observed" and shape == "gabled"
    template, *_ = choose_roof({"roof:shape": "gabled", "roof:levels": "2"}, RESIDENTIAL, CFG.seed, "b", 100.0, storey_m=3.5)
    assert template.pitched is not None and template.pitched.rise_m == 7.0 and template.pitched.material == "roof_tile"
    # Without a storey height a roof level is 3 m.
    template, *_ = choose_roof({"roof:shape": "gabled", "roof:levels": "1"}, RESIDENTIAL, CFG.seed, "b", 100.0)
    assert template.pitched is not None and template.pitched.rise_m == 3.0
    # A template with a hip lends the tagged roof its material, and the hip is dropped for the tagged shape.
    hipped = replace(RESIDENTIAL, hip=HipRoof(200.0, 30.0, 4.0, "roof_flat_light"))
    template, material, *_ = choose_roof({"roof:shape": "gabled"}, hipped, CFG.seed, "b", 100.0)
    assert material == "roof_flat_light" and template.hip is None and template.pitched is not None
    flat, material, origin, shape = choose_roof({"roof:shape": "flat", "roof:colour": "grey"}, RESIDENTIAL, CFG.seed, "b", 100.0, colours=colours)
    assert flat is RESIDENTIAL and material == "roof_flat_grey__tint_grey" and origin == "observed" and shape == "flat"
    plain, material, origin, _ = choose_roof({"roof:colour": "white"}, RESIDENTIAL, CFG.seed, "osm-way-311", 180.0, colours=colours)
    assert plain is RESIDENTIAL and material == "roof_flat_grey__tint_light" and origin == "observed"


def test_an_unknown_shape_tag_gets_no_estimated_hip() -> None:
    hips = [choose_roof({}, RESIDENTIAL, CFG.seed, f"b{i}", 100.0)[2] for i in range(40)]
    assert "estimated" in hips
    tagged = [choose_roof({"roof:shape": "crosspitched"}, RESIDENTIAL, CFG.seed, f"b{i}", 100.0) for i in range(40)]
    assert all(t is RESIDENTIAL and origin == "generated" and shape == "crosspitched" for t, _, origin, shape in tagged)


# Plan C2: an Overture colour lent to the footprint, below every OSM tag.


def test_a_lent_facade_colour_ranks_below_the_osm_wall_tags() -> None:
    colours = CFG.materials
    assert (OVERTURE_FACADE, OVERTURE_ROOF) == ("overture:facade_color", "overture:roof_color")
    assert tagged_wall({}, colours, lent="#c88c3c") == ("plaster_white__tint_ochre", OVERTURE_FACADE)
    assert tagged_wall({"building:colour": "blue"}, colours, lent="#c88c3c") == ("plaster_white__tint_blue", "building:colour")
    assert tagged_wall({"building:material": "brick"}, colours, lent="#c88c3c") == ("brick_red", "building:material")
    assert tagged_wall({"material": "limestone"}, colours, lent="#c88c3c") == ("stone_light", "material")
    # A material the table does not know names nothing, so the lent colour tints plain plaster.
    assert tagged_wall({"building:material": "glass"}, colours, lent="#c88c3c") == ("plaster_white__tint_ochre", OVERTURE_FACADE)
    # An unreadable OSM colour falls to the lent one.
    assert tagged_wall({"building:colour": "nope"}, colours, lent="#FFFFFF") == ("plaster_white__tint_white", OVERTURE_FACADE)
    # No colours defined, no such tint, or a lent value that is not a colour: the generated wall.
    assert tagged_wall({}, None, lent="#c88c3c") is None
    assert tagged_wall({}, {"plaster_white": colours["plaster_white"]}, lent="#c88c3c") is None
    assert tagged_wall({}, colours, lent="nope") is None
    assert tagged_wall({}, colours) is None
    palette = CFG.facade_palettes["residential"]
    assert choose_wall({}, palette, CFG.seed, "b", colours=colours, lent="#c88c3c") == ("plaster_white__tint_ochre", "observed")
    assert choose_wall({}, palette, CFG.seed, "b", colours=colours, lent="nope") == choose_wall({}, palette, CFG.seed, "b", colours=colours)
    assert choose_wall({}, palette, CFG.seed, "b", colours=colours, lent="nope")[1] == "generated"


def test_a_lent_roof_colour_ranks_below_the_osm_roof_tags_and_above_the_template() -> None:
    colours = CFG.materials
    assert tagged_roof({}, colours, lent="#b43c28") == ("roof_flat_grey__tint_red", OVERTURE_ROOF)
    assert tagged_roof({"roof:shape": "gabled"}, colours, lent="#b43c28") == ("roof_tile__tint_red", OVERTURE_ROOF)
    assert tagged_roof({"roof:shape": "flat"}, colours, lent="#b43c28") == ("roof_flat_grey__tint_red", OVERTURE_ROOF)
    assert tagged_roof({"roof:colour": "grey"}, colours, lent="#b43c28") == ("roof_flat_grey__tint_grey", "roof:colour")
    assert tagged_roof({"roof:material": "tiles"}, colours, lent="#b43c28") == ("roof_tile", "roof:material")
    assert tagged_roof({}, colours, lent="nope") is None
    assert tagged_roof({}, None, lent="#b43c28") is None
    assert tagged_roof({}, {"roof_tile": colours["roof_tile"]}, lent="#b43c28") is None
    big = 400.0  # no estimated hip
    template, material, origin, shape = choose_roof({}, RESIDENTIAL, CFG.seed, "osm-way-311", big, colours=colours, lent="#b43c28")
    assert (material, origin, shape) == ("roof_flat_grey__tint_red", "observed", None) and template == RESIDENTIAL
    _, material, origin, _ = choose_roof({}, RESIDENTIAL, CFG.seed, "osm-way-311", big, colours=colours)
    assert origin == "generated" and "__tint_" not in material
    pitched, material, origin, shape = choose_roof({"roof:shape": "gabled"}, RESIDENTIAL, CFG.seed, "b", 100.0, colours=colours, lent="#b43c28")
    assert (material, origin, shape) == ("roof_tile__tint_red", "observed", "gabled") and pitched.pitched is not None
    assert pitched.pitched.material == "roof_tile__tint_red"
    _, material, _, _ = choose_roof({"roof:shape": "flat"}, RESIDENTIAL, CFG.seed, "b", 100.0, colours=colours, lent="#b43c28")
    assert material == "roof_flat_grey__tint_red"


@pytest.mark.parametrize("tags", [{}, {"building:colour": "red"}, {"building:material": "brick"}, {"roof:shape": "gabled", "roof:colour": "grey"},
                                  {"roof:material": "tiles"}, {"roof:shape": "flat"}])
def test_without_a_lent_colour_nothing_changes(tags: dict[str, str]) -> None:
    colours = CFG.materials
    palette = CFG.facade_palettes["residential"]
    assert choose_wall(tags, palette, CFG.seed, "b", colours=colours, lent=None) == choose_wall(tags, palette, CFG.seed, "b", colours=colours)
    for area in (100.0, 400.0):
        assert choose_roof(tags, RESIDENTIAL, CFG.seed, "b", area, colours=colours, lent=None) == choose_roof(tags, RESIDENTIAL, CFG.seed, "b", area, colours=colours)
    assert tagged_wall(tags, colours, lent=None) == tagged_wall(tags, colours)
    assert tagged_roof(tags, colours, lent=None) == tagged_roof(tags, colours)


# Plan C3: a roof class from the nDSM spread, below every OSM roof tag.

SPREAD = RoofSpread("measured", 10.0, 11.5, 20, 24)


def _coin_hip_id() -> str:
    """A building id whose roof_grammar coin draws the estimated hip on a 100 m² convex parapet footprint."""
    return next(f"b{i}" for i in range(100) if choose_roof({}, RESIDENTIAL, CFG.seed, f"b{i}", 100.0)[2] == "estimated")


def test_a_pitched_class_draws_an_estimated_gable_on_any_area() -> None:
    for area in (100.0, 400.0, 5000.0):
        template, material, origin, shape = choose_roof({}, RESIDENTIAL, CFG.seed, "b", area, convex=False, lidar=("pitched", SPREAD))
        assert (material, origin, shape) == ("roof_tile", "estimated", None)
        assert template.parapet_m == 0 and template.hip is None and template.pitched is not None
        assert template.pitched == replace(template.pitched, shape="gabled", tag="gabled", material="roof_tile", rise_m=1.5,
                                           rise_rule="ndsm_spread", direction_deg=None, across=False)
        assert replace(template, parapet_m=RESIDENTIAL.parapet_m, pitched=None) == RESIDENTIAL


def test_a_pitched_class_takes_a_tagged_or_lent_colour_as_a_tile_tint() -> None:
    colours = CFG.materials
    template, material, origin, _ = choose_roof({"roof:colour": "grey"}, RESIDENTIAL, CFG.seed, "b", 100.0, colours=colours,
                                                lidar=("pitched", SPREAD))
    assert (material, origin) == ("roof_tile__tint_grey", "observed") and template.pitched is not None
    assert template.pitched.material == material
    _, material, origin, _ = choose_roof({}, RESIDENTIAL, CFG.seed, "b", 100.0, colours=colours, lent="#b43c28", lidar=("pitched", SPREAD))
    assert (material, origin) == ("roof_tile__tint_red", "observed")
    _, material, origin, _ = choose_roof({"roof:material": "concrete"}, RESIDENTIAL, CFG.seed, "b", 100.0, lidar=("pitched", SPREAD))
    assert (material, origin) == ("roof_flat_grey", "observed")


def test_a_pitched_class_drops_a_template_hip() -> None:
    hipped = replace(RESIDENTIAL, hip=HipRoof(200.0, 30.0, 4.0, "roof_flat_light"))
    template, material, *_ = choose_roof({}, hipped, CFG.seed, "b", 100.0, lidar=("pitched", SPREAD))
    assert template.hip is None and template.pitched is not None and material == "roof_tile"


def test_a_flat_class_drops_a_template_hip() -> None:
    hipped = replace(RESIDENTIAL, hip=HipRoof(200.0, 22.0, 2.5, "roof_tile"))
    template, material, origin, shape = choose_roof({}, hipped, CFG.seed, "b", 100.0, lidar=("flat", SPREAD))
    assert template == replace(hipped, hip=None) and template.pitched is None
    assert material in RESIDENTIAL.flat_roof_materials and origin == "generated" and shape is None
    assert choose_roof({}, hipped, CFG.seed, "b", 100.0)[0].hip is not None


def test_a_flat_class_keeps_the_template_and_draws_no_estimated_hip() -> None:
    bid = _coin_hip_id()
    assert choose_roof({}, RESIDENTIAL, CFG.seed, bid, 100.0, lidar=None)[0].hip is not None
    template, material, origin, shape = choose_roof({}, RESIDENTIAL, CFG.seed, bid, 100.0, lidar=("flat", SPREAD))
    assert template is RESIDENTIAL and origin == "generated" and shape is None
    assert material == choose_roof({}, RESIDENTIAL, CFG.seed, bid, 400.0)[1]


@pytest.mark.parametrize("tags", [{"roof:shape": "hipped"}, {"roof:shape": "flat"}, {"roof:shape": "crosspitched"}])
@pytest.mark.parametrize("lidar_class", ["pitched", "flat"])
def test_a_roof_shape_tag_wins_over_the_class(tags: dict[str, str], lidar_class: str) -> None:
    bid = _coin_hip_id()
    assert choose_roof(tags, RESIDENTIAL, CFG.seed, bid, 100.0, lidar=(lidar_class, SPREAD)) == choose_roof(tags, RESIDENTIAL, CFG.seed, bid, 100.0)


@pytest.mark.parametrize("tags", [{}, {"roof:colour": "red"}, {"roof:shape": "gabled", "roof:levels": "1"}, {"roof:levels": "1"}])
def test_without_a_class_nothing_changes(tags: dict[str, str]) -> None:
    for bid in ("b", _coin_hip_id()):
        for area in (100.0, 400.0):
            assert choose_roof(tags, RESIDENTIAL, CFG.seed, bid, area, lidar=None) == choose_roof(tags, RESIDENTIAL, CFG.seed, bid, area)
