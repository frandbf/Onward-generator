"""Plan C3 end to end: geo → heights → phase 03 → ``detail --lidar-roofs`` on a block at MURCIA_CENTER.

A is a 20 x 10 m rectangle whose nDSM spread is 1.5 m over 20 cells (pitched), B a 12 x 12 m square with a 0.2 m
spread (flat), C a rectangle with 4 valid cells (unclassified), D a tagged gabled house with a wide spread (the tag
wins), E a rectangle the report does not name and that lies outside a 50 m core. The report is synthetic and made for the fixture scene.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.__main__ import load_roof_spread
from onward_generator.detail.__main__ import main as detail_main
from onward_generator.generation import load_generation_config
from onward_generator.generation.mesh import merge
from tests.conftest import _detail_inputs

GEN = load_generation_config()
CFG = load_detail_config()
PLAIN = {"building": "residential", "building:levels": "2"}
BUILDINGS: dict[int, tuple[list[tuple[float, float]], dict[str, str]]] = {
    901: ([(-40, 8), (-20, 8), (-20, 18), (-40, 18)], PLAIN),
    902: ([(-10, 8), (2, 8), (2, 20), (-10, 20)], PLAIN),
    903: ([(10, 8), (22, 8), (22, 14), (10, 14)], PLAIN),
    904: ([(30, 8), (42, 8), (42, 16), (30, 16)], {**PLAIN, "roof:shape": "gabled"}),
    905: ([(60, 8), (72, 8), (72, 18), (60, 18)], PLAIN),  # outside a 50 m core; not in the report
}
SPREADS = {
    "osm-way-901": {"valid": 20, "cells": 24, "p25_m": 5.0, "p75_m": 6.5},
    "osm-way-902": {"valid": 12, "cells": 14, "p25_m": 6.0, "p75_m": 6.2},
    "osm-way-903": {"valid": 4, "cells": 6, "p25_m": 5.0, "p75_m": 6.5},
    "osm-way-904": {"valid": 12, "cells": 12, "p25_m": 4.0, "p75_m": 6.0},
}
ATTRIBUTION = "Obra derivada de MDSn 2008-2015 CC-BY 4.0 ign.es"


@pytest.fixture(scope="module")
def block(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path, Path]:
    root = tmp_path_factory.mktemp("lidar_roofs")
    scene, heights, base = _detail_inputs(root, BUILDINGS, {})
    report = root / "es.pnoa_ndsm_report.json"
    report.write_text(json.dumps(_report(scene)), encoding="utf-8")
    return scene, heights, base, report


def _report(scene_path: Path, **changes: Any) -> dict[str, Any]:
    entries = [{"id": bid, "status": "measured", "date": "PNOA-LiDAR 1st coverage, 2008-2015", "buffer_m": 1.0,
                "height_m": round((s["p25_m"] + s["p75_m"]) / 2, 2), **s} for bid, s in SPREADS.items()]
    return {"schema": "onward_generator.source_heights_report", "schema_version": "1.0.0", "source": "es.pnoa_ndsm",
            "provider": "IGN MDSn 2.5 m building class", "license": "CC-BY-4.0", "attribution": ATTRIBUTION,
            "scene": {"path": str(scene_path), "sha256": hashlib.sha256(scene_path.read_bytes()).hexdigest()}, "buildings": entries} | changes


def _detail(block: tuple[Path, Path, Path, Path], lidar: bool) -> Any:
    scene, heights, base, report = block
    generated, ref = load_base(base, scene, heights, GEN)
    spread = load_roof_spread(str(report), str(scene)) if lidar else None
    return detail_scene(generated, ref, heights, scene, CFG, roof_spread=spread)


def _meshes(meshes: Any) -> list[tuple[bytes, bytes]]:
    return [(m.vertices.tobytes(), m.triangles.tobytes()) for m in meshes]


def _building(detailed: Any, bid: str) -> Any:
    return next(o for o in detailed.objects if o.id == f"building/{bid}")


def test_the_spread_draws_flat_and_pitched_roofs_with_their_evidence(block: tuple[Path, Path, Path, Path]) -> None:
    off, on = _detail(block, False), _detail(block, True)
    assert not on.critical
    a = _building(on, "osm-way-901")
    roof = a.record["roof"]
    assert roof["type"] == "gabled" and roof["origin"] == "estimated" and roof["roof_shape"] is None
    assert (roof["rise_m"], roof["rise_rule"], roof["rise_origin"], roof["direction_origin"]) == (1.5, "ndsm_spread", "estimated", "estimated")
    assert roof["lidar"] == {"source": "es.pnoa_ndsm", "provider": "IGN MDSn 2.5 m building class", "license": "CC-BY-4.0",
                             "p25_m": 5.0, "p75_m": 6.5, "valid": 20, "cells": 24, "rule": "ndsm_spread", "class": "pitched",
                             "origin": "estimated"}
    assert a.record["facade"]["materials"]["roof"] == "roof_tile" and a.record["facade"]["materials"]["roof_origin"] == "estimated"
    # The ridge runs along the 20 m side, through the middle of the 10 m one, 1.5 m over the recorded height.
    tiles = merge([p.mesh for p in a.visual if p.material == "roof_tile"])
    top = float(a.record["height"]["height_m"])
    ridge = tiles.vertices[np.isclose(tiles.vertices[:, 2], top + 1.5)]
    assert len(ridge) and np.allclose(ridge[:, 1], ridge[0, 1]) and np.ptp(ridge[:, 0]) == pytest.approx(20.0)
    assert tiles.vertices[:, 2].min() == pytest.approx(top)
    b = _building(on, "osm-way-902").record["roof"]
    # The coin drew B an estimated hip; the flat class keeps its template roof and parapet.
    assert _building(off, "osm-way-902").record["roof"]["type"] == "hip" and b["type"] == "flat_parapet"
    assert b["origin"] == "estimated" and b["lidar"]["class"] == "flat"
    c = _building(on, "osm-way-903").record
    assert "lidar" not in c["roof"] and c == _building(off, "osm-way-903").record
    d = _building(on, "osm-way-904").record
    assert d["roof"]["origin"] == "observed" and "lidar" not in d["roof"] and d == _building(off, "osm-way-904").record
    assert on.stats["lidar_roofs"] == {
        "source": "es.pnoa_ndsm", "buildings": 5, "core_buildings": 5, "flat": 1, "pitched": 1,
        "unclassified": {"tagged": 1, "scenic": 0, "terrace": 0, "no_data": 1, "too_few_cells": 1, "not_rectangular": 0, "mixed": 0,
                         "ambiguous": 0, "fallback": 0},
    }
    assert on.inputs["roof_spread"] == {"path": str(block[3]), "sha256": hashlib.sha256(block[3].read_bytes()).hexdigest(),
                                        "source": "es.pnoa_ndsm"}
    assert on.attribution == [*off.attribution, {"text": ATTRIBUTION, "license": "CC-BY-4.0"}]
    # No building added or removed, no height changed, no material outside what the scene already defines.
    assert [o.id for o in on.objects] == [o.id for o in off.objects]
    assert all(o.record.get("height") == p.record.get("height") for o, p in zip(on.objects, off.objects, strict=True))
    # B's flat roof takes one of its template's flat materials, A's gable roof_tile: both already in the catalogue.
    added = {p.material for o in on.objects for p in o.visual} - {p.material for o in off.objects for p in o.visual}
    assert added <= {"roof_tile", *CFG.templates[_building(on, "osm-way-902").record["generation"]["template"]].flat_roof_materials}
    assert added <= set(CFG.materials)
    # A pitched roof gives no roof access (ADR-029): A's stair stops under it and its flat roof's stair well and parapet go.
    # B's flat class drops the coin hip, so its stair reaches the roof again. Every other collider is unchanged.
    interior = {o.id: o.record for o in on.objects if o.type == "interior"}
    interior_off = {o.id: o.record for o in off.objects if o.type == "interior"}
    assert interior_off["interior/osm-way-901"]["roof_access"] is True and interior["interior/osm-way-901"]["roof_access"] is False
    assert interior_off["interior/osm-way-902"]["roof_access"] is False and interior["interior/osm-way-902"]["roof_access"] is True
    assert "parapet_m" not in a.record["roof"] and _building(off, "osm-way-901").record["roof"]["parapet_m"] > 0
    changed = {"building/osm-way-901", "building/osm-way-902", "interior/osm-way-901", "interior/osm-way-902"}
    assert [(o.id, _meshes(o.colliders)) for o in on.objects if o.id not in changed] == [
        (o.id, _meshes(o.colliders)) for o in off.objects if o.id not in changed]
    assert [(o.id, _meshes(o.colliders)) for o in on.objects if o.id == "building/osm-way-903"]
    assert "lidar_roofs" not in off.stats and "roof_spread" not in off.inputs


def test_a_pitched_class_that_cannot_be_drawn_falls_back_flat_and_is_counted(block: tuple[Path, Path, Path, Path],
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.detail import grammar
    from onward_generator.detail.lidar_roofs import spread_spec

    monkeypatch.setattr(grammar, "spread_spec", lambda spread, material: replace(spread_spec(spread, material), rise_m=0.01))
    detailed = _detail(block, True)
    a = _building(detailed, "osm-way-901").record["roof"]
    assert a["type"] == "flat" and a["fallback"] == "rise_too_small" and a["lidar"]["class"] == "pitched"
    counts = detailed.stats["lidar_roofs"]
    assert counts["pitched"] == 0 and counts["unclassified"]["fallback"] == 1 and counts["flat"] == 1
    issue = next(i for i in detailed.issues if i.code == "roof_shape_fallback")
    assert issue.message == "the estimated gable (nDSM spread) drawn flat: rise_too_small"


@pytest.mark.parametrize("kept", [None, "osm-way-901", "osm-way-902"])
def test_the_attribution_line_comes_with_a_classified_roof(block: tuple[Path, Path, Path, Path], tmp_path: Path, kept: str | None) -> None:
    scene, heights, base, _ = block
    few = tmp_path / "few.json"
    doc = _report(scene)
    for entry in doc["buildings"]:
        if entry["id"] != kept:
            entry["valid"] = 2
    few.write_text(json.dumps(doc), encoding="utf-8")
    generated, ref = load_base(base, scene, heights, GEN)
    detailed = detail_scene(generated, ref, heights, scene, CFG, roof_spread=load_roof_spread(str(few), str(scene)))
    off = _detail(block, False)
    counts = detailed.stats["lidar_roofs"]
    assert (counts["flat"], counts["pitched"]) == {None: (0, 0), "osm-way-901": (0, 1), "osm-way-902": (1, 0)}[kept]
    assert counts["unclassified"]["too_few_cells"] == (3 if kept is None else 2)
    line = {"text": ATTRIBUTION, "license": "CC-BY-4.0"}
    assert detailed.attribution == off.attribution + ([] if kept is None else [line])
    if kept is None:
        assert detailed.manifest()["objects"] == off.manifest()["objects"]


def test_a_scenic_shell_is_counted_apart_and_keeps_its_cap(block: tuple[Path, Path, Path, Path]) -> None:
    scene, heights, base, report = block
    generated, ref = load_base(base, scene, heights, GEN)
    on = detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=50.0, roof_spread=load_roof_spread(str(report), str(scene)))
    off = detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=50.0)
    counts = on.stats["lidar_roofs"]
    assert (counts["buildings"], counts["core_buildings"], counts["unclassified"]["scenic"], counts["unclassified"]["no_data"]) == (5, 4, 1, 0)
    assert _building(on, "osm-way-905").record == _building(off, "osm-way-905").record
    assert _building(on, "osm-way-901").record["roof"]["type"] == "gabled"


def test_a_mid_shell_takes_its_roof_form_and_evidence_from_the_spread(block: tuple[Path, Path, Path, Path], tmp_path: Path) -> None:
    """Plan D3: 905 lies in a 20 m ring around the 50 m core; a mid shell draws the roof form the spread gives it."""
    scene, heights, base, _ = block
    entry = {"id": "osm-way-905", "status": "measured", "date": "PNOA-LiDAR 1st coverage, 2008-2015", "buffer_m": 1.0,
             "height_m": 5.75, "valid": 20, "cells": 24, "p25_m": 5.0, "p75_m": 6.5}
    report = tmp_path / "es.pnoa_ndsm_report.json"
    report.write_text(json.dumps(_report(scene, buildings=[entry])), encoding="utf-8")
    generated, ref = load_base(base, scene, heights, GEN)
    on = detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=50.0, mid_ring_m=20.0,
                      roof_spread=load_roof_spread(str(report), str(scene)))
    mid = _building(on, "osm-way-905").record
    assert mid["tier"] == "mid" and mid["roof"]["origin"] == "estimated"
    assert mid["roof"]["lidar"]["p25_m"] == 5.0 and mid["roof"]["lidar"]["p75_m"] == 6.5


def _cli(block: tuple[Path, Path, Path, Path], out: Path, *extra: str) -> int:
    scene, heights, base, _ = block
    return detail_main(["--scene", str(scene), "--heights", str(heights), "--base", str(base), "--no-preview", "--out", str(out), *extra])


def _outputs(out: Path) -> dict[str, bytes]:
    return {name: (out / name).read_bytes() for name in ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json")}


def test_the_cli_is_off_by_default_and_deterministic_when_on(block: tuple[Path, Path, Path, Path], tmp_path: Path) -> None:
    report = str(block[3])
    assert _cli(block, tmp_path / "off1") == 0 and _cli(block, tmp_path / "off2") == 0
    assert _cli(block, tmp_path / "on1", "--lidar-roofs", report) == 0 and _cli(block, tmp_path / "on2", "--lidar-roofs", report) == 0
    off, on = _outputs(tmp_path / "off1"), _outputs(tmp_path / "on1")
    assert off == _outputs(tmp_path / "off2") and on == _outputs(tmp_path / "on2")
    for name in ("scene_detail.json", "detail_report.json"):
        assert b'"lidar' not in off[name] and b"ndsm_spread" not in off[name] and b"roof_spread" not in off[name]
    assert b"ndsm_spread" in on["scene_detail.json"]
    assert json.loads(on["detail_report.json"])["counts"]["lidar_roofs"]["pitched"] == 1
    assert json.loads(on["detail_report.json"])["schema_version"] == json.loads(off["detail_report.json"])["schema_version"]


def test_a_report_of_another_scene_stops_detail_before_any_output(block: tuple[Path, Path, Path, Path], tmp_path: Path,
                                                                  capsys: pytest.CaptureFixture[str]) -> None:
    other = tmp_path / "other.json"
    other.write_text(json.dumps(_report(block[0], scene={"path": "x", "sha256": "f" * 64})), encoding="utf-8")
    out = tmp_path / "detail"
    assert _cli(block, out, "--lidar-roofs", str(other)) == 1
    assert capsys.readouterr().err == f"detail: {other} was measured on another scene than {block[0]}\n"
    assert not out.exists()


def test_a_flat_class_drops_the_hip_of_a_minor_template(tmp_path_factory: pytest.TempPathFactory) -> None:
    # A 10 x 10 m garage takes generic_minor, whose template hip covers 200 m²; a 0.3 m spread over 12 cells is flat.
    root = tmp_path_factory.mktemp("lidar_minor")
    scene, heights, base = _detail_inputs(root, {911: ([(-40, 8), (-30, 8), (-30, 18), (-40, 18)], {"building": "garage"})}, {})
    doc = _report(scene)
    doc["buildings"] = [{**doc["buildings"][0], "id": "osm-way-911", "valid": 12, "cells": 14, "p25_m": 3.0, "p75_m": 3.3,
                         "height_m": 3.15}]
    report = root / "minor_report.json"
    report.write_text(json.dumps(doc), encoding="utf-8")
    generated, ref = load_base(base, scene, heights, GEN)
    off = detail_scene(generated, ref, heights, scene, CFG)
    on = detail_scene(generated, ref, heights, scene, CFG, roof_spread=load_roof_spread(str(report), str(scene)))
    garage_off, garage_on = _building(off, "osm-way-911").record, _building(on, "osm-way-911").record
    assert garage_off["generation"]["template"] == "generic_minor" and garage_off["roof"]["type"] == "hip"
    assert garage_on["roof"]["type"] == "flat" and garage_on["roof"]["lidar"]["class"] == "flat"
    assert garage_on["facade"]["materials"]["roof"] in CFG.templates["generic_minor"].flat_roof_materials
    assert not [p for p in _building(on, "osm-way-911").visual if p.material == "roof_tile"]
    assert on.stats["lidar_roofs"]["flat"] == 1 and on.stats["lidar_roofs"]["pitched"] == 0
