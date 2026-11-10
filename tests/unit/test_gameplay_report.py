"""The gameplay report document on a synthetic corridor (ADR-032), and the collision reader."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from shapely import box
from shapely.geometry import Polygon

from onward_generator.gameplay.headline import REPORT_FILE, headline_text
from onward_generator.gameplay.metrics import Collider, GameplayError, collider_kind
from onward_generator.gameplay.report import (
    LAYOUT_SCHEMA,
    LAYOUT_VERSION,
    REPORT_SCHEMA,
    REPORT_VERSION,
    _r,
    build_report,
    gameplay_report,
    parameters,
    proposed_checks,
    read_colliders,
    report_text,
)
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh

CORRIDOR = [box(-1, -1, 61, 1)]


def _layout(**blocks: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "schema": LAYOUT_SCHEMA,
        "schema_version": LAYOUT_VERSION,
        "attackers": {"centre_m": [0.0, 0.0]},
        "defenders": {"centre_m": [60.0, 0.0]},
        "attackers_b": {"centre_m": [2.0, 0.0]},
        "defenders_b": {"centre_m": [58.0, 0.0]},
        "uplink_m": [30.0, 0.0],
    }
    for key, value in blocks.items():
        if value is None:
            doc.pop(key)
        else:
            doc[key] = value
    return doc


def _prop(bounds: tuple[float, float, float, float], height: float, name: str = "overgrow/collider/prop/x/1") -> Collider:
    return Collider(name, collider_kind(name), bounds, 0.0, height)


# Spans the 25 m boundary, 1.2 m off the route line: within reach of the first two stretches.
BARREL = _prop((24.0, 1.2, 26.0, 1.8), 1.0)


def test_the_report_has_its_schema_provenance_and_parameters() -> None:
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout())
    assert (report["schema"], report["schema_version"]) == (REPORT_SCHEMA, REPORT_VERSION) == ("onward_generator.gameplay_report", "0.1.0")
    assert report["provenance"] == "estimated"
    assert report["note"].startswith("Estimated on a 2D grid over the navmesh islands of perf layout")
    assert report["note"].endswith("never block a stage or an export (ADR-032).")
    assert report["parameters"] == parameters() == {
        "grid_m": 2.0, "snap_m": 2.0, "max_routes": 6, "route_penalty": 3, "max_overlap": 0.5, "corridor_m": 8.0,
        "sample_step_m": 5.0, "stretch_m": 25.0, "cover_reach_m": 1.5, "sight_block_height_m": 1.2,
        "ground_contact_m": 0.5, "ray_count": 72, "ray_cap_m": 300.0, "run_speed_m_s": 4.0,
    }
    assert report["graph"] == {"nodes": 31, "edges": 30}
    assert isinstance(report["graph"]["edges"], int)
    assert report["status"] == "ok" and report["problems"] == []
    assert REPORT_FILE == "gameplay_report.json"


def test_a_corridor_has_one_route_per_pair_and_three_stretches() -> None:
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout())
    assert [(pair["from"], pair["to"]) for pair in report["pairs"]] == [("attackers", "defenders"), ("attackers_b", "defenders_b")]
    main = report["pairs"][0]
    assert len(main["routes"]) == 1 and main["overlaps"] == []
    route = main["routes"][0]
    assert (route["index"], route["attempt"]) == (0, 1)
    assert route["length_m"] == 60.0 and route["points_m"] == [[0.0, 0.0], [60.0, 0.0]]
    assert [(s["start_m"], s["end_m"], s["cover"], s["cover_per_100m"]) for s in route["stretches"]] == [
        (0.0, 25.0, 1, 4.0), (25.0, 50.0, 1, 4.0), (50.0, 60.0, 0, 0.0),
    ]
    assert route["samples"] == 13
    # Only the sample at 25 m has the barrel within reach (1.2 m); at 20 and 30 m it is 4.2 m away.
    assert route["open_samples"] == 12
    assert route["stretches"][2]["longest_open_sight_m"] == 300.0
    assert report["headline"]["cover_per_100m_min_stretch"] == 0.0
    assert report["headline"]["max_overlap_pct"] == 0.0


def test_a_ring_reports_the_overlap_of_its_two_routes() -> None:
    ring = [box(-1, -1, 21, 15).difference(box(1, 1, 19, 13))]
    layout = _layout(defenders={"centre_m": [20.0, 14.0]}, attackers_b={"centre_m": [0.0, 0.0]},
                     defenders_b={"centre_m": [20.0, 14.0]}, uplink_m=[20.0, 0.0])
    report = build_report(ring, Polygon(), [BARREL], layout)
    main = report["pairs"][0]
    assert [(route["index"], route["attempt"], route["length_m"]) for route in main["routes"]] == [(0, 1, 34.0), (1, 2, 34.0)]
    # 8 m by each spawn of a 34 m route lies within the 8 m corridor of the other route.
    assert main["overlaps"] == [{"routes": [0, 1], "overlap": 0.4706}]
    assert report["headline"]["max_overlap_pct"] == 47.06
    assert report["headline"]["distinct_routes"] == 2


def test_a_route_with_cover_all_along_has_no_open_sightline() -> None:
    hedge = _prop((-1.0, 1.2, 61.0, 1.8), 1.0)
    report = build_report(CORRIDOR, Polygon(), [hedge], _layout())
    assert report["sightline"] is None
    assert report["headline"]["longest_open_sight_m"] == 0.0
    assert report["pairs"][0]["routes"][0]["open_samples"] == 0
    assert {s["longest_open_sight_m"] for s in report["pairs"][0]["routes"][0]["stretches"]} == {None}


def test_the_balance_reads_the_nearer_block_of_each_team() -> None:
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(attackers_b={"centre_m": [10.0, 0.0]}))
    assert report["balance"] == {
        "contact_point": "uplink_m", "attackers_m": 20.0, "defenders_m": 28.0,
        "attackers_s": 5.0, "defenders_s": 7.0, "diff_pct": round(8 / 28 * 100, 2),
    }
    assert report["headline"]["contact_diff_pct"] == 28.57
    assert report["proposed_checks"]["contact_diff_pct"]["result"] == "fail"


def test_the_longest_open_sightline_has_both_ends() -> None:
    walls = box(-5, -10, 70, -2).union(box(-5, 2, 70, 10))
    wall = _prop((-2, -20, 62, 20), 12.0, "envelope/perimeter_wall#collider")
    report = build_report(CORRIDOR, walls, [BARREL, wall], _layout())
    # Both ends of the corridor see 62 m down it; the tie goes to the larger start.
    assert report["sightline"] == {"length_m": 62.0, "from_m": [60.0, 0.0], "to_m": [-2.0, 0.0]}
    assert report["headline"]["longest_open_sight_m"] == 62.0
    assert report["proposed_checks"]["longest_open_sight_m"]["result"] == "fail"
    stretches = report["pairs"][0]["routes"][0]["stretches"]
    # From 25 to 50 m the best is the sample at 50 m looking west to the wall at -2 m.
    assert [s["longest_open_sight_m"] for s in stretches] == [62.0, 52.0, 62.0]


def test_every_check_failing_still_gives_a_report_and_cover_is_never_judged() -> None:
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(attackers_b={"centre_m": [10.0, 0.0]}))
    checks = report["proposed_checks"]
    assert checks["distinct_routes"]["result"] == "fail"
    assert checks["longest_open_sight_m"]["result"] == "fail"
    assert checks["contact_diff_pct"]["result"] == "fail"
    assert checks["cover_per_100m"]["result"] is None and checks["cover_per_100m"]["value"] == 0.0
    assert checks["blocking"] is False


def test_proposed_checks_are_inclusive() -> None:
    def results(routes: int | None, sight: float | None, diff: float | None) -> tuple[Any, ...]:
        checks = proposed_checks({
            "distinct_routes": routes, "longest_open_sight_m": sight, "contact_diff_pct": diff,
            "max_overlap_pct": None, "cover_per_100m_min_stretch": 3.0,
        })
        return tuple(checks[key]["result"] for key in ("distinct_routes", "longest_open_sight_m", "contact_diff_pct", "cover_per_100m"))

    assert results(3, 60.0, 15.0) == ("pass", "pass", "pass", None)
    assert results(2, 60.01, 15.01) == ("fail", "fail", "fail", None)
    assert results(None, None, None) == (None, None, None, None)
    assert proposed_checks({
        "distinct_routes": 3, "longest_open_sight_m": 61.0, "contact_diff_pct": 2.0,
        "max_overlap_pct": 10.0, "cover_per_100m_min_stretch": 1.5,
    }) == {
        "distinct_routes": {"proposal": ">= 3 routes per spawn pair, no two sharing more than 50%", "value": 3, "result": "pass"},
        "longest_open_sight_m": {"proposal": "<= 60 m, adjustable per mode", "value": 61.0, "result": "fail"},
        "contact_diff_pct": {"proposal": "<= 15 %", "value": 2.0, "result": "pass"},
        "cover_per_100m": {"proposal": "N cover colliders per 100 m of route, fixed after the first measurement", "value": 1.5, "result": None},
        "blocking": False,
    }


def test_empty_collision_is_no_cover_and_still_measured() -> None:
    report = build_report(CORRIDOR, Polygon(), [], _layout())
    assert report["status"] == "no_cover"
    assert report["problems"] == ["no_cover: the collision GLB has no cover collider"]
    assert report["colliders"]["cover"] == 0
    assert report["headline"]["distinct_routes"] == 1
    assert report["headline"]["cover_per_100m_min_stretch"] == 0.0
    assert report["headline"]["longest_open_sight_m"] == 300.0


def _nulls(report: dict[str, Any]) -> None:
    assert report["pairs"] == [] and report["sightline"] is None and report["balance"] is None
    assert report["headline"] == dict.fromkeys(
        ("distinct_routes", "max_overlap_pct", "longest_open_sight_m", "cover_per_100m_min_stretch", "contact_diff_pct"),
    )
    assert all(check["result"] is None for key, check in report["proposed_checks"].items() if key != "blocking")


def test_a_missing_block_is_a_status_with_null_metrics() -> None:
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(defenders=None))
    assert report["status"] == "spawn_missing" and report["problems"] == ["spawn_missing: defenders"]
    _nulls(report)
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(uplink_m=None))
    assert report["problems"] == ["spawn_missing: uplink_m"]
    _nulls(report)
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(attackers={"centre_m": [1.0]}))
    assert report["problems"] == ["spawn_missing: attackers"]
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(defenders=None, defenders_b=None))
    assert report["problems"] == ["spawn_missing: defenders", "spawn_missing: defenders_b"]
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(attackers={"centre_m": [0.0, 9.0]}, defenders={"centre_m": [60.0, 9.0]}))
    assert report["problems"] == ["spawn_off_navmesh: attackers at 0.0, 9.0", "spawn_off_navmesh: defenders at 60.0, 9.0"]


def test_a_spawn_off_the_navmesh_is_a_status_with_null_metrics() -> None:
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(attackers={"centre_m": [0.0, 10.0]}))
    assert report["status"] == "spawn_off_navmesh"
    assert report["problems"] == ["spawn_off_navmesh: attackers at 0.0, 10.0"]
    _nulls(report)
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(uplink_m=[30.0, 9.0]))
    assert report["status"] == "uplink_off_navmesh"
    assert report["problems"] == ["uplink_off_navmesh: 30.0, 9.0"]
    _nulls(report)


def test_no_path_between_the_spawns_is_a_status_with_null_metrics() -> None:
    islands = [box(-1, -1, 29, 1), box(31, -1, 61, 1)]
    report = build_report(islands, Polygon(), [BARREL], _layout(uplink_m=[28.0, 0.0]))
    assert report["status"] == "no_path"
    assert report["problems"] == [
        "no_path: attackers to defenders", "no_path: attackers_b to defenders_b", "no_path: defenders to uplink_m",
    ]
    _nulls(report)


def test_a_missing_pair_does_not_hide_the_next_one() -> None:
    islands = [box(-1, -1, 29, 1), box(31, -1, 61, 1)]
    report = build_report(islands, Polygon(), [BARREL], _layout(attackers=None, uplink_m=[28.0, 0.0]))
    assert report["problems"] == [
        "spawn_missing: attackers", "no_path: attackers_b to defenders_b", "no_path: defenders to uplink_m",
    ]


def test_spawns_on_one_node_give_a_route_of_no_length() -> None:
    near = {"centre_m": [0.4, 0.0]}
    report = build_report(CORRIDOR, Polygon(), [BARREL], _layout(defenders=near, attackers_b={"centre_m": [0.0, 0.0]}, defenders_b=near))
    route = report["pairs"][0]["routes"][0]
    assert (route["length_m"], route["points_m"], route["samples"], route["stretches"]) == (0.0, [[0.0, 0.0]], 1, [])
    assert report["headline"]["cover_per_100m_min_stretch"] is None


def test_a_layout_that_is_not_the_playable_one_is_refused() -> None:
    with pytest.raises(GameplayError, match="^the gameplay report reads a onward_generator.gameplay_layout 0.3.0 layout, "
                                            "not 'onward_generator.perf_layout' '0.3.0'$"):
        build_report(CORRIDOR, Polygon(), [], _layout(schema="onward_generator.perf_layout"))
    with pytest.raises(GameplayError, match="not 'onward_generator.gameplay_layout' '0.2.0'$"):
        build_report(CORRIDOR, Polygon(), [], _layout(schema_version="0.2.0"))


def test_two_reports_are_byte_identical() -> None:
    walls = box(-5, 3, 70, 10)
    first = report_text(build_report(CORRIDOR, walls, [BARREL], _layout()))
    second = report_text(build_report(CORRIDOR, walls, [BARREL], _layout()))
    assert first == second
    assert first.endswith("}\n") and json.loads(first)["schema"] == REPORT_SCHEMA


def test_the_collider_counts_say_what_was_left_out() -> None:
    colliders = [
        BARREL,
        _prop((0, 0, 1, 1), 2.0, "overgrow/collider/prop/y/2"),
        _prop((-100, -100, 100, 100), 0.0, "collider/walkable#collider"),
        _prop((0, 0, 10, 10), 9.0, "building/osm-way-1#collider"),
        _prop((-50, -50, 50, 50), 12.0, "envelope/perimeter_wall#collider"),
    ]
    report = build_report(CORRIDOR, Polygon(), colliders, _layout())
    assert report["colliders"] == {"cover": 2, "sight_blockers": 2, "ignored_ground": 1, "ignored_buildings": 1}


def test_report_text_is_sorted_indented_and_keeps_unicode() -> None:
    assert report_text({"b": "é", "a": [1]}) == '{\n  "a": [\n    1\n  ],\n  "b": "é"\n}\n'


def test_values_are_rounded_to_centimetres() -> None:
    assert _r(1.23456) == 1.23 and _r(2) == 2.0


def test_gameplay_report_reads_the_islands_inside_the_core(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import onward_generator.perf.layout as layout_module

    seen: list[Any] = []
    core = box(-1, -1, 61, 1)

    def islands(base: Any, inside: Any) -> list[Any]:
        seen.append(inside)
        return [inside]

    monkeypatch.setattr(layout_module, "nav_islands", islands)
    monkeypatch.setattr(layout_module, "playable_area", lambda manifest: core if manifest == {"core": True} else None)
    path = _glb(tmp_path / "scene_collision.glb", [Node("overgrow/collider/prop/box/1", (Primitive(_cube((2.0, 1.0, 1.5)), "collider"),))])

    base = SimpleNamespace(partition=SimpleNamespace(buildings=Polygon()))
    report = gameplay_report(base, {"core": True}, _layout(), path)
    assert seen == [core]
    assert report["graph"] == {"nodes": 31, "edges": 30}


def test_headline_text() -> None:
    report = {"headline": {"distinct_routes": 3, "longest_open_sight_m": 42.5, "contact_diff_pct": 7.1}}
    assert headline_text(report) == "routes 3  sight 42.5 m  balance 7.1 %"
    assert headline_text({"headline": {"distinct_routes": None, "longest_open_sight_m": "x", "contact_diff_pct": True}}) == "routes -  sight -  balance -"
    assert headline_text({}) == "routes -  sight -  balance -"


# ------------------------------------------------------------------------------------------------ collision GLB


def _cube(size: tuple[float, float, float]) -> Mesh:
    sx, sy, sz = size
    vertices = np.array([[x, y, z] for z in (0.0, sz) for y in (0.0, sy) for x in (0.0, sx)])
    triangles = np.array([[0, 1, 2], [1, 3, 2], [4, 6, 5], [5, 6, 7]], dtype=np.int64)
    return Mesh(vertices, triangles)


def _glb(path: Path, nodes: list[Node]) -> Path:
    path.write_bytes(build_glb(nodes, {"collider": Material((0.5, 0.5, 0.5, 1.0), 1.0)}))
    return path


def test_read_colliders_maps_the_gltf_axes_to_local_east_and_north(tmp_path: Path) -> None:
    yaw = np.eye(4)
    yaw[:3, :3] = [[0.0, -1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]  # 90 degrees, east turns north
    yaw[:3, 3] = [10.0, 5.0, 0.0]  # then 10 m east and 5 m north
    path = _glb(tmp_path / "scene_collision.glb", [
        Node("overgrow/collider/prop/box/1", (Primitive(_cube((2.0, 1.0, 1.5)), "collider"),), {"type": "collision"}, yaw),
        Node("collider/other#collider", (Primitive(_cube((4.0, 4.0, 0.0)), "collider"),), {"type": "ground_collider"}),
    ])
    colliders = read_colliders(path)
    assert [c.name for c in colliders] == ["collider/other#collider", "overgrow/collider/prop/box/1"]
    ground, prop = colliders
    assert ground.kind == "ground"
    assert prop.kind == "prop"
    assert prop.bounds == pytest.approx((9.0, 5.0, 10.0, 7.0))
    assert (prop.min_z, prop.max_z) == pytest.approx((0.0, 1.5))


def test_read_colliders_skips_a_node_without_vertices(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = Node("overgrow/collider/prop/empty/0", ())
    solid = Node("overgrow/collider/prop/box/1", (Primitive(_cube((1.0, 1.0, 1.0)), "collider"),))
    monkeypatch.setattr("onward_generator.gameplay.report.nodes_from_glb", lambda data: ([empty, solid], {}))
    path = tmp_path / "scene_collision.glb"
    path.write_bytes(b"read by the patched reader")
    assert [c.name for c in read_colliders(path)] == ["overgrow/collider/prop/box/1"]


def test_read_colliders_refuses_a_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "scene_collision.glb"
    path.write_bytes(b"not a glb at all, just text that is long enough")
    with pytest.raises(ValueError, match="not a readable collision GLB"):
        read_colliders(path)
    with pytest.raises(FileNotFoundError):
        read_colliders(tmp_path / "missing.glb")
