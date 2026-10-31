"""A parcel with a lower street part and a taller rear part, beside another parcel (ADR-016, setbacks).

geo → cadastre split → heights → generate → detail on a synthetic block (CC0-1.0) around MURCIA_CENTER.
The two parts of parcel 2000001 look like one building: one wall material, a flat terrace on the
lower part, and windows on the rear part's wall above that terrace. The wall of parcel 2000002 over
the same terrace stays a blank party wall.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from onward_generator.cadastre import download_building_parts
from onward_generator.cli import main
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.facades import party_material
from onward_generator.detail.scene import DetailedScene, DetailObject
from onward_generator.generation import load_generation_config
from onward_generator.geo.aoi import load_aoi_geojson
from onward_generator.geo.pipeline import run_geo_pipeline, write_outputs
from onward_generator.geo.projection import LocalFrame
from onward_generator.geo.sources import register_local_source
from tests.conftest import AOI_FIXTURE, FIXED_NOW, ORIGIN, SYNTHETIC_LICENSE
from tests.detail_fixture import write_block_osm
from tests.integration.test_cadastre_split_pipeline import _gml

BLOCK = 900
BUILDINGS = {
    BLOCK: ([(-30, 6), (14, 6), (14, 22), (-30, 22)], {"building": "apartments"}),
    # 0.5 m east of the block, inside probe_m: the block's east wall is a blind party wall.
    901: ([(14.5, 6), (30, 6), (30, 22), (14.5, 22)], {"building": "residential", "building:levels": "2"}),
}
# localId, storeys above ground, (x0, y0, x1, y1). The street is south of the block (y < 6).
PARTS = [
    ("2000001AA0000A_part1", 3, (-30.0, 6.0, -10.0, 14.0)),  # parcel 2000001, on the street
    ("2000001AA0000A_part2", 6, (-30.0, 14.0, -10.0, 22.0)),  # parcel 2000001, behind it
    ("2000002AA0000A_part1", 6, (-10.0, 6.0, 14.0, 22.0)),  # parcel 2000002, next door
]
FRONT, REAR, NEXT = (f"osm-way-{BLOCK}:{local_id}" for local_id, _, _ in PARTS)


@pytest.fixture(scope="module")
def inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    root = tmp_path_factory.mktemp("setbacks")
    osm = write_block_osm(root / "block.osm", ORIGIN, buildings=BUILDINGS, pois={})
    source = register_local_source(osm, license_info=SYNTHETIC_LICENSE, retrieved_at=FIXED_NOW,
                                   data_timestamp="2026-10-18T00:00:00Z", now=FIXED_NOW)
    geo_scene, _ = write_outputs(run_geo_pipeline(source, load_aoi_geojson(AOI_FIXTURE), origin=ORIGIN), root / "geo")
    frame = LocalFrame.create(*ORIGIN)
    origin_en = (frame.origin_easting, frame.origin_northing)
    gml = root / "raw" / "catastro.gml"
    bbox = (origin_en[0] - 100, origin_en[1] - 100, origin_en[0] + 100, origin_en[1] + 100)
    download_building_parts(bbox, gml, fetch=lambda url, timeout: _gml(origin_en, PARTS), now=FIXED_NOW)
    cadastre = root / "cadastre"
    assert main(["cadastre", "split", "--scene", str(geo_scene), "--gml", str(gml), "--out", str(cadastre)]) == 0
    scene = cadastre / "geo_scene.json"
    heights = root / "heights" / "heights.json"
    assert main(["heights", "--scene", str(scene), "--overrides", str(cadastre / "heights_overrides.catastro.json"),
                 "--out", str(heights.parent)]) == 0
    base = root / "scene3d"
    assert main(["generate", "--scene", str(scene), "--heights", str(heights), "--out", str(base), "--no-preview"]) == 0
    return base, scene, heights


@pytest.fixture
def detailed(inputs: tuple[Path, Path, Path]) -> DetailedScene:
    """Detailed in each test, not once per module, so mutation testing runs every test against detail_scene."""
    base, scene, heights = inputs
    generated, ref = load_base(base, scene, heights, load_generation_config())
    return detail_scene(generated, ref, heights, scene, load_detail_config())


def _buildings(detailed: DetailedScene) -> dict[str, DetailObject]:
    return {o.record["source_ids"][0]: o for o in detailed.objects if o.type == "building"}


def test_the_parts_of_one_parcel_look_like_one_building(detailed: DetailedScene) -> None:
    assert not detailed.critical
    b = _buildings(detailed)
    assert sorted(b) == sorted([FRONT, REAR, NEXT, "osm-way-901"])
    # NEXT's walls: 8 m against the street part, 8 m against the rear part, 16 m to 901 across the gap.
    assert b[NEXT].record["facade"]["party_wall_length_m"] == pytest.approx(32.0, abs=0.05)
    walls = {bid: o.record["facade"]["materials"]["wall"] for bid, o in b.items()}
    assert walls[FRONT] == walls[REAR] != walls[NEXT]
    # Both parts draw the roof grammar for the parcel, and this seed gives the parcel a hip. The rear part,
    # the top of the building, takes it; the street part is the terrace under the setback: flat, behind its parapet.
    assert b[REAR].record["roof"]["type"] == "hip"
    assert b[FRONT].record["roof"]["type"] == "flat_parapet"
    for o in b.values():
        assert o.record["checks"]["facade_open_edges"] == 0


def _south_faces(o: DetailObject, material: str) -> np.ndarray:
    """Triangles of ``material`` on the plane y = 14 between x −30 and −10 that face south."""
    (mesh,) = [p.mesh for p in o.visual if p.material == material]
    tri = mesh.vertices[mesh.triangles]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    on_plane = np.all(np.isclose(tri[:, :, 1], 14.0, atol=0.01), axis=1) & np.all((tri[:, :, 0] > -30.01) & (tri[:, :, 0] < -9.99), axis=1)
    return tri[on_plane & (normal[:, 1] < 0)]


def test_the_rear_wall_over_the_terrace_has_windows_above_it(detailed: DetailedScene) -> None:
    b = _buildings(detailed)
    front_roof = b[FRONT].record["height"]["height_m"]
    assert front_roof < b[REAR].record["height"]["height_m"]
    # The 20 m wall holds 5 bays; storeys 4 to 6 of the rear part are above the 3-storey street part.
    assert detailed.stats["setback_walls"] == {"walls": 1, "opened": 1, "openings": 15}
    rear_wall = b[REAR].record["facade"]["materials"]["wall"]
    faces = _south_faces(b[REAR], rear_wall)
    assert len(faces)
    # The band the terrace hides is gone; the facade starts at or above the terrace roof.
    assert faces[:, :, 2].min() >= front_roof - 1e-6
    assert party_material(rear_wall) not in [p.material for p in b[REAR].visual]
    # The rear part's openings are counted with the building's.
    openings = b[REAR].record["facade"]["openings"]
    assert sum(openings.values()) > 0


def test_the_next_parcel_keeps_a_blank_party_wall_over_the_terrace(detailed: DetailedScene) -> None:
    b = _buildings(detailed)
    wall = b[NEXT].record["facade"]["materials"]["wall"]
    party = [p.mesh for p in b[NEXT].visual if p.material == party_material(wall)]
    assert len(party) == 1
    tri = party[0].vertices[party[0].triangles]
    # On x = −10 it stands above the street part's roof (and above the hip-roofed rear part's eaves).
    west = tri[np.all(np.isclose(tri[:, :, 0], -10.0, atol=0.01), axis=1)]
    assert len(west)
    assert west[:, :, 2].min() >= b[FRONT].record["height"]["height_m"] - 1e-6
