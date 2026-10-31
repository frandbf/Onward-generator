"""``overture match``: places as third-party POIs (offline)."""

from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest
from shapely import Point, box

from onward_generator.overture.acquire import FetchRecord
from onward_generator.overture.match import (
    DEFAULT_CONFIG_PATH,
    OvertureConfigError,
    load_config,
    match_report,
    place_tag,
    places_document,
    read_places,
    scene_frame,
)
from tests.overture_fixture import frame, place, scene, source, wgs84, write_places

CFG = load_config()
ALLOWED = frozenset({"CDLA-Permissive-2.0", "Apache-2.0", "CC0-1.0", "CC-BY-4.0"})


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
    assert CFG.categories["bakery"] == ("shop", "bakery") and CFG.categories["restaurant"] == ("amenity", "restaurant")
    assert CFG.path == str(DEFAULT_CONFIG_PATH) and CFG.version == "0.1.0"


@pytest.mark.parametrize(("changes", "fragment"), [
    ({"places__min_confidence": 1.5}, "/places/min_confidence"),
    ({"places__operating_status": ["gone"]}, "/places/operating_status/0"),
    ({"places__categories": {"bakery": {"shop": "bakery", "amenity": "cafe"}}}, "/places/categories/bakery"),
    ({"places__categories": {"bakery": {"building": "yes"}}}, "/places/categories/bakery"),
])
def test_invalid_config_is_refused_with_its_path(tmp_path: Path, changes: dict, fragment: str) -> None:
    with pytest.raises(OvertureConfigError, match=fragment):
        load_config(_write_config(tmp_path, **changes))


def test_unreadable_config_is_refused(tmp_path: Path) -> None:
    with pytest.raises(OvertureConfigError) as caught:
        load_config(tmp_path / "missing.json")
    assert str(caught.value).startswith(f"{tmp_path / 'missing.json'}: ")


def test_every_config_problem_is_listed(tmp_path: Path) -> None:
    path = _write_config(tmp_path, places__min_confidence=2, places__operating_status=["gone"])
    with pytest.raises(OvertureConfigError) as caught:
        load_config(path)
    assert str(caught.value) == (
        f"{path.resolve()}: /places/min_confidence: 2 is greater than the maximum of 1; "
        "/places/operating_status/0: 'gone' is not one of ['open', 'temporarily_closed', 'permanently_closed']"
    )


def test_config_values_are_read_from_the_file(tmp_path: Path) -> None:
    cfg = load_config(_write_config(tmp_path, places__accept_missing_status=False, places__min_confidence=0.3))
    assert cfg.accept_missing_status is False and cfg.min_confidence == 0.3
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


def test_match_report_names_the_scene_the_config_and_the_places() -> None:
    places = {"counts": {"rows": 3, "kept": 1, "skipped": {}}, "licenses": [{"dataset": "meta"}], "places": []}
    report = match_report(scene_ref={"path": "s"}, cfg=CFG, places=places)
    assert report == {
        "schema": "onward_generator.overture_match_report", "schema_version": "0.1.0", "scene": {"path": "s"},
        "config": {"path": CFG.path, "version": "0.1.0"},
        "places": {"counts": {"rows": 3, "kept": 1, "skipped": {}}, "licenses": [{"dataset": "meta"}]},
    }
