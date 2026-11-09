"""materials apply with the opt-in regional atlases (plan D1) on the synthetic Murcia block.

The block is mediterranean by its centre; the regional styles are named with
``facade_style``. One test holds the checks of the shared runs: mutation testing
credits a module fixture to the first test that uses it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.generation.gltf import read_glb
from onward_generator.materials.apply import apply_materials
from onward_generator.materials.library_plan import select_texture_plan
from onward_generator.materials.trim_style import REGIONAL_TABLE_PATH
from onward_generator.perf.measure import combine_groups
from tests.conftest import MURCIA_CENTER

REGIONAL = ("central_european", "anglo", "latin_american")


def _glb(path: Path) -> bytes:
    return (path / "scene_visual.glb").read_bytes()


def _facades(path: Path) -> dict:
    return json.loads((path / "materials_report.json").read_text())["facades"]


@pytest.fixture(scope="module")
def runs(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("regional")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    lon, lat = MURCIA_CENTER
    plan = root / "texture_plan.json"
    plan.write_text(json.dumps(select_texture_plan(name="Murcia", map_id="murcia", latitude=lat, longitude=lon, brief=None, seed=1)))
    doc = json.loads(REGIONAL_TABLE_PATH.read_text(encoding="utf-8"))
    for atlas in doc["atlases"].values():
        atlas.update(size_px=1024, height_px=1024, fit_row_px=192, fit_cell_px=96, cornice_px=64, zocalo_px=64, stone_px=32)
    table = root / "regional.json"
    table.write_text(json.dumps(doc), encoding="utf-8")
    out: dict[str, Path] = {"detail": detail, "plan": plan, "scene": scene, "table": table}

    def run(name: str, **options: object) -> None:
        out[name] = root / name
        apply_materials(detail, out[name], plan_path=plan, scene_path=scene, tile_cache=False, regional_table=table, **options)  # type: ignore[arg-type]

    run("off", facade_style="central_european")
    run("legacy", facade_style="central_european", facades="legacy")
    for style in REGIONAL:
        run(style, facade_style=style, regional_facades=True)
    run("again", facade_style="anglo", regional_facades=True)
    run("medi_off")
    run("medi_on", regional_facades=True)
    return out


def test_regional_atlases_against_the_per_material_path(runs: dict[str, Path]) -> None:
    # Off: central_european keeps the per-material path, byte for byte.
    off = _facades(runs["off"])
    assert off["mode"] == "legacy" and off["reason"] == "style_has_no_atlas" and "regional_table_sha256" not in off
    assert _glb(runs["off"]) == _glb(runs["legacy"])
    legacy_materials = {m["name"] for m in read_glb(_glb(runs["legacy"]))[0]["materials"]}
    legacy_draws = combine_groups(runs["legacy"] / "scene_visual.glb", 100.0)["draws"]
    for style in REGIONAL:
        facades = _facades(runs[style])
        assert facades["mode"] == "trim" and facades["style"] == style and facades["style_origin"] == "config"
        assert facades["atlas_origin"] == "regional" and facades["regional_table_sha256"] == _facades(runs["anglo"])["regional_table_sha256"]
        assert facades["era_bands"]["era_origin"] == "observed" and facades["era_bands"]["band_origin"] == "estimated"
        assert sum(facades["era_bands"]["buildings"].values()) == facades["stats"]["buildings"] >= 3
        assert all(s["license"].startswith("CC0") for s in facades["atlas"]["sources"])
        materials = {m["name"] for m in read_glb(_glb(runs[style]))[0]["materials"]}
        assert f"facade_trim_{style}" in materials and not {"plaster_ochre", "frame_stone", "glass_dark"} & materials
        assert len(materials) < len(legacy_materials) - 3
        assert combine_groups(runs[style] / "scene_visual.glb", 100.0)["draws"] < legacy_draws - 3
        # Collision is never touched by the facade pass.
        assert (runs[style] / "scene_collision.glb").read_bytes() == (runs["off"] / "scene_collision.glb").read_bytes()
    # Deterministic: the same atlas and scene twice.
    assert (runs["anglo"] / "facade_trim.png").read_bytes() == (runs["again"] / "facade_trim.png").read_bytes()
    assert _glb(runs["anglo"]) == _glb(runs["again"])
    assert len({(runs[s] / "facade_trim.png").read_bytes() for s in REGIONAL}) == 3
    # A mediterranean map is the same with the option on or off.
    assert _facades(runs["medi_on"]) == _facades(runs["medi_off"])
    assert "regional_table_sha256" not in _facades(runs["medi_on"])
    assert _glb(runs["medi_on"]) == _glb(runs["medi_off"])
    assert (runs["medi_on"] / "facade_trim.png").read_bytes() == (runs["medi_off"] / "facade_trim.png").read_bytes()


def test_the_cli_switch_reaches_the_regional_atlas(runs: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    detail, plan = runs["detail"], runs["plan"]
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "on"),
                 "--no-tile-cache", "--facade-style", "anglo", "--regional-facades"]) == 0
    assert "facades: trim style=anglo (config) materials=facade_trim_anglo" in capsys.readouterr().out
    assert _facades(tmp_path / "on")["atlas"]["size"] == [2048, 4096]
    assert main(["materials", "apply", "--base", str(detail), "--plan", str(plan), "--out", str(tmp_path / "off"),
                 "--no-tile-cache", "--facade-style", "anglo"]) == 0
    assert "facades: legacy style=anglo (config) reason=style_has_no_atlas" in capsys.readouterr().out
