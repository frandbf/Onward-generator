"""The playable core and the scenic ring (ADR-017) on a synthetic block around MURCIA_CENTER.

The block is the phase-04 street block with the church moved east to x 60–80 m, a
shop POI inside the shed at x −60 m, a house 316 across the core edge at x 45–58 m
and a tree at y 70 m. With a 50 m core, the row 311–313 and 316 are the core; the
church (a landmark with a bell tower) and the shed (a shop) are the scenic ring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from onward_generator.cli import main
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.scene import FIDELITY_NOTE
from onward_generator.export.package import EXPORT_CONFIG_DEFAULT, build_package
from onward_generator.generation import load_generation_config
from onward_generator.generation.gltf import nodes_from_glb
from onward_generator.tiers import SCENIC_ROOF, SCENIC_WALLS, core_square, scenic_wall, tier_summary
from tests.conftest import FIXED_NOW, _detail_inputs
from tests.detail_fixture import BUILDINGS, POIS

GEN = load_generation_config()
CFG = load_detail_config()
CORE_M = 50.0
REPO = Path(__file__).resolve().parents[2]
EXPORT_PROJECT = REPO / "config" / "murcia-belluga.export.json"
RING_BUILDINGS = {
    **BUILDINGS,
    314: ([(60, 6), (80, 6), (80, 30), (60, 30)], BUILDINGS[314][1]),
    316: ([(45, 6), (58, 6), (58, 18), (45, 18)], {"building": "house", "building:levels": "2"}),
}
RING_POIS = {**POIS, 406: ((70, 18), POIS[406][1]), 407: ((-57, 32), {"shop": "bakery"}), 408: ((10, 70), {"natural": "tree"})}


@pytest.fixture(scope="module")
def ring_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    return _detail_inputs(tmp_path_factory.mktemp("ring"), RING_BUILDINGS, RING_POIS)


@pytest.fixture
def cored(ring_inputs: tuple[Path, Path, Path]) -> Any:
    scene, heights, base = ring_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=CORE_M)


@pytest.fixture
def ring_detail(ring_inputs: tuple[Path, Path, Path], tmp_path: Path) -> Path:
    scene, heights, base = ring_inputs
    out = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(out), "--no-preview", "--core-radius-m", str(CORE_M)]) == 0
    return out


def _record(d: Any, oid: str) -> dict[str, Any]:
    return next(o.record for o in d.objects if o.id == oid)


def _within(vertices: np.ndarray, half: float) -> bool:
    return bool((np.abs(vertices[:, :2]) <= half + 1e-6).all())


def test_detail_tiers_each_building(cored: Any) -> None:
    manifest = cored.manifest()
    assert manifest["core"] == {"half_side_m": CORE_M, "buildings": {"core": 4, "scenic": 2}}
    tiers = {o.record["source_ids"][0]: o.record["tier"] for o in cored.objects if o.type == "building"}
    assert tiers == {"osm-way-311": "core", "osm-way-312": "core", "osm-way-313": "core", "osm-way-316": "core",
                     "osm-way-314": "scenic", "osm-way-315": "scenic"}
    assert all(o.record["tier"] == "core" for o in cored.objects if o.type == "interior")
    assert cored.report()["phase_04_checks"]["automatic_checks_passed"] is True


def test_a_scenic_building_is_a_plain_shell_without_a_collider(cored: Any, ring_inputs: tuple[Path, Path, Path]) -> None:
    shed = next(o for o in cored.objects if o.type == "building" and o.record["source_ids"] == ["osm-way-315"])
    record = shed.record
    scene, heights, base = ring_inputs
    phase03 = next(o for o in load_base(base, scene, heights, GEN)[0].objects if o.id == "building/osm-way-315")
    assert {k: v for k, v in record.items() if k not in ("tier", "generation", "facade", "roof", "interior", "materials",
                                                          "visual", "collider")} == {
        k: v for k, v in phase03.record.items() if k not in ("checks", "generation", "materials", "visual", "collider")}
    assert "checks" not in record and record["tier"] == "scenic"
    assert record["generation"] == {"method": "phase03_volume + scenic shell", "phase03": phase03.record["generation"],
                                    "template_config_version": CFG.version, "seed": CFG.seed}
    # Walls on four edges and a two-triangle roof: no base, no openings.
    assert record["visual"] == {"triangles": 10}
    assert sum(p.mesh.triangle_count for p in shed.visual) == 10
    assert shed.colliders == [] and shed.glass_colliders == []
    assert record["collider"] == {"source": "none_scenic", "parts": 0, "triangles": 0, "balconies": 0, "glass_panes": 0}
    wall, roof = record["materials"]
    assert wall in SCENIC_WALLS and roof == SCENIC_ROOF == "roof_flat_grey"
    assert record["roof"] == {"type": "flat", "material": SCENIC_ROOF, "origin": "generated", "roof_shape": "flat"}
    assert wall == scenic_wall(CFG.seed, "osm-way-315")
    facade = record["facade"]
    assert facade == {
        "template": "scenic_shell",
        "representation": "scenic_shell",
        "representative_of_real_facade": False,
        "attribute_origin": "generated",
        "note": FIDELITY_NOTE,
        "needs_custom_model": False,
        "ground_floor": "none",
        "openings": {},
        "shops": [],
        "materials": {"wall": wall, "wall_origin": "generated", "roof": SCENIC_ROOF, "roof_origin": "generated"},
        "use": facade["use"],
        "door": "none",
    }
    assert facade["use"]["use"] == "retail"
    assert record["interior"] is False
    assert record["generation"]["method"] == "phase03_volume + scenic shell"
    assert record["generation"]["seed"] == CFG.seed
    zs = np.concatenate([p.mesh.vertices[:, 2] for p in shed.visual])
    assert zs.min() == 0.0 and zs.max() == pytest.approx(record["height"]["height_m"])
    assert "visual" not in record["generation"]


def test_a_scenic_landmark_keeps_no_tower(cored: Any) -> None:
    church = _record(cored, "building/osm-way-314")
    assert church["facade"]["needs_custom_model"] is True
    assert not [o for o in cored.objects if o.type == "landmark_part"]
    assert {n.name for n in cored.collision_nodes()}.isdisjoint({"building/osm-way-314#collider", "building/osm-way-315#collider"})


def test_core_buildings_keep_their_facades(cored: Any) -> None:
    bakery = _record(cored, "building/osm-way-312")
    assert bakery["facade"]["template"] != "scenic_shell"
    assert bakery["facade"]["shops"]
    assert bakery["collider"]["triangles"] > 0
    stats = cored.stats
    assert set(stats["wall_materials"]).isdisjoint(SCENIC_WALLS)
    assert sum(stats["wall_materials"].values()) == 4
    assert sum(stats["facade_uses"].values()) == 4


def test_tier_counts_add_up(cored: Any) -> None:
    tiers = cored.stats["tiers"]
    assert tiers["core"]["buildings"] == 4 and tiers["scenic"]["buildings"] == 2
    scenic = [o for o in cored.objects if o.record.get("tier") == "scenic"]
    assert tiers["scenic"]["visual_triangles"] == sum(p.mesh.triangle_count for o in scenic for p in o.visual) > 0
    assert tiers["scenic"]["collision_triangles"] == 0
    core = [o for o in cored.objects if o.record.get("tier") == "core"]
    assert tiers["core"]["visual_triangles"] == sum(p.mesh.triangle_count for o in core for p in o.visual)
    assert tiers["core"]["collision_triangles"] == sum(
        m.triangle_count for o in core for m in (*o.colliders, *o.glass_colliders)) > 0
    assert any(o.glass_colliders for o in core)


def test_urban_elements_outside_the_core_are_dropped(cored: Any, ring_inputs: tuple[Path, Path, Path]) -> None:
    urban = [o.record for o in cored.objects if o.type == "urban_element"]
    assert urban and all(abs(x) <= CORE_M and abs(y) <= CORE_M for x, y in (r["position_m"] for r in urban))
    scene, heights, base = ring_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    every = [o.record for o in detail_scene(generated, ref, heights, scene, CFG).objects if o.type == "urban_element"]
    outside = [r for r in every if max(abs(r["position_m"][0]), abs(r["position_m"][1])) > CORE_M]
    # The bicycle rack at x 62 m and the tree at y 70 m are among them.
    assert len(outside) >= 2
    assert any(r["position_m"][1] > CORE_M for r in outside) and any(r["position_m"][0] > CORE_M for r in outside)
    assert cored.stats["tiers"]["scenic"]["urban_elements_dropped"] == len(outside) == len(every) - len(urban)


def test_ground_colliders_end_at_the_core_edge(cored: Any) -> None:
    ground = [o for o in cored.objects if o.type == "ground_collider"]
    assert {o.id for o in ground} >= {"collider/walkable", "collider/carriageway"}
    for o in ground:
        assert o.record["clipped_to_core"] is True
        (mesh,) = o.colliders
        assert _within(mesh.vertices, CORE_M)
        if o.id != "collider/walkable_crossing":
            # The ground and the street run past the core; they now stop at its edge.
            assert mesh.vertices[:, 0].max() == pytest.approx(CORE_M)
    assert not [i for i in cored.issues if i.severity == "critical"]
    # The visual ground stays over the whole site.
    surfaces = [o for o in cored.objects if o.type == "surface"]
    assert surfaces and max(float(p.mesh.vertices[:, 0].max()) for o in surfaces for p in o.visual) > CORE_M


def _part(fid: str, ring: list[tuple[float, float]], height: str) -> dict[str, Any]:
    return {"id": fid, "kind": "area", "tags": {"building:part": "yes", "height": height},
            "geometry": {"type": "Polygon", "coordinates": [[*ring, ring[0]]]}}


def test_building_parts_take_the_tier_of_their_building(ring_inputs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """geo does not keep OSM building:part ways yet, so two parts are handed to detail directly."""
    import onward_generator.detail.scene as detail_module

    original = detail_module._building_parts
    extra = [_part("osm-way-317", [(-59, 31), (-56, 31), (-56, 34), (-59, 34)], "9"),
             _part("osm-way-318", [(-28, 8), (-24, 8), (-24, 12), (-28, 12)], "20")]
    monkeypatch.setattr(detail_module, "_building_parts", lambda features, footprints, cfg: original([*features, *extra], footprints, cfg))
    scene, heights, base = ring_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    cored = detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=CORE_M)
    parts = {o.record["source_ids"][0]: o for o in cored.objects if o.type == "building_part"}
    assert set(parts) == {"osm-way-315", "osm-way-311"}
    assert parts["osm-way-315"].record["tier"] == "scenic" and parts["osm-way-315"].colliders == []
    assert parts["osm-way-311"].record["tier"] == "core" and parts["osm-way-311"].colliders
    assert parts["osm-way-315"].visual


def test_without_a_core_nothing_is_tiered(ring_inputs: tuple[Path, Path, Path]) -> None:
    scene, heights, base = ring_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    plain = detail_scene(generated, ref, heights, scene, CFG)
    assert plain.manifest()["core"] is None
    assert "tiers" not in plain.stats
    assert not any("tier" in o.record for o in plain.objects)
    assert not any("clipped_to_core" in o.record for o in plain.objects)
    assert {o.id for o in plain.objects if o.type == "ground_collider"} >= {"collider/walkable", "collider/carriageway"}
    assert any(o.type == "landmark_part" for o in plain.objects)


def test_detail_cli_writes_the_core_and_the_scenic_materials(ring_detail: Path) -> None:
    manifest = json.loads((ring_detail / "scene_detail.json").read_text(encoding="utf-8"))
    assert manifest["core"]["half_side_m"] == CORE_M
    nodes, materials = nodes_from_glb((ring_detail / "scene_visual.glb").read_bytes())
    used = {p.material for n in nodes for p in n.primitives}
    assert used & set(SCENIC_WALLS)
    for name in used & set(SCENIC_WALLS):
        assert materials[name].color == SCENIC_WALLS[name].color
    report = json.loads((ring_detail / "detail_report.json").read_text(encoding="utf-8"))
    assert report["counts"]["tiers"]["scenic"]["buildings"] == 2


def test_dress_keeps_generated_furniture_in_the_core(ring_inputs: tuple[Path, Path, Path], ring_detail: Path, tmp_path: Path) -> None:
    scene, heights, _ = ring_inputs
    out = tmp_path / "dress"
    assert main(["dress", "style", "--detail", str(ring_detail), "--scene", str(scene), "--heights", str(heights),
                 "--out", str(out)]) == 0
    objects = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))["objects"]
    generated = [o for o in objects if o.get("type") == "urban_element" and o.get("attribute_origin") == "generated"]
    assert generated
    assert all(abs(x) <= CORE_M and abs(y) <= CORE_M for x, y in (o["position_m"] for o in generated))


def test_bounds_puts_the_wall_on_the_core_and_the_backdrop_on_the_site(ring_detail: Path, tmp_path: Path) -> None:
    out = tmp_path / "playable"
    assert main(["bounds", "--base", str(ring_detail), "--out", str(out), "--backdrop"]) == 0
    report = json.loads((out / "bounds_report.json").read_text(encoding="utf-8"))
    assert report["wall"]["core_half_side_m"] == CORE_M
    assert report["wall"]["site_area_m2"] == pytest.approx((2 * CORE_M) ** 2)
    collision, _ = nodes_from_glb((out / "scene_collision.glb").read_bytes())
    (wall,) = [n for n in collision if n.name == "envelope/perimeter_wall#collider"]
    assert _within(wall.primitives[0].mesh.vertices, CORE_M)
    assert wall.primitives[0].mesh.vertices[:, 0].max() == pytest.approx(CORE_M)
    visual, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    (ring,) = [n for n in visual if n.name == "horizon/backdrop"]
    assert ring.primitives[0].mesh.vertices[:, 0].max() > 100


def test_export_reports_the_tiers(ring_inputs: tuple[Path, Path, Path], ring_detail: Path, tmp_path: Path) -> None:
    scene, heights, base = ring_inputs
    layout = tmp_path / "layout.json"
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--detail", str(ring_detail), "--out", str(layout)]) == 0
    doc = json.loads(layout.read_text(encoding="utf-8"))
    for team in ("attackers", "defenders"):
        assert all(abs(p["x"]) < CORE_M and abs(p["y"]) < CORE_M for p in doc[team]["points_m"])
    result = build_package(EXPORT_PROJECT, ring_detail, layout, tmp_path / "pkg", now=FIXED_NOW)
    tiers = result.manifest["tiers"]
    assert tiers["cell_m"] == 100.0
    manifest = json.loads((ring_detail / "scene_detail.json").read_text(encoding="utf-8"))
    visual = (ring_detail / "scene_visual.glb").read_bytes()
    assert tiers == tier_summary(visual, manifest["objects"], core_square(CORE_M), cell_m=100.0)
    # House 316 crosses the core edge: its record makes it core, its bounds alone would make it shared.
    assert tiers != tier_summary(visual, [], core_square(CORE_M), cell_m=100.0)
    assert tiers["scenic"]["visual_triangles"] >= 20 and tiers["scenic"]["draws"] >= 2
    assert tiers["core"]["visual_triangles"] > tiers["scenic"]["visual_triangles"]
    assert tiers["draws"] <= sum(tiers[t]["draws"] for t in ("core", "scenic", "shared"))


@pytest.mark.parametrize(("cell_m", "predicted"), [(50.0, 50.0), (0.0, 100.0)])
def test_export_predicts_draws_at_the_cards_cell(ring_inputs: tuple[Path, Path, Path], ring_detail: Path, tmp_path: Path,
                                                cell_m: float, predicted: float) -> None:
    scene, heights, base = ring_inputs
    layout = tmp_path / "layout.json"
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--detail", str(ring_detail), "--out", str(layout)]) == 0
    card = json.loads(EXPORT_CONFIG_DEFAULT.read_text(encoding="utf-8"))
    card["build"]["combine_cell_m"] = cell_m
    (tmp_path / "card.json").write_text(json.dumps(card), encoding="utf-8")
    result = build_package(EXPORT_PROJECT, ring_detail, layout, tmp_path / "pkg", now=FIXED_NOW, export_config=tmp_path / "card.json")
    assert result.manifest["tiers"]["cell_m"] == predicted


def test_the_scenic_wall_follows_the_seed(ring_inputs: tuple[Path, Path, Path]) -> None:
    from dataclasses import replace

    scene, heights, base = ring_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    seeded = detail_scene(generated, ref, heights, scene, replace(CFG, seed=3), core_half_side_m=CORE_M)
    assert _record(seeded, "building/osm-way-315")["materials"][0] == scenic_wall(3, "osm-way-315") == "scenic_wall_warm"
