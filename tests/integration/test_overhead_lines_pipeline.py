"""Overhead power lines end to end (ADR-045): overgrow on a map whose facade style is wired, and the others unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.generation.gltf import nodes_from_glb, read_glb
from onward_generator.overgrow.place import overgrow
from onward_generator.overgrow.power_lines import LINE_MATERIAL
from tests.integration.test_overgrow_furnish import _detail_out


@pytest.fixture(scope="module")
def runs(detail_inputs, tmp_path_factory: pytest.TempPathFactory) -> dict[str, object]:  # type: ignore[no-untyped-def]
    tmp = tmp_path_factory.mktemp("lines")
    base = _detail_out(detail_inputs, tmp, False)
    scene = detail_inputs[0]
    cases = {"plain": {}, "styled": {"style_request": "east_african"}, "forced": {"overhead_lines": True},
             "refused": {"style_request": "east_african", "overhead_lines": False}, "kept": {"overhead_lines": False}}
    reports = {name: overgrow(base, scene, tmp / name, seed=777, **options) for name, options in cases.items()}  # type: ignore[arg-type]
    return {"tmp": tmp, "reports": reports, "base": base, "scene": scene}


def _report(out: Path) -> dict:
    report = json.loads((out / "overgrow_report.json").read_text(encoding="utf-8"))
    for key in ("created_at", "base_dir"):
        report.pop(key)
    return report


def test_a_mediterranean_map_is_unchanged_unless_it_asks(runs) -> None:  # type: ignore[no-untyped-def]
    tmp = runs["tmp"]
    for name in ("scene_visual.glb", "scene_collision.glb", "scene_detail.json"):
        assert (tmp / "plain" / name).read_bytes() == (tmp / "kept" / name).read_bytes(), name
    assert "overhead_lines" not in _report(tmp / "plain") and _report(tmp / "plain") == _report(tmp / "kept")
    refused = _report(tmp / "refused")
    assert "overhead_lines" not in refused  # map.json false wins over a wired style


def test_a_wired_style_or_the_map_s_word_stands_poles_wires_and_drops(runs) -> None:  # type: ignore[no-untyped-def]
    tmp = runs["tmp"]
    styled, forced = runs["reports"]["styled"], runs["reports"]["forced"]
    lines = styled["overhead_lines"]
    assert lines["style"] == "east_african" and lines["requested"] is None
    assert lines["poles"] > 0 and lines["spans"] > 0 and lines["colliders"] == lines["poles"]
    assert forced["overhead_lines"]["style"] == "mediterranean" and forced["overhead_lines"]["requested"] is True
    assert forced["overhead_lines"]["poles"] > 0
    nodes, materials = nodes_from_glb((tmp / "styled" / "scene_visual.glb").read_bytes())
    power = [node for node in nodes if node.name.startswith("overgrow/power/")]
    assert len([n for n in power if "/pole/" in n.name]) == lines["poles"]
    assert len([n for n in power if "/wire/" in n.name]) == 3 * lines["spans"] + lines["drops"]
    assert LINE_MATERIAL in materials and tuple(materials[LINE_MATERIAL].color) == (1.0, 1.0, 1.0, 1.0)
    doc, _ = read_glb((tmp / "styled" / "scene_visual.glb").read_bytes())
    assert any(m.get("name") == LINE_MATERIAL and m["pbrMetallicRoughness"].get("baseColorTexture") for m in doc["materials"])
    colliders, _ = nodes_from_glb((tmp / "styled" / "scene_collision.glb").read_bytes())
    assert len([n for n in colliders if n.name.startswith("overgrow/collider/pole/")]) == lines["poles"]
    objects = json.loads((tmp / "styled" / "scene_detail.json").read_text(encoding="utf-8"))["objects"]
    poles = [o for o in objects if o.get("kind") == "power_pole"]
    assert len(poles) == lines["poles"] and all(o["type"] == "overgrowth" and o["origin"] == "generated" for o in poles)
    assert poles[0]["size_m"] == [0.3, 0.3, 8.0] and poles[0]["role"] == "pole" and poles[0]["id"].startswith("overgrow/power_pole/")
    wires = [o for o in objects if o.get("kind") == "power_line"]
    assert len(wires) == 3 * lines["spans"] + lines["drops"] and {o["role"] for o in wires} <= {"span", "drop"}
    assert lines["triangles"] <= runs["reports"]["plain"]["triangle_headroom"]


def test_a_bad_configuration_stops_overgrow(runs, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from onward_generator.overgrow import place
    from onward_generator.overgrow.power_lines import LinesError

    def broken() -> None:
        raise LinesError("overhead_lines: broken")

    monkeypatch.setattr(place, "load_lines", broken)
    out = runs["tmp"] / "broken"
    with pytest.raises(place.OvergrowError, match="^overhead_lines: broken$"):
        overgrow(runs["base"], runs["scene"], out, seed=777)
    assert not out.exists()
