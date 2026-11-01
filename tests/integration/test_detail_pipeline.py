"""Phase 04 on a synthetic street block around MURCIA_CENTER (geo → heights → phase 03 base → detail)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from shapely import Point
from shapely.geometry import shape

from onward_generator.cli import main
from onward_generator.detail import DetailError, config_from_document, detail_scene, load_base
from onward_generator.detail.config import DEFAULT_CONFIG_PATH
from onward_generator.dress.atlas import decode_png_rgba
from onward_generator.generation import load_generation_config
from onward_generator.generation.gltf import build_glb, read_glb
from onward_generator.generation.mesh import Mesh, closed_manifold_defects, signed_volume, transform
from tests.detail_fixture import selective_interiors

GEN = load_generation_config()
DOC = selective_interiors(json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))
CFG = config_from_document(DOC)


def _detail(inputs: tuple[Path, Path, Path], cfg=CFG):
    scene, heights, base = inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, cfg)


@pytest.fixture(scope="module")
def detailed(detail_inputs: tuple[Path, Path, Path]):
    return _detail(detail_inputs)


@pytest.fixture(scope="module")
def residential(residential_inputs: tuple[Path, Path, Path]):
    """The block without its bakery, so all three matching buildings take the stair interior."""
    return _detail(residential_inputs)


def _obj(d, oid: str):
    return next(o for o in d.objects if o.id == oid)


def _inside(mesh: Mesh, p: np.ndarray) -> bool:
    """Ray-parity point-in-closed-mesh test (Möller–Trumbore), generic ray direction."""
    direction = np.array([1.0, 0.1234, 0.0567])
    v = mesh.vertices[mesh.triangles]
    e1, e2 = v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]
    h = np.cross(direction, e2)
    a = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(a) > 1e-12
    f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
    s = p - v[:, 0]
    u = f * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    w = f * (q @ direction)
    t = f * np.einsum("ij,ij->i", e2, q)
    hits = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t > 1e-9)
    return bool(hits.sum() % 2)


# --- Base validation -------------------------------------------------------------


def test_base_is_validated_against_inputs(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    assert ref["manifest_sha256"] == hashlib.sha256((base / "scene3d.json").read_bytes()).hexdigest()
    with pytest.raises(DetailError, match="sha256 mismatch"):
        load_base(base, heights, heights, GEN)
    with pytest.raises(DetailError, match="not found"):
        load_base(tmp_path, scene, heights, GEN)

    def copy_base(mutate) -> Path:
        out = tmp_path / f"b{len(list(tmp_path.iterdir()))}"
        out.mkdir()
        for name in ("scene3d.json", "generation_report.json"):
            doc = json.loads((base / name).read_text(encoding="utf-8"))
            mutate(name, doc)
            (out / name).write_text(json.dumps(doc), encoding="utf-8")
        return out

    failed = copy_base(lambda n, d: d["phase_03_exit"].update(passed=False) if n == "generation_report.json" else None)
    with pytest.raises(DetailError, match="did not pass"):
        load_base(failed, scene, heights, GEN)
    tampered = copy_base(lambda n, d: d["objects"][0]["height"].update(height_m=99.0) if n == "scene3d.json" else None)
    with pytest.raises(DetailError, match="differs from the stored base"):
        load_base(tampered, scene, heights, GEN)


# --- Facades ---------------------------------------------------------------------


def test_no_critical_errors_and_every_facade_is_labelled_generic(detailed) -> None:
    report = detailed.report()
    assert report["phase_04_checks"]["automatic_checks_passed"], report["issues"]
    assert report["phase_04_checks"]["visual_review"].startswith("pending")
    buildings = [o for o in detailed.objects if o.type == "building"]
    assert len(buildings) == 5
    for o in buildings:
        facade = o.record["facade"]
        assert facade["representation"] == "generic_template" and facade["representative_of_real_facade"] is False
        assert facade["attribute_origin"] == "generated" and "does not represent the real facade" in facade["note"]
        assert o.record["checks"]["facade_open_edges"] == 0 and o.record["checks"]["roof_area_error_m2"] < 0.02
        assert o.record["generation"]["seed"] == CFG.seed and o.record["generation"]["template_config_version"] == CFG.version
    assert detailed.manifest()["fidelity"]["facades"] == "generic_templates"


def test_templates_roofs_and_landmark(detail_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(detail_inputs)  # in the test, not the module fixture, so mutation testing runs it
    church = _obj(detailed, "building/osm-way-314").record
    assert church["facade"]["template"] == "landmark_placeholder" and church["facade"]["needs_custom_model"] is True
    assert church["facade"]["openings"] == {"landmark_door": 1}
    towers = [o.record for o in detailed.objects if o.type == "landmark_part" and o.record["kind"] == "tower"]
    assert len(towers) == 1 and towers[0]["source_ids"][0] == "osm-way-314"
    assert towers[0]["height"] == {"height_m": 40.0, "origin": "observed", "source_tag": "height",
                                  "source_id": towers[0]["source_ids"][1]}
    assert towers[0]["footprint"]["origin"] == "estimated" and towers[0]["representative_of_real_tower"] is False
    shed = _obj(detailed, "building/osm-way-315").record
    assert shed["facade"]["template"] == "generic_minor" and shed["roof"]["type"] == "hip"
    assert 0 < shed["roof"]["rise_m"] <= 2.5
    flat = _obj(detailed, "building/osm-way-311").record
    assert flat["roof"]["type"] == "flat_parapet" and flat["roof"]["parapet_m"] == 1.0


def test_party_walls_have_no_openings(detailed) -> None:
    rec = _obj(detailed, "building/osm-way-312").record
    assert rec["facade"]["party_wall_length_m"] == pytest.approx(28.0, abs=0.1)  # west 12 m (to 311) + east 16 m (to 313)
    o = _obj(detailed, "building/osm-way-312")
    for prim in o.visual:
        if prim.material in ("glass_dark", "glass_view", "door_wood", "frame_stone", "railing_metal"):
            x, y, z = prim.mesh.vertices.T
            # Party spans: west wall against 311 (y 6–18), east wall against 313 (y 6–20). Parapet copings sit on top.
            west = (x < -14.5) & (y > 6.5) & (y < 17.5)
            east = (x > -0.5) & (y > 6.5) & (y < 19.5)
            assert not ((west | east) & (z < rec["height"]["height_m"])).any(), prim.material
    # The 4 m of the west wall above 311 (y 18–22) is street-facing and does get openings.
    glass = next(p for p in o.visual if p.material == "glass_dark").mesh.vertices
    assert ((glass[:, 0] < -14.5) & (glass[:, 1] > 18)).any()


def _wall_faces(meshes: list[Mesh], x: float, nx: float) -> np.ndarray:
    """Triangles lying on the plane ``x`` and facing ``nx`` (+1 east, -1 west), as an (n, 3, 3) array."""
    tri = np.concatenate([m.vertices[m.triangles] for m in meshes if m.triangle_count])
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    on = np.all(np.abs(tri[:, :, 0] - x) < 0.01, axis=1) & (normal[:, 0] * nx > 0)
    return tri[on]


def test_party_walls_against_a_neighbour_leave_the_visual_mesh(detail_inputs: tuple[Path, Path, Path]) -> None:
    detailed = _detail(detail_inputs)  # in the test, not the module fixture, so mutation testing runs it
    objs = {n: _obj(detailed, f"building/osm-way-{n}") for n in (311, 312, 313)}
    walls = {n: o.record["facade"]["materials"]["wall"] for n, o in objs.items()}
    top = {n: max(float(p.mesh.vertices[:, 2].max()) for p in o.visual if p.material == walls[n]) for n, o in objs.items()}
    assert top[311] < top[312] and top[313] < top[312]
    areas = {n: o.record["facade"]["wall_area_m2"] for n, o in objs.items()}
    # 311 and 313 are lower than 312: their walls against it are hidden whole.
    assert areas[311]["hidden"] == pytest.approx(12 * top[311], abs=0.1) and areas[311]["party"] == 0.0
    assert areas[313]["hidden"] == pytest.approx(14 * top[313], abs=0.1) and areas[313]["party"] == 0.0
    # 312 keeps its wall above each neighbour, and the 2 m blind run past 313 (y 20–22) whole.
    assert areas[312]["hidden"] == pytest.approx(12 * top[311] + 14 * top[313], abs=0.1)
    assert areas[312]["party"] == pytest.approx(12 * (top[312] - top[311]) + 14 * (top[312] - top[313]) + 2 * top[312], abs=0.1)
    for n, perimeter in ((311, 54.0), (312, 62.0), (313, 56.0)):
        assert sum(areas[n].values()) == pytest.approx(perimeter * top[n], abs=0.2)
        assert objs[n].record["materials"] == [p.material for p in objs[n].visual]
        assert objs[n].record["visual"]["triangles"] == sum(p.mesh.triangle_count for p in objs[n].visual)
    # Nothing of 311 faces east on x = -15; 312 faces west there only above 311, in its party material.
    assert not len(_wall_faces([p.mesh for p in objs[311].visual], -15.0, 1.0))
    party = _wall_faces([p.mesh for p in objs[312].visual if p.material == f"party_{walls[312]}"], -15.0, -1.0)
    assert party[:, :, 2].min() == pytest.approx(top[311]) and party[:, :, 2].max() == pytest.approx(top[312])
    assert party[:, :, 1].min() == pytest.approx(6.0, abs=0.01) and party[:, :, 1].max() == pytest.approx(18.0, abs=0.01)
    assert f"party_{walls[312]}" in objs[312].record["materials"] and f"party_{walls[311]}" not in objs[311].record["materials"]
    # The collider keeps the whole wall.
    assert len(_wall_faces(objs[311].colliders, -15.0, 1.0))
    report = detailed.report()["counts"]["wall_area_m2"]
    buildings = [o.record["facade"]["wall_area_m2"] for o in detailed.objects if o.type == "building"]
    assert set(report) == {"street", "party", "hidden"}
    assert all(report[k] == pytest.approx(sum(b[k] for b in buildings), abs=0.5) for k in report)
    assert report["hidden"] > 0 and report["party"] > 0 and report["street"] > 0


def test_shop_evidence_turns_the_ground_floor_into_shopfronts(detailed) -> None:
    shop = _obj(detailed, "building/osm-way-312").record["facade"]
    assert shop["ground_floor"] == "shopfront" and shop["ground_floor_evidence"]["source"] == "osm_poi"
    assert shop["ground_floor_evidence"]["osm_poi_ids"]
    assert shop["openings"].get("shopfront", 0) > 0
    plain = _obj(detailed, "building/osm-way-311").record["facade"]
    assert plain["ground_floor"] == "door_and_windows" and plain["ground_floor_evidence"] is None


def test_shop_records_name_the_poi_and_place_one_sign(detailed) -> None:
    [shop] = _obj(detailed, "building/osm-way-312").record["facade"]["shops"]
    assert shop["poi_id"].startswith("osm-node-") and shop["id"] == f"shop/osm-way-312/{shop['poi_id']}"
    assert (shop["name"], shop["name_origin"], shop["name_rule"]) == ("Panadería", "estimated", "shop=bakery")
    assert shop["sign"]["kind"] in ("fascia", "blade") and shop["sign"]["attribute_origin"] == "generated"
    assert all(v["depth_m"] == 0.0 for v in shop["vinyls"])  # on the see-through pane of the shop interior too
    assert all(o.record["facade"]["shops"] == [] for o in detailed.objects if o.type == "building" and o.id != "building/osm-way-312")


def test_only_shops_get_a_glazed_shop_door_and_vinyls(detail_inputs: tuple[Path, Path, Path]) -> None:
    doc = copy.deepcopy(DOC)
    doc["interiors"]["enabled"] = False
    doc["shops"]["vinyl_share"] = 1.0
    fresh = _detail(detail_inputs, config_from_document(doc))
    openings = {o.record["source_ids"][0]: o.record["facade"]["openings"] for o in fresh.objects if o.type == "building"}
    assert {bid for bid, found in openings.items() if "shop_door" in found} == {"osm-way-312"}
    assert {bid for bid, found in openings.items() if "door" in found} == {"osm-way-311", "osm-way-313", "osm-way-315"}
    assert openings["osm-way-312"]["shop_door"] == 1 and "door" not in openings["osm-way-312"]
    assert {bid for bid, found in openings.items() if "shopfront" in found} == {"osm-way-312"}
    [shop] = _obj(fresh, "building/osm-way-312").record["facade"]["shops"]
    assert len(shop["vinyls"]) == openings["osm-way-312"]["shopfront"]
    poi = _obj(fresh, "building/osm-way-312").record["facade"]["ground_floor_evidence"]["osm_poi_ids"][0]
    assert (shop["id"], shop["poi_id"], shop["name"]) == (f"shop/osm-way-312/{poi}", poi, "Panadería")
    assert all(o.record["facade"]["shops"] == [] for o in fresh.objects if o.type == "building" and o.id != "building/osm-way-312")
    assert not fresh.critical


def test_a_commercial_building_without_a_poi_is_one_shop(detail_inputs: tuple[Path, Path, Path]) -> None:
    doc = copy.deepcopy(DOC)
    doc["interiors"]["enabled"] = False
    doc["templates"]["generic_residential"]["applies_to"] = ["unknown"]
    doc["templates"]["generic_commercial"]["applies_to"] = ["commercial", "residential_multi", "residential_small"]
    fresh = _detail(detail_inputs, config_from_document(doc))
    facade = _obj(fresh, "building/osm-way-313").record["facade"]
    assert facade["ground_floor_evidence"] == {"source": "building_category", "category": "residential_multi", "osm_poi_ids": []}
    [shop] = facade["shops"]
    assert (shop["id"], shop["poi_id"], shop["name"], shop["name_rule"]) == ("shop/osm-way-313/building", None, "Comercio", "default")
    assert shop["sign"] is not None


def test_a_main_door_only_template_keeps_its_door_over_a_shop_poi(detail_inputs: tuple[Path, Path, Path]) -> None:
    doc = copy.deepcopy(DOC)
    doc["interiors"]["enabled"] = False
    for template in doc["templates"].values():
        template["ground"] = "main_door_only"
    facade = _obj(_detail(detail_inputs, config_from_document(doc)), "building/osm-way-312").record["facade"]
    assert (facade["ground_floor"], facade["ground_floor_evidence"], facade["shops"]) == ("main_door_only", None, [])


def test_each_building_lays_out_its_own_ground_floor_entrance_and_balconies(detail_inputs: tuple[Path, Path, Path]) -> None:
    # A fresh run, not the module fixture, so this test itself exercises the layout.
    fresh = _detail(detail_inputs)
    openings = {o.record["source_ids"][0]: o.record["facade"]["openings"] for o in fresh.objects if o.type == "building"}
    for bid, found in openings.items():
        assert sum(found.get(k, 0) for k in ("door", "landmark_door", "shop_door", "interior_door")) == 1, (bid, found)
        assert ("landmark_door" in found) is (bid == "osm-way-314"), (bid, found)  # only the landmark has one
    assert openings["osm-way-312"].get("shopfront", 0) > 0
    assert openings["osm-way-314"] == {"landmark_door": 1}  # main_door_only: no ground-floor windows either
    # Balcony placement hashes the building id with the seed (golden values for the default seed).
    assert {bid: found.get("balcony", 0) for bid, found in openings.items()} == {
        "osm-way-311": 12, "osm-way-312": 8, "osm-way-313": 10, "osm-way-314": 0, "osm-way-315": 0,
    }


def test_opening_dimensions_stay_inside_the_facade(detailed) -> None:
    for o in (x for x in detailed.objects if x.type == "building"):
        h = o.record["height"]["height_m"]
        parapet = o.record["roof"].get("parapet_m", 0.0)
        hip = o.record["roof"].get("rise_m", 0.0)
        for prim in o.visual:
            z = prim.mesh.vertices[:, 2]
            assert z.min() >= -1e-9, (o.id, prim.material)
            assert z.max() <= h + parapet + hip + 1e-3, (o.id, prim.material)  # rise_m is rounded to mm
            if prim.material in ("glass_dark", "glass_view", "door_wood"):
                assert z.max() <= h - CFG.head_clearance_m + 1e-6, (o.id, prim.material)
        assert o.record["visual"]["triangles"] > 2 * o.record["facade"]["openings"].get("window", 0)


def test_balconies_do_not_hit_neighbours(detailed, detail_inputs: tuple[Path, Path, Path]) -> None:
    scene = json.loads(detail_inputs[0].read_text(encoding="utf-8"))
    fps = {f["id"]: shape(f["geometry"]) for f in scene["features"] if f["kind"] == "building"}
    for o in (x for x in detailed.objects if x.type == "building"):
        bid = o.record["source_ids"][0]
        others = [g for k, g in fps.items() if k != bid]
        for prim in o.visual:
            if prim.material == "railing_metal":
                for x, y in prim.mesh.vertices[:, :2]:
                    assert not any(g.buffer(-0.01).contains(Point(x, y)) for g in others)


# --- Colliders and interiors ------------------------------------------------------


def test_colliders_are_separate_closed_and_positive(detailed) -> None:
    for node in detailed.collision_nodes():
        for prim in node.primitives:
            assert prim.material == "collider"
            assert closed_manifold_defects(prim.mesh) == 0, node.name
            mesh = transform(prim.mesh, node.matrix) if node.matrix is not None else prim.mesh
            assert signed_volume(mesh) > 0, node.name
    assert all(p.material != "collider" for n in detailed.visual_nodes() for p in n.primitives)


def test_interiors_only_in_selected_buildings_with_walkable_door_and_stair(residential) -> None:
    detailed = residential
    interiors = [o for o in detailed.objects if o.type == "interior"]
    # The block has three buildings that match the rule, fewer than the quota floor: all three get one.
    assert len(interiors) == 3 < CFG.interiors.min_buildings
    assert sorted(o.record["source_ids"][0] for o in interiors) == sorted(detailed.stats["interiors"])
    church = _obj(detailed, "building/osm-way-314").record
    assert church["interior"] is False
    for it in interiors:
        bid = it.record["source_ids"][0]
        b = _obj(detailed, f"building/{bid}")
        assert it.record["representative_of_real_interior"] is False
        door = next(c for c in it.record["connections"] if c["type"] == "door")
        stairs = [c for c in it.record["connections"] if c["type"] == "stair"]
        assert it.record["accessible_floors"] in (2, 3)
        assert len(it.record["rooms"]) == it.record["accessible_floors"]
        furnished = [
            c for c in stairs
            if c["to"].rsplit("/", 1)[-1].startswith("L")
            and int(c["to"].rsplit("L", 1)[-1]) < it.record["accessible_floors"]
        ]
        assert len(furnished) == it.record["accessible_floors"] - 1
        assert stairs[-1]["to"] == "roof"
        storey = it.record["rooms"][1]["floor_z_m"]
        for stair in stairs:
            assert stair["riser_m"] <= CFG.interiors.stair.max_riser_m and stair["slope_deg"] < 40
            assert stair["risers"] * stair["riser_m"] == pytest.approx(stair["rise_m"], abs=stair["risers"] * 5e-5)
            assert stair["rise_m"] == pytest.approx(storey)
        colliders = b.colliders
        assert b.record["collider"]["source"] == "interior_shell"
        # Door: inside the wall thickness at knee height is free; beside the door the wall blocks.
        px, py = door["position_m"]
        t = CFG.interiors.wall_thickness_m
        candidates = [np.array([px + dx, py + dy]) for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))]
        verts = np.vstack([m.vertices for m in colliders])
        centre = verts[:, :2].mean(axis=0)
        inward = min(candidates, key=lambda c: np.linalg.norm(c - centre)) - np.array([px, py])
        along = np.array([-inward[1], inward[0]])
        mid_wall = np.array([px, py]) + inward * (t / 2)
        assert not any(_inside(m, np.array([*mid_wall, 1.0])) for m in colliders)
        beside = mid_wall + along * (door["width_m"] / 2 + 0.4)
        assert any(_inside(m, np.array([*beside, 1.0])) for m in colliders)
        room = np.array([px, py]) + inward * (t + 1.0)
        assert not any(_inside(m, np.array([*room, 1.0])) for m in colliders)
        # The furnished floor above the entrance is open. The roof deck is solid,
        # and a stair in the connections reaches it.
        assert not any(_inside(m, np.array([*room, storey + 0.5])) for m in colliders)
        assert any(c["to"] == "roof" for c in it.record["connections"])
        roof_z = b.record["height"]["height_m"]
        assert any(_inside(m, np.array([*room, roof_z - 0.05])) for m in colliders)
        ceiling = it.record["rooms"][-1]["ceiling_z_m"]
        assert it.record["through_windows"] > 0
        opaque = [p for p in b.visual if p.material == "glass_dark"]
        for prim in opaque:
            assert prim.mesh.vertices[:, 2].min() >= ceiling - 1e-3
        view = [p for p in b.visual if p.material == "glass_view"]
        assert len(view) == 1
        assert view[0].mesh.triangle_count == 2 * it.record["through_windows"]
        assert view[0].mesh.vertices[:, 2].min() < ceiling
        assert all(p.material != "glass_view" for n in detailed.collision_nodes() for p in n.primitives)
        glass_nodes = [n for n in detailed.collision_nodes() if n.name.endswith("#glass")]
        open_ids = {it.record["source_ids"][0] for it in interiors if it.record["through_windows"] > 0}
        assert {n.name for n in glass_nodes} == {f"building/{bid}#glass" for bid in open_ids}
        for it in interiors:
            assert it.record["glass_panes"] == it.record["through_windows"]
        for node in glass_nodes:
            concrete = next(n for n in detailed.collision_nodes() if n.name == node.name.removesuffix("#glass") + "#collider")
            for prim in node.primitives:
                centre = prim.mesh.vertices.mean(axis=0)
                assert _inside(prim.mesh, centre)
                assert not any(_inside(part.mesh, centre) for part in concrete.primitives)


def _with_interiors(**changes: object):
    doc = copy.deepcopy(DOC)
    doc["interiors"].update(changes)
    return config_from_document(doc)


def _interior_costs(d) -> dict[str, int]:
    return {o.record["source_ids"][0]: sum(p.mesh.triangle_count for p in o.visual) for o in d.objects if o.type == "interior"}


def _queue_order(ids: list[str]) -> list[str]:
    return sorted(ids, key=lambda b: hashlib.sha256(f"{CFG.seed}:interior:{b}".encode()).hexdigest())


def test_interiors_go_to_the_hash_order_and_objects_stay_in_building_order(residential_inputs: tuple[Path, Path, Path],
                                                                           residential) -> None:
    detailed = residential
    built = detailed.stats["interiors"]
    assert built == _queue_order(built) != sorted(built)
    one = _detail(residential_inputs, _with_interiors(min_buildings=1, max_buildings=1))
    assert one.stats["interiors"] == built[:1] != sorted(built)[:1]
    assert _detail(residential_inputs, _with_interiors(min_buildings=2, max_buildings=2)).stats["interiors"] == built[:2]
    base_ids = [o.id for o in detailed.base.objects if o.type == "building"]
    for d in (detailed, one):
        expected = [oid for bid in base_ids
                    for oid in ([f"interior/{bid.split('/', 1)[1]}"] if bid.split("/", 1)[1] in d.stats["interiors"] else []) + [bid]]
        assert [o.id for o in d.objects if o.type in ("building", "interior")] == expected


def test_interior_triangle_budget_skips_what_does_not_fit(residential_inputs: tuple[Path, Path, Path], residential) -> None:
    detail_inputs, detailed = residential_inputs, residential
    order = detailed.stats["interiors"]
    costs = _interior_costs(detailed)
    assert len(order) == 3 and detailed.stats["interior_visual_triangles"] == sum(costs.values())
    first, second, third = order
    assert costs[second] > costs[third]
    # The second does not fit after the first; the third still does, exactly at the budget.
    budget = costs[first] + costs[third]
    cut = _detail(detail_inputs, _with_interiors(max_triangles=budget, include_ids=["osm-way-nope"]))
    assert not cut.critical
    assert cut.stats["interiors"] == [first, third]
    assert cut.stats["interior_visual_triangles"] == budget
    skipped = [(i.object_id, i.severity, i.message) for i in cut.issues if i.code == "interior_not_generated"]
    assert skipped == [(second, "info", f"triangle_budget: needs {costs[second]} triangles, {costs[third]} left of max_triangles {budget}")]
    assert cut.issues[0].code == "interior_include_unknown"
    assert [i.code for i in cut.issues].count("interior_include_unknown") == 1
    # A building the budget turns down is the same building as with interiors off.
    plain = _detail(detail_inputs, _with_interiors(enabled=False))
    rejected, untouched = _obj(cut, f"building/{second}"), _obj(plain, f"building/{second}")
    assert rejected.record == untouched.record
    assert [p.mesh.triangle_count for p in rejected.visual] == [p.mesh.triangle_count for p in untouched.visual]
    assert [m.triangle_count for m in rejected.colliders] == [m.triangle_count for m in untouched.colliders]
    assert not rejected.glass_colliders
    assert [i for i in cut.issues if i.object_id == second and i.code != "interior_not_generated"] == \
        [i for i in plain.issues if i.object_id == second]

    one_short = _detail(detail_inputs, _with_interiors(max_triangles=budget - 1))
    assert one_short.stats["interiors"] == [first]
    assert one_short.stats["interior_visual_triangles"] == costs[first]

    # The landmark church only gets in through include_ids; the budget turns it down like the rest.
    nothing = _detail(detail_inputs, _with_interiors(max_triangles=0, include_ids=["osm-way-314"]))
    assert nothing.stats["interiors"] == [] and nothing.stats["interior_visual_triangles"] == 0
    # Issues come back in building order, not in the order the queue tried them.
    assert [i.object_id for i in nothing.issues if i.code == "interior_not_generated"] == sorted([*order, "osm-way-314"])
    assert sorted(order) != order
    for o in plain.objects:
        if o.type == "building":
            assert _obj(nothing, o.id).record == o.record, o.id


# --- Urban elements -------------------------------------------------------------


def test_urban_elements_observed_generated_and_rejected(detailed) -> None:
    elements = [o for o in detailed.objects if o.type == "urban_element"]
    kinds = {(o.record["kind"], o.record["attribute_origin"]) for o in elements}
    assert {("tree", "observed"), ("bollard", "observed"), ("bicycle_parking", "observed"), ("street_lamp", "generated")} <= kinds
    rejected = [i for i in detailed.issues if i.code == "urban_element_not_placed" and i.severity == "warning"]
    assert len(rejected) == 1 and "tree" in rejected[0].message  # the tree inside building 311
    nodes = [n for n in detailed.visual_nodes() if n.mesh_key]
    assert len(nodes) == len(elements) and len({n.mesh_key for n in nodes}) == detailed.stats["shared_meshes"]
    doc, _ = read_glb(build_glb(detailed.visual_nodes(), {**GEN.materials, **CFG.materials}))
    lamp_nodes = [n for n in doc["nodes"] if n["name"].startswith("urban/street_lamp/")]
    assert len({n["mesh"] for n in lamp_nodes}) == 1 and all("matrix" in n for n in lamp_nodes)


# --- Budget and reproducibility ------------------------------------------------


def test_budget_is_reported_and_exceeding_it_warns(detail_inputs: tuple[Path, Path, Path], detailed) -> None:
    assert all(v["within"] for v in detailed.stats["budget"].values())
    doc = copy.deepcopy(DOC)
    doc["budget"]["max_visual_triangles"] = 100
    tight = _detail(detail_inputs, config_from_document(doc))
    assert not tight.stats["budget"]["max_visual_triangles"]["within"]
    assert any(i.code == "budget_exceeded" and i.severity == "warning" for i in tight.issues)


def test_seed_changes_variation_but_not_validity(detail_inputs: tuple[Path, Path, Path], detailed) -> None:
    doc = copy.deepcopy(DOC)
    doc["seed"] = 7
    other = _detail(detail_inputs, config_from_document(doc))
    assert not other.critical
    first = [o.record["facade"]["materials"]["wall"] for o in detailed.objects if o.type == "building"]
    second = [o.record["facade"]["materials"]["wall"] for o in other.objects if o.type == "building"]
    balconies = lambda d: [o.record["facade"]["openings"].get("balcony", 0) for o in d.objects if o.type == "building"]  # noqa: E731 - a one-line helper local to this test
    assert first != second or balconies(detailed) != balconies(other)


def _png_pixels(data: bytes) -> np.ndarray:
    return decode_png_rgba(data)[..., :3]


def test_cli_is_reproducible_and_writes_before_after(detail_inputs: tuple[Path, Path, Path], tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    scene, heights, base = detail_inputs
    before = {p.name: p.read_bytes() for p in base.iterdir()}
    args = ["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base)]
    assert main([*args, "--out", str(tmp_path / "a")]) == 0
    assert "phase 04 automatic checks: PASSED" in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "b")]) == 0
    names = ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json", "compare_overview.png", "compare_closeup.png")
    for name in names:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    assert {p.name: p.read_bytes() for p in base.iterdir()} == before  # the phase 03 base is untouched

    visual, _ = read_glb((tmp_path / "a" / "scene_visual.glb").read_bytes())
    collision, _ = read_glb((tmp_path / "a" / "scene_collision.glb").read_bytes())
    assert [m["name"] for m in collision["materials"]] == ["collider"]
    assert "collider" not in [m["name"] for m in visual["materials"]]
    assert visual["extras"]["attribution"][0]["license"] == "CC0-1.0"
    assert "not a faithful reconstruction" in visual["extras"]["fidelity"]

    for name in ("compare_overview.png", "compare_closeup.png"):
        img = _png_pixels((tmp_path / "a" / name).read_bytes())
        half = (img.shape[1] - 8) // 2
        left, right = img[:, :half], img[:, half + 8 :]
        assert left.shape == right.shape
        assert (left != right).any(axis=2).mean() > 0.005, name  # the detail is visible

    assert main([*args, "--out", str(tmp_path / "a")]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert main([*args, "--out", str(base), "--overwrite", "--no-preview"]) == 1  # base files are protected
    assert {p.name: p.read_bytes() for p in base.iterdir()} == before
