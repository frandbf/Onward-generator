"""Splitting OSM footprints by Catastro building parts (ADR-015). Synthetic parts in local metres."""

from __future__ import annotations

import pytest
from shapely import affinity, box, union_all
from shapely.geometry import shape

from onward_generator.cadastre import CadastreError, DownloadRecord
from onward_generator.cadastre.gml import BuildingPart
from onward_generator.cadastre.split import (
    ParentSplit,
    SplitOptions,
    SubBuilding,
    _nearest,
    is_sliver,
    split_footprint,
    split_overrides,
    split_scene,
    split_summary,
    subdivided_scene,
)

OPTIONS = SplitOptions()
ORIGIN_EN = (664343.977, 4205699.652)
RECORD = DownloadRecord(
    provider="catastro-inspire-bu", license="CC-BY-4.0", license_url="https://creativecommons.org/licenses/by/4.0/",
    attribution="Dirección General del Catastro, INSPIRE Buildings (BU)", license_notes="notes",
    url="https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx?x", bbox=[0.0, 0.0, 1.0, 1.0], crs="EPSG:25830",
    retrieved_at="2026-10-18T12:00:00+00:00", sha256="abc", size_bytes=10, user_agent="ua",
)


def part(local_id: str, floors: int, geometry, refcat: str | None = None) -> tuple[BuildingPart, object]:
    return BuildingPart(local_id, refcat or local_id.split("_part")[0], floors, 0, geometry), geometry


def split(footprint, *overlaps, options: SplitOptions = OPTIONS, parent: str = "osm-way-1") -> ParentSplit:
    return split_footprint(parent, footprint, list(overlaps), options)


# --- Options and slivers. ---


def test_default_options() -> None:
    assert OPTIONS.to_dict() == {
        "min_coverage": 0.8, "min_piece_area_m2": 6.0, "min_piece_width_m": 2.0, "confident_share": 0.8,
        "confidence_high": 0.85, "confidence_low": 0.7,
    }


@pytest.mark.parametrize("name", ["min_coverage", "confident_share", "confidence_high", "confidence_low"])
@pytest.mark.parametrize("value", [-0.01, 1.01])
def test_shares_must_be_within_unit_interval(name: str, value: float) -> None:
    with pytest.raises(ValueError, match=rf"^{name} must be within \[0, 1\]$"):
        SplitOptions(**{name: value})


@pytest.mark.parametrize("name", ["min_piece_area_m2", "min_piece_width_m"])
def test_sizes_must_be_non_negative(name: str) -> None:
    with pytest.raises(ValueError, match=rf"^{name} must be non-negative$"):
        SplitOptions(**{name: -0.01})
    assert getattr(SplitOptions(**{name: 0.0}), name) == 0.0


def test_unit_interval_bounds_are_accepted() -> None:
    assert SplitOptions(min_coverage=0.0, confident_share=1.0).min_coverage == 0.0


@pytest.mark.parametrize(
    ("piece", "sliver"),
    [
        (box(0, 0, 1.9, 10), True),  # narrower than 2 m
        (box(0, 0, 2.1, 10), False),
        (box(0, 0, 2.5, 2.39), True),  # 5.975 m² < 6 m²
        (box(0, 0, 2.5, 2.4), False),  # exactly 6 m²
    ],
)
def test_sliver_is_small_or_narrow(piece, sliver: bool) -> None:
    assert is_sliver(piece, OPTIONS) is sliver


# --- Leftover assignment. ---


def test_leftover_goes_to_the_longest_shared_boundary() -> None:
    groups = {"a": box(0, 0, 10, 10), "b": box(10, 0, 20, 5)}
    assert _nearest(box(10, 5, 12, 10), groups) == "a"  # shares 5 m with a, 2 m with b


def test_leftover_goes_to_the_nearest_group_first() -> None:
    groups = {"a": box(0, 0, 10, 10), "b": box(18, 0, 28, 10)}
    assert _nearest(box(14, 0, 16, 10), groups) == "b"  # 2 m from b, 4 m from a


def test_leftover_tie_goes_to_the_smaller_id() -> None:
    groups = {"z": box(0, 0, 10, 10), "a": box(12, 0, 22, 10)}
    assert _nearest(box(10, 0, 12, 10), groups) == "a"


# --- One footprint. ---


def test_a_block_becomes_one_building_per_construction() -> None:
    result = split(
        box(0, 0, 30, 10),
        part("1000003AA0000A_part1", 6, box(18, 0, 30, 10)),
        part("1000001AA0000A_part1", 4, box(0, 0, 12, 10)),
        part("1000002AA0000A_part1", 1, box(12, 0, 18, 10)),  # one storey still counts
    )
    assert result.status == "split" and result.coverage == 1.0 and result.courtyard_m2 == 0.0
    assert [(s.id, s.levels, s.geometry.area, s.leftover_m2) for s in result.subs] == [
        ("osm-way-1:1000001AA0000A_part1", 4, 120.0, 0.0),
        ("osm-way-1:1000002AA0000A_part1", 1, 60.0, 0.0),
        ("osm-way-1:1000003AA0000A_part1", 6, 120.0, 0.0),
    ]
    assert all(s.parent_id == "osm-way-1" for s in result.subs)
    assert union_all([s.geometry for s in result.subs]).area == pytest.approx(300.0)
    assert result.to_dict() == {
        "building_id": "osm-way-1", "status": "split", "coverage": 1.0, "courtyard_m2": 0.0, "dropped_m2": 0.0,
        "sub_buildings": [s.to_dict() for s in result.subs],
    }
    assert result.subs[1].to_dict() == {
        "id": "osm-way-1:1000002AA0000A_part1", "refcat": "1000002AA0000A", "parts": ["1000002AA0000A_part1"],
        "levels": 1, "area_m2": 60.0, "leftover_m2": 0.0,
    }


def test_parts_of_one_parcel_and_storey_count_merge() -> None:
    result = split(
        box(0, 0, 30, 10),
        part("A_part2", 3, box(0, 0, 10, 10), refcat="A"),
        part("A_part1", 3, box(10, 0, 20, 10), refcat="A"),
        part("A_part3", 5, box(20, 0, 30, 10), refcat="A"),
    )
    assert [(s.id, s.refcat, s.parts, s.levels, s.geometry.area) for s in result.subs] == [
        ("osm-way-1:A_part1", "A", ("A_part1", "A_part2"), 3, 200.0),
        ("osm-way-1:A_part3", "A", ("A_part3",), 5, 100.0),
    ]


def test_different_parcels_with_the_same_storeys_stay_apart() -> None:
    result = split(box(0, 0, 20, 10), part("A_part1", 3, box(0, 0, 10, 10)), part("B_part1", 3, box(10, 0, 20, 10)))
    assert [s.refcat for s in result.subs] == ["A", "B"]


def test_overlapping_parts_are_made_disjoint_in_id_order() -> None:
    result = split(box(0, 0, 30, 10), part("B_part1", 2, box(15, 0, 30, 10)), part("A_part1", 4, box(0, 0, 20, 10)))
    a, b = result.subs
    assert (a.geometry.area, b.geometry.area) == (200.0, 100.0)
    assert a.geometry.intersection(b.geometry).area == 0.0


def test_a_sliver_is_dropped_and_its_area_joins_the_neighbour() -> None:
    result = split(
        box(0, 0, 30, 10),
        part("A_part1", 4, box(0, 0, 15, 10)),
        part("B_part1", 2, box(15, 0, 29.5, 10)),
        part("C_part1", 6, box(29.5, 0, 31, 10)),  # a 0.5 m strip inside the footprint
    )
    assert [s.id for s in result.subs] == ["osm-way-1:A_part1", "osm-way-1:B_part1"]
    b = result.subs[1]
    assert b.geometry.area == pytest.approx(150.0) and b.leftover_m2 == pytest.approx(5.0)
    assert b.own_share == pytest.approx(145 / 150)


def test_courtyard_without_storeys_becomes_open_ground() -> None:
    footprint = box(0, 0, 30, 20)
    result = split(
        footprint,
        part("A_part1", 4, box(0, 0, 30, 6)),
        part("B_part1", 2, box(0, 14, 30, 20)),
        part("C_part1", 0, box(5, 5, 25, 14)),  # basement under a courtyard, overlapping A by 1 m
    )
    assert result.status == "split" and result.courtyard_m2 == pytest.approx(160.0)
    a, b = result.subs
    # The patio sides (x 0–5 and 25–30) are leftover; each touches A and B equally, so A (smaller id) takes them.
    assert a.leftover_m2 == pytest.approx(80.0) and b.leftover_m2 == 0.0
    union = union_all([a.geometry, b.geometry])
    assert union.area == pytest.approx(440.0)
    assert union.union(box(5, 6, 25, 14)).symmetric_difference(footprint).area == pytest.approx(0.0, abs=1e-6)
    assert union.intersection(box(5, 6, 25, 14)).area == pytest.approx(0.0, abs=1e-6)


def test_a_narrow_open_part_stays_built() -> None:
    result = split(
        box(0, 0, 30, 10),
        part("A_part1", 4, box(0, 0, 14, 10)),
        part("B_part1", 2, box(15, 0, 30, 10)),
        part("C_part1", 0, box(14, 0, 15, 10)),
    )
    assert result.courtyard_m2 == 0.0
    assert union_all([s.geometry for s in result.subs]).area == pytest.approx(300.0)


def test_union_equals_the_footprint_for_a_ragged_cover() -> None:
    footprint = box(0, 0, 40, 12)
    result = split(
        footprint,
        part("A_part1", 5, box(-0.4, -0.3, 17.2, 12.5)),
        part("B_part1", 3, box(17.2, 0.6, 28.0, 12.2)),
        part("C_part1", 1, box(28.0, -0.2, 39.6, 11.8)),
    )
    assert len(result.subs) == 3
    union = union_all([s.geometry for s in result.subs])
    assert union.symmetric_difference(footprint).area == pytest.approx(0.0, abs=0.05)
    for first in result.subs:
        for second in result.subs:
            if first is not second:
                assert first.geometry.intersection(second.geometry).area == pytest.approx(0.0, abs=1e-6)
        x, y = first.geometry.exterior.coords[0]
        assert (round(x * 1000) / 1000, round(y * 1000) / 1000) == (x, y)  # snapped to 1 mm


def test_only_open_parts_is_no_parts() -> None:
    result = split(box(0, 0, 10, 10), part("A_part1", 0, box(0, 0, 10, 10)))
    assert (result.status, result.coverage, result.subs) == ("no_parts", 0.0, ())


def test_coverage_below_the_minimum_is_not_split() -> None:
    result = split(box(0, 0, 100, 10), part("A_part1", 4, box(0, 0, 40, 10)), part("B_part1", 2, box(40, 0, 79, 10)))
    assert (result.status, result.coverage, result.subs) == ("low_coverage", pytest.approx(0.79), ())


def test_coverage_at_the_minimum_is_split() -> None:
    result = split(box(0, 0, 100, 10), part("A_part1", 4, box(0, 0, 40, 10)), part("B_part1", 2, box(40, 0, 80, 10)))
    assert result.status == "split" and result.coverage == pytest.approx(0.8)


def test_one_group_left_is_not_split() -> None:
    result = split(box(0, 0, 30, 10), part("A_part1", 4, box(0, 0, 15, 10)), part("A_part2", 4, box(15, 0, 30, 10)))
    assert (result.status, result.coverage, result.subs) == ("single_group", 1.0, ())
    sliver = split(box(0, 0, 30, 10), part("A_part1", 4, box(0, 0, 29, 10)), part("B_part1", 2, box(29, 0, 30, 10)))
    assert sliver.status == "single_group"


# --- Scene. ---


def _feature(fid: str, geometry, tags: dict[str, str], attributes: dict | None = None) -> dict:
    return {
        "id": fid,
        "source": {"provider": "openstreetmap", "license": "ODbL-1.0", "osm_type": "way", "osm_id": int(fid.rsplit("-", 1)[1])},
        "kind": "building",
        "tags": tags,
        "geometry": {"type": "Polygon", "coordinates": [list(geometry.exterior.coords)], "unit": "m", "coordinate_system": "local_grid"},
        "geometry_repaired": False,
        "clip_status": "inside",
        "attributes": attributes or {},
    }


def _scene() -> dict:
    observed = {"value": 12.0, "origin": "observed", "source_tag": "height"}
    levels = {"value": 4, "origin": "observed", "source_tag": "building:levels"}
    name = {"value": "x", "origin": "observed"}
    return {
        "schema": "onward_generator.geo_scene",
        "schema_version": "0.2.0",
        "frame": {"crs": "EPSG:25830", "coordinate_system": "local_grid", "unit": "m", "origin_projected": list(ORIGIN_EN)},
        "features": [
            _feature("osm-way-3", box(0, 0, 20, 10), {"building": "apartments"},
                     {"height_m": observed, "levels": levels, "roof": name}),
            {"id": "osm-node-9", "kind": "poi", "tags": {"shop": "bakery"}, "geometry": {"type": "Point", "coordinates": [15.0, 5.0]}},
            _feature("osm-way-1", box(0, 20, 20, 30), {"building": "church"}),
            _feature("osm-way-2", box(40, 0, 50, 10), {"building": "yes"}),
        ],
    }


def _projected(local_id: str, floors: int, geometry) -> BuildingPart:
    return BuildingPart(local_id, local_id.split("_part")[0], floors, 0, affinity.translate(geometry, *ORIGIN_EN))


PARTS = [
    _projected("A_part1", 5, box(0, 0, 10, 10)),
    _projected("B_part1", 1, box(10, 0, 20, 10)),
    _projected("C_part1", 3, box(0, 20, 10, 30)),
    _projected("D_part1", 2, box(10, 20, 20, 30)),
]


def _is_church(tags) -> bool:
    return tags.get("building") == "church"


def test_scene_replaces_split_footprints_and_keeps_the_rest() -> None:
    scene = _scene()
    features, results = split_scene(scene, PARTS, RECORD, OPTIONS, is_landmark=_is_church)
    assert [f["id"] for f in features] == ["osm-way-3:A_part1", "osm-way-3:B_part1", "osm-node-9", "osm-way-1", "osm-way-2"]
    assert features[2:] == scene["features"][1:]
    assert [(r.parent_id, r.status, r.coverage) for r in results] == [
        ("osm-way-1", "landmark", 0.0), ("osm-way-2", "no_parts", 0.0), ("osm-way-3", "split", 1.0),
    ]
    bakery = features[1]
    parent = scene["features"][0]
    assert bakery == {
        **parent,
        "id": "osm-way-3:B_part1",
        "geometry": {**parent["geometry"], "type": "Polygon", "coordinates": bakery["geometry"]["coordinates"]},
        "attributes": {"roof": {"value": "x", "origin": "observed"}},
        "parent": {"id": "osm-way-3"},
        "subdivision": {
            "method": "catastro_split",
            "source": "Dirección General del Catastro, INSPIRE BU BuildingPart.numberOfFloorsAboveGround",
            "provider": "catastro-inspire-bu",
            "license": "CC-BY-4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Dirección General del Catastro, INSPIRE Buildings (BU)",
            "retrieved_at": "2026-10-18T12:00:00+00:00",
            "refcat": "B",
            "parts": ["B_part1"],
            "levels": 1,
            "leftover_m2": 0.0,
            "parent_observed": {"height_m": parent["attributes"]["height_m"], "levels": parent["attributes"]["levels"]},
        },
    }
    assert shape(bakery["geometry"]).equals(box(10, 0, 20, 10))
    assert features[0]["subdivision"]["parent_observed"]["height_m"]["value"] == 12.0
    assert scene["features"][0]["attributes"]["levels"]["value"] == 4  # the input scene is not modified


def test_scene_without_height_attributes_keeps_an_empty_parent_observed() -> None:
    scene = _scene()
    scene["features"][0]["attributes"] = {}
    features, _ = split_scene(scene, PARTS, RECORD, OPTIONS, is_landmark=_is_church)
    assert features[0]["subdivision"]["parent_observed"] == {} and features[0]["attributes"] == {}


def test_landmarks_are_split_when_not_flagged() -> None:
    features, results = split_scene(_scene(), PARTS, RECORD, OPTIONS, is_landmark=lambda tags: False)
    assert "osm-way-1:C_part1" in [f["id"] for f in features]
    assert dict((r.parent_id, r.status) for r in results)["osm-way-1"] == "split"


@pytest.mark.parametrize(("key", "value"), [("crs", "EPSG:4326"), ("coordinate_system", "wgs84"), ("unit", "deg")])
def test_scene_must_be_a_local_metric_grid(key: str, value: str) -> None:
    scene = _scene()
    scene["frame"][key] = value
    with pytest.raises(CadastreError, match="local grid"):
        split_scene(scene, PARTS, RECORD, OPTIONS, is_landmark=_is_church)


def test_summary_counts_split_buildings_and_storeys() -> None:
    _, results = split_scene(_scene(), PARTS, RECORD, OPTIONS, is_landmark=_is_church)
    assert split_summary(results) == {
        "osm_buildings": 3, "osm_buildings_split": 1, "sub_buildings": 2, "levels_range": [1, 5], "courtyard_m2": 0.0, "dropped_m2": 0.0,
        "status_split": 1, "status_single_group": 0, "status_low_coverage": 0, "status_no_parts": 1, "status_landmark": 1,
    }
    empty = split_summary([ParentSplit("osm-way-1", "low_coverage", 0.5, (), 0.0)])
    assert empty["levels_range"] is None and empty["status_low_coverage"] == 1 and empty["sub_buildings"] == 0
    courtyard = split_summary([ParentSplit("osm-way-1", "split", 1.0, (), 12.3456)])
    assert courtyard["courtyard_m2"] == 12.346


def test_overrides_give_each_sub_building_its_storeys() -> None:
    whole = SubBuilding("osm-way-1:A_part1", "osm-way-1", "A", ("A_part1",), 5, box(0, 0, 10, 10), 20.0)
    patched = SubBuilding("osm-way-1:B_part1", "osm-way-1", "B", ("B_part1", "B_part2"), 1, box(0, 0, 10, 10), 20.5)
    entries = split_overrides([ParentSplit("osm-way-1", "split", 1.0, (whole, patched))], RECORD, OPTIONS)
    assert entries == [
        {
            "id": "osm-way-1:A_part1", "levels": 5,
            "source": "Dirección General del Catastro, INSPIRE BU BuildingPart.numberOfFloorsAboveGround",
            "license": "CC-BY-4.0", "retrieved_at": "2026-10-18T12:00:00+00:00", "confidence": 0.85,
            "note": "split from osm-way-1; refcat A; 1 part(s); own share 0.80",
        },
        {
            "id": "osm-way-1:B_part1", "levels": 1,
            "source": "Dirección General del Catastro, INSPIRE BU BuildingPart.numberOfFloorsAboveGround",
            "license": "CC-BY-4.0", "retrieved_at": "2026-10-18T12:00:00+00:00", "confidence": 0.7,
            "note": "split from osm-way-1; refcat B; 2 part(s); own share 0.80",
        },
    ]


def test_subdivided_scene_keeps_the_contract_and_records_the_source() -> None:
    scene = _scene()
    features, results = split_scene(scene, PARTS, RECORD, OPTIONS, is_landmark=_is_church)
    ref = {"path": "/x/geo_scene.json", "sha256": "f00", "schema_version": "0.2.0"}
    out = subdivided_scene(scene, features, results, scene_ref=ref, record=RECORD, options=OPTIONS)
    assert out["schema"] == "onward_generator.geo_scene" and out["frame"] == scene["frame"]
    assert [f["id"] for f in out["features"]] == [f["id"] for f in features]
    assert out["subdivision"] == {
        "method": "catastro_split",
        "geo_scene": ref,
        "cadastre": {
            "provider": "catastro-inspire-bu", "license": "CC-BY-4.0", "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": "Dirección General del Catastro, INSPIRE Buildings (BU)", "retrieved_at": "2026-10-18T12:00:00+00:00",
            "sha256": "abc", "url": "https://ovc.catastro.meh.es/INSPIRE/wfsBU.aspx?x",
        },
        "options": OPTIONS.to_dict(),
        "counts": split_summary(results),
    }


def test_summary_adds_dropped_area() -> None:
    dropped = split_summary([ParentSplit("osm-way-1", "split", 1.0, (), 1.0, 2.34567), ParentSplit("osm-way-2", "split", 1.0, (), 0.0, 1.0)])
    assert dropped["dropped_m2"] == 3.346
    assert ParentSplit("osm-way-1", "split", 1.0, (), 0.0, 2.34567).to_dict()["dropped_m2"] == 2.346


def test_disconnected_pieces_of_a_group_become_separate_buildings() -> None:
    result = split(
        box(0, 0, 30, 10),
        part("A_part1", 4, box(0, 0, 8, 10)),
        part("B_part1", 2, box(8, 0, 22, 10)),
        part("A_part2", 4, box(22, 0, 30, 10), refcat="A"),
    )
    assert [(s.id, s.parts, s.levels, s.geometry.area) for s in result.subs] == [
        ("osm-way-1:A_part1", ("A_part1", "A_part2"), 4, 80.0),
        ("osm-way-1:A_part1.2", ("A_part1", "A_part2"), 4, 80.0),
        ("osm-way-1:B_part1", ("B_part1",), 2, 140.0),
    ]
    assert result.subs[0].geometry.bounds == (0.0, 0.0, 8.0, 10.0)  # equal areas: the lower bounds first
    bigger = split(
        box(0, 0, 30, 10),
        part("A_part1", 4, box(0, 0, 8, 10)),
        part("B_part1", 2, box(8, 0, 20, 10)),
        part("A_part2", 4, box(20, 0, 30, 10), refcat="A"),
    )
    assert [s.geometry.bounds[0] for s in bigger.subs[:2]] == [20.0, 0.0]  # the larger piece keeps the plain id


def test_pieces_touching_at_a_corner_are_separate_polygons() -> None:
    footprint = box(0, 0, 20, 20)
    result = split(
        footprint,
        part("A_part1", 4, box(0, 0, 10, 10)),
        part("A_part2", 4, box(10, 10, 20, 20), refcat="A"),
        part("B_part1", 2, box(10, 0, 20, 10)),
        part("C_part1", 3, box(0, 10, 10, 20)),
    )
    assert [s.id for s in result.subs] == ["osm-way-1:A_part1", "osm-way-1:A_part1.2", "osm-way-1:B_part1", "osm-way-1:C_part1"]
    assert all(s.geometry.geom_type == "Polygon" for s in result.subs)


def test_a_stray_leftover_crumb_is_left_open() -> None:
    # A 2 x 2 m notch in the courtyard touches no group; it goes to B, 2 m away, as a separate crumb.
    result = split(
        box(0, 0, 30, 10),
        part("A_part1", 4, box(0, 4, 10, 10)),
        part("B_part1", 2, box(10, 4, 30, 10)),
        part("C_part1", 0, box(0, 0, 30, 4).difference(box(14, 0, 16, 2))),
    )
    assert result.courtyard_m2 == pytest.approx(116.0)
    assert result.dropped_m2 == pytest.approx(4.0)
    assert [(s.id, s.geometry.area) for s in result.subs] == [("osm-way-1:A_part1", 60.0), ("osm-way-1:B_part1", 120.0)]


def test_a_stray_piece_of_the_minimum_area_stays_a_building() -> None:
    result = split(
        box(0, 0, 30, 10),
        part("A_part1", 4, box(0, 4, 10, 10)),
        part("B_part1", 2, box(10, 4, 30, 10)),
        part("C_part1", 0, box(0, 0, 30, 4).difference(box(14, 0, 16, 3))),
    )
    assert result.dropped_m2 == 0.0
    assert [(s.id, s.geometry.area, s.leftover_m2) for s in result.subs] == [
        ("osm-way-1:A_part1", 60.0, 0.0), ("osm-way-1:B_part1", 120.0, 0.0), ("osm-way-1:B_part1.2", 6.0, 6.0),
    ]


def test_records_round_areas_and_coverage() -> None:
    sub = SubBuilding("osm-way-1:A_part1", "osm-way-1", "A", ("A_part1",), 3, box(0, 0, 1, 12.34567), 1.23456)
    assert sub.to_dict() == {
        "id": "osm-way-1:A_part1", "refcat": "A", "parts": ["A_part1"], "levels": 3, "area_m2": 12.346, "leftover_m2": 1.235,
    }
    parent = ParentSplit("osm-way-1", "split", 0.123456, (sub,), 7.65432, 0.98765)
    assert parent.to_dict() == {
        "building_id": "osm-way-1", "status": "split", "coverage": 0.1235, "courtyard_m2": 7.654, "dropped_m2": 0.988,
        "sub_buildings": [sub.to_dict()],
    }
    from onward_generator.cadastre.split import _sub_feature

    feature = _sub_feature(_scene()["features"][0], sub, RECORD)
    assert feature["subdivision"]["leftover_m2"] == 1.235
    assert feature["geometry"]["coordinates"] == (((1.0, 0.0), (1.0, 12.34567), (0.0, 12.34567), (0.0, 0.0), (1.0, 0.0)),)


def test_polygons_skip_empty_and_non_polygon_parts() -> None:
    from shapely import GeometryCollection, Point, Polygon

    from onward_generator.cadastre.split import _polygons

    square = box(0, 0, 1, 1)
    assert _polygons(Polygon()) == []
    assert _polygons(GeometryCollection([Point(5, 5), square])) == [square]
    assert _polygons(Point(0, 0)) == []


def test_crumbs_of_several_groups_add_up() -> None:
    result = split(
        box(0, 0, 30, 10),
        part("A_part1", 4, box(0, 4, 10, 10)),
        part("B_part1", 2, box(10, 4, 30, 10)),
        part("C_part1", 0, box(0, 0, 30, 4).difference(box(14, 0, 16, 2)).difference(box(4, 0, 6, 2))),
    )
    assert result.dropped_m2 == pytest.approx(8.0)


def test_leftover_without_extra_area_is_a_float() -> None:
    result = split(box(0, 0, 20, 10), part("A_part1", 3, box(0, 0, 10, 10)), part("B_part1", 4, box(10, 0, 20, 10)))
    assert repr(result.subs[0].leftover_m2) == "0.0"


def test_a_part_whose_box_overlaps_but_whose_shape_does_not_is_ignored() -> None:
    from shapely import Polygon

    scene = _scene()
    ell = Polygon([(40, 0), (50, 0), (50, 2), (42, 2), (42, 10), (40, 10)])  # L-shaped, inside osm-way-2's box
    scene["features"][3]["geometry"]["coordinates"] = [list(ell.exterior.coords)]
    near = _projected("E_part1", 4, box(45, 5, 49, 9))  # in the notch: the boxes overlap, the shapes do not
    _, results = split_scene(scene, [*PARTS, near], RECORD, OPTIONS, is_landmark=_is_church)
    assert dict((r.parent_id, r.status) for r in results)["osm-way-2"] == "no_parts"
