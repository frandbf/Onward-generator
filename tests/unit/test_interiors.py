"""Interior windows open both ways, with one glass pane. The fixture origin is MURCIA_CENTER."""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely import Polygon

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import Hole, Layout, Segment, stable_unit
from onward_generator.detail.interiors import (
    GLASS_VIEW,
    _inner_walls,
    build_interior,
    glass_penetrations,
    pierce_room_windows,
    plan_interior,
    room_window_cuts,
)
from onward_generator.detail.modules import recess
from onward_generator.detail.scene import interior_quota
from onward_generator.dress.atlas import decode_png_rgba
from onward_generator.generation.mesh import Mesh, closed_manifold_defects
from onward_generator.materials.openings import GLASS_ALPHA, boarded_rgba, window_png, window_rgba
from tests.conftest import MURCIA_CENTER


def _square(size: float = 20.0) -> tuple[Polygon, Polygon, list[Segment]]:
    footprint = Polygon([(0.0, 0.0), (size, 0.0), (size, size), (0.0, size)])
    inner = footprint.buffer(-0.3, join_style="mitre", mitre_limit=2.0)
    south = Segment(0, 0, 0, (0.0, 0.0), (size, 0.0), True)
    return footprint, inner, [south]


def _window(s0: float, v0: float) -> Hole:
    return Hole("window", 0, s0, v0, 1.2, 1.4, 0.15, "glass_dark", 0, 0)


def _area(mesh) -> float:
    triangles = mesh.vertices[mesh.triangles]
    return float(0.5 * np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]), axis=1).sum())


def _inside(mesh: Mesh, p: np.ndarray) -> bool:
    """Ray-parity point-in-closed-mesh test. Same ray as the detail integration test."""
    direction = np.array([1.0, 0.1234, 0.0567])
    v = mesh.vertices[mesh.triangles]
    e1, e2 = v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]
    h = np.cross(direction, e2)
    a = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(a) > 1e-12
    f = np.where(ok, 1.0 / np.where(ok, a, 1.0), 0.0)
    s = p - v[:, 0]
    u = f * np.einsum("ij,ij->i", s, h)
    q = np.cross(s, e1)
    w = f * (q @ direction)
    t = f * np.einsum("ij,ij->i", e2, q)
    hits = ok & (u >= 0) & (w >= 0) & (u + w <= 1) & (t > 1e-9)
    return bool(hits.sum() % 2)


def test_see_through_window_opens_the_wall_and_keeps_a_pane() -> None:
    """A shot through the pane meets glass, not concrete, from either side. The sill and the pier stay solid."""
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    cfg = load_detail_config()
    size = 24.0
    footprint = Polygon([(0.0, 0.0), (size, 0.0), (size, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (size, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plan = plan_interior("block-window", footprint, door, segment, 18.0, 3.0, 6, cfg)
    assert not isinstance(plan, str), plan
    window = Hole("window", 0, 4.0, 0.9, 1.2, 1.4, 0.15, "glass_dark", 0, 0)
    shop = Hole("shopfront", 0, 18.0, 0.0, 2.4, 2.5, 0.15, "glass_dark", 1, 0)
    high = Hole("window", 0, 7.0, 16.0, 1.2, 1.4, 0.15, "glass_dark", 2, 5)
    layout = Layout(holes=[window, shop, high])
    thickness = cfg.interiors.wall_thickness_m
    ceiling = plan.floors * plan.storey_m
    opened, omitted = pierce_room_windows(layout, [segment], plan.inner, footprint, thickness, ceiling)
    assert opened == 2 and omitted == 0
    assert high.v0 >= ceiling
    built = build_interior(
        plan, 18.0, 18.0, footprint, cfg,
        room_window_cuts(layout, [segment]), glass_penetrations(layout, [segment]),
    )
    assert built.defects == 0
    assert built.spec["glass_panes"] == 2
    assert len(built.glass_colliders) == 2
    concrete = [mesh for _name, mesh in built.colliders]

    def at(s: float, z: float, depth: float) -> np.ndarray:
        return segment.point(s, z, depth)

    window_s, window_z = 4.6, 1.6
    for depth in (0.02, thickness / 2, thickness - 0.02):
        point = at(window_s, window_z, depth)
        assert not any(_inside(mesh, point) for mesh in concrete)
    centre = at(window_s, window_z, thickness / 2)
    assert sum(_inside(mesh, centre) for mesh in built.glass_colliders) == 1
    # Just outside each face of the wall: the hole is open to the street and to the room.
    # A deeper room point can meet the stair, which is a separate solid.
    outside = at(window_s, window_z, -0.04)
    just_inside = at(window_s, window_z, thickness + 0.02)
    for point in (outside, just_inside):
        assert not any(_inside(mesh, point) for mesh in concrete)
        assert not any(_inside(mesh, point) for mesh in built.glass_colliders)
    sill = at(window_s, 0.4, thickness / 2)
    pier = at(4.0 + 1.2 + 0.35, window_z, thickness / 2)
    assert any(_inside(mesh, sill) for mesh in concrete) and not any(_inside(mesh, sill) for mesh in built.glass_colliders)
    assert any(_inside(mesh, pier) for mesh in concrete) and not any(_inside(mesh, pier) for mesh in built.glass_colliders)
    shop_point = at(19.2, 1.2, thickness / 2)
    assert not any(_inside(mesh, shop_point) for mesh in concrete)
    assert sum(_inside(mesh, shop_point) for mesh in built.glass_colliders) == 1
    door_point = at(11.6, 1.0, thickness / 2)
    assert not any(_inside(mesh, door_point) for mesh in [*concrete, *built.glass_colliders])
    blocked = at(7.6, 16.7, thickness / 2)
    assert any(_inside(mesh, blocked) for mesh in concrete)
    assert not any(_inside(mesh, blocked) for mesh in built.glass_colliders)


def test_room_window_is_one_pane_and_a_hole() -> None:
    assert MURCIA_CENTER == (-1.12863, 37.98407)
    footprint, inner, segments = _square()
    layout = Layout(holes=[_window(4.0, 1.0), _window(8.0, 6.2)])
    opened, omitted = pierce_room_windows(layout, segments, inner, footprint, 0.3, 6.0)
    assert (opened, omitted) == (1, 0)
    assert layout.holes[0].back == GLASS_VIEW and layout.holes[0].depth == 0.3
    assert layout.holes[1].back == "glass_dark" and layout.holes[1].depth == 0.15
    proto = recess(1.2, 1.4, 0.3, GLASS_VIEW)
    panes = [mesh for name, mesh in proto.parts if name == GLASS_VIEW]
    assert len(panes) == 1 and panes[0].triangle_count == 2 and proto.collider is None
    cuts = room_window_cuts(layout, segments)
    assert len(cuts) == 1
    wall = _inner_walls(inner, 0.0, 6.0, cuts)
    ring = list(inner.exterior.coords)
    perimeter = sum(math.dist(ring[i], ring[i + 1]) for i in range(len(ring) - 1))
    assert _area(wall) == pytest.approx(perimeter * 6.0 - 1.2 * 1.4)


def test_window_glass_is_translucent_and_the_frame_stays_opaque() -> None:
    image = window_rgba(256)
    assert image[40, 40, 3] == round(GLASS_ALPHA * 255)
    assert image[0, 0, 3] == 255 and image[128, 128, 3] == 255
    decoded = decode_png_rgba(window_png(256))
    assert decoded[40, 40, 3] == image[40, 40, 3] and decoded[0, 0, 3] == 255
    board = boarded_rgba(256)
    assert board[40, 40, 3] == 255 and board[0, 0, 3] == 255


def test_window_off_the_inner_wall_is_omitted() -> None:
    footprint, inner, _segments = _square()
    # An edge inside the footprint, not on the inner ring.
    floating = Segment(0, 0, 0, (2.0, 5.0), (18.0, 5.0), True)
    layout = Layout(holes=[_window(4.0, 1.0)])
    opened, omitted = pierce_room_windows(layout, [floating], inner, footprint, 0.3, 6.0)
    assert (opened, omitted) == (0, 1)
    assert layout.holes == []


def test_interior_quota_is_between_two_and_five() -> None:
    cfg = load_detail_config()
    assert (cfg.interiors.min_buildings, cfg.interiors.max_buildings) == (2, 5)
    assert interior_quota(cfg) == interior_quota(cfg)
    seen = {interior_quota(replace_seed(cfg, seed)) for seed in range(40)}
    assert seen == {2, 3, 4, 5}


def replace_seed(cfg, seed: int):
    from dataclasses import replace
    return replace(cfg, seed=seed, interiors=replace(cfg.interiors, min_buildings=2, max_buildings=5))


def test_seed_splits_tall_interiors_between_two_and_three_floors() -> None:
    cfg = load_detail_config()
    assert cfg.interiors.floors == 3
    size = 24.0
    footprint = Polygon([(0.0, 0.0), (size, 0.0), (size, 18.0), (0.0, 18.0)])
    segment = Segment(0, 0, 0, (0.0, 0.0), (size, 0.0), True)
    door = Hole("interior_door", 0, 11.0, 0.0, 1.2, 2.4, cfg.interiors.wall_thickness_m, None, 0, 0)
    plans: dict[int, object] = {}
    for n in range(40):
        building_id = f"block-{n}"
        plan = plan_interior(building_id, footprint, door, segment, 18.0, 3.0, 6, cfg)
        assert not isinstance(plan, str), plan
        choice = 3 if stable_unit(cfg.seed, building_id, "interior_floors") < 0.5 else 2
        assert plan.floors == choice
        assert len([flight for flight in plan.flights if flight.role == "storey"]) == choice - 1
        assert any(flight.role == "roof" for flight in plan.flights)
        plans[choice] = plan
        if len(plans) == 2:
            break
    assert set(plans) == {2, 3}
    tall = plans[3]
    built = build_interior(tall, 18.0, 18.0, footprint, cfg)
    assert built.defects == 0
    assert set(built.visual) <= {"interior_wall", "interior_floor", "stair"}
    assert all(mesh.triangle_count for meshes in built.visual.values() for mesh in meshes)
    stairs = [c for c in built.spec["connections"] if c["type"] == "stair"]
    assert [c["from"] for c in stairs[:2]] == [f"{tall.building_id}/L0", f"{tall.building_id}/L1"]
    assert stairs[-1]["to"] == "roof"
    assert built.spec["roof_access"] is True
    assert built.roof_well is not None
    assert any(name == "roof_deck" for name, _mesh in built.colliders)
    for _name, mesh in built.colliders:
        assert closed_manifold_defects(mesh) == 0
