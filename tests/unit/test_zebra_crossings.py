"""Zebra paint on the walkable crossings that OSM records (generation/markings.py)."""

from __future__ import annotations

import math

from shapely import LineString, Point, Polygon, box, union_all
from shapely.affinity import rotate
from shapely.geometry.base import BaseGeometry

from onward_generator.export.lightmap import surface_class
from onward_generator.generation.markings import (
    ZEBRA_LIFT_M,
    ZEBRA_MATCH_M,
    CrossingEvidence,
    Markings,
    PaintedCrossing,
    crossing_evidence,
    is_crossing,
    is_painted,
    paint_crossings,
    summary,
    zebra_bars,
)
from onward_generator.generation.surfaces import CrossingSite, LinearWay, crossing_sites

# A 6 m street along x: kerb to kerb from y = -3 to y = 3.
STREET = box(-50, -3, 50, 3)


def _site(x: float = 10.0, span: float = 8.0) -> CrossingSite:
    return CrossingSite((x, 0.0), (1.0, 0.0), span)


def _same(left: BaseGeometry, right: BaseGeometry) -> bool:
    return left.symmetric_difference(right).area < 1e-9


def _boxes(*ys: float, x: float = 10.0) -> BaseGeometry:
    return union_all([box(x - 2, y, x + 2, y + 0.5) for y in ys])


def test_crossing_nodes_and_crossing_ways_count_as_evidence() -> None:
    assert is_crossing("poi", {"highway": "crossing"})
    assert is_crossing("poi", {"crossing": "traffic_signals", "highway": "traffic_signals"})
    assert is_crossing("highway", {"highway": "footway", "footway": "crossing"})
    assert is_crossing("highway", {"highway": "cycleway", "cycleway": "crossing"})
    assert is_crossing("highway", {"highway": "path", "path": "crossing"})


def test_other_elements_are_not_crossings() -> None:
    assert not is_crossing("poi", {"highway": "traffic_signals"})
    assert not is_crossing("poi", {"railway": "crossing", "crossing": "barrier"})
    assert not is_crossing("highway", {"highway": "footway", "footway": "sidewalk"})
    assert not is_crossing("highway", {"highway": "footway", "crossing": "zebra"})
    assert not is_crossing("line", {"highway": "crossing", "crossing": "zebra"})


def test_unmarked_crossings_get_no_paint() -> None:
    for value in ("no", "unmarked", "informal"):
        assert not is_painted({"crossing": value})
    assert is_painted({"crossing": "uncontrolled"})
    assert is_painted({"crossing": "zebra"})
    assert is_painted({"highway": "crossing"})
    # crossing:markings, when present, decides.
    assert not is_painted({"crossing": "uncontrolled", "crossing:markings": "no"})
    assert is_painted({"crossing": "unmarked", "crossing:markings": "zebra"})


def test_evidence_is_read_from_the_scene_features_sorted_by_id() -> None:
    features = [
        {"id": "osm-node-2", "kind": "poi", "tags": {"highway": "crossing", "crossing": "unmarked"},
         "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}},
        {"id": "osm-node-3", "kind": "poi", "tags": {"amenity": "bench"}, "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
        {"id": "osm-way-1", "kind": "highway", "tags": {"highway": "footway", "footway": "crossing"},
         "geometry": {"type": "LineString", "coordinates": [[0.0, -5.0], [0.0, 5.0]]}},
        {"id": "osm-node-4", "kind": "poi", "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
    ]
    evidence = crossing_evidence(features)
    assert [e.id for e in evidence] == ["osm-node-2", "osm-way-1"]
    assert [e.painted for e in evidence] == [False, True]
    assert _same(evidence[0].geometry.buffer(1), Point(1.0, 2.0).buffer(1))
    assert evidence[1].geometry.equals(LineString([(0, -5), (0, 5)]))


def test_bars_run_along_the_street_half_a_metre_wide_and_apart() -> None:
    """A 6 m street: 5 bars, 0.5 m clear of each kerb, each as long as the 4 m strip is wide."""
    bars = zebra_bars(_site(), STREET)
    assert len(bars) == 5
    assert all(len(bar.exterior.coords) == 5 for bar in bars)
    assert _same(union_all(bars), _boxes(-2.25, -1.25, -0.25, 0.75, 1.75))


def test_the_bar_count_rounds_down_to_fit_between_the_kerb_margins() -> None:
    """7.4 m kerb to kerb leaves 6.4 m: six bars and five gaps fill 5.5 m; a seventh does not fit."""
    bars = zebra_bars(_site(), box(-50, -3.7, 50, 3.7))
    assert _same(union_all(bars), _boxes(-2.75, -1.75, -0.75, 0.25, 1.25, 2.25))
    # Exactly room for 6: 7.5 m leaves 6.5 m, and 6 bars with 5 gaps take 5.5 m.
    assert len(zebra_bars(_site(), box(-50, -3.0, 50, 4.0))) == 6
    assert _same(union_all(zebra_bars(_site(), box(-50, -3.0, 50, 4.0))), _boxes(-2.25, -1.25, -0.25, 0.75, 1.75, 2.75))


def test_the_bars_turn_with_the_street() -> None:
    angle = 30.0
    street = rotate(STREET, angle, origin=(0, 0))
    along = (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
    centre = rotate(Point(10, 0), angle, origin=(0, 0))
    bars = zebra_bars(CrossingSite((centre.x, centre.y), along, 8.0), street)
    expected = rotate(_boxes(-2.25, -1.25, -0.25, 0.75, 1.75), angle, origin=(0, 0))
    assert len(bars) == 5 and union_all(bars).symmetric_difference(expected).area < 1e-6


def test_the_bars_stop_at_the_kerb_of_the_piece_that_holds_the_centre() -> None:
    """A median 2 m past the far kerb is a separate piece of carriageway and gets no bars."""
    carriageway = union_all([STREET, box(-50, 5, 50, 9)])
    assert _same(union_all(zebra_bars(_site(span=30.0), carriageway)), _boxes(-2.25, -1.25, -0.25, 0.75, 1.75))
    assert zebra_bars(CrossingSite((10.0, 20.0), (1.0, 0.0), 8.0), carriageway) == []


def test_the_bars_stay_within_the_strip_span() -> None:
    """On a carriageway wider than the 8 m span, the run is the span: 7 bars between -3.5 and 3.5."""
    bars = zebra_bars(_site(), box(-50, -10, 50, 10))
    assert _same(union_all(bars), _boxes(-3.25, -2.25, -1.25, -0.25, 0.75, 1.75, 2.75))


def test_the_bars_are_centred_between_the_kerbs_not_on_the_centreline() -> None:
    """Kerbs at -2 and 4 on a street turned 30 degrees: 5 bars centred on y = 1."""
    angle = 30.0
    street = rotate(box(-50, -2, 50, 4), angle, origin=(0, 0))
    along = (math.cos(math.radians(angle)), math.sin(math.radians(angle)))
    centre = rotate(Point(10, 0), angle, origin=(0, 0))
    bars = zebra_bars(CrossingSite((centre.x, centre.y), along, 10.0), street)
    expected = rotate(_boxes(-1.25, -0.25, 0.75, 1.75, 2.75), angle, origin=(0, 0))
    assert len(bars) == 5 and union_all(bars).symmetric_difference(expected).area < 1e-6


def test_a_bar_cut_by_the_kerb_is_left_out() -> None:
    notch = box(11.9, 2.0, 13, 3)
    bars = zebra_bars(_site(), STREET.difference(notch))
    assert _same(union_all(bars), _boxes(-2.25, -1.25, -0.25, 0.75))
    # Even a 5 cm² bite drops the bar: a bar stays a whole quad or goes.
    sliver = box(11.95, 2.0, 12.05, 2.01)
    assert _same(union_all(zebra_bars(_site(), STREET.difference(sliver))), _boxes(-2.25, -1.25, -0.25, 0.75))


def test_a_street_too_narrow_for_one_bar_gets_none() -> None:
    assert zebra_bars(_site(), box(-50, -0.7, 50, 0.7)) == []
    assert len(zebra_bars(_site(), box(-50, -0.75, 50, 0.75))) == 1


def _node(fid: str, x: float, y: float = 0.0, painted: bool = True) -> CrossingEvidence:
    return CrossingEvidence(fid, Point(x, y), painted)


def test_only_strips_near_an_osm_crossing_are_painted() -> None:
    sites = [_site(10.0), _site(-30.0), _site(40.0)]
    evidence = [
        _node("osm-node-1", 12.0 + ZEBRA_MATCH_M),  # the strip edge is at x = 12
        _node("osm-node-2", -28.0 + ZEBRA_MATCH_M + 0.01),
        _node("osm-node-3", 40.0, painted=False),
        _node("osm-node-4", 0.0, 30.0),
    ]
    markings = paint_crossings(sites, STREET, evidence)
    first, second, third = markings.crossings
    assert first.evidence == ("osm-node-1",) and first.origin == "observed" and first.painted and len(first.bars) == 5
    assert second.evidence == () and second.origin == "generated" and not second.painted and second.bars == ()
    assert third.evidence == ("osm-node-3",) and third.origin == "observed" and not third.painted and third.bars == ()
    assert markings.unmatched == ("osm-node-2", "osm-node-4")
    assert len(markings.bars) == 5


def test_one_painted_crossing_is_enough_and_evidence_is_listed_by_id() -> None:
    evidence = [_node("osm-node-9", 10.0, painted=False), _node("osm-node-1", 11.0)]
    (crossing,) = paint_crossings([_site()], STREET, sorted(evidence, key=lambda e: e.id)).crossings
    assert crossing.evidence == ("osm-node-1", "osm-node-9") and crossing.painted


def test_overlapping_strips_are_painted_once() -> None:
    evidence = [_node("osm-node-1", 10.0)]
    markings = paint_crossings([_site(10.0), _site(11.0), _site(14.0)], STREET, evidence)
    first, second, third = markings.crossings
    assert first.painted and not second.painted and second.bars == () and second.origin == "observed"
    # A strip that only touches the painted one is painted too.
    assert third.painted and len(markings.bars) == 10


def test_a_strip_off_the_carriageway_is_not_counted() -> None:
    markings = paint_crossings([_site(80.0), _site(10.0)], STREET, [_node("osm-node-1", 80.0)])
    assert [c.site.centre for c in markings.crossings] == [(10.0, 0.0)] and markings.unmatched == ("osm-node-1",)


def test_the_summary_counts_strips_by_origin_and_paint() -> None:
    site = _site()
    bar = box(0, 0, 1, 1)
    markings = Markings((
        PaintedCrossing(site, ("a",), True, (bar, bar)),
        PaintedCrossing(site, ("d",), True, (bar,)),
        PaintedCrossing(site, ("b",), False),
        PaintedCrossing(site, (), False),
        PaintedCrossing(site, (), False),
    ), ("c",))
    assert summary(markings) == {
        "strips": 5, "observed": 3, "observed_painted": 2, "observed_unpainted": 1,
        "generated_unpainted": 2, "bars": 3, "osm_crossings_unmatched": 1,
    }
    assert summary(Markings()) == dict.fromkeys(summary(markings), 0)


def test_crossing_sites_carry_the_street_direction() -> None:
    street = LinearWay("s", "vehicular", LineString([(0, 0), (0, 100)]), 6.0, "config_default")
    sites = crossing_sites([street])
    assert [s.centre for s in sites] == [(0.0, 6.0), (0.0, 50.0), (0.0, 94.0)]
    assert all(s.along == (0.0, 1.0) and s.span_m == 8.0 for s in sites)
    assert _same(sites[0].strip(), box(-4, 4, 4, 8))


def test_paint_is_lifted_and_baked_with_the_ground() -> None:
    assert 0.0 < ZEBRA_LIFT_M <= 0.01
    assert surface_class("road_marking") == "ground"


def test_an_empty_street_has_no_paint() -> None:
    markings = paint_crossings([], Polygon(), [])
    assert markings == Markings() and markings.bars == []


def test_a_crossing_record_keeps_its_centre_to_the_millimetre() -> None:
    site = CrossingSite((1.23456, -7.89012), (1.0, 0.0), 8.0)
    bar = box(0, 0, 1, 1)
    assert PaintedCrossing(site, ("osm-node-1",), True, (bar, bar)).to_record() == {
        "centre_m": [1.235, -7.89], "origin": "observed", "evidence": ["osm-node-1"], "painted": True, "bars": 2,
    }
    assert PaintedCrossing(site, (), False).to_record()["origin"] == "generated"
