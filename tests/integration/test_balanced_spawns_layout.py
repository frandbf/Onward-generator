"""``perf layout --playable --balanced-spawns`` on the synthetic street block around MURCIA_CENTER (plan C6).

Off, the layout and the report are byte for byte what HEAD wrote before C6. On, the placement is
deterministic, no worse on balance and routes, and every SDK rule of the layout still holds.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest
from shapely import Point, contains_xy

from onward_generator.cli import main
from onward_generator.detail.scene import load_base
from onward_generator.export.package import MANIFEST_FILE, _check_layout, build_package
from onward_generator.gameplay.metrics import SNAP_M, nav_graph
from onward_generator.gameplay.report import REPORT_FILE
from onward_generator.generation.config import load_generation_config
from onward_generator.perf.layout import (
    AI_COUNT,
    AMMO_COUNT,
    BLOCK_GAP_M,
    CLEARANCE_M,
    MIN_SPAWN_SEPARATION_M,
    SPAWN_SEPARATION_M,
    TEAM_SEPARATION_M,
    free_ground,
    load_detail_manifest,
    nav_islands,
    playable_area,
)
from tests.conftest import FIXED_NOW

REPO = Path(__file__).resolve().parents[2]
EXPORT_PROJECT = REPO / "config" / "murcia-belluga.export.json"
# perf layout --playable --gameplay-report on this fixture at workshop/c5 (c3c90b4), before C6 changed any code.
# Re-pinned on 12 November 2026 (ADR-029 amendment): lift 1 left the carriageway, so it and its roof wind moved;
# nothing else in the layout changed, and the report changed only its layout_sha256.
HEAD_LAYOUT_SHA256 = "b4a41ccf2c3a36c57e76c96e2a7d5f6c545ff2042d7180deec50c97450fecef7"
# The report was re-pinned on 10 November 2026: more props are solid (overgrow.ambient.SOLID_DEPTH_M 0.2, tall props),
# so the report counts more solids; the layout itself, what the off path must keep, is unchanged.
HEAD_REPORT_SHA256 = "f4065bb07ea796bc60bb4ec037b10a30d83c8f9ed3e47d711d3f4f7b4f4615e9"
BLOCKS = ("attackers", "defenders", "attackers_b", "defenders_b")


@pytest.fixture(scope="module")
def playable(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[list[str], Path]:
    """The ``perf layout`` arguments without ``--out``, and the playable directory (detail, then bounds)."""
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("balanced")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    walled = root / "scene_playable"
    assert main(["bounds", "--base", str(detail), "--out", str(walled)]) == 0
    args = ["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(walled),
            "--playable", "--gameplay-report"]
    return args, walled


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _centre(doc: dict[str, Any], block: str) -> tuple[float, float]:
    x, y = doc[block]["centre_m"]
    return (float(x), float(y))


def test_balanced_spawns_off_is_head_and_on_is_better_and_valid(
    playable: tuple[list[str], Path], detail_inputs: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    args, walled = playable
    off, on, again = tmp_path / "off", tmp_path / "on", tmp_path / "again"
    assert main([*args, "--out", str(off / "layout.json")]) == 0
    printed = capsys.readouterr().out
    assert "placement:" not in printed and "" not in printed.splitlines()
    assert (_sha(off / "layout.json"), _sha(off / REPORT_FILE)) == (HEAD_LAYOUT_SHA256, HEAD_REPORT_SHA256)

    lines = []
    for target in (on, again):
        assert main([*args, "--balanced-spawns", "--out", str(target / "layout.json")]) == 0
        out = capsys.readouterr().out
        found = [line for line in out.splitlines() if line.startswith("placement: ")]
        assert len(found) == 1
        assert out.index("placement: ") < out.index(f"wrote {target / 'layout.json'}")
        lines.append(found[0])
    assert lines[0] == lines[1]
    assert (on / "layout.json").read_bytes() == (again / "layout.json").read_bytes()
    assert (on / REPORT_FILE).read_bytes() == (again / REPORT_FILE).read_bytes()

    before, after = _read(off / REPORT_FILE), _read(on / REPORT_FILE)
    assert after["status"] in ("ok", "no_cover")
    assert not any(p.startswith(("spawn_off_navmesh", "uplink_off_navmesh", "no_path")) for p in after["problems"])
    assert after["headline"]["contact_diff_pct"] <= before["headline"]["contact_diff_pct"]
    assert after["headline"]["distinct_routes"] >= before["headline"]["distinct_routes"]
    # The fixture is unbalanced today (34 %); the balanced placement brings it under the 15 % proposal.
    assert after["headline"]["contact_diff_pct"] < before["headline"]["contact_diff_pct"]
    assert after["proposed_checks"]["contact_diff_pct"]["result"] == "pass"
    match = re.fullmatch(
        r"placement: balanced, candidate (\d+) of (\d+), contact diff ([\d.]+) -> ([\d.]+) %, routes (\d+) -> (\d+), "
        r"overlap ([\d.]+) -> ([\d.]+) % \(estimated from the 2D navmesh\)",
        lines[0],
    )
    assert match, lines[0]
    index, count, diff0, diff1, routes0, routes1, overlap0, overlap1 = match.groups()
    # At most two Uplinks for each of the K_DEFENDERS positions and for each pair of the K_SITES spread sites.
    from onward_generator.perf.layout import K_DEFENDERS, K_SITES

    assert 0 < int(index) < int(count) <= 2 * K_DEFENDERS + 2 * (K_SITES * (K_SITES - 1) // 2)
    assert (float(diff0), int(routes0), float(overlap0)) == (
        before["headline"]["contact_diff_pct"], before["headline"]["distinct_routes"], before["headline"]["max_overlap_pct"])
    assert (float(diff1), int(routes1), float(overlap1)) == (
        after["headline"]["contact_diff_pct"], after["headline"]["distinct_routes"], after["headline"]["max_overlap_pct"])

    doc, today = _read(on / "layout.json"), _read(off / "layout.json")
    assert set(doc) == set(today)
    # Since the C6 amendment of 11 November 2026 the attackers may move as well.
    assert any(doc[key] != today[key] for key in ("attackers", "defenders", "uplink_m"))
    assert _check_layout(doc) == []

    scene, heights, base_dir = detail_inputs
    base, _ = load_base(str(base_dir), str(scene), str(heights), load_generation_config(None))
    manifest = load_detail_manifest(walled)
    urban = [tuple(o["position_m"]) for o in manifest.get("objects", []) if o.get("type") == "urban_element"]
    inside = playable_area(manifest)
    free = free_ground(base, urban)
    # The synthetic block has no core: the playable area is then the whole site (ADR-017).
    area = inside if inside is not None else base.partition.site
    spawn_ground = free.intersection(area).buffer(-CLEARANCE_M)
    for block in BLOCKS:
        points = doc[block]["points_m"]
        assert len(points) == 8
        xy = [(p["x"], p["y"]) for p in points]
        assert all(spawn_ground.covers(Point(p)) for p in xy), block
    assert math.dist(_centre(doc, "attackers"), _centre(doc, "defenders")) >= TEAM_SEPARATION_M
    for second in ("attackers_b", "defenders_b"):
        for other in BLOCKS:
            if other != second:
                assert math.dist(_centre(doc, second), _centre(doc, other)) >= BLOCK_GAP_M - 1e-6
    assert len(doc["ai_m"]) == AI_COUNT
    starts = [(p["x"], p["y"]) for p in doc["ai_m"]]
    assert MIN_SPAWN_SEPARATION_M <= doc["ai_separation_m"] <= SPAWN_SEPARATION_M
    assert all(math.dist(p, q) >= doc["ai_separation_m"] - 0.01 for i, p in enumerate(starts) for q in starts[i + 1:])
    assert len(doc["ammo_m"]) >= AMMO_COUNT
    assert doc["escort_a_m"] and doc["escort_b_m"] and doc["evac_m"] and len(doc["harddrives_m"]) == 3
    graph = nav_graph(nav_islands(base, inside))
    nodes = graph.xy
    for centre in [*(_centre(doc, block) for block in BLOCKS), (doc["uplink_m"][0], doc["uplink_m"][1])]:
        assert area.covers(Point(centre))
        assert min(math.dist(centre, (float(x), float(y))) for x, y in nodes) <= SNAP_M
    route_points = [(p["x"], p["y"]) for route in doc["routes"] for p in route["points"]]
    assert all(contains_xy(area, x, y) for x, y in route_points)

    packages = []
    for name, layout in (("pkg_off", off / "layout.json"), ("pkg_on", on / "layout.json")):
        package = tmp_path / name
        build_package(EXPORT_PROJECT, walled, layout, package, now=FIXED_NOW)
        packages.append(package)
    listing = [sorted(p.name for p in package.iterdir()) for package in packages]
    assert listing[0] == listing[1]
    assert _read(packages[1] / MANIFEST_FILE)["files"]


def test_balanced_spawns_needs_the_playable_layout(playable: tuple[list[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args, _ = playable
    plain = [arg for arg in args if arg not in ("--playable", "--gameplay-report")]
    assert main([*plain, "--balanced-spawns", "--out", str(tmp_path / "layout.json")]) == 1
    assert capsys.readouterr().err == "perf: --balanced-spawns needs --playable\n"
    assert not (tmp_path / "layout.json").exists()


def test_balanced_spawns_pass_the_time_of_day_storm_and_footprints(
    playable: tuple[list[str], Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import onward_generator.perf.__main__ as perf_main

    args, _ = playable
    seen: list[dict[str, Any]] = []
    real = perf_main.balanced_playable_layout

    def spy(*positional: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return real(*positional, **kwargs)

    monkeypatch.setattr(perf_main, "balanced_playable_layout", spy)
    assert main([*args, "--balanced-spawns", "--time-of-day", "night", "--storm", "--out", str(tmp_path / "layout.json")]) == 0
    assert [(k["time_of_day"], k["storm"]) for k in seen] == [("night", True)]
    scene = json.loads(Path(args[args.index("--scene") + 1]).read_text(encoding="utf-8"))
    assert sorted(seen[0]["footprints"]) == sorted(f["id"] for f in scene["features"]) and seen[0]["footprints"]
    doc = _read(tmp_path / "layout.json")
    assert (doc["time_of_day"], doc["lightning"]) == ("Night", 1)
    assert "placement: " in capsys.readouterr().out
