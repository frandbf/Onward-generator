"""Facade walls per building: OSM evidence of the use, and neighbours that do not share a wall."""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Any

from shapely import STRtree, box
from shapely.geometry import mapping

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.grammar import FacadeUse
from onward_generator.detail.scene import _facade_walls, _use_evidence

CFG = load_detail_config()


def _poi(fid: str, x: float, y: float, tags: dict[str, str]) -> dict[str, Any]:
    return {"id": fid, "kind": "poi", "tags": tags, "geometry": {"type": "Point", "coordinates": [x, y]}}


def _area(fid: str, geom: Any, tags: dict[str, str]) -> dict[str, Any]:
    return {"id": fid, "kind": "area", "tags": tags, "geometry": mapping(geom)}


# Three touching buildings along y = 0, and one 20 m away.
FOOTPRINTS = {"a": box(0, 0, 10, 10), "b": box(10, 0, 20, 10), "c": box(20, 0, 30, 10), "far": box(50, 0, 60, 10)}


def test_use_evidence_matches_pois_and_areas_to_footprints() -> None:
    features = [
        _area("area-school", box(-5, -5, 12, 15), {"amenity": "school"}),  # holds a's centre, not b's
        _poi("poi-tree", 15, 5, {"natural": "tree"}),  # no use
        _poi("poi-bank", 5, 5, {"amenity": "bank"}),  # inside a
        _poi("poi-cafe", 25, -1.5, {"amenity": "cafe"}),  # 1.5 m outside c
        _poi("poi-gap", 40, 5, {"shop": "bakery"}),  # 10 m from both c and far: nobody's
        _poi("poi-edge", 20, -1, {"office": "company"}),  # 1 m from b and from c
        _poi("poi-bench", 15, 5, {"amenity": "bench"}),  # street furniture, no use
        {"id": "road", "kind": "highway", "tags": {"amenity": "school"},
         "geometry": {"type": "LineString", "coordinates": [[0, 5], [30, 5]]}},
    ]
    related = _use_evidence(features, FOOTPRINTS, CFG)
    assert dict(related) == {
        "a": [("area-school", {"amenity": "school"}, "enclosing_area"), ("poi-bank", {"amenity": "bank"}, "poi_inside")],
        "c": [("poi-cafe", {"amenity": "cafe"}, "poi_near"), ("poi-edge", {"office": "company"}, "poi_near")],
        "b": [("poi-edge", {"office": "company"}, "poi_near")],
    }


def test_use_evidence_skips_pois_the_config_places_as_street_furniture() -> None:
    cfg = replace(CFG, urban_observed=MappingProxyType({"kiosk": MappingProxyType({"shop": frozenset({"kiosk"})})}))
    features = [_poi("poi-kiosk", 5, 5, {"shop": "kiosk"}), _poi("poi-books", 6, 5, {"shop": "books"})]
    assert dict(_use_evidence(features, FOOTPRINTS, cfg)) == {"a": [("poi-books", {"shop": "books"}, "poi_inside")]}


def _walls(cfg: Any, footprints: dict[str, Any], tags: dict[str, dict[str, str]], features: list[dict[str, Any]],
           landmarks: frozenset[str] = frozenset()) -> dict[str, tuple[FacadeUse, str, str]]:
    ids = sorted(footprints)
    tree = STRtree([footprints[i] for i in ids])
    feature_of = {bid: {"tags": tags.get(bid, {"building": "yes"})} for bid in ids}
    height_of = {bid: {"category": "unknown", "landmark": bid in landmarks} for bid in ids}
    return _facade_walls(ids, footprints, tree, feature_of, height_of, features, cfg)


def test_touching_neighbours_never_share_a_wall_when_the_palette_allows() -> None:
    two = replace(CFG, facade_palettes=MappingProxyType({**CFG.facade_palettes, "residential": ("plaster_ochre", "plaster_cream")}))
    row = {f"b{i}": box(10 * i, 0, 10 * i + 10, 10) for i in range(8)}
    walls = _walls(two, row, {}, [])
    ordered = [walls[f"b{i}"][1] for i in range(8)]
    assert all(left != right for left, right in zip(ordered, ordered[1:], strict=False))
    assert set(ordered) == {"plaster_ochre", "plaster_cream"}
    # A gap wider than probe_m is not a neighbour: both may draw the same wall.
    apart = {"x": box(0, 0, 10, 10), "y": box(10 + CFG.probe_m + 0.5, 0, 30, 10)}
    one = replace(CFG, facade_palettes=MappingProxyType({**CFG.facade_palettes, "residential": ("plaster_ochre",)}))
    assert [w for _, w, _ in _walls(one, apart, {}, []).values()] == ["plaster_ochre", "plaster_ochre"]


def test_each_building_draws_from_the_palette_of_its_use() -> None:
    features = [_poi("poi-school", 15, 5, {"amenity": "school"})]
    tags = {"a": {"building": "office"}, "c": {"building": "yes", "building:material": "brick"}}
    walls = _walls(CFG, FOOTPRINTS, tags, features, landmarks=frozenset({"far"}))
    use_a, wall_a, origin_a = walls["a"]
    assert use_a == FacadeUse("office", "observed", "a", "building=office", "building")
    assert wall_a in CFG.facade_palettes["office"] and origin_a == "generated"
    assert walls["b"][0] == FacadeUse("institutional", "observed", "poi-school", "amenity=school", "poi_inside")
    assert walls["b"][1] in CFG.facade_palettes["institutional"]
    assert walls["c"] == (FacadeUse("residential", "estimated"), "brick_red", "observed")
    assert walls["far"] == (FacadeUse("landmark", "observed", "far", None, "landmark"), "stone_warm", "generated")
