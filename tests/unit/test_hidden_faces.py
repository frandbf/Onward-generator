"""Plan P0: a core wall facing no ground a player reaches is laid out plain (ADR-017 amendment of 12 November 2026)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from shapely.geometry import LineString, Point, Polygon, box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Hole, Layout, Segment, Storeys, entrance, is_flat, layout_segment
from onward_generator.detail.reach import AGENT_RADIUS_M, HIDDEN_FACE_M, PROBE_OUT_M, hidden_face, reached_ground
from onward_generator.generation import load_generation_config
from onward_generator.generation.surfaces import LinearWay, build_partition

CFG = load_detail_config()
TEMPLATE = CFG.template_for("residential_multi", False)
SEG = Segment(0, 0, 0, (0.0, 0.0), (12.0, 0.0), True)  # outward normal -y


def test_a_wall_is_hidden_past_six_metres_of_the_reached_ground() -> None:
    assert (HIDDEN_FACE_M, PROBE_OUT_M, AGENT_RADIUS_M) == (6.0, 1.0, 0.35)
    # The probe stands 1 m in front of the wall's middle, at (6, -1).
    near = box(0, -1 - HIDDEN_FACE_M + 0.01, 12, -1 - HIDDEN_FACE_M + 0.02)
    far = box(0, -1 - HIDDEN_FACE_M - 0.02, 12, -1 - HIDDEN_FACE_M - 0.01)
    assert not hidden_face(SEG, near)
    assert hidden_face(SEG, far)
    assert not hidden_face(SEG, Point(6.0, -1.0).buffer(0.1))
    # The probe follows the outward normal: the same wall turned round probes (6, 1), 1.9 m from that ground.
    back = Segment(0, 0, 0, (12.0, 0.0), (0.0, 0.0), True)  # outward normal +y
    ground = Point(6.0, -1.0).buffer(0.1)
    assert ground.distance(Point(6.0, 1.0)) == pytest.approx(1.9, abs=1e-3)
    assert not hidden_face(back, ground)
    assert hidden_face(back, Point(6.0, -6.5).buffer(0.1))  # 7.4 m from (6, 1)
    assert not hidden_face(SEG, Point(6.0, -6.5).buffer(0.1))  # 5.4 m from (6, -1)
    # Exactly HIDDEN_FACE_M away is still seen.
    assert not hidden_face(SEG, box(0, -2 - HIDDEN_FACE_M, 12, -1 - HIDDEN_FACE_M))
    # The probe is the wall's middle: ground 5.95 m below (6, -1) is seen, though 6.28 m from (4, -1).
    assert not hidden_face(SEG, Point(6.0, -6.95))


def test_the_probe_stands_probe_out_m_in_front_of_the_wall(monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.detail import reach

    monkeypatch.setattr(reach, "PROBE_OUT_M", 2.0)
    # The probe now stands at (6, -2): ground 6.5 m below it is hidden, ground 5.5 m below it is not.
    assert hidden_face(SEG, Point(6.0, -8.5))
    assert not hidden_face(SEG, Point(6.0, -7.5))


def test_the_reached_ground_is_the_largest_island_and_leaves_the_carriageway_out() -> None:
    config = load_generation_config()
    street = LinearWay("street", "vehicular", LineString([(-60, 0), (60, 0)]), 6.0, "config_default")
    # A closed block south of the street: its courtyard is a small island of its own.
    block = Polygon([(-20, -40), (20, -40), (20, -10), (-20, -10)], [[(-8, -30), (8, -30), (8, -20), (-8, -20)]])
    partition = build_partition(box(-50, -50, 50, 50), [block], [street], [], config)
    base = type("Base", (), {"partition": partition})()
    reached = reached_ground(base)  # type: ignore[arg-type]
    assert reached.contains(Point(0, -5))  # the ground between the street and the block
    assert not reached.contains(Point(0, -25))  # the closed courtyard: an island of its own
    assert not reached.contains(Point(0, 0))  # the carriageway
    # Shrunk by the agent radius off the block's north face at y = -10.
    assert not reached.contains(Point(0, -9.8)) and reached.contains(Point(0, -9.5))


def test_ground_too_narrow_for_the_agent_reaches_nothing() -> None:
    from types import SimpleNamespace

    from onward_generator.generation.surfaces import SurfaceClass

    strip = box(0, 0, 20, 2 * AGENT_RADIUS_M - 0.1)  # a sidewalk the agent cannot stand on
    surfaces = {SurfaceClass.SIDEWALK: SimpleNamespace(geometry=strip), SurfaceClass.CARRIAGEWAY: SimpleNamespace(geometry=box(0, 1, 20, 9))}
    partition = SimpleNamespace(surfaces=surfaces, crossings=Polygon(), buildings=Polygon())
    reached = reached_ground(SimpleNamespace(partition=partition))  # type: ignore[arg-type]
    assert reached.is_empty


def _layout(hidden: bool, ground: str = "shopfront", door: bool = False) -> Layout:
    layout = Layout()
    kind, opening = entrance(False, ground == "shopfront", CFG)
    template = replace(TEMPLATE, balcony=replace(TEMPLATE.balcony, share=1.0)) if TEMPLATE.balcony else TEMPLATE
    layout_segment(SEG, Storeys(4, 3.0, "heights_levels"), template, ground, opening if door else None, kind, CFG.recess_depth_m,
                   "b1", box(0, 0, 12, 10), Polygon(), CFG, layout, hidden=hidden)
    return layout


def test_a_hidden_wall_has_flat_windows_and_no_balcony_or_shopfront() -> None:
    seen = _layout(False)
    plain = _layout(True)
    assert {h.kind for h in seen.holes} >= {"shopfront", "balcony_door"} and seen.balconies
    assert not any(h.plain for h in seen.holes)
    assert {h.kind for h in plain.holes} <= {"window", "narrow_window"} and not plain.balconies
    assert all(h.plain for h in plain.holes)
    # Every window of the hidden wall is flat, the ground floor's too; the seen wall keeps its relief below relief_storeys.
    assert all(is_flat(h, CFG.relief_storeys) for h in plain.holes)
    assert not all(is_flat(h, CFG.relief_storeys) for h in seen.holes if h.storey < CFG.relief_storeys)
    # The main door stays, even on a hidden wall.
    assert any(h.kind == "shop_door" for h in _layout(True, door=True).holes)


@pytest.mark.parametrize("kind", ["door", "shop_door", "shopfront", "interior_door"])
def test_a_plain_hole_that_is_not_a_window_keeps_its_recess(kind: str) -> None:
    hole = Hole(kind, 0, 2.0, 0.0, 1.1, 2.3, CFG.recess_depth_m, "glass_dark", 0, 0, plain=True)
    assert not is_flat(hole, CFG.relief_storeys)
    window = Hole("window", 0, 2.0, 0.9, 1.1, 1.5, CFG.recess_depth_m, "glass_dark", 0, 0, plain=True)
    assert is_flat(window, CFG.relief_storeys) and not is_flat(replace(window, plain=False), CFG.relief_storeys)
    assert not is_flat(replace(window, back="glass_view"), CFG.relief_storeys)  # a see-through pane keeps its hole
