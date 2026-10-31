"""Simple 3D Buildings tags through geo → heights → generate → detail (``S3DB_BUILDINGS``).

The block is rebuilt for each test (well under a second), so mutation testing maps each line to every test that runs it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.generation import load_generation_config
from onward_generator.generation.mesh import merge, upward_area
from tests.conftest import _detail_inputs

GEN = load_generation_config()
CFG = load_detail_config()


@pytest.fixture
def s3db(tmp_path_factory: pytest.TempPathFactory):
    from tests.detail_fixture import S3DB_BUILDINGS, S3DB_RELATIONS

    scene, heights, base = _detail_inputs(tmp_path_factory.mktemp("s3db"), S3DB_BUILDINGS, {}, S3DB_RELATIONS)
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, CFG)


def _obj(d, oid: str):
    return next(o for o in d.objects if o.id == oid)


def _issues(d, code: str) -> dict[str, str]:
    return {str(i.object_id): i.message for i in d.issues if i.code == code}


def test_tagged_roof_shapes_are_drawn_closed_on_their_walls(s3db) -> None:
    assert not s3db.critical
    gabled = _obj(s3db, "building/osm-way-801").record
    assert gabled["roof"] == {"type": "gabled", "rise_m": 3.0, "pitch_deg": 36.9, "rise_rule": "roof:height", "rise_origin": "observed",
                              "direction_origin": "estimated", "material": "roof_flat_grey", "origin": "observed", "roof_shape": "gabled"}
    # 1 080 m²: an OSM shape is no longer limited to 800 m². One roof level is one 4 m storey (12 m over 3).
    hipped = _obj(s3db, "building/osm-way-802").record
    assert hipped["roof"]["type"] == "hipped" and hipped["roof"]["rise_m"] == 4.0 and hipped["roof"]["rise_rule"] == "roof:levels"
    skillion = _obj(s3db, "building/osm-way-803").record
    assert skillion["roof"]["type"] == "skillion" and skillion["roof"]["direction_origin"] == "observed"
    for bid in ("osm-way-801", "osm-way-802", "osm-way-803", "osm-way-809"):
        building = _obj(s3db, f"building/{bid}")
        assert building.record["checks"]["facade_open_edges"] == 0
        assert building.record["checks"]["roof_area_error_m2"] < 1e-3
    # The skillion faces south: its high eave is the north wall (y = 20 on the west wing), its low one the south (y = 8).
    roof = merge([p.mesh for p in _obj(s3db, "building/osm-way-803").visual if p.material == "roof_tile"])
    top = float(skillion["height"]["height_m"])
    north = roof.vertices[np.isclose(roof.vertices[:, 1], roof.vertices[:, 1].max())]
    south = roof.vertices[np.isclose(roof.vertices[:, 1], roof.vertices[:, 1].min())]
    assert north[:, 2] == pytest.approx(top + 2.0) and south[:, 2] == pytest.approx(top)
    assert s3db.stats["roof_shapes"] == {"crosspitched:flat_parapet": 1, "flat:flat_parapet": 1, "gabled:flat": 1, "gabled:gabled": 2,
                                         "half-hipped:half-hipped": 1, "hipped:hipped": 1, "skillion:skillion": 1}


def test_every_roof_of_the_block(s3db) -> None:
    roofs = {o.record["source_ids"][0]: (o.record["roof"]["type"], o.record["roof"]["material"]) for o in s3db.objects if o.type == "building"}
    assert roofs == {
        "osm-relation-807": ("flat", "roof_tile"), "osm-way-801": ("gabled", "roof_flat_grey"), "osm-way-802": ("hipped", "roof_tile"),
        "osm-way-803": ("skillion", "roof_tile"), "osm-way-805": ("flat_parapet", "roof_flat_grey"),
        "osm-way-806": ("flat_parapet", "roof_flat_grey"), "osm-way-808": ("flat_parapet", "roof_flat_grey"),
        "osm-way-809": ("gabled", "roof_tile"), "osm-way-822": ("half-hipped", "roof_tile"),
        # Untagged small houses: the estimated hip on some, by the seed and the id.
        "osm-way-811": ("hip", "roof_tile"), "osm-way-812": ("hip", "roof_tile"), "osm-way-813": ("hip", "roof_tile"),
        "osm-way-814": ("flat_parapet", "roof_flat_light"), "osm-way-815": ("flat_parapet", "roof_flat_light"),
        "osm-way-816": ("hip", "roof_tile"),
        # Untagged L-shapes are not convex: no estimated hip (818 and 821 would draw one if they were).
        "osm-way-817": ("flat", "roof_flat_grey"), "osm-way-818": ("flat_parapet", "roof_flat_light"),
        "osm-way-819": ("flat_parapet", "roof_flat_light"), "osm-way-820": ("flat_parapet", "roof_flat_grey"),
        "osm-way-821": ("flat_parapet", "roof_flat_light"),
    }


def test_a_footprint_no_roof_fits_is_flat_with_its_reason(s3db) -> None:
    record = _obj(s3db, "building/osm-relation-807").record
    assert record["roof"]["type"] == "flat" and record["roof"]["fallback"] == "footprint_not_a_simple_polygon"
    # A roof that was never tagged has no fallback.
    assert all("fallback" not in o.record["roof"] for o in s3db.objects if o.type == "building" and not o.record["roof"].get("roof_shape"))


def test_every_roof_issue_is_reported(s3db) -> None:
    found = [(i.code, i.severity, i.object_id, i.message) for i in s3db.issues if "roof" in i.code]
    assert found == [
        ("roof_shape_fallback", "info", "osm-relation-807", "roof:shape=gabled drawn flat: footprint_not_a_simple_polygon"),
        ("roof_shape_unsupported", "info", "osm-way-805", "roof:shape=crosspitched is not drawn; the template roof stays"),
    ]


def test_gable_ends_take_the_wall_material(s3db) -> None:
    record = _obj(s3db, "building/osm-way-801").record
    assert record["facade"]["materials"]["roof"] == "roof_flat_grey" and record["facade"]["materials"]["roof_origin"] == "observed"
    # The gable ends take the wall material; the slopes the roof material.
    by_material = {p.material: p.mesh for p in _obj(s3db, "building/osm-way-801").visual}
    wall = record["facade"]["materials"]["wall"]
    assert upward_area(by_material["roof_flat_grey"]) == pytest.approx(96.0)
    assert by_material[wall].vertices[:, 2].max() == pytest.approx(by_material["roof_flat_grey"].vertices[:, 2].max())
    # A half-hipped end wall is a trapezoid half way up the 30° roof, in the wall material.
    ends = {p.material: p.mesh for p in _obj(s3db, "building/osm-way-822").visual}
    top = float(_obj(s3db, "building/osm-way-822").record["height"]["height_m"])
    rise = _obj(s3db, "building/osm-way-822").record["roof"]["rise_m"]
    assert ends["roof_tile"].vertices[:, 2].max() == pytest.approx(top + rise, abs=1e-3)
    wall = next(m for name, m in ends.items() if name.startswith("plaster") or name.startswith("brick"))
    assert wall.vertices[:, 2].max() == pytest.approx(top + rise / 2, abs=1e-3)


def test_the_report_and_manifest_carry_the_new_records(s3db, tmp_path: Path) -> None:
    manifest = s3db.manifest()
    roofs = {o["source_ids"][0]: o["roof"] for o in manifest["objects"] if o["type"] == "building" and o["roof"].get("roof_shape")}
    assert roofs["osm-way-822"]["type"] == "half-hipped" and roofs["osm-relation-807"]["fallback"] == "footprint_not_a_simple_polygon"
    assert s3db.report()["counts"]["roof_shapes"] == s3db.stats["roof_shapes"]
