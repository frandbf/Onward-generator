"""Shop furnishing on the synthetic street block around MURCIA_CENTER (ADR-031).

Building 312 holds a bakery POI and gets a one-storey shop interior (``test_shop_interiors.py``).
With ``interiors.shops.furnish`` off, nothing changes; on, its room gets bakery furniture.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from shapely import Point, Polygon

from onward_generator.detail import config_from_document
from onward_generator.detail.config import load_detail_config
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import build_glb
from tests.integration.test_shop_interiors import DOC, SHOP, _detail, _footprint, _obj

ON = copy.deepcopy(DOC)
ON["interiors"]["shops"]["furnish"]["enabled"] = True
# The hand-written sets fill every room to the cap (ADR-031); the default cap of 12 is for the semantic recipes'
# area-scaled rooms (ADR-036), which test_recipes covers. Five keeps this one room filled to the cap.
ON["interiors"]["shops"]["furnish"]["max_pieces_per_room"] = 5
BAKERY = load_detail_config().interiors.shops.furnish.sets["bakery"]


def _cfg(doc: dict, **furnish: object):
    doc = copy.deepcopy(doc)
    doc["interiors"]["shops"]["furnish"].update(furnish)
    return config_from_document(doc)


def _glb(nodes) -> bytes:
    materials = {p.material: Material((1, 1, 1, 1), 0.9) for n in nodes for p in n.primitives}
    return build_glb(nodes, materials)


def _geometry(nodes) -> list:
    """Each node's name, matrix and primitives: the meshes, without the extras."""
    return [(n.name, None if n.matrix is None else n.matrix.tolist(),
             [(p.material, p.mesh.vertices.tolist(), p.mesh.triangles.tolist()) for p in n.primitives]) for n in nodes]


@pytest.fixture(scope="module")
def off(detail_inputs):
    return _detail(detail_inputs, config_from_document(DOC))


@pytest.fixture(scope="module")
def on(detail_inputs):
    return _detail(detail_inputs, config_from_document(ON))


def test_off_and_without_the_block_nothing_changes(detail_inputs, off) -> None:
    absent = copy.deepcopy(DOC)
    del absent["interiors"]["shops"]["furnish"]
    bare = _detail(detail_inputs, config_from_document(absent))
    assert json.dumps(bare.manifest(), sort_keys=True) == json.dumps(off.manifest(), sort_keys=True)
    assert _glb(bare.visual_nodes()) == _glb(off.visual_nodes())
    assert _glb(bare.collision_nodes()) == _glb(off.collision_nodes())
    assert "shop_furnishing" not in off.stats and "shop_furnishing" not in bare.stats
    assert not any(i.code == "shop_not_furnished" for i in off.issues)
    assert all("furnishing" not in r for o in off.objects if o.type == "interior" for r in o.record["rooms"])


def test_on_the_bakery_room_gets_bakery_furniture_inside_its_walls(detail_inputs, on) -> None:
    assert not on.critical
    room = _obj(on, f"interior/{SHOP}").record["rooms"][0]
    block = room["furnishing"]
    assert block["set"] == "bakery" and block["attribute_origin"] == "generated"
    allowed = {e.id for slot in ("counter", "wall", "decor") for e in BAKERY.slot(slot)}
    assert {p["asset_id"] for p in block["pieces"]} <= allowed
    assert {p["slot"] for p in block["pieces"]} == {"counter", "wall", "decor"}
    assert [p["id"] for p in block["pieces"]] == [f"furnish/{SHOP}/{n}" for n in range(len(block["pieces"]))]
    inner = _footprint(detail_inputs, SHOP).buffer(-0.3, join_style="mitre")
    door = Point(*_obj(on, f"interior/{SHOP}").record["connections"][0]["position_m"])
    for piece in block["pieces"]:
        footprint = Polygon(piece["footprint_m"])
        assert inner.contains(footprint)
        assert footprint.distance(door) > 0.3 + 0.0  # the door zone starts behind the wall and takes 1.5 m
    stats = on.stats["shop_furnishing"]
    assert stats["shop_rooms"] == stats["reachable_rooms"] == stats["furnished_rooms"] == 1
    assert stats["pieces"] == len(block["pieces"]) == ON["interiors"]["shops"]["furnish"]["max_pieces_per_room"] == 5
    assert stats["visual_triangles"] == block["visual_triangles"] == sum(p["triangles"] for p in block["pieces"])
    assert stats["collision_triangles"] == 12 * sum(p["solid"] for p in block["pieces"])
    assert stats["refusals_by_reason"] == {}
    assert stats["max_triangles"] == 45000 and stats["assets"] == len(stats["pieces_by_asset"])


def test_two_runs_with_one_seed_give_the_same_scene(detail_inputs, on) -> None:
    again = _detail(detail_inputs, config_from_document(ON))
    assert json.dumps(again.manifest(), sort_keys=True) == json.dumps(on.manifest(), sort_keys=True)


def test_a_room_over_the_furniture_budget_keeps_its_shop_interior(detail_inputs, off) -> None:
    tight = _detail(detail_inputs, _cfg(ON, max_triangles=10))
    issues = [i for i in tight.issues if i.code == "shop_not_furnished"]
    assert len(issues) == 1 and issues[0].severity == "info" and issues[0].message.startswith("triangle_budget: needs ")
    assert issues[0].object_id == SHOP
    assert issues[0].message.endswith(f"10 left of shops.furnish.max_triangles 10; room {SHOP}/L0")
    record = _obj(tight, f"interior/{SHOP}").record
    assert "furnishing" not in record["rooms"][0]
    assert record["visual_triangles"] == _obj(off, f"interior/{SHOP}").record["visual_triangles"]
    assert tight.stats["shop_furnishing"]["refusals_by_reason"] == {"triangle_budget": 1}
    assert tight.stats["shop_furnishing"]["furnished_rooms"] == 0
    assert _glb(tight.visual_nodes()) == _glb(off.visual_nodes())  # no piece and no probe moved


def test_detail_draws_no_furniture_and_leaves_streets_buildings_and_colliders_alone(on, off) -> None:
    assert not any(p.material.startswith("prop_") for n in on.visual_nodes() for p in n.primitives)
    assert _geometry(on.visual_nodes()) == _geometry(off.visual_nodes())
    assert _glb(on.collision_nodes()) == _glb(off.collision_nodes())
    for a, b in zip(on.objects, off.objects, strict=True):
        if a.type != "interior":
            assert a.record == b.record


def test_the_light_probes_move_off_the_furniture(on, off) -> None:
    pieces = _obj(on, f"interior/{SHOP}").record["rooms"][0]["furnishing"]["pieces"]
    floor = [Polygon(p["footprint_m"]) for p in pieces if p["slot"] != "decor"]
    anchors = _obj(on, f"interior/{SHOP}").light_anchors["rooms"][0]
    probes = anchors["probes_m"]
    assert probes and not any(f.contains(Point(probes[k], probes[k + 1])) for f in floor for k in range(0, len(probes), 3))
    assert anchors["light_m"] == _obj(off, f"interior/{SHOP}").light_anchors["rooms"][0]["light_m"]


def test_the_row_block_shops_get_the_generic_set(row_inputs) -> None:
    rows = _detail(row_inputs, config_from_document(ON))
    stats = rows.stats["shop_furnishing"]
    assert stats["furnished_rooms"] == stats["reachable_rooms"] >= 1
    generic = load_detail_config().interiors.shops.furnish.sets["generic"]
    allowed = {e.id for slot in ("counter", "wall") for e in generic.slot(slot)}
    for o in rows.objects:
        if o.type == "interior" and o.record["kind"] == "shop":
            for room in o.record["rooms"]:
                assert {p["asset_id"] for p in room["furnishing"]["pieces"]} <= allowed


def test_a_missing_catalogue_row_stops_detail(detail_inputs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from onward_generator.detail import furnish, scene
    from onward_generator.detail.scene import DetailError

    monkeypatch.setattr(scene, "REPO_ROOT", tmp_path)
    furnish._CATALOGUES.clear()
    with pytest.raises(DetailError, match=r"kenney_furniture_kitchenCabinet \(not installed\)"):
        _detail(detail_inputs, config_from_document(ON))
