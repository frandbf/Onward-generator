"""Dust in the air (export/dust.py, plan 2026-11-17-efectos-sdk, increment F; ADR-047)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from onward_generator.export.dust import (
    CONFIG_PATH,
    SCHEMA_PATH,
    TEXTURE_FILE,
    TEXTURE_PX,
    TEXTURE_ROLE,
    DustError,
    check_choice,
    choose_volumes,
    dust_options,
    dust_wanted,
    load_dust,
    mote_png,
    parse_dust,
    route_points,
    wind_heading,
)
from onward_generator.maps.workspace import MapError, dust
from onward_generator.next_step import _dust

ADAPTER = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"
LOOK = {"box_m": [30.0, 30.0, 5.5], "base_m": 0.5, "rate_per_s": 18.75, "lifetime_s": 8.0, "min_size_m": 0.03, "max_size_m": 0.1,
        "drift_m_s": 0.5, "color": "#C9B48E", "max_particles": 150}


def _doc() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _config(**changes: Any) -> Any:
    doc = _doc()
    doc.update(changes)
    return parse_dust(doc, _schema(), "here")


def test_the_configuration() -> None:
    config = _config()  # parsed here: a cached load would hide its mutants
    assert load_dust() == config
    assert config.presets == frozenset({"dusty"}) and config.styles == frozenset({"arid", "east_african"})
    assert (config.volumes, config.apart_m, config.spawn_clear_m) == (6, 30.0, 0.0)
    assert dict(config.look) == LOOK
    assert (TEXTURE_FILE, TEXTURE_ROLE, TEXTURE_PX) == ("dust_mote.png", "dust_texture", 32)


@pytest.mark.parametrize(("key", "value", "message"), [
    ("volumes", 0, "volumes: 0 is less than the minimum of 1"),
    ("color", "sand", "color: 'sand' does not match '^#[0-9A-Fa-f]{6}$'"),
    ("box_m", [30, 30], "box_m: [30, 30] is too short"),
])
def test_a_bad_configuration_is_refused_with_its_path(key: str, value: object, message: str) -> None:
    with pytest.raises(DustError) as caught:
        _config(**{key: value})
    assert str(caught.value) == f"here: {message}"


def test_the_largest_mote_may_not_be_smaller_than_the_smallest() -> None:
    assert _config(min_size_m=0.05, max_size_m=0.05).look["max_size_m"] == 0.05
    with pytest.raises(DustError, match=r"^here: max_size_m 0.04 is smaller than min_size_m 0.05$"):
        _config(min_size_m=0.05, max_size_m=0.04)


def test_a_configuration_that_is_not_an_object_is_refused_at_the_root() -> None:
    with pytest.raises(DustError, match=r"^here: \[\] is not of type 'object'$"):
        parse_dust([], _schema(), "here")


def test_an_unreadable_configuration_is_refused(tmp_path: Path) -> None:
    broken = tmp_path / "dust.json"
    broken.write_text("{", encoding="utf-8")
    with pytest.raises(DustError, match=r"dust\.json: Expecting property name"):
        load_dust.__wrapped__(broken)


@pytest.mark.parametrize(("value", "expected"), [(None, "auto"), ("auto", "auto"), ("light", "light"), ("off", "off")])
def test_the_choice(value: object, expected: str) -> None:
    assert check_choice(value) == expected


@pytest.mark.parametrize("value", ["heavy", "", True, 1])
def test_a_bad_choice_is_refused(value: object) -> None:
    with pytest.raises(DustError, match=r'^map.json "dust" must be one of auto, light, off, got '):
        check_choice(value)


@pytest.mark.parametrize(("choice", "preset", "style", "wanted"), [
    ("auto", "dusty", "mediterranean", True), ("auto", None, "arid", True), ("auto", None, "east_african", True),
    ("auto", "golden_hour", "mediterranean", False), ("auto", None, "haussmann", False),
    ("light", None, "mediterranean", True), ("off", "dusty", "arid", False),
])
def test_auto_follows_the_preset_or_the_style_and_a_named_choice_wins(choice: str, preset: str | None, style: str, wanted: bool) -> None:
    assert dust_wanted(choice, preset, style, _config()) is wanted


def test_the_route_points_are_every_point_of_every_route() -> None:
    layout = {"routes": [{"points": [{"x": 1, "y": 2}, {"x": 3.5, "y": 4}]}, {"points": []}, {}, {"points": [{"x": -1, "y": 0}]}]}
    assert route_points(layout) == [(1.0, 2.0), (3.5, 4.0), (-1.0, 0.0)]
    assert route_points({}) == []


def test_volumes_stand_inside_the_core_clear_of_spawns_and_apart_each_bound_included() -> None:
    config = _config(spawn_clear_m=10.0, volumes=12)
    layout = {"attackers": {"points_m": [{"x": -20.0, "y": 30.0}]}, "ground_z_m": 2.0}
    assert choose_volumes([(50.0, 0.0), (50.001, 10.0)], config, 50.0, layout) == [{"x": 50.0, "y": 0.0, "z": 5.25, "seed": 1}]
    assert [(v["x"], v["y"]) for v in choose_volumes([(-10.0, 30.0), (-29.999, 30.0)], config, 50.0, layout)] == [(-10.0, 30.0)]
    assert [(v["x"], v["y"]) for v in choose_volumes([(-20.0, 40.0), (-20.0, 20.001)], config, 50.0, layout)] == [(-20.0, 40.0)]
    both = choose_volumes([(0.0, 0.0), (30.0, 0.0)], config, 50.0, layout)  # exactly 30 m apart
    assert sorted((v["x"], v["y"]) for v in both) == [(0.0, 0.0), (30.0, 0.0)]
    assert len(choose_volumes([(0.0, 0.0), (29.999, 0.0)], config, 50.0, layout)) == 1
    assert len(choose_volumes([(50.001, 10.0)], config, None, layout)) == 1  # no core: anywhere counts


def test_the_order_is_fixed_by_the_points_the_count_caps_it_and_a_repeated_point_counts_once() -> None:
    points = [(float(40 * i), 10.0) for i in range(8)]
    config = _config(volumes=3)
    chosen = choose_volumes(points + points[:2], config, None, {})
    assert chosen == choose_volumes(list(reversed(points)), config, None, {})
    by_hash = sorted(points, key=lambda p: hashlib.sha256(f"{p[0]:.2f},{p[1]:.2f}".encode()).hexdigest())
    assert by_hash != sorted(points)
    assert [(v["x"], v["y"]) for v in chosen] == by_hash[:3] and [v["seed"] for v in chosen] == [1, 2, 3]
    assert {v["z"] for v in chosen} == {3.25}  # no ground in the layout: 0.5 m floor and half of 5.5 m


def test_a_volume_keeps_the_millimetre() -> None:
    assert choose_volumes([(40.12345, -40.98765)], _config(), None, {"ground_z_m": 0.12345}) == [
        {"x": 40.123, "y": -40.988, "z": 3.373, "seed": 1}]


def test_the_wind_is_fixed_by_the_map_id() -> None:
    assert wind_heading("mogadishu_1993") == 351.0 and wind_heading("murcia") == 30.0


def test_the_mote_is_a_soft_white_dot() -> None:
    pixels = np.asarray(Image.open(io.BytesIO(mote_png())))
    assert pixels.shape == (32, 32, 4) and (pixels[..., :3] == 255).all()
    alpha = pixels[..., 3]
    assert alpha[15, 15] == 238 and alpha[0, 0] == 0 and alpha[15, 0] == 0 and alpha[15, 8] == 94
    assert hashlib.sha256(pixels.tobytes()).hexdigest()[:16] == "753d7581d150df68"


def test_the_options_carry_the_look_the_wind_and_the_volumes() -> None:
    config = _config()
    volumes = [{"x": 1.0, "y": 2.0, "z": 3.25, "seed": 1}]
    assert dust_options(volumes, config, None, 351.0) == {"texture": "dust_mote.png", **LOOK, "heading_deg": 351.0, "volumes": volumes}
    assert dust_options(volumes, config, "#E8D2B4", 0.0)["color"] == "#E8D2B4"  # the preset's fog colour wins


def test_the_map_key_reaches_the_export_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "map.json").write_text("{}", encoding="utf-8")
    document: dict[str, Any] = {}
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda directory: document)
    assert dust(tmp_path) == "auto" and _dust(tmp_path) == ["--dust", "auto"]
    document["dust"] = "off"
    assert _dust(tmp_path) == ["--dust", "off"]
    document["dust"] = "thick"
    with pytest.raises(MapError, match=r'^map.json "dust" must be one of auto, light, off, got \'thick\'$'):
        dust(tmp_path)
    assert _dust(tmp_path / "elsewhere") == []  # no map.json there


def test_the_adapter_fills_a_box_per_volume_on_the_sdk_dithered_shader() -> None:
    source = ADAPTER.read_text(encoding="utf-8")
    assert 'private const string DustShaderPath = SdkRoot + "/Internal/Shaders/PP_Particle_UnLit_Dither.shadergraph";' in source
    assert 'private const string DustRole = "dust_texture";' in source
    assert "public DustOptions dust = new DustOptions();" in source and "AddDust(pkg, root);" in source
    assert "adapter_options.json dust has {dustVolumes} volumes, the package lists {dustFiles} '{DustRole}' files" in source
    body = source[source.index("private static void AddDust"):source.index("// One baked point light per furnished room")]
    for line in ('material.SetTexture("_BaseMap", texture);', "shape.shapeType = ParticleSystemShapeType.Box;",
                 "shape.scale = new Vector3(dust.box_m[0], dust.box_m[2], dust.box_m[1]);",
                 "var wind = new Vector3(Mathf.Sin(heading), 0f, Mathf.Cos(heading)) * dust.drift_m_s;",
                 "go.transform.position = Local(volume.x, volume.y, volume.z);", "main.maxParticles = dust.max_particles;",
                 "emission.rateOverTime = dust.rate_per_s;", "renderer.shadowCastingMode = ShadowCastingMode.Off;"):
        assert line in body, line
