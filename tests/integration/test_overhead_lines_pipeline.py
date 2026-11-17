"""Overhead power lines end to end (ADR-045): overgrow on a map whose facade style is wired, and the others unchanged.

Each test runs overgrow itself: a run in a module fixture happens outside every test, so the mutation run would see no
test reach the lines' code in ``place.overgrow``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.generation.gltf import nodes_from_glb, read_glb
from onward_generator.overgrow import place
from onward_generator.overgrow.place import overgrow
from onward_generator.overgrow.power_lines import LINE_MATERIAL, LineResult
from tests.integration.test_overgrow_furnish import _detail_out


@pytest.fixture(scope="module")
def stage(detail_inputs, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    tmp = tmp_path_factory.mktemp("lines")
    return {"base": _detail_out(detail_inputs, tmp, False), "scene": detail_inputs[0]}


def _run(stage: dict[str, Any], out: Path, **options: Any) -> dict[str, Any]:
    return overgrow(stage["base"], stage["scene"], out, seed=777, **options)


def _report(out: Path) -> dict:
    report = json.loads((out / "overgrow_report.json").read_text(encoding="utf-8"))
    for key in ("created_at", "base_dir"):
        report.pop(key)
    return report


def test_a_mediterranean_map_is_unchanged_unless_it_asks(stage, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    _run(stage, tmp_path / "plain")
    _run(stage, tmp_path / "kept", overhead_lines=False)
    for name in ("scene_visual.glb", "scene_collision.glb", "scene_detail.json"):
        assert (tmp_path / "plain" / name).read_bytes() == (tmp_path / "kept" / name).read_bytes(), name
    assert "overhead_lines" not in _report(tmp_path / "plain") and _report(tmp_path / "plain") == _report(tmp_path / "kept")
    refused = _run(stage, tmp_path / "refused", style_request="east_african", overhead_lines=False)
    assert "overhead_lines" not in refused  # map.json false wins over a wired style


def test_a_wired_style_stands_poles_wires_and_drops(stage, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    plain = _run(stage, tmp_path / "plain")
    styled = _run(stage, tmp_path / "styled", style_request="east_african")
    lines = styled["overhead_lines"]
    assert set(lines) == {"style", "requested", "poles", "spans", "drops", "triangles", "colliders", "skipped"}
    assert lines["style"] == "east_african" and lines["requested"] is None
    assert lines["poles"] > 0 and lines["spans"] > 0 and lines["colliders"] == lines["poles"]
    # The lines spend the headroom before the props: what is left for them shrinks by the lines' triangles.
    assert styled["triangle_headroom"] == plain["triangle_headroom"] - lines["triangles"]
    out = tmp_path / "styled"
    nodes, materials = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    power = [node for node in nodes if node.name.startswith("overgrow/power/")]
    assert len([n for n in power if "/pole/" in n.name]) == lines["poles"]
    assert len([n for n in power if "/wire/" in n.name]) == 3 * lines["spans"] + lines["drops"]
    doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    line = next(m for m in doc["materials"] if m.get("name") == LINE_MATERIAL)
    assert line["pbrMetallicRoughness"]["baseColorFactor"] == [1, 1, 1, 1] and line["pbrMetallicRoughness"]["roughnessFactor"] == 0.9
    assert line["pbrMetallicRoughness"].get("baseColorTexture") and LINE_MATERIAL in materials
    colliders, _ = nodes_from_glb((out / "scene_collision.glb").read_bytes())
    assert len([n for n in colliders if n.name.startswith("overgrow/collider/pole/")]) == lines["poles"]
    objects = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))["objects"]
    poles = [o for o in objects if o.get("kind") == "power_pole"]
    assert len(poles) == lines["poles"] and all(o["type"] == "overgrowth" and o["origin"] == "generated" for o in poles)
    assert poles[0]["size_m"] == [0.3, 0.3, 8.0] and poles[0]["role"] == "pole" and poles[0]["id"].startswith("overgrow/power_pole/")
    wires = [o for o in objects if o.get("kind") == "power_line"]
    assert len(wires) == 3 * lines["spans"] + lines["drops"] and {o["role"] for o in wires} <= {"span", "drop"}


def test_the_map_s_word_wires_any_style(stage, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    forced = _run(stage, tmp_path / "forced", overhead_lines=True)
    assert forced["overhead_lines"]["style"] == "mediterranean" and forced["overhead_lines"]["requested"] is True
    assert forced["overhead_lines"]["poles"] > 0


def test_overgrow_hands_the_lines_its_seed_budget_spawns_and_footprints(stage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    seen: dict[str, Any] = {}

    def spy(manifest, features, buildings, zone, seed, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(kwargs, seed=seed, spawn=kwargs["near_spawn"](1.0e6, -1.0e6))
        return LineResult()

    monkeypatch.setattr(place, "place_lines", spy)
    monkeypatch.setattr(place, "_near_spawn", lambda x, y, spawns: (x, y, spawns))
    plain = _run(stage, tmp_path / "plain")
    _run(stage, tmp_path / "spied", style_request="east_african")
    assert seen["seed"] == 777 and seen["budget"] == int(plain["triangle_headroom"] * 0.25)
    assert seen["spawn"][:2] == (1.0e6, -1.0e6) and isinstance(seen["spawn"][2], list)
    assert seen["blocked"] and all(hasattr(area, "distance") for area in seen["blocked"])  # the lamps and other footprints


def test_a_bad_configuration_stops_overgrow(stage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from onward_generator.overgrow.power_lines import LinesError

    def broken() -> None:
        raise LinesError("overhead_lines: broken")

    monkeypatch.setattr(place, "load_lines", broken)
    with pytest.raises(place.OvergrowError, match="^overhead_lines: broken$"):
        _run(stage, tmp_path / "broken")
    assert not (tmp_path / "broken").exists()
