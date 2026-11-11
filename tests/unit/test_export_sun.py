"""The sun's azimuth across the street axis of the playable core (plan E6, ADR-037)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from onward_generator.export.atmosphere import lighting, load_presets
from onward_generator.export.sun import OFFSET_DEG, StreetSun, azimuth_across, street_axis, street_sun, wall_votes
from onward_generator.generation.gltf import Material, Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh, quad


def _wall(heading_deg: float, length_m: float = 10.0, height_m: float = 6.0, at: tuple[float, float] = (0.0, 0.0)) -> Mesh:
    """A vertical wall whose normal points toward ``heading_deg`` (clockwise from north), centred on ``at``."""
    h = math.radians(heading_deg)
    along = np.array([math.cos(h), -math.sin(h), 0.0]) * length_m / 2
    centre = np.array([at[0], at[1], 0.0])
    a, b = centre - along, centre + along
    # Counter-clockwise seen from the normal side: b -> a at the bottom, then up.
    return quad(tuple(b), tuple(a), tuple(a + [0, 0, height_m]), tuple(b + [0, 0, height_m]))


def _votes(*parts: tuple[str, Mesh], half_side_m: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    return wall_votes([Primitive(mesh, material) for material, mesh in parts], half_side_m)


def _gap90(a: np.ndarray, b: float) -> np.ndarray:
    return np.abs((a - b + 45.0) % 90.0 - 45.0)


@pytest.mark.parametrize(("heading", "expected"), [(0.0, 0.0), (20.0, 20.0), (90.0, 0.0), (110.0, 20.0), (200.0, 20.0), (290.0, 20.0)])
def test_a_wall_votes_for_its_normal_modulo_90(heading: float, expected: float) -> None:
    headings, areas = _votes(("plaster_cream", _wall(heading)))
    assert len(headings) == 2 and np.all(_gap90(headings, expected) < 1e-6)
    assert np.all((headings >= 0) & (headings <= 90))
    assert areas.tolist() == pytest.approx([30.0, 30.0])


def test_only_facade_and_party_walls_vote() -> None:
    wall = _wall(30.0)
    floor = quad((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0))
    headings, areas = _votes(("plaster_cream", wall), ("party_plaster_cream", wall), ("roof_flat_grey", wall),
                             ("interior_wall", wall), ("prop_bench", wall), ("ground", wall), ("plaster_cream", floor))
    assert len(headings) == 4 and areas.sum() == pytest.approx(120.0)


def test_a_slanted_face_is_not_a_wall() -> None:
    steep = quad((0, 0, 0), (10, 0, 0), (10, 1, 6), (0, 1, 6))
    assert len(_votes(("plaster_cream", steep))[0]) == 0
    nearly = quad((0, 0, 0), (10, 0, 0), (10, 0.3, 6), (0, 0.3, 6))
    assert len(_votes(("plaster_cream", nearly))[0]) == 2


def test_walls_outside_the_core_do_not_vote() -> None:
    inside, outside = _wall(10.0, at=(40.0, -40.0)), _wall(50.0, at=(60.0, 0.0))
    headings, _areas = _votes(("plaster_cream", inside), ("plaster_cream", outside), half_side_m=50.0)
    assert len(headings) == 2 and np.all(_gap90(headings, 10.0) < 1e-6)
    assert len(_votes(("plaster_cream", inside), ("plaster_cream", outside))[0]) == 4


@pytest.mark.parametrize(("x", "votes"), [(50.0, 2), (-50.0, 2), (50.01, 0), (-50.01, 0)])
def test_a_wall_on_the_core_edge_votes_and_one_past_it_does_not(x: float, votes: int) -> None:
    """A wall in the plane x = ``x``, facing east: its triangle centres lie on that plane."""
    wall = quad((x, -5.0, 0.0), (x, 5.0, 0.0), (x, 5.0, 6.0), (x, -5.0, 6.0))
    assert len(_votes(("plaster_cream", wall), half_side_m=50.0)[0]) == votes
    on_y = quad((-5.0, x, 0.0), (5.0, x, 0.0), (5.0, x, 6.0), (-5.0, x, 6.0))
    assert len(_votes(("plaster_cream", on_y), half_side_m=50.0)[0]) == votes


def test_a_degenerate_triangle_does_not_vote() -> None:
    flat = Mesh(np.array([[0.0, 0, 0], [1, 0, 0], [2, 0, 0]]), np.array([[0, 1, 2]]))
    assert len(_votes(("plaster_cream", flat))[0]) == 0


def test_no_wall_gives_no_axis() -> None:
    assert street_axis(*_votes()) is None
    assert street_axis(np.array([10.0]), np.array([0.0])) is None


def test_the_heaviest_orientation_wins_and_is_refined() -> None:
    assert street_axis(np.array([22.0, 23.0, 60.0]), np.array([10.0, 10.0, 15.0])) == pytest.approx(22.5)
    assert street_axis(np.array([22.0, 60.0]), np.array([10.0, 15.0])) == pytest.approx(60.0)


def test_neighbouring_bins_join_against_a_single_heavier_one() -> None:
    """Two bins side by side outweigh a lone bin a little heavier than either, once smoothed."""
    assert street_axis(np.array([12.0, 17.0, 60.0]), np.array([10.0, 10.0, 12.0])) == pytest.approx(14.5)


def test_the_axis_wraps_at_90_degrees() -> None:
    assert street_axis(np.array([88.0, 2.0]), np.array([1.0, 1.0])) == 0.0
    assert street_axis(np.array([89.0, 3.0]), np.array([1.0, 1.0])) == pytest.approx(1.0)
    assert street_axis(np.array([87.0, 1.0]), np.array([1.0, 1.0])) == pytest.approx(89.0)


def test_an_axis_that_rounds_to_90_is_0() -> None:
    assert street_axis(np.array([89.998]), np.array([1.0])) == 0.0


def test_a_heading_of_90_from_a_rounding_modulo_is_the_last_bin() -> None:
    """A tiny negative angle modulo 90 gives 90.0 in floating point; it counts as 0, not as a bin past the end."""
    assert street_axis(np.array([90.0]), np.array([1.0])) == 0.0
    assert street_axis(np.array([90.0, 30.0]), np.array([1.0, 3.0])) == pytest.approx(30.0)


@pytest.mark.parametrize(("axis", "expected"), [(0.0, 125.0), (84.09, 119.09), (20.0, 145.0), (45.0, 170.0), (70.0, 105.0), (89.0, 124.0)])
def test_the_sun_stands_35_degrees_off_the_grid_nearest_the_neutral_sun(axis: float, expected: float) -> None:
    assert OFFSET_DEG == 35.0
    assert azimuth_across(axis) == pytest.approx(expected)


def test_a_tie_keeps_the_smaller_azimuth() -> None:
    """Axis 70: 105 and 195 are both 45 degrees from 150."""
    assert azimuth_across(70.0) == 105.0


def _glb(*parts: tuple[str, Mesh]) -> bytes:
    nodes = [Node(f"n/{i}", (Primitive(mesh, material),)) for i, (material, mesh) in enumerate(parts)]
    return build_glb(nodes, {material: Material((1, 1, 1, 1), 0.9) for material, _mesh in parts})


def test_the_street_sun_of_a_glb() -> None:
    visual = _glb(("plaster_cream", _wall(20.0)), ("plaster_cream", _wall(110.0, at=(5.0, 5.0))), ("roof_flat_grey", _wall(60.0)))
    sun = street_sun(visual, None)
    assert sun.axis_deg == pytest.approx(20.0, abs=0.01)
    assert sun.azimuth_deg == pytest.approx(145.0, abs=0.01)
    assert sun.wall_area_m2 == 120.0


def test_a_glb_without_walls_keeps_the_neutral_sun() -> None:
    visual = _glb(("ground", quad((0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0))))
    assert street_sun(visual, 50.0) == StreetSun(None, 150.0, 0.0)


def test_lighting_takes_the_street_azimuth_and_records_the_axis() -> None:
    preset = load_presets()["golden_hour"]
    block, record = lighting(preset, StreetSun(84.09, 119.09, 84141.4))
    assert block["sun_azimuth_deg"] == 119.09
    assert record == {"elevation_deg": 30.0, "azimuth_source": "streets", "axis_deg": 84.09, "wall_area_m2": 84141.4, "azimuth_deg": 119.09}
    block, record = lighting(preset, StreetSun(None, 150.0, 0.0))
    assert block["sun_azimuth_deg"] == 150.0
    assert record == {"elevation_deg": 30.0, "azimuth_source": "streets_none", "axis_deg": None, "wall_area_m2": 0.0, "azimuth_deg": 150.0}


def test_a_numeric_azimuth_ignores_the_street_sun() -> None:
    block, record = lighting(load_presets()["overcast"], StreetSun(84.09, 119.09, 1.0))
    assert block["sun_azimuth_deg"] == 150.0 and record["azimuth_source"] == "preset" and "axis_deg" not in record
