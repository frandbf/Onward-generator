"""Catastro coverage zones, request CRS, tiled downloads and reprojection (fake transport, no network)."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pyproj import Transformer
from shapely import box, union_all

from onward_generator.cadastre import CadastreError, download_building_parts, load_download_record, parse_building_parts
from onward_generator.cadastre.acquire import WFS_BU_URL, bbox_from_scene, merge_tiles, split_bbox
from onward_generator.cadastre.gml import reproject_parts
from onward_generator.cadastre.zones import (
    ZONES_PATH,
    CatastroZone,
    catastro_zone,
    load_zones,
    reproject_bbox,
    scene_request,
    utm_zone,
    zone_table,
)
from onward_generator.geo.pipeline import GeoResult
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures" / "cadastre" / "synthetic_bu.gml"

# --- Zones. ---

PENINSULA_29 = CatastroZone("peninsula", "EPSG:25829")
PENINSULA_30 = CatastroZone("peninsula", "EPSG:25830")
PENINSULA_31 = CatastroZone("peninsula", "EPSG:25831")
BALEARIC = CatastroZone("balearic-islands", "EPSG:25831")
CANARY_28 = CatastroZone("canary-islands", "EPSG:32628")

COVERED = {
    "Murcia": (MURCIA_CENTER_LON, MURCIA_CENTER_LAT, PENINSULA_30),
    "Madrid": (-3.7038, 40.4168, PENINSULA_30),
    "Santiago de Compostela": (-8.5446, 42.8806, PENINSULA_29),
    "Vigo": (-8.7207, 42.2406, PENINSULA_29),
    "Tui": (-8.6447, 42.0471, PENINSULA_29),
    "Ferrol": (-8.2325, 43.4832, PENINSULA_29),
    "Ourense": (-7.8642, 42.3358, PENINSULA_29),
    "Verín": (-7.4363, 41.9407, PENINSULA_29),
    "Ayamonte": (-7.4026, 37.2131, PENINSULA_29),
    "Badajoz": (-6.9707, 38.8794, PENINSULA_29),
    "Valencia de Alcántara": (-7.2437, 39.4114, PENINSULA_29),
    "Ciudad Rodrigo": (-6.5332, 40.5997, PENINSULA_29),
    "Fermoselle": (-6.3950, 41.3175, PENINSULA_29),
    "Puebla de Sanabria": (-6.6340, 42.0540, PENINSULA_29),
    "Gijón": (-5.6615, 43.5322, PENINSULA_30),
    "Santander": (-3.8044, 43.4623, PENINSULA_30),
    "Castro Urdiales": (-3.2154, 43.3847, PENINSULA_30),
    "Miranda de Ebro": (-2.9469, 42.6866, PENINSULA_30),
    "Treviño (Burgos, inside Álava)": (-2.7458, 42.7339, PENINSULA_30),
    "La Puebla de Arganzón": (-2.8281, 42.7647, PENINSULA_30),
    "Haro": (-2.8476, 42.5776, PENINSULA_30),
    "Logroño": (-2.4450, 42.4627, PENINSULA_30),
    "Calahorra": (-1.9655, 42.3050, PENINSULA_30),
    "Alfaro": (-1.7497, 42.1786, PENINSULA_30),
    "Tarazona": (-1.7270, 41.9046, PENINSULA_30),
    "Sos del Rey Católico": (-1.2152, 42.4970, PENINSULA_30),
    "Jaca": (-0.5498, 42.5700, PENINSULA_30),
    "Zaragoza": (-0.8891, 41.6488, PENINSULA_30),
    "Valencia": (-0.3763, 39.4699, PENINSULA_30),
    "Alicante": (-0.4810, 38.3452, PENINSULA_30),
    "Almería": (-2.4637, 36.8381, PENINSULA_30),
    "Algeciras": (-5.4562, 36.1408, PENINSULA_30),
    "Tarifa": (-5.6045, 36.0143, PENINSULA_30),
    "Cádiz": (-6.2886, 36.5271, PENINSULA_29),
    "Barcelona": (2.1734, 41.3851, PENINSULA_31),
    "Figueres": (2.9615, 42.2675, PENINSULA_31),
    "Puigcerdà": (1.9276, 42.4317, PENINSULA_31),
    "Vielha": (0.7957, 42.7020, PENINSULA_31),
    "Tortosa": (0.5216, 40.8125, PENINSULA_31),
    "Dénia": (0.1057, 38.8408, PENINSULA_31),
    "Palma": (2.6502, 39.5696, BALEARIC),
    "Ibiza": (1.4329, 38.9067, BALEARIC),
    "Maó": (4.2659, 39.8885, BALEARIC),
    "Las Palmas de Gran Canaria": (-15.4134, 28.1235, CANARY_28),
    "Santa Cruz de Tenerife": (-16.2518, 28.4636, CANARY_28),
    "Arrecife": (-13.5476, 28.9630, CANARY_28),
    "Valverde": (-17.9156, 27.8086, CANARY_28),
    "El Hierro, west of 18° W": (-18.14, 27.72, CatastroZone("canary-islands", "EPSG:32627")),
    "Ceuta": (-5.3162, 35.8894, CatastroZone("ceuta", "EPSG:25830")),
    "Melilla": (-2.9381, 35.2923, CatastroZone("melilla", "EPSG:25830")),
}

NOT_COVERED = {
    "Bilbao": (-2.9350, 43.2630),
    "Getxo": (-3.0068, 43.3569),
    "Vitoria-Gasteiz": (-2.6727, 42.8467),
    "Laguardia": (-2.5848, 42.5537),
    "Donostia": (-1.9812, 43.3183),
    "Irun": (-1.7894, 43.3390),
    "Pamplona": (-1.6432, 42.8125),
    "Tudela": (-1.6052, 42.0617),
    "Estella": (-2.0278, 42.6716),
    "Viana": (-2.3720, 42.5150),
    "Lodosa": (-2.0786, 42.4247),
    "Sangüesa": (-1.2826, 42.5752),
    "Lisbon": (-9.1393, 38.7223),
    "Porto": (-8.6291, 41.1579),
    "Braga": (-8.4265, 41.5454),
    "Faro": (-7.9304, 37.0194),
    "Bordeaux": (-0.5792, 44.8378),
    "Toulouse": (1.4442, 43.6047),
    "Perpignan": (2.8948, 42.6887),
    "Pau": (-0.3708, 43.2951),
    "Tangier": (-5.8340, 35.7595),
    "Nador": (-2.9335, 35.1681),
    "Tarfaya": (-12.9220, 27.9378),
    "Funchal": (-16.9241, 32.6669),
    "Algiers": (3.0588, 36.7538),
    "Buenos Aires": (-58.3816, -34.6037),
}


@pytest.mark.parametrize("name", sorted(COVERED))
def test_covered_places_get_their_territory_and_request_crs(name: str) -> None:
    longitude, latitude, zone = COVERED[name]
    assert catastro_zone(longitude, latitude) == zone


@pytest.mark.parametrize("name", sorted(NOT_COVERED))
def test_foral_cadastres_and_places_outside_spain_have_no_zone(name: str) -> None:
    assert catastro_zone(*NOT_COVERED[name]) is None


def test_the_table_lists_the_territories_and_the_service_crs() -> None:
    table = load_zones()
    assert [t.name for t in table.territories] == ["peninsula", "balearic-islands", "canary-islands", "ceuta", "melilla"]
    assert [name for name, _ in table.excluded] == ["navarre", "basque-country"]
    assert {"EPSG:25829", "EPSG:25830", "EPSG:25831", "EPSG:32627", "EPSG:32628"} <= set(table.service_crs)
    assert dict(table.territories[2].crs_by_utm_zone) == {27: "EPSG:32627", 28: "EPSG:32628"}


@pytest.mark.parametrize(("longitude", "zone"), [(-6.0, 30), (-6.0001, 29), (-0.0001, 30), (0.0, 31), (-18.0, 28), (-18.0001, 27)])
def test_utm_zone_edges(longitude: float, zone: int) -> None:
    assert utm_zone(longitude) == zone


def _document() -> dict[str, Any]:
    return copy.deepcopy(json.loads(ZONES_PATH.read_text(encoding="utf-8")))


SQUARE = [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]]


def test_an_excluded_area_wins_and_a_missing_utm_zone_gives_no_zone() -> None:
    document = _document()
    document["territories"] = [{"name": "t", "crs_by_utm_zone": {"31": "EPSG:25831"}, "polygon": SQUARE}]
    document["excluded"] = [{"name": "x", "reason": "r", "polygon": [[[0.5, 0.5], [1.0, 0.5], [1.0, 1.0], [0.5, 1.0], [0.5, 0.5]]]}]
    table = zone_table(document)
    assert catastro_zone(0.2, 0.2, table) == CatastroZone("t", "EPSG:25831")
    assert catastro_zone(0.7, 0.7, table) is None  # excluded
    assert catastro_zone(1.5, 0.2, table) is None  # outside every territory
    document["territories"][0]["crs_by_utm_zone"] = {"30": "EPSG:25830"}
    assert catastro_zone(0.2, 0.2, zone_table(document)) is None


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(schema="other"), "schema must be onward_generator.catastro_zones"),
        (lambda d: d["territories"][0]["crs_by_utm_zone"].update({"32": "EPSG:25832"}), "peninsula: EPSG:25832 is not a CRS the service offers"),
        (lambda d: d["territories"][0]["crs_by_utm_zone"].update({"30": "EPSG:4326"}), "peninsula: EPSG:4326 is not UTM zone 30 north"),
        (lambda d: d["territories"][0]["crs_by_utm_zone"].update({"30": "EPSG:3857"}), "peninsula: EPSG:3857 is not UTM zone 30 north"),
        (lambda d: d["territories"][0]["crs_by_utm_zone"].update({"30": "EPSG:25831"}), "peninsula: EPSG:25831 is not UTM zone 30 north"),
        (lambda d: d["territories"][3].update(crs_by_utm_zone={}), "ceuta: no request CRS"),
        (lambda d: d["territories"][4].update(polygon=[]), "melilla: polygon is not a list of rings"),
        (lambda d: d["territories"][4].update(polygon=[[[1, 2], [3]]]), "melilla: polygon is not a list of rings"),
        (lambda d: d["territories"][4].update(polygon=[[[600000.0, 4000000.0], [600100.0, 4000000.0], [600100.0, 4000100.0]]]),
         "melilla: polygon is not in longitude/latitude degrees"),
        (lambda d: d["excluded"][0].update(polygon=[[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]), "navarre: polygon is not valid"),
    ],
)
def test_an_invalid_table_is_refused(change, message: str) -> None:  # type: ignore[no-untyped-def]
    document = _document()
    change(document)
    with pytest.raises(CadastreError, match=f"^catastro zones: {re.escape(message)}$"):
        zone_table(document)


@pytest.mark.parametrize(
    "corner",
    [(-180.1, 0.0), (0.0, -90.1), (180.1, 0.0), (0.0, 90.1)],
)
def test_each_degree_limit_is_checked(corner: tuple[float, float]) -> None:
    document = _document()
    x, y = corner
    document["excluded"][0]["polygon"] = [[[0.0, 0.0], [x, y], [x / 2 + 0.5, y / 2 + 0.5], [0.0, 0.0]]]
    with pytest.raises(CadastreError, match="navarre: polygon is not in longitude/latitude degrees"):
        zone_table(document)


def test_a_polygon_on_the_degree_limits_is_accepted() -> None:
    document = _document()
    document["excluded"][0]["polygon"] = [[[-180.0, -90.0], [180.0, -90.0], [180.0, 90.0], [-180.0, 90.0], [-180.0, -90.0]]]
    assert zone_table(document).excluded[0][1].area == 360.0 * 180.0


def test_holes_are_read() -> None:
    basque = load_zones().excluded[1][1]
    assert len(basque.interiors) == 1


# --- Request box. ---


def test_reproject_bbox_keeps_a_box_in_the_same_crs() -> None:
    bbox = (664243.98, 4205599.65, 664443.98, 4205799.65)
    assert reproject_bbox(bbox, "EPSG:25830", "EPSG:25830") is bbox


def test_reproject_bbox_holds_every_corner_in_the_other_crs() -> None:
    bbox = (799000.0, 4580000.0, 800000.0, 4581000.0)  # east edge of zone 30, around 1° E
    out = reproject_bbox(bbox, "EPSG:25830", "EPSG:25831")
    forward = Transformer.from_crs("EPSG:25830", "EPSG:25831", always_xy=True)
    corners = [forward.transform(x, y) for x in (bbox[0], bbox[2]) for y in (bbox[1], bbox[3])]
    assert all(out[0] <= x <= out[2] and out[1] <= y <= out[3] for x, y in corners)
    assert out[0] == pytest.approx(min(x for x, _ in corners), abs=0.5)
    assert out[3] == pytest.approx(max(y for _, y in corners), abs=0.5)


def test_reproject_bbox_from_wgs84_utm_to_etrs89_utm_moves_less_than_a_millimetre() -> None:
    bbox = (537000.0, 4747000.0, 538000.0, 4748000.0)
    out = reproject_bbox(bbox, "EPSG:32629", "EPSG:25829")
    assert out == pytest.approx(bbox, abs=0.001)


def test_scene_request_in_murcia_is_the_scene_box(pipeline_result: GeoResult) -> None:
    scene = pipeline_result.scene_dict()
    assert scene_request(scene, 20.0) == (bbox_from_scene(scene, 20.0), PENINSULA_30)


def _utm29_scene() -> dict[str, Any]:
    return {
        "frame": {"crs": "EPSG:32629", "unit": "m", "origin_wgs84": [-8.5446, 42.8806], "origin_projected": [537190.73, 4747656.34]},
        "features": [{"kind": "building", "geometry": {"type": "Polygon", "coordinates": [[[-10, -10], [10, -10], [10, 10], [-10, -10]]]}}],
    }


def test_scene_request_in_galicia_asks_in_etrs89_utm29() -> None:
    bbox, zone = scene_request(_utm29_scene(), 5.0)
    assert zone == PENINSULA_29
    assert bbox == pytest.approx((537190.73 - 15, 4747656.34 - 15, 537190.73 + 15, 4747656.34 + 15), abs=0.001)


def test_scene_request_outside_the_zones_is_refused() -> None:
    scene = _utm29_scene()
    scene["frame"]["origin_wgs84"] = [-1.6432, 42.8125]
    with pytest.raises(CadastreError, match=r"^\(42\.8125, -1\.6432\) is outside the territories the DG Catastro WFS covers$"):
        scene_request(scene, 5.0)


# --- Tiles. ---


def test_a_box_within_the_limit_is_one_tile_on_whole_centimetres() -> None:
    assert split_bbox((664243.98, 4205599.65, 664443.98, 4205799.65)) == [(664243.98, 4205599.65, 664443.98, 4205799.65)]
    assert split_bbox((500000.004, 4000000.006, 500999.996, 4000999.991)) == [(500000.0, 4000000.0, 501000.0, 4001000.0)]


def test_exactly_one_kilometre_is_one_tile_and_one_centimetre_more_is_two() -> None:
    assert split_bbox((500000.0, 4000000.0, 501000.0, 4001000.0)) == [(500000.0, 4000000.0, 501000.0, 4001000.0)]
    assert split_bbox((500000.0, 4000000.0, 501000.01, 4001000.0)) == [
        (500000.0, 4000000.0, 500500.0, 4001000.0), (500500.0, 4000000.0, 501000.01, 4001000.0),
    ]
    assert split_bbox((500000.0, 4000000.0, 501000.0, 4001000.01)) == [
        (500000.0, 4000000.0, 501000.0, 4000500.0), (500000.0, 4000500.0, 501000.0, 4001000.01),
    ]


def test_tiles_run_south_west_first_row_by_row_and_share_their_edges() -> None:
    tiles = split_bbox((500000.0, 4000000.0, 502500.0, 4001500.0))
    assert tiles == [
        (500000.0, 4000000.0, 500833.33, 4000750.0), (500833.33, 4000000.0, 501666.66, 4000750.0),
        (501666.66, 4000000.0, 502500.0, 4000750.0),
        (500000.0, 4000750.0, 500833.33, 4001500.0), (500833.33, 4000750.0, 501666.66, 4001500.0),
        (501666.66, 4000750.0, 502500.0, 4001500.0),
    ]
    assert union_all([box(*t) for t in tiles]).equals(box(500000.0, 4000000.0, 502500.0, 4001500.0))
    assert sum(box(*t).area for t in tiles) == pytest.approx(2500.0 * 1500.0)


def test_twenty_five_tiles_is_the_limit() -> None:
    assert len(split_bbox((500000.0, 4000000.0, 505000.0, 4005000.0))) == 25
    with pytest.raises(CadastreError, match=r"needs 30 tiles; limit is 25$"):
        split_bbox((500000.0, 4000000.0, 505000.01, 4005000.0))


@pytest.mark.parametrize(
    ("bbox", "message"),
    [((-1.13, 37.98, -1.12, 37.99), "degrees given"), ((664443.98, 4205599.65, 664243.98, 4205799.65), "min must be below max")],
)
def test_tiles_refuse_degrees_and_inverted_boxes(bbox: tuple[float, float, float, float], message: str) -> None:
    with pytest.raises(CadastreError, match=message):
        split_bbox(bbox)


# --- Merging and the tiled download. ---


def _member(local_id: str, x: float, y: float, srs: str = "urn:ogc:def:crs:EPSG::25830") -> str:
    ring = f"{x} {y} {x + 10} {y} {x + 10} {y + 10} {x} {y + 10} {x} {y}"
    return (
        f'<gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.{local_id}">'
        f"<bu-core2d:inspireId><base:Identifier><base:localId>{local_id}</base:localId></base:Identifier></bu-core2d:inspireId>"
        f'<bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface srsName="{srs}"><gml:patches><gml:PolygonPatch>'
        f'<gml:exterior><gml:LinearRing><gml:posList srsDimension="2">{ring}</gml:posList></gml:LinearRing></gml:exterior>'
        f"</gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry></bu-core2d:BuildingGeometry></bu-ext2d:geometry>"
        f"<bu-ext2d:numberOfFloorsAboveGround>3</bu-ext2d:numberOfFloorsAboveGround>"
        f"<bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>"
        f"</bu-ext2d:BuildingPart></gml:featureMember>"
    )


def _collection(*members: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n<!--Edificios de la D.G. del Catastro.-->\n'
        '<gml:FeatureCollection gml:id="ES.SDGC.BU" xmlns:base="urn:x-inspire:specification:gmlas:BaseTypes:3.2" '
        'xmlns:bu-core2d="http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0" '
        'xmlns:bu-ext2d="http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">'
        + "".join(members) + "</gml:FeatureCollection>\n"
    ).encode("latin-1")


WEST = _collection(_member("A_part1", 500100.0, 4000100.0), _member("B_part1", 500495.0, 4000100.0))
EAST = _collection(_member("B_part1", 500495.0, 4000100.0), _member("C_part1", 500700.0, 4000100.0))
TWO_TILES = (500000.0, 4000000.0, 501000.01, 4001000.0)


def test_merge_drops_a_part_a_later_tile_repeats(tmp_path: Path) -> None:
    merged, counts = merge_tiles([WEST, EAST])
    assert counts == [{"members": 2, "repeats_dropped": 0}, {"members": 2, "repeats_dropped": 1}]
    path = tmp_path / "m.gml"
    path.write_bytes(merged)
    parts, issues = parse_building_parts(path)
    assert [p.local_id for p in parts] == ["A_part1", "B_part1", "C_part1"] and issues == []
    assert merged.startswith(b"<?xml version='1.0' encoding='utf-8'?>\n<gml:FeatureCollection")
    assert b"<bu-ext2d:BuildingPart" in merged and b"<base:localId>" in merged


def test_merge_keeps_a_single_tile_byte_for_byte_and_a_repeat_inside_one_tile() -> None:
    doubled = _collection(_member("A_part1", 500100.0, 4000100.0), _member("A_part1", 500100.0, 4000100.0))
    assert merge_tiles([doubled]) == (doubled, [{"members": 2, "repeats_dropped": 0}])
    merged, counts = merge_tiles([doubled, WEST])
    assert counts == [{"members": 2, "repeats_dropped": 0}, {"members": 2, "repeats_dropped": 1}]
    assert merged.count(b"<base:localId>A_part1<") == 2 and merged.count(b"<base:localId>B_part1<") == 1


def test_merge_keeps_later_members_without_a_local_id() -> None:
    nameless = _collection(_member("", 500700.0, 4000100.0))
    merged, counts = merge_tiles([WEST, nameless, nameless])
    assert counts[1:] == [{"members": 1, "repeats_dropped": 0}] * 2
    assert merged.count(b"<bu-ext2d:BuildingPart") == 4


class FakeTransport:
    def __init__(self, *payloads: bytes) -> None:
        self.payloads = list(payloads)
        self.urls: list[str] = []

    def __call__(self, url: str, timeout: float) -> bytes:
        self.urls.append(url)
        return self.payloads.pop(0)


def test_a_wide_box_is_downloaded_tile_by_tile_and_merged(tmp_path: Path) -> None:
    transport = FakeTransport(WEST, EAST)
    target = tmp_path / "raw" / "catastro.gml"
    record = download_building_parts(TWO_TILES, target, crs="EPSG:25829", fetch=transport, now=NOW)
    assert [re.search(r"bbox=([^&]+)", u).group(1) for u in transport.urls] == [  # type: ignore[union-attr]
        "500000.00,4000000.00,500500.00,4001000.00", "500500.00,4000000.00,501000.01,4001000.00",
    ]
    assert all("srsName=EPSG:25829" in u for u in transport.urls)
    assert record.url == WFS_BU_URL and record.crs == "EPSG:25829" and record.bbox == [500000.0, 4000000.0, 501000.01, 4001000.0]
    assert record.tiles == [
        {"url": transport.urls[0], "bbox": [500000.0, 4000000.0, 500500.0, 4001000.0], "sha256": hashlib.sha256(WEST).hexdigest(),
         "size_bytes": len(WEST), "members": 2, "repeats_dropped": 0},
        {"url": transport.urls[1], "bbox": [500500.0, 4000000.0, 501000.01, 4001000.0], "sha256": hashlib.sha256(EAST).hexdigest(),
         "size_bytes": len(EAST), "members": 2, "repeats_dropped": 1},
    ]
    assert load_download_record(target) == record
    assert record.size_bytes == target.stat().st_size and record.sha256 == hashlib.sha256(target.read_bytes()).hexdigest()
    parts, issues = parse_building_parts(target)
    assert [p.local_id for p in parts] == ["A_part1", "B_part1", "C_part1"] and issues == []


def test_a_single_tile_keeps_its_request_url_and_bytes(tmp_path: Path) -> None:
    transport = FakeTransport(WEST)
    target = tmp_path / "c.gml"
    record = download_building_parts((500000.0, 4000000.0, 501000.0, 4001000.0), target, fetch=transport, now=NOW)
    assert record.url == transport.urls[0] and "srsName=EPSG:25830" in record.url
    assert target.read_bytes() == WEST and len(record.tiles) == 1


def test_a_failed_tile_writes_nothing_and_an_existing_file_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "c.gml"
    with pytest.raises(CadastreError, match="not a WFS FeatureCollection"):
        download_building_parts(TWO_TILES, target, fetch=FakeTransport(WEST, b"<html>error</html>"), now=NOW)
    assert list(tmp_path.iterdir()) == []
    download_building_parts(TWO_TILES, target, fetch=FakeTransport(WEST, EAST), now=NOW)
    again = FakeTransport(WEST, EAST)
    with pytest.raises(FileExistsError):
        download_building_parts(TWO_TILES, target, fetch=again, now=NOW)
    assert again.urls == []


def test_a_sidecar_written_before_tiles_still_loads(tmp_path: Path) -> None:
    target = tmp_path / "c.gml"
    record = download_building_parts((500000.0, 4000000.0, 501000.0, 4001000.0), target, fetch=FakeTransport(WEST), now=NOW)
    side = target.with_name("c.gml.source.json")
    document = json.loads(side.read_text(encoding="utf-8"))
    del document["tiles"]
    side.write_text(json.dumps(document), encoding="utf-8")
    assert load_download_record(target).tiles == [] and load_download_record(target).sha256 == record.sha256


# --- Parsing in the download CRS and reprojecting to the scene CRS. ---


@pytest.mark.parametrize("srs", ["urn:ogc:def:crs:EPSG::25829", "EPSG:25829"])
def test_parts_are_read_in_the_download_crs(tmp_path: Path, srs: str) -> None:
    path = tmp_path / "g.gml"
    path.write_bytes(_collection(_member("A_part1", 537100.0, 4747600.0, srs), _member("B_part1", 537100.0, 4747600.0)))
    parts, issues = parse_building_parts(path, "EPSG:25829")
    assert [p.local_id for p in parts] == ["A_part1"]
    assert [(i.code, i.local_id, i.message) for i in issues] == [
        ("malformed_part", "B_part1", "unsupported srsName 'urn:ogc:def:crs:EPSG::25830'"),
    ]


def test_reproject_parts_moves_each_vertex_with_pyproj(tmp_path: Path) -> None:
    path = tmp_path / "g.gml"
    path.write_bytes(_collection(_member("A_part1", 300000.0, 4580000.0, "EPSG:25831")))
    parts, _ = parse_building_parts(path, "EPSG:25831")
    assert reproject_parts(parts, "EPSG:25831", "EPSG:25831") == parts
    moved = reproject_parts(parts, "EPSG:25831", "EPSG:25830")
    forward = Transformer.from_crs("EPSG:25831", "EPSG:25830", always_xy=True)
    expected = [forward.transform(x, y) for x, y in parts[0].geometry.exterior.coords]
    assert list(moved[0].geometry.exterior.coords) == pytest.approx(expected)
    assert moved[0].local_id == "A_part1" and moved[0].floors_above == 3


def test_match_reads_a_download_in_another_crs(pipeline_result: GeoResult, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The Murcia fixture moved to EPSG:25831 and back gives the same match as the EPSG:25830 original."""
    from onward_generator.cadastre.__main__ import main
    from onward_generator.geo.pipeline import write_outputs

    forward = Transformer.from_crs("EPSG:25830", "EPSG:25831", always_xy=True)

    def to_25831(match: re.Match[str]) -> str:
        values = [float(v) for v in match.group(2).split()]
        moved = [forward.transform(x, y) for x, y in zip(values[0::2], values[1::2], strict=True)]
        return match.group(1) + " ".join(f"{x:.4f} {y:.4f}" for x, y in moved) + match.group(3)

    text = FIXTURE.read_text(encoding="latin-1").replace("EPSG::25830", "EPSG::25831")
    moved = re.sub(r"(<gml:posList[^>]*>)([^<]+)(</gml:posList>)", to_25831, text).encode("latin-1")
    scene_path, _ = write_outputs(pipeline_result, tmp_path / "geo")
    for crs, payload in (("EPSG:25830", FIXTURE.read_bytes()), ("EPSG:25831", moved)):
        gml = tmp_path / crs.replace(":", "") / "bu.gml"
        download_building_parts((664243.98, 4205599.65, 664443.98, 4205799.65), gml, crs=crs, fetch=FakeTransport(payload), now=NOW)
        assert main(["match", "--scene", str(scene_path), "--gml", str(gml), "--out", str(gml.parent / "out")]) == 0
    first, second = (line for line in capsys.readouterr().out.splitlines() if line.startswith("parts="))
    assert first == second == "parts=5 buildings=5 matched=3 low_coverage=1 no_parts=1 mixed=0"
