"""The draws-per-view block of the export manifest picks its area and cell (docs/12-export.md)."""

from __future__ import annotations

from onward_generator.export.package import _view_draws
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import quad


def _visual() -> bytes:
    floor = quad((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 10.0, 0.0), (0.0, 10.0, 0.0))
    return build_glb([Node("surface/ground", (Primitive(floor, "ground"),))], {"ground": Material((1, 1, 1, 1), 0.9)})


def test_without_a_core_the_cameras_cover_the_layout_site() -> None:
    views = _view_draws(_visual(), {}, {"site_bounds_m": [-50, -50, 50, 50]}, 50.0)
    assert views is not None
    assert (views["views"], views["cell_m"], views["far_m"]) == (5 * 5 * 16, 50.0, 1000.0)


def test_a_core_wins_over_the_site() -> None:
    views = _view_draws(_visual(), {"core": {"half_side_m": 75.0}}, {"site_bounds_m": [-50, -50, 50, 50]}, 100.0)
    assert views is not None and views["views"] == 7 * 7 * 16


def test_no_core_and_no_site_gives_no_estimate() -> None:
    assert _view_draws(_visual(), {}, {}, 100.0) is None


def test_a_card_without_combine_uses_the_100_m_cell() -> None:
    views = _view_draws(_visual(), {}, {"site_bounds_m": [-50, -50, 50, 50]}, 0.0)
    assert views is not None and views["cell_m"] == 100.0
