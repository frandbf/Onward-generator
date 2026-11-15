"""Smoke columns over wrecks (export/smoke.py, plan 2026-11-15-ambiente-belico, increment C)."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.export import smoke
from onward_generator.export.smoke import (
    APART_M,
    COLUMN,
    FOOT_CLEAR_M,
    SMOKE_MAX,
    SPAWN_CLEAR_M,
    SmokeError,
    check_count,
    choose,
    keep_out,
    puff_png,
    smoke_options,
    wreck_sites,
)
from onward_generator.maps.workspace import MapError, smoke_columns
from onward_generator.next_step import _smoke

ADAPTER = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"


def test_the_settings() -> None:
    assert (SMOKE_MAX, SPAWN_CLEAR_M, APART_M, FOOT_CLEAR_M) == (5, 15.0, 15.0, 3.0)
    assert (smoke.TEXTURE_FILE, smoke.TEXTURE_ROLE, smoke.TEXTURE_PX) == ("smoke_puff.png", "smoke_texture", 128)
    assert COLUMN == {"height_m": 16.0, "rate_per_s": 4.0, "lifetime_s": 10.0, "start_size_m": 2.0, "end_size_m": 8.0,
                      "drift_m_s": 0.5, "color": "#4A4640", "alpha": 0.6, "max_particles": 40}


@pytest.mark.parametrize(("value", "expected"), [(None, 0), (0, 0), (3, 3), (5, 5)])
def test_the_count_is_an_integer_from_0_to_5(value: object, expected: int) -> None:
    assert check_count(value) == expected


@pytest.mark.parametrize("value", [6, -1, True, 2.0, "3"])
def test_a_bad_count_is_refused(value: object) -> None:
    with pytest.raises(SmokeError, match=r'^map.json "smoke" must be an integer from 0 to 5, got '):
        check_count(value)


def test_the_sites_are_the_wrecks() -> None:
    objects = [
        {"id": "overgrow/wreck/0", "type": "overgrowth", "kind": "wreck", "position_m": [1.0, 2.0, -0.15]},
        {"id": "overgrow/prop/car/1", "type": "overgrowth", "kind": "vehicles", "role": "wreck", "position_m": [3.0, 4.0]},
        {"id": "overgrow/prop/barrel/2", "type": "overgrowth", "kind": "waste", "role": "barrel", "position_m": [5.0, 6.0, 0.0]},
        {"id": "overgrow/prop/van/3", "type": "overgrowth", "kind": "vehicles", "role": "vehicle", "position_m": [7.0, 8.0, 0.0]},
        {"id": "building/x", "type": "building", "kind": "wreck", "position_m": [9.0, 9.0, 0.0]},
    ]
    assert wreck_sites(objects) == [("overgrow/wreck/0", 1.0, 2.0, -0.15), ("overgrow/prop/car/1", 3.0, 4.0, 0.0)]


LAYOUT = {
    "attackers": {"points_m": [{"x": 0.0, "y": 0.0, "yaw": 0.0}]},
    "defenders_b": {"points_m": [{"x": 100.0, "y": 0.0, "yaw": 0.0}]},
    "uplink_m": [0.0, 60.0],
    "evac_m": "not a point",
    "ladders": [{"x": 50.0, "y": 50.0}],
    "lifts": [{"x": -50.0, "y": -50.0}],
}


def test_the_keep_outs_are_the_spawns_objectives_and_climb_feet() -> None:
    points, feet = keep_out(LAYOUT)
    assert points == [(0.0, 0.0), (100.0, 0.0), (0.0, 60.0)]
    assert feet == [(50.0, 50.0), (-50.0, -50.0)]
    assert keep_out({}) == ([], [])


def test_columns_keep_clear_of_spawns_climbs_each_other_and_the_core_edge() -> None:
    sites = [
        ("near_spawn", 10.0, 0.0, 0.0),        # 10 m from an attacker
        ("near_climb", 52.0, 50.0, 0.0),       # 2 m from a ladder foot
        ("outside", 120.0, 0.0, 0.0),          # past a 100 m core
        ("a", 40.0, -40.0, -0.2),
        ("b", 45.0, -40.0, 0.5),               # 5 m from a
        ("c", -40.0, 40.0, 0.0),
    ]
    columns, refused = choose(sites, 5, 100.0, LAYOUT)
    assert refused == {"outside_core": 1, "near_spawn": 1, "near_climb": 1, "near_column": 1}
    # In the order of their ids' hashes: c, then b; a comes after b and is 5 m from it.
    assert columns == [{"x": -40.0, "y": 40.0, "z": 1.0, "seed": 1}, {"x": 45.0, "y": -40.0, "z": 1.5, "seed": 2}]
    lowered, _ = choose([("a", 40.0, -40.0, -0.2)], 1, None, {})
    assert lowered[0]["z"] == 1.0  # a column starts a metre over its wreck, never under the ground
    # The boundaries keep: exactly 15 m from a spawn and 3 m from a foot are clear.
    edge, _ = choose([("s", 15.0, 0.0, 0.0), ("f", 53.0, 50.0, 0.0)], 5, None, LAYOUT)
    assert sorted((c["x"], c["y"]) for c in edge) == [(15.0, 0.0), (53.0, 50.0)]


def test_the_order_is_fixed_by_the_ids_and_the_count_caps_it() -> None:
    sites = [(f"w{i}", 30.0 + 20.0 * i, -30.0, 0.0) for i in range(4)]
    first, _ = choose(sites, 2, None, {})
    again, _ = choose(list(reversed(sites)), 2, None, {})
    assert first == again and len(first) == 2
    every, refused = choose(sites, 5, None, {})
    assert len(every) == 4 and sum(refused.values()) == 0
    assert choose([], 3, None, {}) == ([], {"outside_core": 0, "near_spawn": 0, "near_climb": 0, "near_column": 0})


def test_the_puff_is_a_soft_round_white_disc() -> None:
    data = puff_png()
    assert data == puff_png() and data != puff_png(1)
    image = Image.open(io.BytesIO(data))
    assert image.mode == "RGBA" and image.size == (128, 128)
    pixels = np.asarray(image)
    assert (pixels[..., :3] == 255).all()
    alpha = pixels[..., 3].astype(int)
    assert alpha[64, 64] > 100 and alpha[0, 0] == 0 and alpha[0, 64] < 10  # dense in the middle, gone at the edge


def test_the_options_block_carries_the_numbers_and_the_columns() -> None:
    block = smoke_options([{"x": 1.0, "y": 2.0, "z": 1.0, "seed": 1}])
    assert block == {"texture": "smoke_puff.png", **COLUMN, "columns": [{"x": 1.0, "y": 2.0, "z": 1.0, "seed": 1}]}


def test_the_map_key_reaches_the_export_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "map.json").write_text("{}", encoding="utf-8")
    document: dict = {}
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda directory: document)
    assert smoke_columns(tmp_path) == 0 and _smoke(tmp_path) == []
    document["smoke"] = 3
    assert smoke_columns(tmp_path) == 3 and _smoke(tmp_path) == ["--smoke", "3"]
    document["smoke"] = 0
    assert _smoke(tmp_path) == []
    document["smoke"] = 9
    with pytest.raises(MapError, match=r'^map.json "smoke" must be an integer from 0 to 5, got 9$'):
        smoke_columns(tmp_path)
    document["smoke"] = 3
    assert _smoke(tmp_path / "elsewhere") == []  # no map.json there


def test_the_adapter_draws_each_column_with_the_sdk_particle_shader() -> None:
    source = ADAPTER.read_text(encoding="utf-8")
    assert 'private const string SmokeShaderPath = SdkRoot + "/Internal/Shaders/PP_Particle_Simple_Lit.shadergraph";' in source
    assert 'private const string SmokeRole = "smoke_texture";' in source
    assert "public SmokeOptions smoke = new SmokeOptions();" in source
    body = source[source.index("private static void AddSmoke"):source.index("// One baked point light per furnished room")]
    for line in ('material.SetTexture("_MainTex", texture);', "go.AddComponent<ParticleSystem>()", "main.startSpeed = smoke.height_m / smoke.lifetime_s;",
                 "main.maxParticles = smoke.max_particles;", "emission.rateOverTime = smoke.rate_per_s;", "renderer.shadowCastingMode = ShadowCastingMode.Off;",
                 "go.transform.position = Local(column.x, column.y, column.z);"):
        assert line in body, line
    assert "AddSmoke(pkg, root);" in source
    assert "the package lists {smokeFiles} '{SmokeRole}' files" in source
