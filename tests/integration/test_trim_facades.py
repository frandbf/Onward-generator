"""materials apply with the trim-sheet facade atlas (ADR-020) against the per-material path, on the synthetic Murcia block.

One test holds the checks of the shared runs: mutation testing credits a
module fixture to the first test that uses it.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from onward_generator.cli import main
from onward_generator.detail.facades import party_base
from onward_generator.generation.gltf import images_by_material, nodes_from_glb, read_glb
from onward_generator.materials.apply import ATLAS_SKIP, apply_materials
from onward_generator.materials.library_plan import select_texture_plan
from onward_generator.materials.trim_style import STYLE_TABLE_PATH
from onward_generator.perf.measure import combine_groups
from tests.conftest import MURCIA_CENTER

FACADE_PARTS = {"plaster_ochre", "plaster_cream", "plaster_white", "plaster_salmon", "stone_warm", "brick_red",
                "frame_stone", "glass_dark", "door_wood"}
TRIMS = ["facade_trim_mediterranean", "party_facade_trim_mediterranean"]


@pytest.fixture(scope="module")
def runs(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("trim")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    lon, lat = MURCIA_CENTER
    plan = root / "texture_plan.json"
    plan.write_text(json.dumps(select_texture_plan(name="Murcia", map_id="murcia", latitude=lat, longitude=lon, brief=None, seed=1)))
    out: dict[str, Path] = {"detail": detail, "plan": plan, "scene": scene}
    for name, flags in (("trim", []), ("again", ["--facades", "trim"]), ("legacy", ["--facades", "legacy"])):
        out[name] = root / name
        assert main(["materials", "apply", "--base", str(detail), "--scene", str(scene), "--plan", str(plan),
                     "--out", str(out[name]), *flags]) == 0
    return out


def _doc(path: Path) -> dict:
    return read_glb((path / "scene_visual.glb").read_bytes())[0]


def test_trim_against_the_per_material_path(runs: dict[str, Path]) -> None:
    trim_doc, legacy_doc = _doc(runs["trim"]), _doc(runs["legacy"])
    trim = {m["name"] for m in trim_doc["materials"]}
    legacy = {m["name"] for m in legacy_doc["materials"]}
    # Fewer materials and draws: every facade part is on the atlas.
    assert set(TRIMS) <= trim and not trim & FACADE_PARTS
    assert not legacy & set(TRIMS) and len(legacy & FACADE_PARTS) >= 5
    assert {"glass_view", "railing_metal"} <= trim
    assert len(trim) < len(legacy) - 3
    assert combine_groups(runs["trim"] / "scene_visual.glb", 100.0)["draws"] < combine_groups(runs["legacy"] / "scene_visual.glb", 100.0)["draws"] - 3
    # The report, and the atlas the trim materials sample.
    report = json.loads((runs["trim"] / "materials_report.json").read_text())
    facades = report["facades"]
    assert facades["mode"] == "trim" and facades["style"] == "mediterranean" and facades["style_origin"] == "map_centre"
    assert facades["materials"] == TRIMS
    assert facades["stats"]["buildings"] >= 3
    atlas = (runs["trim"] / "facade_trim.png").read_bytes()
    assert atlas.startswith(b"\x89PNG")
    images = images_by_material((runs["trim"] / "scene_visual.glb").read_bytes())
    assert images[TRIMS[0]] == images[TRIMS[1]] == atlas
    roughness = {m["name"]: m["pbrMetallicRoughness"]["roughnessFactor"] for m in trim_doc["materials"]}
    assert roughness[TRIMS[0]] == roughness[TRIMS[1]] == 0.9
    nodes, materials = nodes_from_glb((runs["trim"] / "scene_visual.glb").read_bytes())
    assert materials[TRIMS[0]].color == (1.0, 1.0, 1.0, 1.0)
    trims = [p for n in nodes for p in n.primitives if p.material in TRIMS]
    assert trims and all(p.uvs is not None and len(p.uvs) == p.mesh.vertex_count for p in trims)
    v = np.concatenate([p.uvs[:, 1] for p in trims if p.uvs is not None])
    assert v.min() >= 0.0 and v.max() <= 1.0
    # The trim primitives keep the UVs apply_trim gave them: no repeat UV reaches 2.
    u = np.concatenate([p.uvs[:, 0] for p in trims if p.uvs is not None])
    assert u.min() >= 0.0
    # The contact sheet leaves the atlas out: one tile per other image.
    bases = {party_base(name) for name in trim} - ATLAS_SKIP - {TRIMS[0]}
    assert trim_doc["extras"]["atlas"]["tiles"] == len(bases)
    legacy_report = json.loads((runs["legacy"] / "materials_report.json").read_text())["facades"]
    assert legacy_report["mode"] == "legacy" and legacy_report["reason"] == "config"
    assert not (runs["legacy"] / "facade_trim.png").exists()
    # Deterministic, and the collision and the geometry records are not touched.
    assert (runs["trim"] / "scene_visual.glb").read_bytes() == (runs["again"] / "scene_visual.glb").read_bytes()
    assert (runs["trim"] / "scene_collision.glb").read_bytes() == (runs["legacy"] / "scene_collision.glb").read_bytes()
    assert report["geometry"]["identical"] is True


def test_the_style_table_and_the_tile_cache_reach_apply_trim(runs: dict[str, Path], tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    table = json.loads(STYLE_TABLE_PATH.read_text())
    table["default_style"] = "anglo"
    table["selection"] = []
    path = tmp_path / "styles.json"
    path.write_text(json.dumps(table))
    report = apply_materials(runs["detail"], tmp_path / "a", plan_path=runs["plan"], scene_path=runs["scene"], style_table=path)
    assert report["facades"]["style"] == "anglo" and report["facades"]["reason"] == "style_has_no_atlas"
    monkeypatch.delenv("ONWARD_GENERATOR_NO_TILE_CACHE", raising=False)
    monkeypatch.setenv("ONWARD_GENERATOR_CACHE_DIR", str(tmp_path / "cache"))
    report = apply_materials(runs["detail"], tmp_path / "c", plan_path=runs["plan"], scene_path=runs["scene"], tile_cache=False)
    entry = tmp_path / "cache" / "tiles" / f"{report['facades']['atlas']['cache_key']}.tile"
    assert not entry.exists()
    report = apply_materials(runs["detail"], tmp_path / "b", plan_path=runs["plan"], scene_path=runs["scene"])
    assert entry.is_file()


def test_the_cli_prints_the_facade_path(runs: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    detail, plan = runs["detail"], runs["plan"]
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "t")]) == 0
    assert f"facades: trim style=mediterranean (map_centre) materials={','.join(TRIMS)}" in capsys.readouterr().out
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "m"),
                 "--facades", "legacy", "--facade-style", "anglo"]) == 0
    assert "facades: legacy style=anglo (config) reason=config" in capsys.readouterr().out
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "n"),
                 "--facade-style", "nordic"]) == 1
    assert "unknown facade style 'nordic'" in capsys.readouterr().err
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "o"),
                 "--facades", "fancy"]) == 2


def test_the_cli_tones_the_atlas_with_a_preset(runs: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Plan E8: --atmosphere reaches apply_trim through apply_materials, and the CLI prints the palette."""
    detail, plan = runs["detail"], runs["plan"]
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "g"), "--atmosphere", "golden_hour"]) == 0
    out = capsys.readouterr().out
    tone = json.loads((tmp_path / "g" / "materials_report.json").read_bytes())["facades"]["atmosphere"]
    assert (tone["preset"], tone["grime"], tone["wall_desaturation"], tone["wall_value_max"]) == ("golden_hour", 1.0, 0.08, 0.85)
    assert (f"facades: atmosphere golden_hour (ADR-037): grime x1, walls 8% desaturated, value at most 0.85; "
            f"mean wall luminance {tone['wall_luminance_before']:g} -> {tone['wall_luminance_after']:g}\n") in out
    assert tone["wall_luminance_after"] < tone["wall_luminance_before"]
    assert (tmp_path / "g" / "facade_trim.png").read_bytes() != (runs["trim"] / "facade_trim.png").read_bytes()
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "n"), "--atmosphere", "neutral"]) == 0
    assert "atmosphere" not in capsys.readouterr().out
    assert "atmosphere" not in json.loads((tmp_path / "n" / "materials_report.json").read_bytes())["facades"]
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "d"), "--atmosphere", "dusk"]) == 1
    assert "materials: atmosphere must be one of neutral, golden_hour, overcast, not 'dusk'" in capsys.readouterr().err


def test_the_cli_weathers_the_atlas_and_reports_its_calm_sources(runs: dict[str, Path], tmp_path: Path,
                                                                  capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan T2: --facade-weathering and --wide-library reach apply_trim through apply_materials, and the CLI prints them."""
    import onward_generator.materials.trim as trim_module

    detail, plan = runs["detail"], runs["plan"]
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "w"),
                 "--facade-weathering", "worn", "--wide-library"]) == 0
    out = capsys.readouterr().out
    facades = json.loads((tmp_path / "w" / "materials_report.json").read_bytes())["facades"]
    assert (facades["weathering"], facades["calm_sources"]) == ("worn", [])  # every mediterranean photograph is calm
    assert "facades: weathering worn (plan T2)\n" in out
    assert (tmp_path / "w" / "facade_trim.png").read_bytes() != (runs["trim"] / "facade_trim.png").read_bytes()
    plain = apply_materials(detail, tmp_path / "p", plan_path=plan, scene_path=runs["scene"])
    assert "weathering" not in plain["facades"] and "calm_sources" not in plain["facades"]
    swap = {"key": "brick", "from": "a", "to": "b", "family": "brick", "limit": 0.025, "from_contrast": 0.03, "to_contrast": 0.02}
    real = trim_module.calm_sources
    monkeypatch.setattr(trim_module, "calm_sources", lambda atlas, wide: (real(atlas, wide)[0], [swap]))
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "s"), "--wide-library"]) == 0
    assert "facades: brick a (0.03) -> b (0.02), calm limit 0.025\n" in capsys.readouterr().out
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "x"),
                 "--facade-weathering", "rusty"]) == 2


def test_normal_maps_list_the_photographs_that_have_one_and_the_cli_prints_them(runs: dict[str, Path], tmp_path: Path,
                                                                               capsys: pytest.CaptureFixture[str]) -> None:
    """Plan T3: --normal-maps lists, in the GLB and the report, each material whose library photograph has a normal map."""
    from onward_generator.export.library_normals import scene_normal_maps
    from onward_generator.materials.library_plan import catalogue_rows

    lon, lat = MURCIA_CENTER
    plan = tmp_path / "wide_plan.json"
    plan.write_text(json.dumps(select_texture_plan(name="Murcia", map_id="murcia", latitude=lat, longitude=lon, brief=None, seed=1, wide=True)))
    detail = runs["detail"]
    assert main(["materials", "apply", "--base", str(detail), "--scene", str(runs["scene"]), "--plan", str(plan),
                 "--out", str(tmp_path / "n"), "--normal-maps"]) == 0
    out = capsys.readouterr().out
    report = json.loads((tmp_path / "n" / "materials_report.json").read_bytes())
    block = report["normal_maps"]
    rows = {row["id"]: row for row in catalogue_rows()}
    applied = {row["color_id"]: row["asset_id"] for row in report["applied"]}
    assert block["enabled"] is True and block["materials"]
    for name, binding in block["materials"].items():
        library_id = binding["library_id"]
        assert binding == {"library_id": library_id, "path": rows[library_id]["normal"]["path"], "sha256": rows[library_id]["normal"]["sha256"]}
        assert applied[name.removeprefix("party_").split("__tint_")[0]] == library_id
    assert block["without"] == sorted({a for a in applied.values() if "normal" not in rows.get(a, {})})
    assert scene_normal_maps((tmp_path / "n" / "scene_visual.glb").read_bytes()) == block["materials"]
    assert (f"normal maps: {len(block['materials'])} materials ({', '.join(block['materials'])}); "
            f"without one: {', '.join(block['without']) or 'none'}\n") in out
    plain = apply_materials(detail, tmp_path / "p", plan_path=plan, scene_path=runs["scene"])
    assert "normal_maps" not in plain and "normal maps" not in capsys.readouterr().out
    assert scene_normal_maps((tmp_path / "p" / "scene_visual.glb").read_bytes()) == {}


def test_texture_variants_give_buildings_their_own_roof_photograph(runs: dict[str, Path], tmp_path: Path) -> None:
    """Plan T4: --texture-variants adds the variants, moves buildings to them and leaves a run without it as it was."""
    from onward_generator.generation.gltf import nodes_from_glb
    from onward_generator.materials.variants import variant_index

    detail, plan = runs["detail"], runs["plan"]
    on = apply_materials(detail, tmp_path / "on", plan_path=plan, scene_path=runs["scene"], texture_variants=True)
    block = on["texture_variants"]
    assert block["enabled"] is True and block["variants_per_material"] == 2 and block["added_materials"] == len(block["materials"]) > 0
    nodes, materials = nodes_from_glb((tmp_path / "on" / "scene_visual.glb").read_bytes())
    assert set(block["materials"]) | {name.split("__var")[0] for name in block["materials"]} >= set(block["buildings"])
    for name in block["buildings"]:  # only what some building draws reaches the GLB
        assert name in materials and name.split("__var")[0] in {row["color_id"] for row in on["applied"]}
    for node in nodes:
        if (node.extras or {}).get("type") != "building":
            continue
        for prim in node.primitives:
            base = prim.material.split("__var")[0]
            count = sum(1 for name in block["materials"] if name.startswith(base + "__var"))
            if count:
                k = variant_index(node.name, base, count)
                assert prim.material == (base if k == 0 else f"{base}__var{k}")
    off = apply_materials(detail, tmp_path / "off", plan_path=plan, scene_path=runs["scene"])
    assert "texture_variants" not in off
    assert (tmp_path / "off" / "scene_visual.glb").read_bytes() == (runs["trim"] / "scene_visual.glb").read_bytes()
