"""Budget mode of the playable core (ADR-017 amendment, 7 November 2026) on synthetic grids around MURCIA_CENTER.

A grid of r metres (half-side) holds 12 x 12 m houses every 30 m, moved by a fixed jitter of up to
4 m, with 2 to 5 levels, and one row of touching houses south of the centre, so party walls appear. Every third building is a block of
flats, so the core gets interiors. The target is half the core triangles of the largest core
(``radius - 50``). The figure the criterion counts is ``counts.tiers.core.visual_triangles`` of the
kept ``detail`` run, not the cost model.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely import box
from shapely.geometry import shape

from onward_generator.cli import main
from onward_generator.detail import budget as budget_module
from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.budget import detail_scene_budget
from onward_generator.domain.models import AreaOfInterest
from onward_generator.generation import load_generation_config
from onward_generator.geo.projection import LocalFrame
from onward_generator.tiers import CORE, CORE_BUDGET_TOLERANCE, SCENIC, building_tier, core_of, core_reach, core_square
from tests.conftest import FIXED_NOW, MURCIA_CENTER, SYNTHETIC_LICENSE
from tests.integration.test_playable_core import RING_BUILDINGS, RING_POIS

GEN = load_generation_config()
CFG = load_detail_config()
RING = 50.0
BUDGET_KEYS = {"mode", "target_triangles", "max_half_side_m", "predicted_triangles", "achieved_triangles", "tolerance", "within_tolerance"}
# Spacing of the grid per radius. The r500 grid is sparser so the suite stays within minutes.
SPACING = {150: 30.0, 300: 30.0, 500: 60.0}


def _grid(radius: float, spacing: float) -> dict[int, tuple[list[tuple[float, float]], dict[str, str]]]:
    buildings: dict[int, tuple[list[tuple[float, float]], dict[str, str]]] = {}
    n = int((radius - spacing / 2) // spacing)
    centres = [spacing / 2 + spacing * k for k in range(-n - 1, n + 1) if abs(spacing / 2 + spacing * k) + 6 < radius]
    wid = 10_000
    for j, cy in enumerate(centres):
        for i, cx in enumerate(centres):
            levels = str(2 + (3 * i + 7 * j) % 4)
            kind = "apartments" if (i + j) % 3 == 0 else "house"
            if cy == -spacing / 2:  # the touching row: 30 m wide houses edge to edge
                for x0 in np.arange(cx - spacing / 2, cx + spacing / 2, 30.0):
                    wid += 1
                    buildings[wid] = ([(x0, cy - 6), (x0 + 30, cy - 6), (x0 + 30, cy + 6), (x0, cy + 6)],
                                      {"building": kind, "building:levels": levels})
                continue
            # A fixed jitter of up to 4 m, so the houses are not at a handful of reaches.
            x, y = cx + (37 * i + 11 * j) % 9 - 4, cy + (13 * i + 29 * j) % 9 - 4
            wid += 1
            buildings[wid] = ([(x - 6, y - 6), (x + 6, y - 6), (x + 6, y + 6), (x - 6, y + 6)], {"building": kind, "building:levels": levels})
    return buildings


def _write_osm(path: Path, buildings: dict[int, tuple[list[tuple[float, float]], dict[str, str]]], radius: float, spacing: float) -> Path:
    from xml.sax.saxutils import quoteattr

    from shapely import Point

    frame = LocalFrame.create(*MURCIA_CENTER)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<osm version="0.6" generator="onward-synthetic-fixture">',
             '  <meta osm_base="2026-10-18T00:00:00Z"/>']
    ways: list[str] = []
    node_id = 1000

    def node(x: float, y: float) -> int:
        nonlocal node_id
        node_id += 1
        p = frame.to_wgs84(Point(x, y))
        lines.append(f'  <node id="{node_id}" version="1" lat="{p.y:.8f}" lon="{p.x:.8f}"/>')
        return node_id

    def way(wid: int, refs: list[int], tags: dict[str, str]) -> None:
        ways.append(f'  <way id="{wid}" version="1">')
        ways.append("    " + "".join(f'<nd ref="{r}"/>' for r in refs))
        ways.extend(f"    <tag k={quoteattr(k)} v={quoteattr(v)}/>" for k, v in tags.items())
        ways.append("  </way>")

    for wid, (ring, tags) in buildings.items():
        refs = [node(x, y) for x, y in ring]
        way(wid, [*refs, refs[0]], tags)
    # Streets between the rows, every second gap; the touching row faces the one at y = 0.
    k = 0
    for y in np.arange(0.0, radius, 2 * spacing):
        for yy in sorted({y, -y}):
            k += 1
            way(500 + k, [node(-radius, float(yy)), node(radius, float(yy))], {"highway": "residential", "width": "8"})
    lines += ways
    lines.append("</osm>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _grid_inputs(root: Path, radius: float, spacing: float) -> tuple[Path, Path, Path]:
    from onward_generator.generation.__main__ import main as generate_main
    from onward_generator.geo.pipeline import run_geo_pipeline, write_outputs
    from onward_generator.geo.sources import register_local_source
    from onward_generator.heights import run_heights, write_height_outputs

    root.mkdir(parents=True, exist_ok=True)
    osm = _write_osm(root / "grid.osm", _grid(radius, spacing), radius, spacing)
    source = register_local_source(osm, license_info=SYNTHETIC_LICENSE, retrieved_at=FIXED_NOW,
                                   data_timestamp="2026-10-18T00:00:00Z", now=FIXED_NOW)
    frame = LocalFrame.create(*MURCIA_CENTER)
    ring = frame.to_wgs84(box(-radius, -radius, radius, radius)).exterior.coords
    aoi = AreaOfInterest(name=f"Synthetic grid r{radius:g}", polygon=tuple((float(x), float(y)) for x, y in ring), margin_m=0.0)
    scene, _ = write_outputs(run_geo_pipeline(source, aoi, origin=MURCIA_CENTER), root / "geo")
    heights, _ = write_height_outputs(run_heights(scene), root / "heights")
    base = root / "scene3d"
    assert generate_main(["--scene", str(scene), "--heights", str(heights), "--out", str(base), "--no-preview"]) == 0
    return scene, heights, base


@dataclass
class Grid:
    radius: float
    scene: Path
    heights: Path
    base: Path
    probe: Any
    target: int
    budget: Any

    @property
    def cap(self) -> float:
        return self.radius - RING


@pytest.fixture(scope="module", params=sorted(SPACING), ids=lambda r: f"r{r}")
def grid(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Grid]:
    radius = float(request.param)
    scene, heights, base = _grid_inputs(tmp_path_factory.mktemp(f"grid{request.param}"), radius, SPACING[request.param])
    generated, ref = load_base(base, scene, heights, GEN)
    probe = detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=radius - RING)
    target = probe.stats["tiers"][CORE]["visual_triangles"] // 2
    generated, ref = load_base(base, scene, heights, GEN)
    budget = detail_scene_budget(generated, ref, heights, scene, CFG, target_triangles=target, max_half_side_m=radius - RING)
    yield Grid(radius, scene, heights, base, probe, target, budget)


def test_budget_within_five_percent(grid: Grid) -> None:
    core = grid.budget.core
    achieved = grid.budget.stats["tiers"][CORE]["visual_triangles"]
    assert set(core) == {"half_side_m", "buildings"} | BUDGET_KEYS
    assert abs(achieved - grid.target) <= CORE_BUDGET_TOLERANCE * grid.target, (achieved, grid.target, core)
    assert core["achieved_triangles"] == achieved
    assert core["target_triangles"] == grid.target
    assert core["within_tolerance"] is True
    assert core["tolerance"] == 0.05 and core["mode"] == "budget" and core["max_half_side_m"] == grid.cap
    assert 50.0 <= core["half_side_m"] < grid.cap
    assert grid.budget.report()["phase_04_checks"]["automatic_checks_passed"] is True
    codes = {i.code for i in grid.budget.issues}
    assert codes.isdisjoint({"core_budget_missed", "core_budget_floor", "core_budget_ceiling"})
    # Half the triangles of the largest core take a smaller core, and some of its interiors.
    assert grid.budget.core["buildings"][CORE] < grid.probe.core["buildings"][CORE]
    assert grid.budget.stats["interiors"]
    print(f"r{grid.radius:g}: target {grid.target} h {core['half_side_m']:g} predicted {core['predicted_triangles']} achieved {achieved}")


def test_budget_keeps_buildings_whole(grid: Grid) -> None:
    half = grid.budget.core["half_side_m"]
    square = core_square(half)
    footprints = {o.record["source_ids"][0]: o for o in grid.budget.objects if o.type == "building"}
    feature = {f["id"]: shape(f["geometry"]) for f in json.loads(grid.scene.read_text(encoding="utf-8"))["features"] if f["id"] in footprints}
    for bid, o in footprints.items():
        assert o.record["tier"] == building_tier(feature[bid], square)
        assert (o.record["tier"] == CORE) is (core_reach(feature[bid]) < half)
    tier_of = {bid: o.record["tier"] for bid, o in footprints.items()}
    pieces = [o for o in grid.budget.objects if o.type in ("building_part", "landmark_part", "interior")]
    assert any(o.type == "interior" for o in pieces)
    for o in pieces:
        assert o.record["tier"] == tier_of[o.record["source_ids"][0]]
    # The core holds the buildings the square holds, and nothing in the ring has a collider.
    assert grid.budget.core["buildings"] == {CORE: sum(t == CORE for t in tier_of.values()), SCENIC: sum(t == SCENIC for t in tier_of.values())}
    for o in grid.budget.objects:
        if o.record.get("tier") == SCENIC:
            assert o.colliders == [] and o.glass_colliders == []
            if o.type == "building":
                assert o.record["collider"]["parts"] == 0
    tier = {o.id: o.record.get("tier") for o in grid.budget.objects}
    for node in grid.budget.collision_nodes():
        oid = node.extras["object_id"]
        # A core building across the edge keeps its whole collider; the ground colliders end at the edge.
        assert tier[oid] in (CORE, None), oid
        if node.extras["type"] == "ground_collider":
            for p in node.primitives:
                assert (np.abs(p.mesh.vertices[:, :2]) <= half + 1e-6).all(), node.name
    assert any(n.extras["type"] == "ground_collider" for n in grid.budget.collision_nodes())


def _detail_cli(grid: Grid, out: Path, *extra: str) -> int:
    return main(["detail", "--scene", str(grid.scene), "--heights", str(grid.heights), "--base", str(grid.base),
                 "--out", str(out), "--no-preview", *extra])


def _strip(manifest: dict[str, Any]) -> dict[str, Any]:
    return {**manifest, "core": {k: v for k, v in manifest["core"].items() if k not in BUDGET_KEYS}}


@pytest.mark.parametrize("grid", [150], indirect=True, ids=["r150"])
def test_budget_equals_fixed_at_solved_half_side(grid: Grid, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    budget_out, fixed_out, again_out = tmp_path / "budget", tmp_path / "fixed", tmp_path / "again"
    flags = ("--core-radius-m", f"{grid.cap:g}", "--core-target-triangles", str(grid.target))
    assert _detail_cli(grid, budget_out, *flags) == 0
    core = json.loads((budget_out / "scene_detail.json").read_text(encoding="utf-8"))["core"]
    assert core == grid.budget.core
    printed = capsys.readouterr().out
    assert (f"core half_side_m={core['half_side_m']:g} target={grid.target} predicted={core['predicted_triangles']} "
            f"achieved={core['achieved_triangles']} within_tolerance=True") in printed
    assert _detail_cli(grid, fixed_out, "--core-radius-m", repr(core["half_side_m"])) == 0
    assert "core half_side_m=" not in capsys.readouterr().out
    for name in ("scene_visual.glb", "scene_collision.glb"):
        assert (budget_out / name).read_bytes() == (fixed_out / name).read_bytes(), name
    budget_doc = json.loads((budget_out / "scene_detail.json").read_text(encoding="utf-8"))
    fixed_doc = json.loads((fixed_out / "scene_detail.json").read_text(encoding="utf-8"))
    assert set(fixed_doc["core"]) == {"half_side_m", "buildings"}
    assert _strip(budget_doc) == fixed_doc
    budget_report = json.loads((budget_out / "detail_report.json").read_text(encoding="utf-8"))
    fixed_report = json.loads((fixed_out / "detail_report.json").read_text(encoding="utf-8"))
    assert budget_report == fixed_report  # within the tolerance and unclamped: no budget issue
    # A second budget run writes the same bytes.
    assert _detail_cli(grid, again_out, *flags) == 0
    for name in ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json"):
        assert (budget_out / name).read_bytes() == (again_out / name).read_bytes(), name


@pytest.mark.parametrize("grid", [150], indirect=True, ids=["r150"])
def test_budget_feeds_bounds_and_layout(grid: Grid, tmp_path: Path) -> None:
    detail_out, playable, layout = tmp_path / "detail", tmp_path / "playable", tmp_path / "layout.json"
    assert _detail_cli(grid, detail_out, "--core-radius-m", f"{grid.cap:g}", "--core-target-triangles", str(grid.target)) == 0
    manifest = json.loads((detail_out / "scene_detail.json").read_text(encoding="utf-8"))
    assert main(["bounds", "--base", str(detail_out), "--out", str(playable)]) == 0
    report = json.loads((playable / "bounds_report.json").read_text(encoding="utf-8"))
    assert report["wall"]["core_half_side_m"] == manifest["core"]["half_side_m"]
    assert main(["perf", "layout", "--scene", str(grid.scene), "--heights", str(grid.heights), "--base", str(grid.base),
                 "--detail", str(playable), "--playable", "--out", str(layout)]) == 0
    doc = json.loads(layout.read_text(encoding="utf-8"))
    square = core_of(manifest)
    assert square is not None
    half = manifest["core"]["half_side_m"]
    for team in ("attackers", "defenders"):
        assert doc[team]["points_m"]
        assert all(abs(p["x"]) < half and abs(p["y"]) < half for p in doc[team]["points_m"])


@pytest.mark.parametrize("grid", [150], indirect=True, ids=["r150"])
def test_budget_target_above_total_reuses_probe(grid: Grid, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.detail.typology import load_typology_config

    calls: list[float | None] = []
    typology = load_typology_config()

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs.get("core_half_side_m"))
        # The options reach every run, the probe's and the kept one's.
        assert kwargs["places"] == () and kwargs["typology"] is typology
        return detail_scene(*args, **kwargs)

    monkeypatch.setattr(budget_module, "detail_scene", spy)
    generated, ref = load_base(grid.base, grid.scene, grid.heights, GEN)
    total = grid.probe.stats["tiers"][CORE]["visual_triangles"]
    d = detail_scene_budget(generated, ref, grid.heights, grid.scene, CFG, target_triangles=2 * total, max_half_side_m=grid.cap,
                            places=(), typology=typology)
    assert calls == [grid.cap]
    assert d.core["half_side_m"] == grid.cap
    assert d.core["achieved_triangles"] == total and d.core["within_tolerance"] is False
    ceiling = [i for i in d.issues if i.code.startswith("core_budget")]
    assert [(i.code, i.severity, i.object_id) for i in ceiling] == [("core_budget_ceiling", "info", None)]
    assert ceiling[0].message == (f"the largest core ({grid.cap:g} m) is predicted at {d.core['predicted_triangles']} core triangles, "
                                  f"under the target {2 * total}; core {grid.cap:g} m has {total}")
    assert d.report()["phase_04_checks"]["automatic_checks_passed"] is True
    # Two runs when the half-side moves: the probe and the kept run.
    calls.clear()
    generated, ref = load_base(grid.base, grid.scene, grid.heights, GEN)
    detail_scene_budget(generated, ref, grid.heights, grid.scene, CFG, target_triangles=grid.target, max_half_side_m=grid.cap,
                        places=(), typology=typology)
    assert calls == [grid.cap, grid.budget.core["half_side_m"]]


@pytest.mark.parametrize("grid", [150], indirect=True, ids=["r150"])
def test_budget_floor_issue(grid: Grid) -> None:
    generated, ref = load_base(grid.base, grid.scene, grid.heights, GEN)
    d = detail_scene_budget(generated, ref, grid.heights, grid.scene, CFG, target_triangles=1000, max_half_side_m=grid.cap)
    assert d.core["half_side_m"] == 50.0
    floor = [i for i in d.issues if i.code.startswith("core_budget")]
    assert [(i.code, i.severity) for i in floor] == [("core_budget_floor", "info")]
    assert floor[0].message.startswith(f"the smallest core (50 m) is predicted at {d.core['predicted_triangles']} core triangles, over the target 1000")
    assert d.core["within_tolerance"] is False
    assert d.report()["phase_04_checks"]["automatic_checks_passed"] is True


@pytest.fixture(scope="module")
def ring_inputs(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path]:
    from tests.conftest import _detail_inputs

    return _detail_inputs(tmp_path_factory.mktemp("ring_budget"), RING_BUILDINGS, RING_POIS)


def test_budget_misses_on_one_huge_building(tmp_path: Path) -> None:
    # Two buildings: a small house by the centre and one tall block at 60 m. No square lands near half of the two.
    from tests.conftest import _detail_inputs

    buildings = {
        311: ([(-10, 6), (0, 6), (0, 16), (-10, 16)], {"building": "house", "building:levels": "1"}),
        312: ([(55, 6), (90, 6), (90, 40), (55, 40)], {"building": "apartments", "building:levels": "12"}),
    }
    (tmp_path / "huge").mkdir()
    scene, heights, base = _detail_inputs(tmp_path / "huge", buildings, {})
    generated, ref = load_base(base, scene, heights, GEN)
    probe = detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=95.0)
    total = probe.stats["tiers"][CORE]["visual_triangles"]
    small = next(o for o in probe.objects if o.id == "building/osm-way-311")
    target = (total + sum(p.mesh.triangle_count for p in small.visual)) // 2
    generated, ref = load_base(base, scene, heights, GEN)
    d = detail_scene_budget(generated, ref, heights, scene, CFG, target_triangles=target, max_half_side_m=95.0)
    missed = [i for i in d.issues if i.code.startswith("core_budget")]
    assert [(i.code, i.severity, i.object_id) for i in missed] == [("core_budget_missed", "warning", None)]
    core = d.core
    assert core["within_tolerance"] is False
    assert missed[0].message == (f"core {core['half_side_m']:g} m has {core['achieved_triangles']} core triangles, predicted "
                                 f"{core['predicted_triangles']}, more than 5% off the target {target}")
    assert d.report()["phase_04_checks"]["automatic_checks_passed"] is True


def test_absent_target_keeps_the_fixed_core(ring_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = ring_inputs
    out = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(out), "--no-preview", "--core-radius-m", "50"]) == 0
    manifest = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))
    assert manifest["core"] == {"half_side_m": 50.0, "buildings": {"core": 4, "scenic": 2}}
    generated, ref = load_base(base, scene, heights, GEN)
    direct = detail_scene(generated, ref, heights, scene, CFG, core_half_side_m=50.0)
    assert manifest == json.loads(json.dumps(direct.manifest()))
    report = json.loads((out / "detail_report.json").read_text(encoding="utf-8"))
    assert report == json.loads(json.dumps(direct.report()))
    assert not [i for i in report["issues"] if i["code"].startswith("core_budget")]


@pytest.mark.parametrize(("flags", "code", "message"), [
    (("--core-target-triangles", "5000"), 1, "detail: --core-target-triangles needs --core-radius-m\n"),
    (("--core-radius-m", "100", "--core-target-triangles", "0"), 1,
     "detail: target_triangles must be an integer from 1000 to 2000000, got 0\n"),
    (("--core-radius-m", "100", "--core-target-triangles", "2000001"), 1,
     "detail: target_triangles must be an integer from 1000 to 2000000, got 2000001\n"),
    (("--core-radius-m", "40", "--core-target-triangles", "5000"), 1,
     "detail: core half-side must be between 50 and 2000 m, got 40\n"),
    (("--core-radius-m", "100", "--core-target-triangles", "1.5"), 2, None),
])
def test_detail_cli_refuses_a_bad_budget(ring_inputs: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                         flags: tuple[str, ...], code: int, message: str | None) -> None:
    scene, heights, base = ring_inputs
    out = tmp_path / "detail"
    capsys.readouterr()
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(out), "--no-preview", *flags]) == code
    if message is not None:
        assert capsys.readouterr().err == message
    assert not out.exists()


def test_detail_cli_help_describes_the_budget() -> None:
    from onward_generator.detail.__main__ import build_parser

    action = {a.dest: a for a in build_parser()._actions}["core_target_triangles"]
    assert action.type is int and action.default is None
    assert action.help == ("budget mode (ADR-017 amendment): solve the core half-side for this many core visual triangles, "
                           "1000 to 2000000; --core-radius-m is then the largest half-side")


def test_detail_cli_passes_places_and_typology_to_the_budget(ring_inputs: tuple[Path, Path, Path], tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.detail.__main__ as detail_cli
    from onward_generator.detail.scene import DetailError

    seen: dict[str, Any] = {}

    def fake(*args: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        raise DetailError("stop here")

    monkeypatch.setattr(detail_cli, "detail_scene_budget", fake)
    scene, heights, base = ring_inputs
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(tmp_path / "d"),
                 "--no-preview", "--core-radius-m", "60", "--core-target-triangles", "5000"]) == 1
    assert seen["places"] == [] and seen["typology"] is not None
    assert (seen["target_triangles"], seen["max_half_side_m"]) == (5000, 60.0)
    assert seen["mid_ring_m"] is None
    # Plan D3: the ring reaches the budget solve too.
    seen.clear()
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(tmp_path / "d"),
                 "--no-preview", "--core-radius-m", "60", "--core-target-triangles", "5000", "--mid-ring-m", "20"]) == 1
    assert seen["mid_ring_m"] == 20.0 and seen["target_triangles"] == 5000


@pytest.mark.parametrize("grid", [150], indirect=True, ids=["r150"])
def test_a_mid_ring_follows_the_solved_core_and_leaves_the_budget_alone(grid: Grid) -> None:
    """Plan D3: the ring is drawn around the half-side the budget solved; N, the solve and the core are those without it."""
    from onward_generator.tiers import MID, mid_square, ring_tier

    generated, ref = load_base(grid.base, grid.scene, grid.heights, GEN)
    ringed = detail_scene_budget(generated, ref, grid.heights, grid.scene, CFG, target_triangles=grid.target,
                                 max_half_side_m=grid.cap, mid_ring_m=30.0)
    plain = grid.budget
    half = plain.core["half_side_m"]
    assert half < grid.cap  # the solved core is smaller than map.json's largest half-side
    assert {k: v for k, v in ringed.core.items() if k not in ("mid_ring_m", "buildings")} == {
        k: v for k, v in plain.core.items() if k != "buildings"}
    assert ringed.core["mid_ring_m"] == 30.0
    assert ringed.core["buildings"][CORE] == plain.core["buildings"][CORE]
    assert ringed.core["buildings"][MID] + ringed.core["buildings"][SCENIC] == plain.core["buildings"][SCENIC]
    assert ringed.stats["tiers"][CORE] == plain.stats["tiers"][CORE]
    assert ringed.core["achieved_triangles"] == plain.core["achieved_triangles"]
    core_ids = sorted(o.id for o in ringed.objects if o.record.get("tier") == CORE)
    assert core_ids == sorted(o.id for o in plain.objects if o.record.get("tier") == CORE)
    feature = {f["id"]: shape(f["geometry"]) for f in json.loads(grid.scene.read_text(encoding="utf-8"))["features"]}
    mid = [o for o in ringed.objects if o.type == "building" and o.record["tier"] == MID]
    assert mid
    for o in mid:
        bid = o.record["source_ids"][0]
        assert ring_tier(feature[bid], core_square(half), mid_square(half, 30.0)) == MID
        assert core_reach(feature[bid]) >= half
        assert o.colliders == [] and o.record["visual"]["triangles"] <= 150
    # Measured against the solved core, not map.json's: some mid buildings lie inside the largest half-side.
    assert any(core_reach(feature[o.record["source_ids"][0]]) < grid.cap for o in mid)
