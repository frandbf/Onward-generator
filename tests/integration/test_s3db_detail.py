"""Simple 3D Buildings tags through geo → heights → generate → detail (``S3DB_BUILDINGS``).

The block is rebuilt for each test (well under a second), so mutation testing maps each line to every test that runs it.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.scene import _part_polygon
from onward_generator.generation import load_generation_config
from onward_generator.generation.mesh import closed_manifold_defects, merge, open_edge_defects, upward_area
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
                              "direction_origin": "estimated", "material": "roof_tile__tint_grey", "origin": "observed", "roof_shape": "gabled"}
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
        "osm-relation-807": ("flat", "roof_tile"), "osm-way-801": ("gabled", "roof_tile__tint_grey"), "osm-way-802": ("hipped", "roof_tile"),
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


def test_every_roof_and_part_issue_is_reported(s3db) -> None:
    found = [(i.code, i.severity, i.object_id, i.message) for i in s3db.issues if "part" in i.code or "roof" in i.code]
    assert found == [
        ("roof_shape_fallback", "info", "osm-relation-807", "roof:shape=gabled drawn flat: footprint_not_a_simple_polygon"),
        ("roof_shape_unsupported", "info", "osm-way-805", "roof:shape=crosspitched is not drawn; the template roof stays"),
        ("building_part_hidden", "info", "osm-way-902", "6.00 m is not above building osm-way-806 (12.00 m); skipped"),
        ("building_part_no_height", "info", "osm-way-904", "building:part without a parseable height or building:levels; skipped"),
        ("building_part_height_order", "warning", "osm-way-906", "min_height is not below height; skipped"),
        ("building_part_outside_footprint", "info", "osm-way-907", "building:part does not overlap a building footprint; skipped"),
        ("roof_shape_fallback", "info", "osm-way-909", "roof:shape=pyramidal drawn flat: no_room_under_height"),
    ]


def test_colour_tags_tint_shared_materials(s3db) -> None:
    materials = _obj(s3db, "building/osm-way-801").record["facade"]["materials"]
    assert materials == {"wall": "plaster_white__tint_white", "wall_origin": "observed", "wall_source": "building:colour",
                         "roof": "roof_tile__tint_grey", "roof_origin": "observed", "roof_source": "roof:colour"}
    plain = _obj(s3db, "building/osm-way-806").record["facade"]["materials"]
    assert plain["wall_origin"] == "generated" and plain["wall_source"] is None and plain["roof_source"] is None
    brick = _obj(s3db, "building/osm-way-803").record["facade"]["materials"]
    assert (brick["wall"], brick["wall_origin"], brick["wall_source"]) == ("brick_red", "observed", "building:material")
    # The gable ends take the wall material; the slopes the roof material.
    by_material = {p.material: p.mesh for p in _obj(s3db, "building/osm-way-801").visual}
    assert upward_area(by_material["roof_tile__tint_grey"]) == pytest.approx(96.0)
    assert by_material["plaster_white__tint_white"].vertices[:, 2].max() == pytest.approx(by_material["roof_tile__tint_grey"].vertices[:, 2].max())
    # A half-hipped end wall is a trapezoid half way up the 30° roof, in the wall material.
    ends = {p.material: p.mesh for p in _obj(s3db, "building/osm-way-822").visual}
    top = float(_obj(s3db, "building/osm-way-822").record["height"]["height_m"])
    rise = _obj(s3db, "building/osm-way-822").record["roof"]["rise_m"]
    assert ends["roof_tile"].vertices[:, 2].max() == pytest.approx(top + rise, abs=1e-3)
    wall = next(m for name, m in ends.items() if name.startswith("plaster") or name.startswith("brick"))
    assert wall.vertices[:, 2].max() == pytest.approx(top + rise / 2, abs=1e-3)


GENERATED = {"wall_origin": "generated", "wall_source": None, "roof": "roof_tile", "roof_origin": "generated", "roof_source": None}


def test_building_parts_take_their_height_from_levels_and_their_roof_from_tags(s3db) -> None:
    parts = {o.record["source_ids"][1]: o for o in s3db.objects if o.type == "building_part"}
    assert set(parts) == {"osm-way-901", "osm-way-903", "osm-way-905", "osm-way-908", "osm-way-909", "osm-way-910"}
    assert all(p.record["source_ids"][0] == "osm-way-806" for p in parts.values())
    # Industrial: 6 m storeys from heights.json.
    tower = parts["osm-way-901"].record
    assert tower["height"] == {"height_m": 54.0, "min_height_m": 0.0, "origin": "estimated", "min_height_origin": "default",
                               "rule": "(building:levels 8 + roof:levels 1) × 6 m (industrial)", "eave_m": 48.0}
    assert tower["roof"] == {"type": "pyramidal", "rise_m": 6.0, "pitch_deg": 63.4, "rise_rule": "roof:levels", "rise_origin": "observed",
                             "material": "roof_tile", "roof_shape": "pyramidal", "origin": "observed"}
    assert tower["materials"] == {"wall": "plaster_ochre", **GENERATED}
    raised = parts["osm-way-903"].record
    assert raised["height"] == {"height_m": 20.0, "min_height_m": 10.0, "origin": "observed", "min_height_origin": "observed",
                                "rule": "height", "eave_m": 16.0}
    assert raised["roof"] == {"type": "gabled", "rise_m": 4.0, "pitch_deg": 53.1, "rise_rule": "roof:height", "rise_origin": "observed",
                              "direction_origin": "estimated", "material": "roof_tile__tint_grey", "roof_shape": "gabled", "origin": "observed"}
    assert raised["materials"] == {"wall": "plaster_white__tint_red", "wall_origin": "observed", "wall_source": "building:colour",
                                   "roof": "roof_tile__tint_grey", "roof_origin": "observed", "roof_source": "roof:colour"}
    # The gable ends of a part take its wall material.
    assert [(p.material, p.mesh.triangle_count) for p in parts["osm-way-903"].visual] == [
        ("plaster_white__tint_red", 8), ("roof_tile__tint_grey", 4), ("plaster_white__tint_red", 2)]
    assert [(p.material, p.mesh.triangle_count) for p in parts["osm-way-905"].visual] == [("plaster_ochre", 8), ("roof_tile", 2)]
    # Its roof starts at 10.5 m, inside its 12 m building: it keeps no wall, only the roof and its gables.
    assert [p.material for p in parts["osm-way-908"].visual] == ["roof_tile", "plaster_ochre"]
    flat = parts["osm-way-905"].record
    assert flat["roof"] == {"type": "flat", "material": "roof_tile", "roof_shape": None, "origin": "generated"}
    assert flat["height"]["eave_m"] == 15.0 and flat["materials"] == {"wall": "plaster_ochre", **GENERATED}
    # A 15 m roof on a 10 m part keeps 0.5 m of wall; with no room at all the roof is flat.
    cut = parts["osm-way-908"].record
    assert cut["roof"]["rise_m"] == 9.5 and cut["height"]["eave_m"] == 10.5
    assert parts["osm-way-909"].record["roof"] == {"type": "flat", "fallback": "no_room_under_height", "material": "roof_tile",
                                                    "roof_shape": "pyramidal", "origin": "observed"}
    assert parts["osm-way-910"].record["visual"] == {"triangles": 7}  # a closed triangle way
    # Its walls start at the top of its 12 m building, or at its own min_height above that: below it the part is inside.
    # A roof that starts lower (osm-way-908, eave 10.5 m) starts there.
    building = _obj(s3db, "building/osm-way-806")
    top = building.record["height"]["height_m"]
    assert top == pytest.approx(12.0)
    # Along the building's outline they start at its parapet top instead, where its own wall ends: the open foot of the
    # part's wall there lies on the top edge of the building's wall (on Barcelona Sarria the two z-fought along the parapet).
    rim = top + building.record["roof"]["parapet_m"]
    assert rim > top
    shell = merge([p.mesh for p in building.visual])
    tris = shell.vertices[shell.triangles]
    rim_edges = [(t[i], t[(i + 1) % 3]) for t in tris for i in range(3)
                 if abs(t[i][2] - rim) < 1e-6 and abs(t[(i + 1) % 3][2] - rim) < 1e-6]
    raised_on_outline = 0
    for part in parts.values():
        visual = merge([p.mesh for p in part.visual])
        z = visual.vertices[:, 2]
        base = min(max(part.record["height"]["min_height_m"], top), part.record["height"]["eave_m"])
        assert z.max() == pytest.approx(part.record["height"]["height_m"]) and z.min() == pytest.approx(base)
        # Where an inner wall meets the outline, its side from its foot up to the parapet top lies on the building's wall.
        corners = [((x, y, base), (x, y, rim)) for x, y, z in visual.vertices if abs(z - rim) < 1e-6]
        assert open_edge_defects(visual, z_base=base, allowed=[*rim_edges, *corners]) == 0
        raised_on_outline += open_edge_defects(visual, z_base=base) > 0
        assert part.record["visual"]["triangles"] == visual.triangle_count
        [collider] = part.colliders
        assert closed_manifold_defects(collider) == 0 and collider.vertices[:, 2].max() == pytest.approx(part.record["height"]["eave_m"])
    assert raised_on_outline > 0
    assert s3db.stats["building_parts"] == {
        "kept": 6, "height_origin": {"estimated": 1, "observed": 5}, "roofs": {"flat": 3, "gabled": 1, "pyramidal": 1, "skillion": 1},
        "skipped": {"building_part_height_order": 1, "building_part_hidden": 1, "building_part_no_height": 1, "building_part_outside_footprint": 1},
    }


def test_a_part_way_must_be_a_closed_ring() -> None:
    ring = [[0.0, 0.0], [4.0, 0.0], [4.0, 3.0], [0.0, 3.0]]
    assert _part_polygon({"type": "LineString", "coordinates": [*ring, ring[0]]}).area == pytest.approx(12.0)  # type: ignore[union-attr]
    assert _part_polygon({"type": "LineString", "coordinates": [[0.0, 0.0], [4.0, 0.0], [0.0, 3.0], [0.0, 0.0]]}).area == pytest.approx(6.0)  # type: ignore[union-attr]
    assert _part_polygon({"type": "LineString", "coordinates": ring}) is None  # open
    assert _part_polygon({"type": "LineString", "coordinates": [*ring, [0.0, 0.5]]}) is None  # open, five points
    assert _part_polygon({"type": "LineString", "coordinates": [[0.0, 0.0], [4.0, 0.0], [0.0, 0.0]]}) is None  # three points
    assert _part_polygon({"type": "MultiLineString", "coordinates": [ring]}) is None
    assert _part_polygon({"type": "Polygon", "coordinates": [[*ring, ring[0]]]}).area == pytest.approx(12.0)  # type: ignore[union-attr]


def test_the_report_and_manifest_carry_the_new_records(s3db, tmp_path: Path) -> None:
    manifest = s3db.manifest()
    parts = [o for o in manifest["objects"] if o["type"] == "building_part"]
    assert len(parts) == 6 and all("roof" in o and "eave_m" in o["height"] for o in parts)
