"""The intermediate HLOD ring (plan D3, ADR-017 amendment) on the playable-core block around MURCIA_CENTER.

With a 50 m core and a 10 m ring, the row 311-313 and house 316 are the core, the shed at x -60 to -54 m crosses into
the band and is ``mid``, and the church at x 60-80 m only touches the band's outer edge and stays ``scenic``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely.geometry import shape

from onward_generator.cli import main
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.mid import MID_GLASS, MID_MAX_TRIANGLES
from onward_generator.detail.scene import FIDELITY_NOTE
from onward_generator.export.package import build_package
from onward_generator.generation import load_generation_config
from onward_generator.generation.gltf import nodes_from_glb
from onward_generator.materials.trim import trim_nodes
from onward_generator.materials.trim_style import load_style_table, trim_layout
from onward_generator.tiers import CORE, MID, SCENIC, SCENIC_WALLS, CoreError, core_square, mid_square, ring_tier, scenic_wall
from tests.conftest import FIXED_NOW, _detail_inputs
from tests.integration.test_playable_core import EXPORT_PROJECT, RING_BUILDINGS, RING_POIS

GEN = load_generation_config()
CFG = load_detail_config()
CORE_M = 50.0
RING_M = 10.0
DETAIL_FILES = ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json")


@pytest.fixture(scope="module")
def ring_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    return _detail_inputs(tmp_path_factory.mktemp("mid_ring"), RING_BUILDINGS, RING_POIS)


def _detail(ring_inputs: tuple[Path, Path, Path], **options: Any) -> Any:
    scene, heights, base = ring_inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, CFG, **options)


@pytest.fixture(scope="module")
def off(ring_inputs: tuple[Path, Path, Path]) -> Any:
    return _detail(ring_inputs, core_half_side_m=CORE_M)


@pytest.fixture(scope="module")
def on(ring_inputs: tuple[Path, Path, Path]) -> Any:
    return _detail(ring_inputs, core_half_side_m=CORE_M, mid_ring_m=RING_M)


def _cli(ring_inputs: tuple[Path, Path, Path], out: Path, *flags: str) -> int:
    scene, heights, base = ring_inputs
    return main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(out),
                 "--no-preview", *flags])


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _keys(v)}
    return set()


def test_off_adds_no_tier_and_no_key(off: Any, ring_inputs: tuple[Path, Path, Path]) -> None:
    explicit = _detail(ring_inputs, core_half_side_m=CORE_M, mid_ring_m=None)
    assert json.dumps(explicit.manifest()) == json.dumps(off.manifest())
    assert json.dumps(explicit.report()) == json.dumps(off.report())
    assert off.core == {"half_side_m": CORE_M, "buildings": {CORE: 4, SCENIC: 2}}
    assert list(off.stats["tiers"]) == [CORE, SCENIC]
    assert "mid" not in _keys(off.manifest()) | _keys(off.report())
    assert {o.record.get("tier") for o in off.objects} == {CORE, SCENIC, None}


def test_each_building_is_in_one_tier_by_its_whole_footprint(on: Any, ring_inputs: tuple[Path, Path, Path]) -> None:
    scene = json.loads(ring_inputs[0].read_text(encoding="utf-8"))
    footprint = {f["id"]: shape(f["geometry"]) for f in scene["features"]}
    tiers = {o.record["source_ids"][0]: o.record["tier"] for o in on.objects if o.type == "building"}
    assert tiers == {"osm-way-311": CORE, "osm-way-312": CORE, "osm-way-313": CORE, "osm-way-316": CORE,
                     "osm-way-315": MID, "osm-way-314": SCENIC}
    for bid, tier in tiers.items():
        assert tier == ring_tier(footprint[bid], core_square(CORE_M), mid_square(CORE_M, RING_M))
    assert on.core == {"half_side_m": CORE_M, "buildings": {CORE: 4, SCENIC: 1, MID: 1}, "mid_ring_m": RING_M}
    assert on.report()["phase_04_checks"]["automatic_checks_passed"] is True


def test_the_core_is_untouched(off: Any, on: Any) -> None:
    def core(d: Any) -> list[tuple[str, str, list[tuple[str, bytes]]]]:
        return [(o.id, json.dumps(o.record, sort_keys=True), [(p.material, p.mesh.vertices.tobytes()) for p in o.visual])
                for o in d.objects if o.record.get("tier") == CORE]

    assert core(on) == core(off)
    assert on.stats["tiers"][CORE] == off.stats["tiers"][CORE]
    assert on.core["buildings"][CORE] == off.core["buildings"][CORE]
    collision = [(n.name, [p.mesh.vertices.tobytes() for p in n.primitives]) for n in on.collision_nodes()]
    assert collision == [(n.name, [p.mesh.vertices.tobytes() for p in n.primitives]) for n in off.collision_nodes()]


def test_a_mid_building_is_a_shell_with_painted_windows_and_no_collider(on: Any, ring_inputs: tuple[Path, Path, Path]) -> None:
    shed = next(o for o in on.objects if o.id == "building/osm-way-315")
    record = shed.record
    scene, heights, base = ring_inputs
    phase03 = next(o for o in load_base(base, scene, heights, GEN)[0].objects if o.id == "building/osm-way-315")
    wall = scenic_wall(CFG.seed, "osm-way-315")
    assert record["tier"] == MID
    assert record["generation"] == {"method": "phase03_volume + mid shell", "phase03": phase03.record["generation"],
                                    "template_config_version": CFG.version, "seed": CFG.seed}
    facade = record["facade"]
    assert facade == {
        "template": "mid_shell", "representation": "mid_shell", "representative_of_real_facade": False,
        "attribute_origin": "generated", "note": FIDELITY_NOTE, "needs_custom_model": False, "ground_floor": "none",
        "storeys": facade["storeys"], "openings": {},
        "mid": {"windows": facade["mid"]["windows"], "exposed_faces": 4, "cap": MID_MAX_TRIANGLES},
        "shops": [],
        "materials": {"wall": wall, "wall_origin": "generated", "roof": record["roof"]["material"],
                      "roof_origin": record["roof"]["origin"], "roof_source": None},
        "use": facade["use"], "typology": facade["typology"], "door": "none", "doors_m": [],
    }
    assert facade["mid"]["windows"] > 0
    # The shed's minor template has a hip: the mid shell draws it, as the core would.
    assert record["roof"]["type"] == "hip" and record["roof"]["origin"] == "generated"
    assert record["interior"] is False
    assert record["collider"] == {"source": "none_mid", "parts": 0, "triangles": 0, "balconies": 0, "glass_panes": 0}
    assert record["checks"] == {"facade_open_edges": 0}
    assert shed.colliders == [] and shed.glass_colliders == []
    assert set(record["materials"]) == {wall, MID_GLASS, record["roof"]["material"]}
    assert record["visual"]["triangles"] == sum(p.mesh.triangle_count for p in shed.visual) <= MID_MAX_TRIANGLES
    assert shed.uv_frame is not None
    zs = np.concatenate([p.mesh.vertices[:, 2] for p in shed.visual])
    assert zs.min() == 0.0 and zs.max() > record["height"]["height_m"]  # the hip rises over the eave
    assert not [o for o in on.objects if o.type in ("interior", "landmark_part") and o.record["source_ids"][0] == "osm-way-315"]


def test_the_report_counts_the_mid_tier(on: Any) -> None:
    tiers = on.stats["tiers"]
    assert list(tiers) == [CORE, SCENIC, MID]
    shed = next(o for o in on.objects if o.id == "building/osm-way-315")
    triangles = sum(p.mesh.triangle_count for p in shed.visual)
    assert tiers[MID] == {"buildings": 1, "visual_triangles": triangles, "collision_triangles": 0,
                          "triangles_per_building": {"mean": float(triangles), "min": triangles, "max": triangles}}
    assert 50 <= triangles <= MID_MAX_TRIANGLES
    assert tiers[SCENIC]["buildings"] == 1 and tiers[SCENIC]["urban_elements_dropped"] > 0
    # Street elements outside the core are still dropped (ADR-017 section 5).
    for o in on.objects:
        if o.type == "urban_element":
            assert max(abs(c) for c in o.record["position_m"]) <= CORE_M


def test_a_building_part_takes_the_mid_tier_and_loses_its_collider(ring_inputs: tuple[Path, Path, Path],
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.detail.scene as detail_module

    original = detail_module._building_parts
    extra = [{"id": "osm-way-317", "kind": "area", "tags": {"building:part": "yes", "height": "9"},
              "geometry": {"type": "Polygon", "coordinates": [[(-59, 31), (-56, 31), (-56, 34), (-59, 34), (-59, 31)]]}}]
    monkeypatch.setattr(detail_module, "_building_parts", lambda features, footprints, cfg, **kw: original([*features, *extra], footprints, cfg, **kw))
    d = _detail(ring_inputs, core_half_side_m=CORE_M, mid_ring_m=RING_M)
    (part,) = [o for o in d.objects if o.type == "building_part"]
    assert part.record["tier"] == MID and part.colliders == [] and part.visual
    assert d.stats["tiers"][MID]["collision_triangles"] == 0
    assert d.stats["tiers"][MID]["buildings"] == 1  # the part is counted in the triangles, not as a building


def test_detail_cli_on_is_deterministic_and_reaches_the_export(ring_inputs: tuple[Path, Path, Path], tmp_path: Path,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    first, second, plain = tmp_path / "a", tmp_path / "b", tmp_path / "off"
    flags = ("--core-radius-m", f"{CORE_M:g}", "--mid-ring-m", f"{RING_M:g}")
    assert _cli(ring_inputs, first, *flags) == 0
    assert "mid ring_m=10 buildings=1 visual_triangles=" in capsys.readouterr().out
    assert _cli(ring_inputs, second, *flags) == 0
    capsys.readouterr()
    for name in DETAIL_FILES:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
    assert _cli(ring_inputs, plain, "--core-radius-m", f"{CORE_M:g}") == 0
    assert "mid ring_m=" not in capsys.readouterr().out
    assert (first / "scene_collision.glb").read_bytes() == (plain / "scene_collision.glb").read_bytes()
    manifest = json.loads((first / "scene_detail.json").read_text(encoding="utf-8"))
    assert manifest["core"]["mid_ring_m"] == RING_M
    # No new material family: the mid tier brings at most roof materials the core already uses or the configuration names.
    used_on = {p.material for n in nodes_from_glb((first / "scene_visual.glb").read_bytes())[0] for p in n.primitives}
    used_off = {p.material for n in nodes_from_glb((plain / "scene_visual.glb").read_bytes())[0] for p in n.primitives}
    assert used_on - used_off <= set(CFG.materials) and all(m.startswith("roof_") for m in used_on - used_off)
    assert used_on & set(SCENIC_WALLS)

    scene, heights, base = ring_inputs
    exported: dict[str, Any] = {}
    for name, detail in (("on", first), ("off", plain)):
        layout = tmp_path / f"layout_{name}.json"
        assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                     "--detail", str(detail), "--out", str(layout)]) == 0
        exported[name] = build_package(EXPORT_PROJECT, detail, layout, tmp_path / f"pkg_{name}", now=FIXED_NOW).manifest
    tiers_on, tiers_off = exported["on"]["tiers"], exported["off"]["tiers"]
    assert list(tiers_off) == ["cell_m", "draws", CORE, SCENIC, "shared"]
    assert list(tiers_on) == ["cell_m", "draws", CORE, SCENIC, "shared", MID]
    assert tiers_on[CORE] == tiers_off[CORE]
    assert tiers_on[MID]["visual_triangles"] > 0 and tiers_on[MID]["draws"] >= 1
    assert not any(p["x"] < -CORE_M or p["x"] > CORE_M for team in ("attackers", "defenders")
                   for p in json.loads((tmp_path / "layout_on.json").read_text(encoding="utf-8"))[team]["points_m"])


def test_trim_puts_the_mid_shell_on_the_scenic_trim(on: Any) -> None:
    style = "mediterranean"
    atlas = load_style_table().styles[style].atlas
    assert atlas is not None
    records = {o.id: o.record for o in on.objects}
    nodes, _ = trim_nodes(on.visual_nodes(), records, style, atlas, trim_layout(atlas))
    shed = next(n for n in nodes if n.name == "building/osm-way-315")
    materials = [p.material for p in shed.primitives]
    assert materials[0] == f"scenic_trim_{style}" and "glass_dark" not in materials and f"facade_trim_{style}" not in materials
    assert all(m.startswith("roof_") for m in materials[1:])


def test_a_ring_without_a_core_is_refused_before_anything_is_written(ring_inputs: tuple[Path, Path, Path], tmp_path: Path,
                                                                    capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(CoreError, match=r"^mid_ring_m needs a core \(core_half_side_m\)$"):
        _detail(ring_inputs, mid_ring_m=RING_M)
    with pytest.raises(CoreError, match="from 10 to 1000"):
        _detail(ring_inputs, core_half_side_m=CORE_M, mid_ring_m=5.0)
    out = tmp_path / "detail"
    capsys.readouterr()
    assert _cli(ring_inputs, out, "--mid-ring-m", "30") == 1
    assert capsys.readouterr().err == "detail: --mid-ring-m needs --core-radius-m\n"
    assert _cli(ring_inputs, out, "--core-radius-m", "50", "--mid-ring-m", "5") == 1
    assert capsys.readouterr().err == "detail: mid_ring_m must be a number of metres from 10 to 1000, got 5.0\n"
    assert not out.exists()


def test_detail_cli_help_describes_the_ring() -> None:
    from onward_generator.detail.__main__ import build_parser

    action = {a.dest: a for a in build_parser()._actions}["mid_ring_m"]
    assert action.type is float and action.default is None
    assert action.help == ("intermediate ring (plan D3, opt-in): buildings outside the core within this many metres of it are boxes "
                           "with painted windows and their roof form, 10 to 1000; needs --core-radius-m")
