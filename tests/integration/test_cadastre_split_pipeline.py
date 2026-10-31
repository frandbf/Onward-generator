"""A block footprint split by Catastro parts, through geo → cadastre split → heights → generate → detail (ADR-016).

The OSM block and the GML are synthetic (CC0-1.0), written at test time around MURCIA_CENTER.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.cadastre import download_building_parts
from onward_generator.cadastre.acquire import BUILDINGS_TYPE, buildings_path
from onward_generator.cli import main
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.generation import load_generation_config
from onward_generator.geo.aoi import load_aoi_geojson
from onward_generator.geo.pipeline import run_geo_pipeline, write_outputs
from onward_generator.geo.projection import LocalFrame
from onward_generator.geo.sources import register_local_source
from tests.conftest import AOI_FIXTURE, FIXED_NOW, ORIGIN, SYNTHETIC_LICENSE
from tests.detail_fixture import write_block_osm

BLOCK = 900
BUILDINGS = {
    BLOCK: ([(-30, 6), (14, 6), (14, 22), (-30, 22)], {"building": "apartments"}),
    901: ([(30, 6), (50, 6), (50, 20), (30, 20)], {"building": "residential", "building:levels": "3"}),
}
POIS = {401: ((-1.0, 7.0), {"shop": "bakery"})}  # inside the one-storey shop, 1 m from the six-storey party wall
# localId, storeys above ground, local x range; every part spans the block depth (y 6–22).
PARTS = [
    ("1000001AA0000A_part1", 4, (-30.0, -15.0)),
    ("1000002AA0000A_part1", 1, (-15.0, 0.0)),
    ("1000003AA0000A_part1", 6, (0.0, 14.0)),
]
SHOP = f"osm-way-{BLOCK}:1000002AA0000A_part1"
RETAIL = f"osm-way-{BLOCK}:1000001AA0000A_part1"
# Catastro's predominant use per parcel (ADR-022): the four-storey parcel is retail, the others residential.
USES = {"1000001AA0000A": "4_2_retail", "1000002AA0000A": "1_residential", "1000003AA0000A": "1_residential"}


def _buildings_gml() -> bytes:
    members = "".join(
        f'<gml:featureMember><bu-ext2d:Building gml:id="ES.SDGC.BU.{refcat}">'
        f"<bu-core2d:inspireId><base:Identifier><base:localId>{refcat}</base:localId></base:Identifier></bu-core2d:inspireId>"
        f"<bu-ext2d:currentUse>{use}</bu-ext2d:currentUse>"
        f"<bu-ext2d:numberOfBuildingUnits>4</bu-ext2d:numberOfBuildingUnits><bu-ext2d:numberOfDwellings>3</bu-ext2d:numberOfDwellings>"
        f"</bu-ext2d:Building></gml:featureMember>"
        for refcat, use in USES.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gml:FeatureCollection xmlns:base="urn:x-inspire:specification:gmlas:BaseTypes:3.2" '
        'xmlns:bu-core2d="http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0" '
        'xmlns:bu-ext2d="http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">'
        + members + "</gml:FeatureCollection>\n"
    ).encode()


def _gml(origin_en: tuple[float, float], parts: list[tuple[str, int, tuple[float, float, float, float]]] | None = None) -> bytes:
    """A Catastro GML of ``parts``: ``(localId, storeys, (x0, y0, x1, y1))``; by default ``PARTS`` over the block depth."""
    e0, n0 = origin_en
    members = []
    boxes = parts if parts is not None else [(local_id, floors, (x0, 6.0, x1, 22.0)) for local_id, floors, (x0, x1) in PARTS]
    for index, (local_id, floors, (x0, y0, x1, y1)) in enumerate(boxes):
        ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)]
        pos = " ".join(f"{e0 + x:.3f} {n0 + y:.3f}" for x, y in ring)
        members.append(
            f'<gml:featureMember><bu-ext2d:BuildingPart gml:id="ES.SDGC.BU.{local_id}">'
            f"<bu-core2d:inspireId><base:Identifier><base:localId>{local_id}</base:localId></base:Identifier></bu-core2d:inspireId>"
            f'<bu-ext2d:geometry><bu-core2d:BuildingGeometry><bu-core2d:geometry><gml:Surface gml:id="S{index}" '
            f'srsName="urn:ogc:def:crs:EPSG::25830"><gml:patches><gml:PolygonPatch><gml:exterior><gml:LinearRing>'
            f'<gml:posList srsDimension="2" count="5">{pos}</gml:posList>'
            f"</gml:LinearRing></gml:exterior></gml:PolygonPatch></gml:patches></gml:Surface></bu-core2d:geometry>"
            f"</bu-core2d:BuildingGeometry></bu-ext2d:geometry>"
            f"<bu-ext2d:numberOfFloorsAboveGround>{floors}</bu-ext2d:numberOfFloorsAboveGround>"
            f"<bu-ext2d:numberOfFloorsBelowGround>0</bu-ext2d:numberOfFloorsBelowGround>"
            f"</bu-ext2d:BuildingPart></gml:featureMember>"
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gml:FeatureCollection xmlns:base="urn:x-inspire:specification:gmlas:BaseTypes:3.2" '
        'xmlns:bu-core2d="http://inspire.jrc.ec.europa.eu/schemas/bu-core2d/2.0" '
        'xmlns:bu-ext2d="http://inspire.jrc.ec.europa.eu/schemas/bu-ext2d/2.0" xmlns:gml="http://www.opengis.net/gml/3.2">'
        + "".join(members)
        + "</gml:FeatureCollection>\n"
    ).encode()


@pytest.fixture(scope="module")
def split_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("split")
    osm = write_block_osm(root / "block.osm", ORIGIN, buildings=BUILDINGS, pois=POIS)
    source = register_local_source(osm, license_info=SYNTHETIC_LICENSE, retrieved_at=FIXED_NOW,
                                   data_timestamp="2026-10-18T00:00:00Z", now=FIXED_NOW)
    geo_scene, _ = write_outputs(run_geo_pipeline(source, load_aoi_geojson(AOI_FIXTURE), origin=ORIGIN), root / "geo")
    frame = LocalFrame.create(*ORIGIN)
    origin_en = (frame.origin_easting, frame.origin_northing)
    gml = root / "raw" / "catastro.gml"
    bbox = (origin_en[0] - 100, origin_en[1] - 100, origin_en[0] + 100, origin_en[1] + 100)
    download_building_parts(bbox, gml, fetch=lambda url, timeout: _gml(origin_en), now=FIXED_NOW)
    uses = buildings_path(gml)
    download_building_parts(bbox, uses, fetch=lambda url, timeout: _buildings_gml(), now=FIXED_NOW, typename=BUILDINGS_TYPE)
    cadastre = root / "cadastre"
    assert main(["cadastre", "split", "--scene", str(geo_scene), "--gml", str(gml), "--buildings", str(uses), "--out", str(cadastre)]) == 0
    scene = cadastre / "geo_scene.json"
    heights = root / "heights"
    overrides = cadastre / "heights_overrides.catastro.json"
    assert main(["heights", "--scene", str(scene), "--overrides", str(overrides), "--out", str(heights)]) == 0
    base = root / "scene3d"
    assert main(["generate", "--scene", str(scene), "--heights", str(heights / "heights.json"), "--out", str(base), "--no-preview"]) == 0
    return {"geo": geo_scene, "scene": scene, "heights": heights, "base": base, "cadastre": cadastre}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_block_becomes_one_building_per_construction(split_run: dict[str, Path]) -> None:
    before = [f["id"] for f in _read(split_run["geo"])["features"] if f["kind"] == "building"]
    after = [f["id"] for f in _read(split_run["scene"])["features"] if f["kind"] == "building"]
    assert sorted(before) == ["osm-way-900", "osm-way-901"]
    assert sorted(after) == sorted([f"osm-way-{BLOCK}:{local_id}" for local_id, _, _ in PARTS] + ["osm-way-901"])
    report = _read(split_run["cadastre"] / "cadastre_match_report.json")
    assert report["split"]["counts"]["osm_buildings_split"] == 1
    assert report["split"]["counts"]["sub_buildings"] == 3
    assert report["split"]["counts"]["levels_range"] == [1, 6]


def test_each_construction_gets_its_own_height(split_run: dict[str, Path]) -> None:
    heights = {b["building_id"]: b for b in _read(split_run["heights"] / "heights.json")["buildings"]}
    levels = {bid: (b["estimated_levels"], b["height_source"]) for bid, b in heights.items() if bid.startswith(f"osm-way-{BLOCK}:")}
    assert levels == {
        f"osm-way-{BLOCK}:1000001AA0000A_part1": (4, "authoritative_levels"),
        SHOP: (1, "authoritative_levels"),
        f"osm-way-{BLOCK}:1000003AA0000A_part1": (6, "authoritative_levels"),
    }
    assert len({heights[bid]["height_m"] for bid in levels}) == 3
    assert heights["osm-way-901"]["height_source"] == "explicit_levels"  # no Catastro part: OSM estimate as before
    report = _read(split_run["heights"] / "heights_report.json")
    assert report["counts"]["subdivided"] == {"osm_buildings_split": 1, "sub_buildings": 3, "levels_range": [1, 6]}
    sources = _read(split_run["heights"] / "heights.json")["overrides"]["sources"]
    assert [s["license"] for s in sources] == ["CC-BY-4.0"]


def test_generate_closes_every_volume_and_credits_catastro(split_run: dict[str, Path]) -> None:
    report = _read(split_run["base"] / "generation_report.json")
    assert report["severity"]["critical"] == 0
    assert not [i for i in report["issues"] if i["code"] in ("overlapping_buildings", "duplicate_building_skipped")]
    scene3d = _read(split_run["base"] / "scene3d.json")
    assert any(a.get("license") == "CC-BY-4.0" for a in scene3d["attribution"])


def test_detail_puts_the_shop_on_its_own_ground_floor_and_blanks_shared_walls(split_run: dict[str, Path]) -> None:
    generated, ref = load_base(split_run["base"], split_run["scene"], split_run["heights"] / "heights.json", load_generation_config())
    detailed = detail_scene(generated, ref, split_run["heights"] / "heights.json", split_run["scene"], load_detail_config())
    assert not detailed.critical
    facades = {o.record["source_ids"][0]: o.record for o in detailed.objects if o.type == "building"}
    shop = facades[SHOP]["facade"]
    assert shop["ground_floor"] == "shopfront" and shop["ground_floor_evidence"]["osm_poi_ids"] == ["osm-node-1011"]
    tall = facades[f"osm-way-{BLOCK}:1000003AA0000A_part1"]["facade"]
    assert tall["ground_floor"] != "shopfront"  # the bakery is 1 m away, but inside its neighbour; its parcel is residential
    assert shop["party_wall_length_m"] == pytest.approx(32.0, abs=0.01)  # both 16 m sides are shared
    assert tall["party_wall_length_m"] == pytest.approx(16.0, abs=0.01)
    for record in facades.values():
        assert record["checks"]["facade_open_edges"] == 0
    # Sub-buildings keep the block's OSM tags, so the facade use is the parent's observed building=apartments.
    for local_id, _, _ in PARTS:
        bid = f"osm-way-{BLOCK}:{local_id}"
        assert facades[bid]["facade"]["use"] == {
            "use": "residential", "origin": "observed",
            "evidence": [{"source_id": bid, "tag": "building=apartments", "match": "building"}],
        }


def test_a_retail_parcel_gets_a_shopfront_from_its_catastro_use(split_run: dict[str, Path]) -> None:
    generated, ref = load_base(split_run["base"], split_run["scene"], split_run["heights"] / "heights.json", load_generation_config())
    detailed = detail_scene(generated, ref, split_run["heights"] / "heights.json", split_run["scene"], load_detail_config())
    assert not detailed.critical
    facades = {o.record["source_ids"][0]: o.record["facade"] for o in detailed.objects if o.type == "building"}
    retail = facades[RETAIL]
    assert retail["ground_floor"] == "shopfront"
    assert retail["ground_floor_evidence"] == {
        "source": "catastro_use", "osm_poi_ids": [], "current_use": "4_2_retail", "refcat": "1000001AA0000A", "share": 1.0,
        "use_origin": "observed", "floor_assignment": "estimated",
        "rule": "Catastro predominant use 4_2_retail: commercial ground floor",
        "provider": "catastro-inspire-bu", "license": "CC-BY-4.0", "attribution": "Dirección General del Catastro, INSPIRE Buildings (BU)",
    }
    [shop] = retail["shops"]
    assert (shop["poi_id"], shop["name"], shop["name_origin"], shop["name_rule"]) == (None, "Comercio", "estimated", "catastro_use=4_2_retail")
    # The OSM bakery still decides its own building, ahead of that parcel's residential use.
    assert facades[SHOP]["ground_floor_evidence"]["source"] == "osm_poi"
    assert detailed.stats["shopfront_evidence"] == {"catastro_use": 1, "osm_poi": 1}
    assert RETAIL in detailed.stats["shop_interiors"]
    # The facade use stays the parent's observed building=apartments: the Catastro use only sets the ground floor.
    assert retail["use"]["use"] == "residential"


def test_a_catastro_shop_waits_with_the_shops_nearest_the_centre_first(split_run: dict[str, Path]) -> None:
    import copy
    import json as _json

    from onward_generator.detail import config_from_document
    from onward_generator.detail.config import DEFAULT_CONFIG_PATH

    heights = split_run["heights"] / "heights.json"
    generated, ref = load_base(split_run["base"], split_run["scene"], heights, load_generation_config())
    full = detail_scene(generated, ref, heights, split_run["scene"], load_detail_config())
    cost = {b: next(o for o in full.objects if o.id == f"interior/{b}").record["visual_triangles"] for b in (SHOP, RETAIL)}
    doc = copy.deepcopy(_json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))
    doc["interiors"]["shops"]["max_triangles"] = max(cost.values())
    assert sum(cost.values()) > max(cost.values())
    tight = detail_scene(generated, ref, heights, split_run["scene"], config_from_document(doc))
    # The bakery (centre x = -7.5) is nearer the map centre than the retail parcel (x = -22.5), so it goes first,
    # although the retail parcel also matches the stair-interior rule.
    assert tight.stats["shop_interiors"] == [SHOP]


def test_a_catastro_use_ranked_above_osm_replaces_the_poi_shops(
    split_run: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from onward_generator.sources.registry import load_source_config

    scene = json.loads(split_run["scene"].read_text(encoding="utf-8"))
    retail_use = next(f["catastro_use"] for f in scene["features"] if f["id"] == RETAIL)
    next(f for f in scene["features"] if f["id"] == SHOP)["catastro_use"] = {**retail_use, "refcat": "1000002AA0000A"}
    edited = tmp_path / "geo_scene.json"
    edited.write_text(json.dumps(scene), encoding="utf-8")
    heights = split_run["heights"] / "heights.json"
    generated, ref = load_base(split_run["base"], split_run["scene"], heights, load_generation_config())

    config = load_source_config()
    monkeypatch.setattr("onward_generator.detail.scene.load_source_config",
                        lambda: type(config)(config.licence_allowlist, config.priority, ("es.catastro", "osm")))
    detailed = detail_scene(generated, ref, heights, edited, load_detail_config())
    facades = {o.record["source_ids"][0]: o.record["facade"] for o in detailed.objects if o.type == "building"}
    shop = facades[SHOP]
    assert shop["ground_floor_evidence"]["source"] == "catastro_use"
    assert shop["ground_floor_evidence"]["refcat"] == "1000002AA0000A"
    assert [(s["poi_id"], s["name"]) for s in shop["shops"]] == [(None, "Comercio")]  # the bakery POI is not its shop
    assert detailed.stats["shopfront_evidence"] == {"catastro_use": 2}
