"""The playable core and the scenic ring (ADR-017).

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

import pytest
from shapely import box

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh, quad
from onward_generator.tiers import (
    CORE,
    SCENIC,
    SCENIC_WALLS,
    SHARED,
    CoreError,
    bounds_tier,
    building_tier,
    core_of,
    core_square,
    scenic_wall,
    tier_summary,
)

CORE_100 = box(-100, -100, 100, 100)


def _floor(x0: float, y0: float, x1: float, y1: float) -> Mesh:
    return quad((x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0))


def test_core_square_is_centred_on_the_origin() -> None:
    assert core_square(150).bounds == (-150, -150, 150, 150)
    assert core_square(50).area == 100 * 100
    assert core_square(2000).bounds[2] == 2000


@pytest.mark.parametrize("half", [49.99, 2000.01, 0, -150])
def test_core_square_refuses_a_half_side_out_of_range(half: float) -> None:
    with pytest.raises(CoreError, match="between 50 and 2000 m"):
        core_square(half)


def test_core_of_reads_the_detail_manifest() -> None:
    assert core_of({"core": {"half_side_m": 120.0}}).equals(box(-120, -120, 120, 120))
    assert core_of({"core": None}) is None
    assert core_of({}) is None


@pytest.mark.parametrize(("footprint", "tier"), [
    (box(10, 10, 20, 20), CORE),
    (box(95, 10, 110, 20), CORE),       # crosses the edge
    (box(100, 10, 110, 20), SCENIC),    # only touches the edge
    (box(120, 10, 130, 20), SCENIC),
])
def test_building_tier(footprint: object, tier: str) -> None:
    assert building_tier(footprint, CORE_100) == tier  # type: ignore[arg-type]


def test_every_building_is_core_without_a_core() -> None:
    assert building_tier(box(500, 500, 510, 510), None) == CORE


def test_scenic_wall_is_seeded_per_building() -> None:
    picks = {scenic_wall(48152, f"osm-way-{n}") for n in range(60)}
    assert picks == set(SCENIC_WALLS)
    assert scenic_wall(48152, "osm-way-1") == scenic_wall(48152, "osm-way-1")
    assert [scenic_wall(48152, f"osm-way-{n}") for n in range(4)] == [
        "scenic_wall_warm", "scenic_wall_light", "scenic_wall_warm", "scenic_wall_ochre",
    ]
    assert [scenic_wall(7, f"osm-way-{n}") for n in range(4)] != [scenic_wall(48152, f"osm-way-{n}") for n in range(4)]


@pytest.mark.parametrize(("bounds", "tier"), [
    ((-10.0, -10.0, 10.0, 10.0), CORE),
    ((90.0, 0.0, 100.0, 10.0), CORE),        # on the edge, still inside
    ((100.0, 0.0, 120.0, 10.0), SCENIC),     # touches from outside
    ((150.0, 0.0, 160.0, 10.0), SCENIC),
    ((-300.0, -300.0, 300.0, 300.0), SHARED),
    ((90.0, 0.0, 110.0, 10.0), SHARED),
])
def test_bounds_tier(bounds: tuple[float, float, float, float], tier: str) -> None:
    assert bounds_tier(bounds, CORE_100) == tier


def _glb(*nodes: tuple[str, str, Mesh]) -> bytes:
    built = [Node(f"node/{k}", (Primitive(mesh, material),), {"object_id": name}) for k, (name, material, mesh) in enumerate(nodes)]
    return build_glb(built, {material: Material((1, 1, 1, 1), 0.9) for _, material, _ in nodes})


def test_tier_summary_counts_triangles_and_draws_per_tier() -> None:
    visual = _glb(
        ("building/a", "plaster", _floor(10, 10, 20, 20)),           # record: core
        ("building/b", "scenic_wall_light", _floor(110, 10, 120, 20)),  # record: scenic
        ("building/c", "scenic_wall_light", _floor(130, 10, 140, 20)),  # record: scenic, same cell as b
        ("surface/ground", "ground", _floor(-300, -300, 300, 300)),   # no record tier: crosses the edge
        ("urban/bench", "urban_metal", _floor(1, 1, 2, 2)),           # no record tier: inside
        ("surface/far", "ground", _floor(250, 250, 260, 260)),        # no record tier: outside
    )
    objects = [{"id": "building/a", "tier": CORE}, {"id": "building/b", "tier": SCENIC},
               {"id": "building/c", "tier": SCENIC}, {"id": "surface/ground", "type": "surface"}]
    summary = tier_summary(visual, objects, CORE_100, cell_m=100.0)
    assert summary == {
        "cell_m": 100.0,
        "draws": 5,
        CORE: {"visual_triangles": 4, "draws": 2},
        SCENIC: {"visual_triangles": 6, "draws": 2},
        SHARED: {"visual_triangles": 2, "draws": 1},
    }


def test_tier_summary_puts_nodes_in_the_adapters_cells() -> None:
    # Centres at y = 50 and y = 100 share the glTF cell (z = -north): floor(-0.5) and floor(-1.0) are both -1.
    same = _glb(("a", "ground", _floor(0, 40, 10, 60)), ("b", "ground", _floor(0, 90, 10, 110)))
    assert tier_summary(same, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 1
    # Centres at x = 50 and x = 100 do not: floor(0.5) is 0, floor(1.0) is 1.
    apart = _glb(("a", "ground", _floor(40, 0, 60, 10)), ("b", "ground", _floor(90, 0, 110, 10)))
    assert tier_summary(apart, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 2
    # A node goes to the cell of its bounds centre, not of its corner.
    wide = _glb(("a", "ground", _floor(0, 0, 10, 10)), ("b", "ground", _floor(-20, 0, 60, 10)))
    assert tier_summary(wide, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 1
    assert tier_summary(wide, [], box(-500, -500, 500, 500), cell_m=15.0)["draws"] == 2


def test_tier_summary_ignores_an_unknown_tier_and_refuses_no_cell() -> None:
    visual = _glb(("building/a", "plaster", _floor(150, 0, 160, 10)))
    assert tier_summary(visual, [{"id": "building/a", "tier": "outer"}], CORE_100, cell_m=100.0)[SCENIC]["visual_triangles"] == 2
    with pytest.raises(CoreError, match="cell_m must be positive"):
        tier_summary(visual, [], CORE_100, cell_m=0.0)


def _wall(x0: float, y0: float, x1: float, y1: float, height: float) -> Mesh:
    return quad((x0, y0, 0.0), (x1, y1, 0.0), (x1, y1, height), (x0, y0, height))


def test_a_record_tier_wins_over_the_bounds() -> None:
    # A core building that crosses the edge, and a scenic one whose bounds lie inside the core.
    visual = _glb(("building/edge", "plaster", _floor(90, 0, 120, 10)), ("building/odd", "scenic_wall_light", _floor(0, 0, 10, 10)))
    objects = [{"id": "building/edge", "tier": CORE}, {"id": "building/odd", "tier": SCENIC}]
    summary = tier_summary(visual, objects, CORE_100, cell_m=100.0)
    assert (summary[CORE]["visual_triangles"], summary[SCENIC]["visual_triangles"], summary[SHARED]["visual_triangles"]) == (2, 2, 0)
    # Without the records the bounds decide.
    plain = tier_summary(visual, [{"id": "building/edge"}, {"id": "building/odd", "tier": "outer"}], CORE_100, cell_m=100.0)
    assert (plain[CORE]["visual_triangles"], plain[SHARED]["visual_triangles"]) == (2, 2)


def test_tier_summary_reads_the_ground_plane_not_the_height() -> None:
    # A 300 m tall wall at x 10 stands inside the core; its height is not a ground extent.
    visual = _glb(("tower", "plaster", _wall(10, 10, 20, 10, 300.0)))
    assert tier_summary(visual, [], CORE_100, cell_m=100.0)[CORE]["visual_triangles"] == 2


def test_tier_summary_uses_the_centre_of_the_north_extent() -> None:
    # Centres at y = 60 and 140: glTF cells floor(-0.6) = -1 and floor(-1.4) = -2.
    visual = _glb(("a", "ground", _floor(0, 55, 10, 65)), ("b", "ground", _floor(0, 135, 10, 145)))
    assert tier_summary(visual, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 2
    # Two nodes of one height far apart in y are two cells.
    visual = _glb(("a", "ground", _floor(0, 0, 10, 10)), ("b", "ground", _floor(0, 300, 10, 310)))
    assert tier_summary(visual, [], box(-500, -500, 500, 500), cell_m=100.0)["draws"] == 2


def test_tier_summary_accepts_a_cell_under_a_metre() -> None:
    visual = _glb(("a", "ground", _floor(0, 0, 0.2, 0.2)), ("b", "ground", _floor(0.6, 0, 0.8, 0.2)))
    assert tier_summary(visual, [], CORE_100, cell_m=0.5)["draws"] == 2
