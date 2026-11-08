"""``overture match``: places as third-party POIs and buildings as secondary height overrides (offline)."""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest
from shapely import MultiPolygon, Point, box

from onward_generator.heights import load_height_config
from onward_generator.overture.acquire import FetchRecord
from onward_generator.overture.match import (
    DEFAULT_CONFIG_PATH,
    BuildingMatch,
    BuildingTint,
    OvertureConfigError,
    TintLend,
    load_config,
    match_buildings,
    match_report,
    match_tints,
    overrides_document,
    place_tag,
    places_document,
    read_places,
    scene_frame,
    tints_document,
)
from tests.overture_fixture import BLOCK, building, frame, place, scene, source, wgs84, write_buildings, write_places

CFG = load_config()
ALLOWED = frozenset({"CDLA-Permissive-2.0", "Apache-2.0", "CC0-1.0", "CC-BY-4.0"})
BUILDING_ALLOWED = ALLOWED | {"ODbL-1.0"}


def _record(path: Path, rows: int = 0) -> FetchRecord:
    return FetchRecord(
        provider="overture-maps-foundation", theme="buildings", type="building", release="2026-10-21.0", source="s3://x",
        query="q", bbox=[0, 0, 1, 1], bbox_order="south,west,north,east", retrieved_at="2026-10-30T12:00:00+00:00",
        rows=rows, licenses=[], attribution=[], sha256="0" * 64, size_bytes=1, duckdb_version="1.5.6", path=str(path),
    )


def _write_config(tmp_path: Path, **changes: object) -> Path:
    doc = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    for dotted, value in changes.items():
        target = doc
        *parents, last = dotted.split("__")
        for key in parents:
            target = target[key]
        target[last] = value
    path = tmp_path / "overture.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def test_default_config_is_read_as_given() -> None:
    CFG = load_config()
    assert CFG.min_confidence == 0.7 and CFG.operating_status == frozenset({"open"}) and CFG.accept_missing_status is True
    assert CFG.skip_datasets == frozenset({"OpenStreetMap"})
    assert CFG.categories["bakery"] == ("shop", "bakery") and CFG.categories["restaurant"] == ("amenity", "restaurant")
    assert CFG.min_iou == 0.5 and CFG.default_origin == "estimated"
    assert CFG.origins["Microsoft ML Buildings"] == "ai_generated" and CFG.origins["OpenStreetMap"] == "observed"
    assert CFG.confidence == {"observed": 0.8, "estimated": 0.6, "ai_generated": 0.5}
    assert CFG.path == str(DEFAULT_CONFIG_PATH) and CFG.version == "0.1.0"


@pytest.mark.parametrize(("changes", "fragment"), [
    ({"places__min_confidence": 1.5}, "/places/min_confidence"),
    ({"places__operating_status": ["gone"]}, "/places/operating_status/0"),
    ({"places__categories": {"bakery": {"shop": "bakery", "amenity": "cafe"}}}, "/places/categories/bakery"),
    ({"places__categories": {"bakery": {"building": "yes"}}}, "/places/categories/bakery"),
    ({"buildings__min_iou": 0}, "/buildings/min_iou"),
    ({"buildings__default_origin": "guess"}, "/buildings/default_origin"),
])
def test_invalid_config_is_refused_with_its_path(tmp_path: Path, changes: dict, fragment: str) -> None:
    with pytest.raises(OvertureConfigError, match=fragment):
        load_config(_write_config(tmp_path, **changes))


def test_unreadable_config_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OvertureConfigError) as caught:
        load_config(tmp_path / "missing.json")
    assert str(caught.value).startswith(f"{tmp_path / 'missing.json'}: ")


def test_every_config_problem_is_listed(tmp_path: Path) -> None:
    path = _write_config(tmp_path, places__min_confidence=2, buildings__min_iou=0)
    with pytest.raises(OvertureConfigError) as caught:
        load_config(path)
    assert str(caught.value) == (
        f"{path.resolve()}: /buildings/min_iou: 0 is less than or equal to the minimum of 0; "
        "/places/min_confidence: 2 is greater than the maximum of 1"
    )


def test_config_values_are_read_from_the_file(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, places__accept_missing_status=False, places__min_confidence=0.3, buildings__min_iou=0.8))
    assert cfg.accept_missing_status is False and cfg.min_confidence == 0.3 and cfg.min_iou == 0.8
    assert cfg.path == str((tmp_path / "overture.json").resolve())


def test_place_tag_tries_the_taxonomy_before_the_basic_category() -> None:
    assert place_tag("bakery", "casual_eatery", CFG) == ("shop", "bakery", "bakery")
    assert place_tag("tapas_bar", "casual_eatery", CFG) == ("amenity", "bar", "tapas_bar")
    assert place_tag("spanish_restaurant", "restaurant", CFG) == ("amenity", "restaurant", "restaurant")
    assert place_tag(None, "restaurant", CFG) == ("amenity", "restaurant", "restaurant")
    assert place_tag("school", "place_of_learning", CFG) is None
    assert place_tag(None, None, CFG) is None


def test_scene_frame_needs_the_local_metric_grid() -> None:
    doc = scene({})
    assert scene_frame(doc).to_dict()["origin_projected"] == frame().to_dict()["origin_projected"]
    for key, value in (("unit", "deg"), ("coordinate_system", "wgs84")):
        bad = copy.deepcopy(doc)
        bad["frame"][key] = value
        with pytest.raises(ValueError, match=r"^scene must be in the local metric grid \(unit m\)$"):
            scene_frame(bad)
    from onward_generator.geo.projection import LocalFrame

    utm = copy.deepcopy(doc)
    utm["frame"]["crs"] = "EPSG:32630"
    assert scene_frame(utm).crs == "EPSG:32630" and LocalFrame.create(-1.12863, 37.98407).crs != "EPSG:32630"


def _places(tmp_path: Path) -> Path:
    return write_places(tmp_path / "places.parquet", [
        place("p1", 5.12345, 5.54321, name="  Horno   San Juan ", sources=[source("meta", "CDLA-Permissive-2.0", "/properties/name"),
                                                             source("Foursquare", "Apache-2.0")]),
        place("p2", 15.0, 5.0, confidence=0.7, primary="pharmacy", basic="health_and_medical"),
        place("p3", 1.0, 1.0, confidence=0.69),
        place("p4", 1.0, 1.0, confidence=None),
        place("p5", 1.0, 1.0, status=None),
        place("p6", 1.0, 1.0, status="permanently_closed"),
        place("p7", 1.0, 1.0, primary="school", basic="place_of_learning"),
        place("p8", 2.0, 2.0, name=None, primary=None, basic="restaurant", sources=[source("Instituto Geográfico Nacional (España)", None)]),
        place("p9", 3.0, 3.0, status="temporarily_closed"),
        place("pa", 4.0, 4.0, confidence=0.876543, sources=[source("Uberall", "CDLA-Permissive-2.0")]),
        place("pc", 4.0, 4.0, sources=[]),
        place("pd", 4.0, 4.0, sources=[source("Somebody", "LicenseRef-closed")]),
        place("pb", 4.0, 4.0, sources=[{"property": None, "dataset": "AllThePlaces", "license": "CC0-1.0"}]),
        place("q5", 1.0, 1.0, status=None),
        place("q6", 1.0, 1.0, status="permanently_closed"),
        place("q7", 1.0, 1.0, primary="school", basic="place_of_learning"),
        place("q9", 3.0, 3.0, status="temporarily_closed"),
    ])


STRICT = dataclasses.replace(CFG, accept_missing_status=False)


def test_read_places_keeps_confident_open_mapped_places_in_the_local_grid(tmp_path: Path) -> None:
    kept, skipped = read_places(_places(tmp_path), frame(), STRICT, ALLOWED)
    assert [p["gers_id"] for p in kept] == ["p1", "p2", "p8", "pa", "pb"]
    assert skipped == {"licence_not_allowed": 2, "low_confidence": 2, "no_operating_status": 2, "status_permanently_closed": 2,
                       "status_temporarily_closed": 2, "unmapped_category": 2}
    first = kept[0]
    exact = frame().to_local(wgs84(Point(5.12345, 5.54321)))
    assert first["id"] == "overture-place-p1" and first["point_m"] == [round(exact.x, 3), round(exact.y, 3)]
    assert kept[3]["confidence"] == 0.8765 and (kept[3]["dataset"], kept[3]["license"]) == ("Uberall", "CDLA-Permissive-2.0")
    assert (kept[4]["dataset"], kept[4]["license"]) == ("AllThePlaces", "CC0-1.0")
    assert first["tags"] == {"shop": "bakery", "name": "Horno San Juan"}
    assert first["category"] == "bakery" and first["basic_category"] == "casual_eatery" and first["taxonomy_primary"] == "bakery"
    assert first["confidence"] == 0.9 and first["operating_status"] == "open"
    assert first["dataset"] == "Foursquare" and first["license"] == "Apache-2.0"
    assert first["attribute_origin"] == "observed" and first["observed_by"] == "third_party"
    assert first["attribution"]["notice"].startswith("Copyright 2024 Foursquare Labs")
    assert kept[1]["tags"] == {"amenity": "pharmacy", "name": "Horno"} and kept[1]["confidence"] == 0.7
    assert kept[2]["tags"] == {"amenity": "restaurant"} and kept[2]["license"] == "CC-BY-4.0"
    assert kept[2]["taxonomy_primary"] is None


def test_read_places_can_accept_a_missing_status(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, places__accept_missing_status=True, places__operating_status=["open", "temporarily_closed"]))
    kept, skipped = read_places(_places(tmp_path), frame(), cfg, ALLOWED)
    assert [p["gers_id"] for p in kept] == ["p1", "p2", "p5", "p8", "p9", "pa", "pb", "q5", "q9"]
    assert "no_operating_status" not in skipped


def test_read_places_skips_a_place_that_is_not_a_point(tmp_path: Path) -> None:
    path = write_places(tmp_path / "p.parquet", [place("x", 0, 0), place("x2", 0, 0)])
    import duckdb

    from tests.overture_fixture import wgs84

    con = duckdb.connect()
    con.execute(f"CREATE TABLE t AS SELECT * FROM read_parquet('{path}')")
    con.execute("UPDATE t SET geometry = ST_GeomFromWKB(?)", [__import__("shapely").to_wkb(wgs84(box(0, 0, 1, 1)))])
    con.execute("INSERT INTO t SELECT * REPLACE ('y' AS id) FROM t LIMIT 1")
    con.execute("UPDATE t SET geometry = ST_GeomFromWKB(?) WHERE id = 'y'", [__import__("shapely").to_wkb(wgs84(Point(1, 1)))])
    con.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    kept, skipped = read_places(path, frame(), CFG, ALLOWED)
    assert [p["gers_id"] for p in kept] == ["y"] and skipped == {"not_a_point": 2}


def test_a_wkb_blob_column_is_read_as_a_place(tmp_path: Path) -> None:
    """A release can store geometry as WKB bytes. ST_AsWKB rejects that BLOB."""
    path = write_places(
        tmp_path / "p.parquet",
        [place("p1", 5.12345, 5.54321, sources=[source("Foursquare", "Apache-2.0")])],
        geometry_blob=True,
    )
    kept, skipped = read_places(path, frame(), STRICT, ALLOWED)
    exact = frame().to_local(wgs84(Point(5.12345, 5.54321)))
    assert skipped == {} and kept[0]["gers_id"] == "p1"
    assert kept[0]["point_m"] == [round(exact.x, 3), round(exact.y, 3)]


def test_a_geometry_column_of_another_type_is_refused(tmp_path: Path) -> None:
    import duckdb

    path = tmp_path / "places.parquet"
    connection = duckdb.connect()
    try:
        connection.execute("CREATE TABLE t (id VARCHAR, geometry INTEGER)")
        connection.execute("INSERT INTO t VALUES ('x', 1)")
        connection.execute(f"COPY t TO '{path}' (FORMAT parquet)")
    finally:
        connection.close()
    with pytest.raises(ValueError, match=r"^places.parquet: geometry is INTEGER, expected BLOB or GEOMETRY$"):
        read_places(path, frame(), STRICT, ALLOWED)


def test_a_place_without_a_dataset_or_licence_is_unknown(tmp_path: Path) -> None:
    path = write_places(tmp_path / "p.parquet", [place("u", 1.0, 1.0, sources=[{"property": "", "dataset": None, "license": None}])])
    assert read_places(path, frame(), CFG, ALLOWED) == ([], {"licence_not_allowed": 1})
    kept, _ = read_places(path, frame(), CFG, ALLOWED | {"unknown"})
    assert (kept[0]["dataset"], kept[0]["license"]) == ("unknown", "unknown")


def test_places_document_counts_and_attributes(tmp_path: Path) -> None:
    kept, skipped = read_places(_places(tmp_path), frame(), STRICT, ALLOWED)
    doc = places_document(kept, _record(tmp_path / "places.parquet", rows=9), skipped, frame())
    assert doc["schema"] == "onward_generator.overture_places" and doc["schema_version"] == "0.1.0"
    assert doc["source"] == {"path": str(tmp_path / "places.parquet"), "sha256": "0" * 64, "release": "2026-10-21.0",
                             "retrieved_at": "2026-10-30T12:00:00+00:00"}
    assert doc["frame"] == frame().to_dict()
    assert doc["counts"] == {"rows": 9, "kept": 5, "skipped": skipped}
    assert doc["schema"] == "onward_generator.overture_places"
    assert doc["licenses"] == [
        {"dataset": "AllThePlaces", "license": "CC0-1.0", "places": 1},
        {"dataset": "Foursquare", "license": "Apache-2.0", "places": 1},
        {"dataset": "Instituto Geográfico Nacional (España)", "license": "CC-BY-4.0", "places": 1},
        {"dataset": "Uberall", "license": "CDLA-Permissive-2.0", "places": 1},
        {"dataset": "meta", "license": "CDLA-Permissive-2.0", "places": 1},
    ]
    assert [line["license"] for line in doc["attribution"]] == ["CC0-1.0", "Apache-2.0", "CC-BY-4.0", "CDLA-Permissive-2.0", "CDLA-Permissive-2.0"]
    assert doc["places"] == kept


def _buildings(tmp_path: Path, rows: list) -> Path:
    return write_buildings(tmp_path / "buildings.parquet", rows)


def test_a_wkb_blob_column_still_matches_a_footprint(tmp_path: Path) -> None:
    path = write_buildings(
        tmp_path / "buildings.parquet",
        [building("o1", box(0, 0, 10, 10), height=14.5, floors=None, sources=[source("Esri Community Maps", "CC-BY-4.0")])],
        geometry_blob=True,
    )
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    assert [(m.building_id, m.gers_id, m.height_m) for m in matches] == [("osm-way-1", "o1", 14.5)]
    assert skipped == {"unmatched": 0}


def test_buildings_match_by_intersection_over_union_one_to_one(tmp_path: Path) -> None:
    path = _buildings(tmp_path, [
        building("o1", box(0, 0, 10, 10), height=14.5, floors=None, facade="#aa8866", roof="#552211",  # IoU 1 with way-1
                 sources=[source("Esri Community Maps", "CC-BY-4.0")]),
        building("o1b", box(0.5, 0, 10, 10), height=30.0, floors=9),  # also way-1, IoU 0.95: loses, way-1 is taken
        building("o2", box(12, 0, 19, 10), height=None, floors=3,  # IoU 0.7 with way-2
                 sources=[source("Esri Community Maps", None), source("Microsoft ML Buildings", "ODbL-1.0", "/properties/num_floors")]),
        building("o3", box(0, 20, 4.9, 30), height=9.0, floors=3),  # IoU 0.49 with way-3: below min_iou
        building("o4", box(100, 100, 110, 110), height=8.0),  # meets no OSM footprint
        building("o5", box(0, 20, 10, 30), height=None, floors=None),  # nothing to lend
        building("o6", box(0, 20, 10, 30), height=None, floors=None),
    ])
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    assert [(m.building_id, m.gers_id) for m in matches] == [("osm-way-1", "o1"), ("osm-way-2", "o2")]
    one, two = matches
    assert one.iou == pytest.approx(1.0, abs=1e-3) and one.height_m == 14.5 and one.levels is None
    assert one.dataset == "Esri Community Maps" and one.license == "CC-BY-4.0" and one.origin == "observed"
    assert one.facade_color == "#aa8866" and one.roof_color == "#552211"
    assert two.iou == pytest.approx(0.7, abs=1e-3) and two.height_m is None and two.levels == 3
    assert one.building_id == "osm-way-1" and one.gers_id == "o1"
    assert two.dataset == "Microsoft ML Buildings" and two.origin == "ai_generated"
    assert skipped == {"no_height_or_floors": 2, "unmatched": 3}


def test_the_height_source_names_the_dataset_and_unknown_datasets_are_estimated(tmp_path: Path) -> None:
    path = _buildings(tmp_path, [
        building("o1", box(0, 0, 10, 10), height=11.0, floors=4,
                 sources=[source("Someone", "CC0-1.0"), source("Google Open Buildings", None, "/properties/height")]),
        building("o2", box(12, 0, 22, 10), height=7.0, floors=2, sources=[source("Someone", "CC0-1.0")]),
        building("o3", box(0, 20, 10, 30), height=8.0, floors=None, sources=[source("Nobody", None)]),
    ])
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    # o1: height from Google, floors from another dataset: only the height is kept, with Google's licence.
    # o3: no licence stated or documented, so "unknown", which no allowlist has: not lent.
    assert [(m.dataset, m.license, m.origin, m.height_m, m.levels) for m in matches] == [
        ("Google Open Buildings", "CC-BY-4.0", "ai_generated", 11.0, None), ("Someone", "CC0-1.0", "estimated", 7.0, 2),
    ]
    assert skipped == {"licence_not_allowed": 1, "unmatched": 0}


def test_a_building_whose_licence_is_not_allowed_lends_nothing(tmp_path: Path) -> None:
    path = _buildings(tmp_path, [
        building("o1", box(0, 0, 10, 10), height=9.0, floors=None, sources=[]),
        building("o2", box(12, 0, 22, 10), height=9.0, floors=3, sources=[source("Microsoft ML Buildings", "ODbL-1.0")]),
    ])
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, ALLOWED)
    assert matches == [] and skipped == {"licence_not_allowed": 2, "unmatched": 0}
    lenient = match_buildings(path, scene(BLOCK), CFG, ALLOWED | {"unknown", "ODbL-1.0"})[0]
    assert [(m.dataset, m.license, m.origin) for m in lenient] == [
        ("unknown", "unknown", "estimated"), ("Microsoft ML Buildings", "ODbL-1.0", "ai_generated"),
    ]


def test_openstreetmap_values_are_not_lent_again(tmp_path: Path) -> None:
    path = _buildings(tmp_path, [
        building("o1", box(0, 0, 10, 10), height=11.0, floors=4, sources=[source("OpenStreetMap", "ODbL-1.0")]),
        building("o1b", box(0, 0, 10, 10), height=11.0, floors=4, sources=[source("OpenStreetMap", "ODbL-1.0")]),
        building("o2", box(12, 0, 22, 10), height=13.0, floors=4,
                 sources=[source("OpenStreetMap", "ODbL-1.0"), source("Microsoft ML Buildings", "ODbL-1.0", "/properties/height")]),
        building("o3", box(0, 20, 10, 30), height=13.0, floors=5,
                 sources=[source("Microsoft ML Buildings", "ODbL-1.0"), source("OpenStreetMap", "ODbL-1.0", "/properties/height")]),
    ])
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    assert [(m.building_id, m.height_m, m.levels, m.dataset) for m in matches] == [
        ("osm-way-2", 13.0, None, "Microsoft ML Buildings"), ("osm-way-3", None, 5, "Microsoft ML Buildings"),
    ]
    assert skipped == {"skipped_dataset": 2, "unmatched": 0}


def test_a_height_outside_the_heights_bounds_keeps_only_the_floors(tmp_path: Path) -> None:
    path = _buildings(tmp_path, [
        building("o1", box(0, 0, 10, 10), height=2.4, floors=1),
        building("o2", box(12, 0, 22, 10), height=121.0, floors=None),
        building("o3", box(0, 20, 10, 30), height=120.0, floors=None),
    ])
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    assert [(m.building_id, m.height_m, m.levels) for m in matches] == [("osm-way-1", None, 1), ("osm-way-3", 120.0, None)]
    assert skipped["height_out_of_bounds"] == 2
    low = _buildings(tmp_path, [building("o1", box(0, 0, 10, 10), height=2.5, floors=None)])
    assert [m.height_m for m in match_buildings(low, scene(BLOCK), CFG, BUILDING_ALLOWED)[0]] == [2.5]


def test_iou_at_the_threshold_matches_and_a_point_footprint_does_not(tmp_path: Path) -> None:
    from onward_generator.overture.match import _iou

    path = _buildings(tmp_path, [
        building("half", box(0, 0, 5, 10), height=9.0),  # IoU about 0.5 with way-1
        building("dot", Point(15, 5), height=9.0),
        building("dot2", Point(15, 5), height=9.0),
        building("pair", MultiPolygon([box(12, 0, 22, 10), box(12, 20, 13, 21)]), height=11.0),
        building("third", box(0, 20, 10, 35), height=12.0),  # IoU 2/3 with way-3
    ])
    exact = _iou(BLOCK["osm-way-1"], frame().to_local(wgs84(box(0, 0, 5, 10))))
    matches, skipped = match_buildings(path, scene(BLOCK), dataclasses.replace(CFG, min_iou=exact), BUILDING_ALLOWED)
    assert [(m.building_id, m.gers_id, m.iou) for m in matches] == [
        ("osm-way-1", "half", 0.5), ("osm-way-2", "pair", 0.9901), ("osm-way-3", "third", 0.6667),
    ]
    assert skipped == {"no_footprint": 2, "unmatched": 0}
    assert _iou(Point(0, 0), Point(0, 0)) == 0.0  # no area: no division by zero
    assert _iou(box(0, 0, 0.5, 0.5), box(0, 0, 0.5, 1)) == 0.5  # a union under 1 m² still counts


def test_a_tie_goes_to_the_first_row_then_the_lowest_id(tmp_path: Path) -> None:
    loose = dataclasses.replace(CFG, min_iou=0.2)
    path = _buildings(tmp_path, [
        building("b", box(5, 0, 17, 10), height=9.0),  # IoU 0.294 with way-1 and with way-2
        building("a", box(5, 0, 17, 10), height=7.0),
    ])
    matches, _ = match_buildings(path, scene(BLOCK), loose, BUILDING_ALLOWED)
    assert [(m.building_id, m.gers_id) for m in matches] == [("osm-way-1", "a"), ("osm-way-2", "b")]


def test_zero_height_or_floors_lend_nothing(tmp_path: Path) -> None:
    path = _buildings(tmp_path, [building("o1", box(0, 0, 10, 10), height=0.0, floors=0),
                                 building("o2", box(12, 0, 22, 10), height=1.0, floors=None)])
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    assert matches == [] and skipped == {"height_out_of_bounds": 1, "no_height_or_floors": 1, "unmatched": 0}


def test_a_scene_without_buildings_matches_nothing(tmp_path: Path) -> None:
    path = _buildings(tmp_path, [building("o1", box(0, 0, 10, 10)), building("o2", box(0, 0, 10, 10))])
    matches, skipped = match_buildings(path, scene({}), CFG, BUILDING_ALLOWED)
    assert matches == [] and skipped == {"no_footprint": 2, "unmatched": 0}


def test_overrides_are_valid_secondary_overrides_for_heights(tmp_path: Path) -> None:
    matches = [
        BuildingMatch("osm-way-1", "o1", 0.91, 14.5, 4, "OpenStreetMap", "ODbL-1.0", "observed", None, None),
        BuildingMatch("osm-way-2", "o2", 0.7, None, 3, "Microsoft ML Buildings", "ODbL-1.0", "ai_generated", "#fff", None),
        BuildingMatch("osm-way-3", "o3", 0.6, 9.0, None, "Someone", "CC0-1.0", "estimated", None, None),
    ]
    doc = overrides_document(matches, _record(tmp_path / "b.parquet"), CFG)
    assert doc["version"] == "0.1.0"
    assert doc["overrides"][0] == {
        "id": "osm-way-1", "height_m": 14.5, "levels": 4, "source": "Overture Maps buildings 2026-10-21.0: OpenStreetMap",
        "license": "ODbL-1.0", "retrieved_at": "2026-10-30T12:00:00+00:00", "confidence": 0.8, "origin": "observed",
        "note": "IoU 0.91 with Overture o1",
    }
    assert "height_m" not in doc["overrides"][1] and doc["overrides"][1]["confidence"] == 0.5
    assert "levels" not in doc["overrides"][2] and doc["overrides"][2]["confidence"] == 0.6
    path = tmp_path / "heights_overrides.overture.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    cfg = load_height_config(secondary_overrides_path=path)
    assert cfg.secondary_overrides["osm-way-2"].origin == "ai_generated" and cfg.overrides == {}


def test_match_report_counts_by_origin_and_licence(tmp_path: Path) -> None:
    matches = [
        BuildingMatch("osm-way-1", "o1", 0.91, 14.5, 4, "OpenStreetMap", "ODbL-1.0", "observed", "#a", "#b"),
        BuildingMatch("osm-way-2", "o2", 0.7, None, 3, "Microsoft ML Buildings", "ODbL-1.0", "ai_generated", None, None),
        BuildingMatch("osm-way-3", "o3", 0.6, 9.0, None, "Microsoft ML Buildings", "ODbL-1.0", "ai_generated", None, None),
    ]
    places = {"counts": {"rows": 3, "kept": 1, "skipped": {}}, "licenses": [{"dataset": "meta"}]}
    report = match_report(scene_ref={"path": "s"}, cfg=CFG, places=places, matches=matches, building_skips={"unmatched": 2},
                          buildings_record=_record(tmp_path / "b.parquet", rows=5), osm_buildings=7)
    assert report["schema"] == "onward_generator.overture_match_report" and report["scene"] == {"path": "s"}
    assert report["schema_version"] == "0.1.0"
    assert report["config"] == {"path": CFG.path, "version": "0.1.0"}
    assert report["places"] == places
    b = report["buildings"]
    assert b["source"] == {"path": str(tmp_path / "b.parquet"), "sha256": "0" * 64, "release": "2026-10-21.0"}
    assert b["counts"] == {"overture_rows": 5, "osm_buildings": 7, "matched": 3, "with_height": 2, "with_levels": 2,
                           "by_origin": {"ai_generated": 2, "observed": 1}, "skipped": {"unmatched": 2}}
    assert b["licenses"] == [{"dataset": "Microsoft ML Buildings", "license": "ODbL-1.0", "buildings": 2},
                             {"dataset": "OpenStreetMap", "license": "ODbL-1.0", "buildings": 1}]
    assert b["matches"][0]["tint_hints"] == {"facade_color": "#a", "roof_color": "#b"}
    assert b["matches"][1] == matches[1].to_dict()


# Plan C2: Overture colours as tints, a pairing apart from the heights.

ESRI = "Esri Community Maps"


def _tints(tmp_path: Path, rows: list, cfg: object = CFG, allowed: frozenset[str] = BUILDING_ALLOWED) -> tuple[list, dict[str, int]]:
    return match_tints(_buildings(tmp_path, rows), scene(BLOCK), cfg, allowed)  # type: ignore[arg-type]


def test_an_observed_colour_is_lent_with_its_evidence(tmp_path: Path) -> None:
    tints, skipped = _tints(tmp_path, [
        building("o1", box(0, 0, 10, 10), facade="#C88C3C", roof="#b43c28",
                 sources=[source("OpenStreetMap", "ODbL-1.0"), source(ESRI, None, "/properties/facade_color"),
                          source(ESRI, "CC-BY-4.0", "/properties/roof_color")]),
    ])
    (tint,) = tints
    assert (tint.building_id, tint.gers_id, tint.iou) == ("osm-way-1", "o1", 1.0)
    assert tint.facade == TintLend("#C88C3C", ESRI, "CC-BY-4.0", "observed")
    assert tint.roof == TintLend("#b43c28", ESRI, "CC-BY-4.0", "observed")
    assert skipped == {"unmatched": 0}
    assert tint.to_dict() == {
        "building_id": "osm-way-1", "gers_id": "o1", "iou": 1.0,
        "facade": {"colour": "#C88C3C", "dataset": ESRI, "license": "CC-BY-4.0", "origin": "observed",
                   "attribution": {"text": "Esri Community Maps contributors; Overture Maps Foundation", "license": "CC-BY-4.0",
                                   "license_url": "https://creativecommons.org/licenses/by/4.0/"}},
        "roof": {"colour": "#b43c28", "dataset": ESRI, "license": "CC-BY-4.0", "origin": "observed",
                 "attribution": {"text": "Esri Community Maps contributors; Overture Maps Foundation", "license": "CC-BY-4.0",
                                 "license_url": "https://creativecommons.org/licenses/by/4.0/"}},
    }
    assert "roof" not in BuildingTint("a", "g", 0.5, tint.facade, None).to_dict()
    assert "facade" not in BuildingTint("a", "g", 0.5, None, tint.roof).to_dict()


def test_an_openstreetmap_colour_is_not_lent_and_the_property_source_wins(tmp_path: Path) -> None:
    tints, skipped = _tints(tmp_path, [
        building("osm", box(0, 0, 10, 10), facade="#ffffff", roof="#778899", sources=[source("OpenStreetMap", "ODbL-1.0")]),
        # The property's own source decides, either way.
        building("prop-osm", box(12, 0, 22, 10), facade="#ffffff",
                 sources=[source(ESRI, None), source("OpenStreetMap", "ODbL-1.0", "/properties/facade_color")]),
        building("prop-esri", box(0, 20, 10, 30), roof="#778899",
                 sources=[source("OpenStreetMap", "ODbL-1.0"), source(ESRI, None, "/properties/roof_color")]),
    ])
    assert [(t.building_id, t.gers_id, t.facade, t.roof is not None) for t in tints] == [("osm-way-3", "prop-esri", None, True)]
    assert skipped == {"skipped_dataset": 3, "unmatched": 0}


def test_a_licence_off_the_allowlist_is_refused(tmp_path: Path) -> None:
    tints, skipped = _tints(tmp_path, [
        building("unknown", box(0, 0, 10, 10), facade="#ffffff", sources=[source("Esri Community Maps", "unknown")]),
        building("odd", box(12, 0, 22, 10), roof="#ffffff", sources=[source("Esri Community Maps", "Proprietary-1.0")]),
    ])
    assert tints == [] and skipped == {"licence_not_allowed": 2, "unmatched": 0}
    # Without any stated or documented licence the dataset is unknown too.
    tints, skipped = _tints(tmp_path, [building("bare", box(0, 0, 10, 10), facade="#ffffff", sources=[])])
    assert tints == [] and skipped == {"licence_not_allowed": 1, "unmatched": 0}


def test_a_dataset_that_is_not_observed_is_refused(tmp_path: Path) -> None:
    tints, skipped = _tints(tmp_path, [
        building("ml", box(0, 0, 10, 10), facade="#ffffff", sources=[source("Microsoft ML Buildings", "ODbL-1.0")]),
        building("other", box(12, 0, 22, 10), facade="#ffffff", sources=[source("Someone", "CC0-1.0")]),  # estimated by default
        building("ign", box(0, 20, 10, 30), facade="#ffffff", sources=[source("Instituto Geográfico Nacional (España)", None)]),
    ])
    assert [(t.building_id, t.facade.dataset if t.facade else None) for t in tints] == [
        ("osm-way-3", "Instituto Geográfico Nacional (España)")]
    assert skipped == {"origin_not_observed": 2, "unmatched": 0}
    # The refusal order: a skipped dataset before the licence, the licence before the origin, the origin before the value.
    order = _tints(tmp_path, [
        building("a", box(0, 0, 10, 10), facade="nope", sources=[source("OpenStreetMap", "unknown")]),
        building("b", box(12, 0, 22, 10), facade="nope", sources=[source("Microsoft ML Buildings", "unknown")]),
        building("c", box(0, 20, 10, 30), facade="nope", sources=[source("Microsoft ML Buildings", "ODbL-1.0")]),
    ])
    assert order == ([], {"licence_not_allowed": 1, "origin_not_observed": 1, "skipped_dataset": 1, "unmatched": 0})


def test_a_value_that_is_not_a_colour_is_refused(tmp_path: Path) -> None:
    tints, skipped = _tints(tmp_path, [
        building("word", box(0, 0, 10, 10), facade="notacolour"),
        building("short", box(12, 0, 22, 10), facade="#12"),
        building("empty", box(0, 20, 10, 30), facade=""),
    ])
    assert tints == [] and skipped == {"colour_unparseable": 3, "unmatched": 0}
    tints, skipped = _tints(tmp_path, [building("half", box(0, 0, 10, 10), facade="#aabbcc", roof="#zz")])
    assert [(t.facade.colour if t.facade else None, t.roof) for t in tints] == [("#aabbcc", None)]
    assert skipped == {"colour_unparseable": 1, "unmatched": 0}


def test_tints_pair_by_intersection_over_union(tmp_path: Path) -> None:
    from onward_generator.overture.match import _iou

    exact = _iou(BLOCK["osm-way-1"], frame().to_local(wgs84(box(0, 0, 5, 10))))
    at = dataclasses.replace(CFG, min_iou=exact)
    tints, skipped = _tints(tmp_path, [building("half", box(0, 0, 5, 10), facade="#ffffff")], at)
    assert [(t.building_id, t.iou) for t in tints] == [("osm-way-1", 0.5)] and skipped == {"unmatched": 0}
    above = dataclasses.replace(CFG, min_iou=exact + 1e-9)
    assert _tints(tmp_path, [building("half", box(0, 0, 5, 10), facade="#ffffff")], above) == ([], {"unmatched": 1})
    # 0.49 with way-3, and one row far from every footprint: both unmatched. A point is no footprint.
    tints, skipped = _tints(tmp_path, [
        building("low", box(0, 20, 4.9, 30), facade="#ffffff"),
        building("far", box(100, 100, 110, 110), facade="#ffffff"),
        building("dot", Point(15, 5), facade="#ffffff"),
    ])
    assert tints == [] and skipped == {"no_footprint": 1, "unmatched": 2}


def test_one_footprint_takes_one_colour_the_best_overlap_first(tmp_path: Path) -> None:
    tints, skipped = _tints(tmp_path, [
        building("worse", box(0.5, 0, 10, 10), facade="#000000"),  # IoU 0.95 with way-1
        building("best", box(0, 0, 10, 10), facade="#ffffff"),  # IoU 1
    ])
    assert [(t.building_id, t.gers_id) for t in tints] == [("osm-way-1", "best")] and skipped == {"unmatched": 1}
    loose = dataclasses.replace(CFG, min_iou=0.2)
    tie, _ = _tints(tmp_path, [
        building("b", box(5, 0, 17, 10), facade="#000000"),  # IoU 0.294 with way-1 and with way-2
        building("a", box(5, 0, 17, 10), facade="#ffffff"),
    ], loose)
    assert [(t.building_id, t.gers_id) for t in tie] == [("osm-way-1", "a"), ("osm-way-2", "b")]


def test_a_colour_is_lent_without_a_height_and_the_overrides_stay_as_they_were(tmp_path: Path) -> None:
    rows = [
        building("o1", box(0, 0, 10, 10), height=14.5, floors=None, facade="#aa8866", roof="#552211",
                 sources=[source(ESRI, "CC-BY-4.0")]),
        # A colour-only row over the same footprint: it lends only a colour.
        building("c1", box(0, 0, 10, 10), height=None, floors=None, facade="#ffffff", sources=[source(ESRI, None)]),
        building("o2", box(12, 0, 19, 10), height=None, floors=3, sources=[source(ESRI, None)]),
        building("c3", box(0, 20, 10, 30), height=None, floors=None, roof="#ffffff", sources=[source(ESRI, None)]),
    ]
    path = _buildings(tmp_path, rows)
    matches, skipped = match_buildings(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    assert [(m.building_id, m.gers_id) for m in matches] == [("osm-way-1", "o1"), ("osm-way-2", "o2")]
    assert skipped == {"no_height_or_floors": 2, "unmatched": 0}
    record = _record(path, rows=4)
    golden = {
        "version": "0.1.0", "overrides": [
            {"id": "osm-way-1", "height_m": 14.5, "source": "Overture Maps buildings 2026-10-21.0: Esri Community Maps",
             "license": "CC-BY-4.0", "retrieved_at": "2026-10-30T12:00:00+00:00", "confidence": 0.8, "origin": "observed",
             "note": "IoU 1.00 with Overture o1"},
            {"id": "osm-way-2", "levels": 3, "source": "Overture Maps buildings 2026-10-21.0: Esri Community Maps",
             "license": "CC-BY-4.0", "retrieved_at": "2026-10-30T12:00:00+00:00", "confidence": 0.8, "origin": "observed",
             "note": "IoU 0.70 with Overture o2"},
        ],
    }
    assert overrides_document(matches, record, CFG) == golden
    assert [m.to_dict()["tint_hints"] for m in matches] == [{"facade_color": "#aa8866", "roof_color": "#552211"},
                                                             {"facade_color": None, "roof_color": None}]
    tints, tint_skips = match_tints(path, scene(BLOCK), CFG, BUILDING_ALLOWED)
    # Rows are read in id order: c1 ties with o1 for way-1 and comes first, so way-1 takes its height from o1 and its
    # colour from c1 (ADR-021 amendment: two pairings). c3 lends way-3 a roof though it has no height.
    assert [(t.building_id, t.gers_id) for t in tints] == [("osm-way-1", "c1"), ("osm-way-3", "c3")]
    assert tint_skips == {"unmatched": 1}


def test_the_tints_document_lists_licences_and_attribution(tmp_path: Path) -> None:
    tints, skipped = _tints(tmp_path, [
        building("o3", box(0, 20, 10, 30), facade="#ffffff", roof="#778899", sources=[source(ESRI, None)]),
        building("o1", box(0, 0, 10, 10), roof="#778899", sources=[source("Instituto Geográfico Nacional (España)", None)]),
        building("x", box(12, 0, 22, 10), facade="#ffffff", sources=[source("OpenStreetMap", "ODbL-1.0")]),
    ])
    doc = tints_document(tints, _record(tmp_path / "b.parquet", rows=3), skipped, frame())
    assert doc["schema"] == "onward_generator.overture_tints" and doc["schema_version"] == "0.1.0"
    assert doc["source"] == {"path": str(tmp_path / "b.parquet"), "sha256": "0" * 64, "release": "2026-10-21.0",
                             "retrieved_at": "2026-10-30T12:00:00+00:00"}
    assert doc["frame"] == frame().to_dict()
    assert doc["counts"] == {"rows": 3, "matched": 2, "facade": 1, "roof": 2, "skipped": {"skipped_dataset": 1, "unmatched": 0}}
    assert doc["licenses"] == [{"dataset": ESRI, "license": "CC-BY-4.0", "colours": 2},
                               {"dataset": "Instituto Geográfico Nacional (España)", "license": "CC-BY-4.0", "colours": 1}]
    assert [a["text"] for a in doc["attribution"]] == ["Esri Community Maps contributors; Overture Maps Foundation",
                                                       "Work derived from BTN 2024 ign.es; Overture Maps Foundation"]
    assert [t["building_id"] for t in doc["tints"]] == ["osm-way-1", "osm-way-3"]
    assert doc["tints"] == [t.to_dict() for t in tints]
    assert tints_document([], _record(tmp_path / "b.parquet"), {}, frame())["counts"] == {
        "rows": 0, "matched": 0, "facade": 0, "roof": 0, "skipped": {}}
