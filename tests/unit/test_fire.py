"""Fire and embers under the smoke (export/fire.py, plan 2026-11-17-efectos-sdk, increment E)."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from onward_generator.export.fire import (
    BASE_RISE_M,
    EMBERS,
    FLAMES,
    SHEET_FILE,
    SHEET_FLAMES,
    SHEET_PIVOT_Y,
    SHEET_ROLE,
    TEXTURE_FILE,
    TEXTURE_PX,
    TEXTURE_ROLE,
    FireError,
    check_fire,
    fire_options,
    flame_png,
)
from onward_generator.maps.workspace import MapError, fire_enabled, pack_particles
from onward_generator.next_step import _fire, _pack_particles

ADAPTER = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"


def test_the_settings() -> None:
    assert (TEXTURE_FILE, TEXTURE_ROLE, TEXTURE_PX, BASE_RISE_M) == ("fire_flame.png", "fire_texture", 64, 0.6)
    assert FLAMES == {"rate_per_s": 20.0, "lifetime_s": 0.6, "min_size_m": 0.4, "max_size_m": 1.0, "end_scale": 0.3, "speed_m_s": 1.5,
                      "cone_deg": 12.0, "radius_m": 0.4, "start_color": "#FFC860", "end_color": "#FF3C0A", "max_particles": 16}
    assert EMBERS == {"rate_per_s": 3.0, "lifetime_s": 2.5, "min_size_m": 0.04, "max_size_m": 0.07, "end_scale": 0.5, "speed_m_s": 1.8,
                      "cone_deg": 25.0, "radius_m": 0.5, "start_color": "#FFA040", "end_color": "#FF3000", "max_particles": 8}


@pytest.mark.parametrize(("value", "expected"), [(None, False), (False, False), (True, True)])
def test_fire_is_true_or_false(value: object, expected: bool) -> None:
    assert check_fire(value) is expected


@pytest.mark.parametrize("value", [1, 0, "true", [], {}])
def test_anything_else_is_refused(value: object) -> None:
    with pytest.raises(FireError, match=r'^map.json "fire" must be true or false, got '):
        check_fire(value)


def test_the_flame_is_yellow_in_its_low_core_red_at_the_edges_and_narrows_to_a_tip() -> None:
    image = Image.open(io.BytesIO(flame_png()))
    assert image.size == (64, 64) and image.mode == "RGBA"
    pixels = np.asarray(image)
    assert pixels[55, 32, :3].tolist() == [255, 206, 114]  # the low core, near CORE_RGB
    assert pixels[10, 32, :3].tolist() == [255, 106, 34]  # near the tip, towards EDGE_RGB
    assert pixels[55, 20, :3].tolist() == [255, 175, 89]  # off the axis, between
    assert pixels[0, 0, :3].tolist() == [255, 84, 16]  # outside the flame: EDGE_RGB exactly
    alpha = pixels[..., 3]
    assert alpha[63, 32] == 0 and alpha[0, 32] == 0  # it fades in at the foot and out at the tip
    assert alpha[55, 32] == 161 and alpha[32, 32] == 153 and alpha[5, 32] == 50
    assert alpha[55, 0] == 0 and alpha[55, 5] == 0  # narrower than the sprite
    assert int(np.argmax(alpha[:, 32])) == 52 and alpha.max() == 204  # brightest low down, at most ALPHA_PEAK
    assert hashlib.sha256(pixels.tobytes()).hexdigest()[:16] == "6550f9cc11bd5f3a"
    assert flame_png() == flame_png()


def test_the_options_carry_the_numbers_and_a_site_under_each_column() -> None:
    block = fire_options([{"x": 1.0, "y": 2.0, "z": 1.0, "seed": 1}, {"x": -3.5, "y": 4.25, "z": 2.12345, "seed": 2}])
    assert block == {"texture": "fire_flame.png", "flames": FLAMES, "embers": EMBERS,
                     "sites": [{"x": 1.0, "y": 2.0, "z": 1.6, "seed": 1}, {"x": -3.5, "y": 4.25, "z": 2.723, "seed": 2}]}
    block["flames"]["rate_per_s"] = 0.0
    assert FLAMES["rate_per_s"] == 20.0  # the block is a copy


def test_the_map_key_reaches_the_export_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "map.json").write_text("{}", encoding="utf-8")
    document: dict[str, Any] = {}
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda directory: document)
    assert fire_enabled(tmp_path) is False and _fire(tmp_path) == []
    document["fire"] = True
    assert fire_enabled(tmp_path) is True and _fire(tmp_path) == ["--fire"]
    document["fire"] = False
    assert _fire(tmp_path) == []
    document["fire"] = "yes"
    with pytest.raises(MapError, match=r'^map.json "fire" must be true or false, got \'yes\'$'):
        fire_enabled(tmp_path)
    document["fire"] = True
    assert _fire(tmp_path / "elsewhere") == []  # no map.json there


def test_the_adapter_draws_flames_and_embers_on_the_sdk_unlit_shader() -> None:
    source = ADAPTER.read_text(encoding="utf-8")
    assert 'private const string FireShaderPath = SdkRoot + "/Internal/Shaders/PP_Particle_Unlit.shadergraph";' in source
    assert 'private const string FireRole = "fire_texture";' in source
    assert "public FireOptions fire = new FireOptions();" in source and "AddFire(pkg, root);" in source
    assert "adapter_options.json fire has {fireSites} sites, the package lists {fireFiles} '{FireRole}' files" in source
    body = source[source.index("private static void AddFire"):source.index("// One baked point light per furnished room")]
    for line in ('material.SetTexture("_MainTex", texture);', 'FireEmitterSystem(go, "Flames", fire.flames, flameMaterial, site.seed, fire.sheet);',
                 'FireEmitterSystem(go, "Embers", fire.embers, material, site.seed + 1000, null);', "go.transform.position = Local(site.x, site.y, site.z);",
                 "main.maxParticles = e.max_particles;", "emission.rateOverTime = e.rate_per_s;", "main.startSpeed = e.speed_m_s;",
                 "renderer.shadowCastingMode = ShadowCastingMode.Off;", "renderer.lightProbeUsage = LightProbeUsage.Off;"):
        assert line in body, line


def test_the_views_run_the_particles_before_they_render() -> None:
    source = ADAPTER.read_text(encoding="utf-8")
    body = source[source.index("public static void CaptureViews"):]
    simulate = body.index("system.Simulate(system.main.duration * 2f, false, true);")
    assert simulate < body.index('new GameObject("OnwardCaptureCamera")')



def test_a_pack_s_flipbook_draws_the_flames_and_the_embers_keep_the_sprite() -> None:
    assert (SHEET_FILE, SHEET_ROLE, SHEET_PIVOT_Y) == ("fire_flame_sheet.png", "fire_sheet_texture", 0.5)
    assert SHEET_FLAMES == {"rate_per_s": 3.0, "lifetime_s": 3.0, "min_size_m": 0.9, "max_size_m": 1.25, "end_scale": 1.0,
                            "speed_m_s": 0.0, "cone_deg": 25.0, "radius_m": 0.4, "start_color": "#FFFFFF",
                            "end_color": "#FFFFFF", "max_particles": 12}
    block = fire_options([{"x": 1.0, "y": 2.0, "z": 1.0, "seed": 1}], (10, 8))
    assert block == {"texture": "fire_flame.png", "flames": SHEET_FLAMES, "embers": EMBERS,
                     "sites": [{"x": 1.0, "y": 2.0, "z": 1.6, "seed": 1}],
                     "sheet": {"texture": "fire_flame_sheet.png", "tiles": [10, 8], "pivot_y": 0.5}}
    block["flames"]["rate_per_s"] = 0.0
    assert SHEET_FLAMES["rate_per_s"] == 3.0  # the block is a copy
    assert "sheet" not in fire_options([{"x": 1.0, "y": 2.0, "z": 1.0, "seed": 1}])


def test_the_pack_particles_key_reaches_the_export_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "map.json").write_text("{}", encoding="utf-8")
    document: dict[str, Any] = {}
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda directory: document)
    assert pack_particles(tmp_path) is True and _pack_particles(tmp_path) == []
    document["pack_particles"] = False
    assert pack_particles(tmp_path) is False and _pack_particles(tmp_path) == ["--no-pack-particles"]
    assert _pack_particles(tmp_path / "elsewhere") == []  # no map.json there
    document["pack_particles"] = True
    assert _pack_particles(tmp_path) == []
    document["pack_particles"] = "no"
    with pytest.raises(MapError, match=r'^map.json "pack_particles" must be true or false, got \'no\'$'):
        pack_particles(tmp_path)


def test_the_adapter_plays_a_flipbook_once_over_each_particle_s_life() -> None:
    source = ADAPTER.read_text(encoding="utf-8")
    assert 'private const string FireSheetRole = "fire_sheet_texture";' in source and "private const int SheetTilesMax = 16;" in source
    assert "public ParticleSheet sheet = new ParticleSheet();" in source
    assert "the package lists {sheetFiles} '{FireSheetRole}' files" in source
    body = source[source.index("private static void ApplySheet"):source.index("private static void FireEmitterSystem")]
    for line in ("frames.numTilesX = sheet.tiles[0];", "frames.numTilesY = sheet.tiles[1];",
                 "frames.animation = ParticleSystemAnimationType.WholeSheet;",
                 "frames.frameOverTime = new ParticleSystem.MinMaxCurve(1f, AnimationCurve.Linear(0f, 0f, 1f, 1f));",
                 "frames.cycleCount = 1;", "renderer.pivot = new Vector3(0f, sheet.pivot_y, 0f);"):
        assert line in body, line
    fire = source[source.index("private static void AddFire"):source.index("private static void CheckSheet")]
    assert 'File.ReadAllBytes(pkg.Path(FireSheetRole))' in fire and 'flameMaterial.SetTexture("_MainTex", sheetTexture);' in fire
    assert "ApplySheet(ps, renderer, smoke.sheet);" in source and "ApplySheet(ps, renderer, sheet);" in source
