"""Furniture on the floors of the stair interior (ADR-034), on the synthetic street block around MURCIA_CENTER.

Buildings 311 and 313 take the stair interior (two floors each) and 312 a shop (``test_shop_interiors.py``).
With ``interiors.furnish`` off, nothing changes; on, each floor gets wall pieces of the ``generic`` set.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from shapely import Point, Polygon

from onward_generator.detail import config_from_document
from onward_generator.detail.furnish import ShopFurnishing, floor_rooms
from onward_generator.detail.interiors import WELL_CLEARANCE_M
from tests.conftest import MURCIA_CENTER
from tests.integration.test_shop_furnishing import _glb
from tests.integration.test_shop_interiors import DOC, SHOP, _detail, _footprint, _obj

ON = copy.deepcopy(DOC)
ON["interiors"]["furnish"]["enabled"] = True
HOMES = ("osm-way-311", "osm-way-313")


def _doc(base: dict, **furnish: object) -> dict:
    doc = copy.deepcopy(base)
    doc["interiors"]["furnish"].update(furnish)
    return doc


@pytest.fixture(scope="module")
def off(detail_inputs):
    return _detail(detail_inputs, config_from_document(DOC))


@pytest.fixture(scope="module")
def recorded(detail_inputs):
    """The option on, with the floors furnishing saw for each building (wells, feet, arrivals, entries)."""
    seen: dict[str, list] = {}
    original = ShopFurnishing.furnish_floors

    def spy(self, building_id, plan, built, penetrations, ic):
        seen[building_id] = floor_rooms(plan, built.spec["rooms"], penetrations, ic, self.furnish)
        return original(self, building_id, plan, built, penetrations, ic)

    patch = pytest.MonkeyPatch()
    patch.setattr(ShopFurnishing, "furnish_floors", spy)
    try:
        detailed = _detail(detail_inputs, config_from_document(ON))
    finally:
        patch.undo()
    return detailed, seen


@pytest.fixture(scope="module")
def on(recorded):
    return recorded[0]


def _rooms(d, bid: str) -> list[dict]:
    return _obj(d, f"interior/{bid}").record["rooms"]


def test_off_by_default_and_without_the_block_scene_detail_is_identical(detail_inputs, off) -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    absent = copy.deepcopy(DOC)
    del absent["interiors"]["furnish"]
    bare = _detail(detail_inputs, config_from_document(absent))
    assert json.dumps(bare.manifest(), sort_keys=True) == json.dumps(off.manifest(), sort_keys=True)
    assert json.dumps(bare.stats, sort_keys=True) == json.dumps(off.stats, sort_keys=True)
    assert _glb(bare.visual_nodes()) == _glb(off.visual_nodes())
    assert _glb(bare.collision_nodes()) == _glb(off.collision_nodes())
    assert "floor_furnishing" not in off.stats and "floor_furnishing" not in bare.stats
    assert not any(i.code == "floor_not_furnished" for i in off.issues)
    assert all("furnishing" not in r for bid in HOMES for r in _rooms(off, bid))


def test_on_residential_floors_get_pieces_inside_their_floor_and_out_of_the_stairs(detail_inputs, recorded, off) -> None:
    on, seen = recorded
    assert not on.critical
    assert sorted(seen) == sorted(HOMES)
    furnish = config_from_document(ON).interiors.furnish
    allowed = {e.id for e in furnish.sets["generic"].wall}
    for bid in HOMES:
        inner = _footprint(detail_inputs, bid).buffer(-0.3, join_style="mitre").buffer(-furnish.wall_gap_m + 1e-6, join_style="mitre")
        rooms = _rooms(on, bid)
        assert len(rooms) == len(seen[bid]) >= 2
        for record, room in zip(rooms, seen[bid], strict=True):
            block = record["furnishing"]
            assert block["method"] == "residential_furnishing" and block["set"] == "generic"
            assert block["pieces"] and {p["asset_id"] for p in block["pieces"]} <= allowed
            for piece in block["pieces"]:
                footprint = Polygon(piece["footprint_m"])
                assert inner.contains(footprint)
                assert piece["position_m"][2] == record["floor_z_m"]
                for zone in (room.door_zone, *room.keepouts):
                    assert footprint.intersection(zone).area <= 1e-9
                for hole in room.holes:
                    assert footprint.distance(hole) >= WELL_CLEARANCE_M - 1e-6
        ids = [p["id"] for r in rooms for p in r["furnishing"]["pieces"]]
        assert ids == [f"furnish/{bid}/{n}" for n in range(len(ids))]
        assert _obj(on, f"interior/{bid}").record["visual_triangles"] == _obj(off, f"interior/{bid}").record["visual_triangles"]
    stats = on.stats["floor_furnishing"]
    pieces = [p for bid in HOMES for r in _rooms(on, bid) for p in r["furnishing"]["pieces"]]
    assert stats["floor_rooms"] == stats["reachable_rooms"] == stats["furnished_rooms"] == 4
    assert stats["pieces"] == len(pieces) == sum(stats["pieces_by_asset"].values())
    assert stats["visual_triangles"] == sum(p["triangles"] for p in pieces)
    assert stats["collision_triangles"] == 12 * sum(p["solid"] for p in pieces)
    assert stats["max_triangles"] == 12000 and stats["assets"] == len(stats["pieces_by_asset"])
    for key in ("interior_visual_triangles", "interior_collision_triangles", "shop_interior_visual_triangles", "interiors"):
        assert on.stats[key] == off.stats[key]
    assert "shop_furnishing" not in on.stats


def test_detail_draws_no_furniture_and_leaves_the_rest_alone(on, off) -> None:
    assert not any(p.material.startswith("prop_") for n in on.visual_nodes() for p in n.primitives)
    assert _glb(on.collision_nodes()) == _glb(off.collision_nodes())
    for a, b in zip(on.objects, off.objects, strict=True):
        if a.type != "interior" or a.record["kind"] == "shop":
            assert a.record == b.record


def test_two_runs_give_identical_scene_detail(detail_inputs, on) -> None:
    again = _detail(detail_inputs, config_from_document(ON))
    assert json.dumps(again.manifest(), sort_keys=True) == json.dumps(on.manifest(), sort_keys=True)
    assert _glb(again.visual_nodes()) == _glb(on.visual_nodes())


def test_shop_furnishing_result_and_interior_budgets_unchanged_by_residential_on(detail_inputs) -> None:
    shops = copy.deepcopy(DOC)
    shops["interiors"]["shops"]["furnish"]["enabled"] = True
    both = _doc(shops, enabled=True)
    a, b = _detail(detail_inputs, config_from_document(shops)), _detail(detail_inputs, config_from_document(both))
    assert a.stats["shop_furnishing"] == b.stats["shop_furnishing"] and a.stats["shop_furnishing"]["furnished_rooms"] == 1
    assert _obj(a, f"interior/{SHOP}").record == _obj(b, f"interior/{SHOP}").record
    assert _obj(a, f"interior/{SHOP}").light_anchors == _obj(b, f"interior/{SHOP}").light_anchors
    for key in ("interior_visual_triangles", "interior_collision_triangles", "shop_interior_visual_triangles",
                "shop_interior_collision_triangles"):
        assert a.stats[key] == b.stats[key]
    assert "floor_furnishing" not in a.stats and b.stats["floor_furnishing"]["furnished_rooms"] == 4


def test_light_anchors_kept_and_probes_avoid_pieces_per_floor(on, off) -> None:
    for bid in HOMES:
        before = _obj(off, f"interior/{bid}").light_anchors["rooms"]
        after = _obj(on, f"interior/{bid}").light_anchors["rooms"]
        rooms = _rooms(on, bid)
        assert [a["room_id"] for a in after] == [r["id"] for r in rooms]
        for b, a, room in zip(before, after, rooms, strict=True):
            assert (a["light_m"], a["range_m"]) == (b["light_m"], b["range_m"])
            pieces = [Polygon(p["footprint_m"]) for p in room["furnishing"]["pieces"]]
            probes = a["probes_m"]
            assert probes and not any(f.contains(Point(probes[k], probes[k + 1])) for f in pieces for k in range(0, len(probes), 3))
    assert on.stats["interior_lights"] == off.stats["interior_lights"]
    assert on.stats["interior_lights"]["dropped"] == []


def test_over_budget_floor_keeps_its_bare_interior(detail_inputs, on, off) -> None:
    tight = _detail(detail_inputs, config_from_document(_doc(ON, max_triangles=1)))
    issues = [i for i in tight.issues if i.code == "floor_not_furnished"]
    assert len(issues) == 4 and all(i.severity == "info" and i.object_id in HOMES for i in issues)
    assert all(i.message.startswith("triangle_budget: needs ") and " left of interiors.furnish.max_triangles 1; room " in i.message
               for i in issues)
    assert all("furnishing" not in r for bid in HOMES for r in _rooms(tight, bid))
    assert tight.stats["floor_furnishing"]["refusals_by_reason"] == {"triangle_budget": 4}
    assert _glb(tight.visual_nodes()) == _glb(off.visual_nodes())  # no piece and no probe moved
    first = _rooms(on, HOMES[0])[0]["furnishing"]["visual_triangles"]
    one = _detail(detail_inputs, config_from_document(_doc(ON, max_triangles=first)))
    stats = one.stats["floor_furnishing"]
    assert "furnishing" in _rooms(one, HOMES[0])[0]
    assert stats["furnished_rooms"] >= 1 and stats["refusals_by_reason"].get("triangle_budget", 0) >= 1
    assert stats["furnished_rooms"] + stats["refusals_by_reason"]["triangle_budget"] == 4
    assert stats["visual_triangles"] <= first


def test_missing_catalogue_row_stops_detail_with_interiors_furnish_message(detail_inputs, monkeypatch: pytest.MonkeyPatch,
                                                                           tmp_path: Path) -> None:
    from onward_generator.detail import furnish, scene
    from onward_generator.detail.scene import DetailError

    monkeypatch.setattr(scene, "REPO_ROOT", tmp_path)
    furnish._CATALOGUES.clear()
    with pytest.raises(DetailError, match=r"^interiors\.furnish: rows the catalogues under .* kenney_furniture_bookcaseOpen \(not installed\)"):
        _detail(detail_inputs, config_from_document(ON))
