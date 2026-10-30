"""Lightmap scale per surface class and the offline texel estimate of the export package.

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

import pytest

from onward_generator.export.lightmap import (
    DEFAULT_CLASS_SCALES,
    contributes_gi,
    estimate,
    material_scales,
    surface_class,
)
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh, quad


def _floor(x0: float, x1: float, y0: float, y1: float) -> Mesh:
    return quad((x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 0.0), (x0, y1, 0.0))


def _glb(*parts: tuple[str, Mesh]) -> bytes:
    nodes = [Node(f"n/{index}", (Primitive(mesh, material),)) for index, (material, mesh) in enumerate(parts)]
    materials = {material: Material((1, 1, 1, 1), 0.9) for material, _mesh in parts}
    return build_glb(nodes, materials)


@pytest.mark.parametrize(("material", "expected"), [
    ("roof_tile", "roof"),
    ("roof_flat_grey", "roof"),
    ("building_roof", "roof"),
    ("rooftop_plant", "facade"),
    ("ground", "ground"),
    ("pedestrian", "ground"),
    ("sidewalk", "ground"),
    ("carriageway", "ground"),
    ("curb", "ground"),
    ("green", "ground"),
    ("water", "ground"),
    ("cobblestone_paving", "ground"),
    ("interior_wall", "interior"),
    ("interior_floor", "interior"),
    ("stair", "interior"),
    ("interior_shop_bakery", "interior"),
    ("interior_shop_wood", "interior"),
    ("shop_interior", "facade"),
    ("plaster_cream", "facade"),
    ("glass_dark", "facade"),
    ("scenic_wall_light", "scenic"),
    ("scenic_wall_ochre", "scenic"),
    ("wall_scenic_x", "facade"),
])
def test_surface_class(material: str, expected: str) -> None:
    assert surface_class(material) == expected


@pytest.mark.parametrize(("material", "lit"), [
    ("prop_water_manhole_cover", False),
    ("Prop_Barrel_01", False),
    ("tree_canopy", False),
    ("urban_metal", False),
    ("railing_metal", False),
    ("horizon_backdrop", False),
    ("sign_prop_x", True),
    ("plaster_cream", True),
    ("roof_tile", True),
])
def test_contributes_gi_mirrors_the_adapter(material: str, lit: bool) -> None:
    assert contributes_gi(material) is lit


def test_material_scales_default_classes() -> None:
    names = ["roof_tile", "ground", "interior_wall", "plaster_cream", "prop_x", "tree_trunk", "ground"]
    assert material_scales(names, DEFAULT_CLASS_SCALES) == [
        {"material": "ground", "scale": 0.5},
        {"material": "interior_wall", "scale": 0.5},
        {"material": "roof_tile", "scale": 0.25},
    ]


def test_material_scales_leave_out_unlit_and_unit_scales() -> None:
    # A prop is classed as a facade but stays out of the lightmap; a class at 1 writes no row.
    assert material_scales(["prop_x", "plaster", "roof_tile"], {"facade": 0.5, "roof": 1.0}) == [
        {"material": "plaster", "scale": 0.5},
    ]
    assert material_scales(["roof_tile"], {}) == []


def test_estimate_sums_area_times_density_squared() -> None:
    glb = _glb(
        ("ground", _floor(0, 10, 0, 10)),
        ("roof_tile", _floor(0, 4, 0, 5)),
        ("prop_x", _floor(0, 50, 0, 50)),
    )
    found = estimate(glb, texels_per_m=10.0, max_size=2048, cell_m=100.0,
                     scales=[{"material": "roof_tile", "scale": 0.5}])
    # ground 100 m² × 10² + roof 20 m² × 5²; the prop takes no lightmap.
    assert found == {
        "texels_per_m": 10.0,
        "max_size": 2048,
        "renderers": 2,
        "renderers_capped": 0,
        "texels": 10_000 + 500,
        "texels_by_class": {"ground": 10_000, "roof": 500},
        "pages_at_least": 1,
    }


def test_estimate_caps_a_renderer_at_one_page() -> None:
    # One page is 100 × 100 texels. A 100 m² floor at 10 texels/m fills it exactly.
    exact = estimate(_glb(("ground", _floor(0, 10, 0, 10))), texels_per_m=10.0, max_size=100, cell_m=100.0)
    assert (exact["texels"], exact["renderers_capped"], exact["pages_at_least"]) == (10_000, 0, 1)
    over = estimate(_glb(("ground", _floor(0, 10, 0, 11))), texels_per_m=10.0, max_size=100, cell_m=100.0)
    assert (over["texels"], over["renderers_capped"], over["pages_at_least"]) == (10_000, 1, 1)
    two = estimate(_glb(("ground", _floor(0, 10, 0, 10)), ("plaster", _floor(0, 1, 0, 1))),
                   texels_per_m=10.0, max_size=100, cell_m=100.0)
    assert two["pages_at_least"] == 2


def test_estimate_groups_like_the_adapter_combine() -> None:
    # Same material, same 8 m cell (centres x = 5 and x = 2): one renderer, capped at one page.
    same = _glb(("ground", _floor(0, 10, 0, 1)), ("ground", _floor(0, 4, 0, 1)))
    merged = estimate(same, texels_per_m=10.0, max_size=100, cell_m=8.0)
    assert (merged["renderers"], merged["texels"], merged["renderers_capped"]) == (1, 1_400, 0)
    capped = estimate(same, texels_per_m=10.0, max_size=30, cell_m=8.0)
    assert (capped["renderers"], capped["texels"], capped["renderers_capped"]) == (1, 900, 1)
    # Without combining each node is its own renderer: 1 000 and 400 texels, both under 900.
    alone = estimate(same, texels_per_m=10.0, max_size=30, cell_m=0.0)
    assert (alone["renderers"], alone["texels"], alone["renderers_capped"]) == (2, 900 + 400, 1)


def test_estimate_cells_follow_floor_on_negative_coordinates() -> None:
    # Centres at x = -5 and x = +5 fall in cells -1 and 0 of 20 m.
    glb = _glb(("ground", _floor(-10, 0, 0, 1)), ("ground", _floor(0, 10, 0, 1)))
    found = estimate(glb, texels_per_m=10.0, max_size=30, cell_m=20.0)
    assert (found["renderers"], found["texels"], found["texels_by_class"]) == (2, 1_800, {"ground": 1_800})
    assert found["renderers_capped"] == 2


def test_estimate_area_away_from_the_origin() -> None:
    found = estimate(_glb(("ground", _floor(5, 15, 3, 4))), texels_per_m=10.0, max_size=2048, cell_m=100.0)
    assert found["texels"] == 1_000


def test_estimate_skips_only_the_unlit_primitive_of_a_node() -> None:
    node = Node("n", (Primitive(_floor(0, 1, 0, 1), "prop_x"), Primitive(_floor(0, 2, 0, 1), "ground")))
    glb = build_glb([node], {"prop_x": Material((1, 1, 1, 1), 0.9), "ground": Material((1, 1, 1, 1), 0.9)})
    assert estimate(glb, texels_per_m=10.0, max_size=2048, cell_m=100.0)["texels"] == 200


def test_estimate_cells_use_the_centre_of_each_bounding_box() -> None:
    # A and B lie in cells y = 1 and y = 0 of 20 m; A and C share cell (0, 1).
    a, b, c = _floor(0, 1, 30, 31), _floor(0, 1, 0, 1), _floor(10, 11, 34, 35)
    assert estimate(_glb(("ground", a), ("ground", b)), texels_per_m=10.0, max_size=2048, cell_m=20.0)["renderers"] == 2
    assert estimate(_glb(("ground", a), ("ground", c)), texels_per_m=10.0, max_size=2048, cell_m=20.0)["renderers"] == 1


def test_estimate_groups_by_cell_below_one_metre() -> None:
    node = Node("n", (Primitive(_floor(0, 0.2, 0, 0.2), "ground"), Primitive(_floor(5, 5.2, 0, 0.2), "ground")))
    glb = build_glb([node], {"ground": Material((1, 1, 1, 1), 0.9)})
    assert estimate(glb, texels_per_m=10.0, max_size=2048, cell_m=0.5)["renderers"] == 2


def test_scenic_shells_bake_at_the_roof_scale() -> None:
    assert DEFAULT_CLASS_SCALES["scenic"] == DEFAULT_CLASS_SCALES["roof"] == 0.25
    assert material_scales(["scenic_wall_warm", "plaster_cream"], DEFAULT_CLASS_SCALES) == [
        {"material": "scenic_wall_warm", "scale": 0.25},
    ]
