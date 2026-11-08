"""Lightmap scale per surface class and the offline texel estimate of the export package.

Local coordinates are metres from MURCIA_CENTER (x east, y north, z up).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from onward_generator.export.lightmap import (
    DEFAULT_CLASS_SCALES,
    LIGHTMAP_DENSITY_STEP,
    LIGHTMAP_TEXELS_FLOOR,
    LightmapBudgetError,
    contributes_gi,
    estimate,
    lightmap_areas,
    material_scales,
    page_budget_density,
    pages_at_least,
    summarise,
    surface_class,
)
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import Mesh, quad
from onward_generator.tiers import SCENIC_PREFIX

ADAPTER_CS = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"


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
    ("party_plaster_cream", "party"),
    ("party_brick_red", "party"),
    ("counterparty_wall", "facade"),
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
    names = ["roof_tile", "ground", "interior_wall", "plaster_cream", "party_plaster_cream", "prop_x", "tree_trunk", "ground"]
    assert material_scales(names, DEFAULT_CLASS_SCALES) == [
        {"material": "ground", "scale": 0.5},
        {"material": "interior_wall", "scale": 0.5},
        {"material": "party_plaster_cream", "scale": 0.5},
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


@pytest.mark.parametrize(("material", "option", "lit"), [
    ("scenic_wall_light", False, True),
    ("scenic_wall_light", True, False),
    ("scenic_trim_brick", True, False),
    ("Scenic_Wall_Ochre", True, False),
    ("wall_scenic_x", True, True),
    ("roof_flat_grey", True, True),
    ("plaster_cream", True, True),
    ("prop_x", True, False),
    ("prop_x", False, False),
    ("tree_canopy", True, False),
    ("tree_canopy", False, False),
])
def test_contributes_gi_scenic_follows_the_option(material: str, option: bool, lit: bool) -> None:
    """Plan C4: the scenic shell walls leave the lightmap only with the option; the scenic roofs stay baked."""
    assert contributes_gi(material, scenic_out_of_lightmap=option) is lit
    if not option:
        assert contributes_gi(material) is lit


def test_material_scales_drops_scenic_when_out_of_lightmap() -> None:
    names = ["scenic_wall_light", "scenic_trim_x", "roof_flat_grey", "plaster_cream", "party_x"]
    baked = [
        {"material": "party_x", "scale": 0.5},
        {"material": "roof_flat_grey", "scale": 0.25},
        {"material": "scenic_trim_x", "scale": 0.25},
        {"material": "scenic_wall_light", "scale": 0.25},
    ]
    assert material_scales(names, DEFAULT_CLASS_SCALES) == baked
    assert material_scales(names, DEFAULT_CLASS_SCALES, scenic_out_of_lightmap=False) == baked
    assert material_scales(names, DEFAULT_CLASS_SCALES, scenic_out_of_lightmap=True) == [
        {"material": "party_x", "scale": 0.5},
        {"material": "roof_flat_grey", "scale": 0.25},
    ]


def test_estimate_scenic_out_of_lightmap() -> None:
    glb = _glb(("scenic_wall_light", _floor(0, 10, 0, 10)), ("plaster_cream", _floor(0, 2, 0, 5)))
    scales = material_scales(["scenic_wall_light", "plaster_cream"], DEFAULT_CLASS_SCALES)
    off = estimate(glb, texels_per_m=10.0, max_size=2048, cell_m=100.0, scales=scales)
    # Scenic: 100 m² at 10 × 0.25 texels/m = 625 texels; the facade: 10 m² at 10 texels/m = 1 000.
    assert off["texels_by_class"] == {"facade": 1_000, "scenic": 625}
    assert (off["renderers"], off["texels"]) == (2, 1_625)
    assert estimate(glb, texels_per_m=10.0, max_size=2048, cell_m=100.0, scales=scales,
                    scenic_out_of_lightmap=False) == off
    on = estimate(glb, texels_per_m=10.0, max_size=2048, cell_m=100.0, scales=scales, scenic_out_of_lightmap=True)
    assert on["texels_by_class"] == {"facade": 1_000}
    assert (on["renderers"], on["texels"]) == (1, off["texels"] - off["texels_by_class"]["scenic"])
    assert on["pages_at_least"] <= off["pages_at_least"]


def test_estimate_of_a_scenic_only_scene_out_of_lightmap_is_empty() -> None:
    glb = _glb(("scenic_wall_warm", _floor(0, 10, 0, 10)), ("scenic_trim_brick", _floor(0, 3, 0, 3)))
    assert estimate(glb, texels_per_m=10.0, max_size=2048, cell_m=100.0, scenic_out_of_lightmap=True) == {
        "texels_per_m": 10.0,
        "max_size": 2048,
        "renderers": 0,
        "renderers_capped": 0,
        "texels": 0,
        "texels_by_class": {},
        "pages_at_least": 0,
    }


def test_scenic_rule_matches_the_adapter_source() -> None:
    """The adapter names a material M_<glTF name>; its scenic test and the Python prefix must not drift."""
    source = ADAPTER_CS.read_text(encoding="utf-8")
    assert f'StartsWith("m_{SCENIC_PREFIX}", StringComparison.Ordinal)' in source


# ---------------------------------------------------------------------------------------- plan C5: page budget


def _quads(material: str, count: int = 4, side: float = 100.0) -> bytes:
    """``count`` square floors of ``side`` metres, each in its own 100 m cell along x."""
    return _glb(*((material, _floor(i * 100.0, i * 100.0 + side, 0.0, side)) for i in range(count)))


def _areas(glb: bytes, *, scenic_out_of_lightmap: bool = False) -> dict[tuple[tuple[int, ...], str], float]:
    return lightmap_areas(glb, cell_m=100.0, scenic_out_of_lightmap=scenic_out_of_lightmap)


@pytest.mark.parametrize("scenic_out", [False, True])
@pytest.mark.parametrize(("texels_per_m", "max_size", "cell_m"), [(10.0, 2048, 100.0), (10.0, 100, 8.0), (7.5, 30, 0.0)])
def test_estimate_is_summarise_of_areas(scenic_out: bool, texels_per_m: float, max_size: int, cell_m: float) -> None:
    """The C5 split of ``estimate`` keeps its output, capped renderers and the scenic rule included."""
    glb = _glb(("ground", _floor(0, 10, 0, 1)), ("ground", _floor(0, 4, 0, 1)), ("roof_tile", _floor(0, 20, 0, 20)),
               ("scenic_wall_light", _floor(0, 10, 0, 10)), ("prop_x", _floor(0, 5, 0, 5)))
    scales = material_scales(["ground", "roof_tile", "scenic_wall_light"], DEFAULT_CLASS_SCALES,
                             scenic_out_of_lightmap=scenic_out)
    areas = lightmap_areas(glb, cell_m=cell_m, scenic_out_of_lightmap=scenic_out)
    found = estimate(glb, texels_per_m=texels_per_m, max_size=max_size, cell_m=cell_m, scales=scales,
                     scenic_out_of_lightmap=scenic_out)
    assert found == summarise(areas, texels_per_m=texels_per_m, max_size=max_size, scales=scales)
    assert pages_at_least(areas, texels_per_m, max_size=max_size, scales=scales) == found["pages_at_least"]
    assert ("scenic" in found["texels_by_class"]) is not scenic_out


def test_lightmap_areas_by_cell_and_material() -> None:
    glb = _glb(("ground", _floor(0, 10, 0, 10)), ("ground", _floor(20, 22, 0, 1)), ("roof_tile", _floor(0, 4, 0, 5)),
               ("prop_x", _floor(0, 5, 0, 5)))
    assert dict(lightmap_areas(glb, cell_m=100.0)) == {((0, 0), "ground"): 102.0, ((0, 0), "roof_tile"): 20.0}
    assert dict(lightmap_areas(glb, cell_m=0.0)) == {((0,), "ground"): 100.0, ((1,), "ground"): 2.0, ((2,), "roof_tile"): 20.0}


def test_page_budget_constants() -> None:
    # ADR-005 saw lightmap UV overlaps at 2 texels/m; the floor is twice that. The step is exact in binary.
    assert (LIGHTMAP_TEXELS_FLOOR, LIGHTMAP_DENSITY_STEP) == (4.0, 0.25)
    assert issubclass(LightmapBudgetError, ValueError)


def test_page_budget_density_over_budget_exact() -> None:
    # Four 10 000 m² facades at 10 texels/m: 1 000 000 texels each, under the 1 048 576 page, so 4 pages.
    # Two pages hold 2 097 152 texels: 7.25 texels/m gives 4 × 525 625 = 2 102 500, 7.0 gives 1 960 000.
    areas = _areas(_quads("plaster_cream"))
    assert pages_at_least(areas, 10.0, max_size=1024) == 4
    assert page_budget_density(areas, requested=10.0, pages_max=2, max_size=1024) == 7.0
    assert pages_at_least(areas, 7.25, max_size=1024) == 3
    assert page_budget_density(areas, requested=10.0, pages_max=3, max_size=1024) == 8.75
    assert pages_at_least(areas, 8.75, max_size=1024) == 3 and pages_at_least(areas, 9.0, max_size=1024) == 4


def test_page_budget_density_equal_to_budget_is_unchanged() -> None:
    areas = _areas(_quads("plaster_cream"))
    assert page_budget_density(areas, requested=10.0, pages_max=4, max_size=1024) == 10.0
    assert page_budget_density(areas, requested=10.0, pages_max=5, max_size=1024) == 10.0


def test_page_budget_density_under_budget_is_unchanged_and_not_quantised() -> None:
    areas = _areas(_quads("plaster_cream"))
    assert page_budget_density(areas, requested=10.3, pages_max=5, max_size=1024) == 10.3
    # Over budget the result is on the grid, at or under the requested density.
    assert page_budget_density(areas, requested=7.3, pages_max=2, max_size=1024) == 7.0


def test_page_budget_density_is_deterministic_and_maximal() -> None:
    glb = _glb(*(("plaster_cream", _floor(i * 100.0, i * 100.0 + 30.0 + 7.0 * i, 0.0, 40.0 + 3.0 * i)) for i in range(6)))
    areas = _areas(glb)
    reverse = dict(reversed(list(areas.items())))
    applied = page_budget_density(areas, requested=40.0, pages_max=3, max_size=1024)
    assert applied < 40.0
    assert {page_budget_density(areas, requested=40.0, pages_max=3, max_size=1024) for _ in range(3)} == {applied}
    assert page_budget_density(reverse, requested=40.0, pages_max=3, max_size=1024) == applied
    assert applied % LIGHTMAP_DENSITY_STEP == 0
    assert pages_at_least(areas, applied, max_size=1024) <= 3 < pages_at_least(areas, applied + LIGHTMAP_DENSITY_STEP, max_size=1024)


def test_page_budget_density_respects_class_scales() -> None:
    # The same quads as roofs bake at a quarter of the density: 62 500 texels each, one page in all.
    roofs = _areas(_quads("roof_tile"))
    scales = material_scales(["roof_tile"], DEFAULT_CLASS_SCALES)
    assert page_budget_density(roofs, requested=10.0, pages_max=1, max_size=1024, scales=scales) == 10.0
    assert page_budget_density(_areas(_quads("plaster_cream")), requested=10.0, pages_max=1, max_size=1024) < 10.0
    # Without the scales the roofs count as facades and the density is lowered.
    assert page_budget_density(roofs, requested=10.0, pages_max=1, max_size=1024) < 10.0


def test_page_budget_density_with_scenic_out_of_lightmap() -> None:
    """Plan C4 and C5 together: the scenic walls count towards the budget only when they are baked."""
    glb = _glb(("plaster_cream", _floor(0, 100, 0, 100)),
               *(("scenic_wall_light", _floor(i * 100.0, i * 100.0 + 100.0, 0.0, 100.0)) for i in range(1, 5)))
    scales = material_scales(["plaster_cream", "scenic_wall_light"], DEFAULT_CLASS_SCALES)
    # The facade: 1 000 000 texels; each scenic wall at 2.5 texels/m: 62 500. 1 250 000 texels in 2 pages.
    baked = _areas(glb)
    assert pages_at_least(baked, 10.0, max_size=1024, scales=scales) == 2
    assert page_budget_density(baked, requested=10.0, pages_max=1, max_size=1024, scales=scales) == 9.0
    out_scales = material_scales(["plaster_cream", "scenic_wall_light"], DEFAULT_CLASS_SCALES, scenic_out_of_lightmap=True)
    out = _areas(glb, scenic_out_of_lightmap=True)
    assert pages_at_least(out, 10.0, max_size=1024, scales=out_scales) == 1
    assert page_budget_density(out, requested=10.0, pages_max=1, max_size=1024, scales=out_scales) == 10.0


def test_page_budget_density_capped_renderer() -> None:
    # A 40 000 m² facade wants 4 000 000 texels at 10 texels/m and is capped at one page; a second renderer of
    # 100 m² adds 10 000. Two pages at 10 texels/m; one page needs the big one under the cap and room for both.
    glb = _glb(("plaster_cream", _floor(0, 200, 0, 200)), ("plaster_cream", _floor(300, 310, 0, 10)))
    areas = _areas(glb)
    assert estimate(glb, texels_per_m=10.0, max_size=1024, cell_m=100.0)["renderers_capped"] == 1
    assert pages_at_least(areas, 10.0, max_size=1024) == 2
    # 40 100 m² × d² <= 1 048 576: d <= 5.1137, so 5.0 on the grid.
    assert page_budget_density(areas, requested=10.0, pages_max=1, max_size=1024) == 5.0


def test_page_budget_density_capped_renderers_make_budget_impossible() -> None:
    # Three renderers each over a page even at 4 texels/m (90 000 m² × 16 = 1 440 000): three pages at least.
    areas = _areas(_quads("plaster_cream", count=3, side=300.0))
    with pytest.raises(LightmapBudgetError, match="needs at least 3 pages at 4 texels/m"):
        page_budget_density(areas, requested=10.0, pages_max=2, max_size=1024)


def test_page_budget_density_floor_exact() -> None:
    # Four 16 384 m² facades at 4 texels/m: 262 144 texels each, a quarter page, so one page exactly at the floor.
    side = 128.0
    areas = _areas(_quads("plaster_cream", side=side))
    assert pages_at_least(areas, 4.0, max_size=1024) == 1 < pages_at_least(areas, 4.25, max_size=1024)
    assert page_budget_density(areas, requested=10.0, pages_max=1, max_size=1024) == 4.0
    assert page_budget_density(areas, requested=4.1, pages_max=1, max_size=1024) == 4.0


def test_page_budget_density_refuses_below_floor() -> None:
    # 129 m sides: 16 641 m² × 16 = 266 256 texels each, 1 065 024 in all: just over one page at the floor.
    areas = _areas(_quads("plaster_cream", side=129.0))
    with pytest.raises(LightmapBudgetError) as caught:
        page_budget_density(areas, requested=10.0, pages_max=1, max_size=1024)
    assert str(caught.value) == (
        "build.lightmap_pages_max 1 cannot be met: the lightmap needs at least 2 pages at 4 texels/m, "
        "and the density never goes below the 4 texels/m floor (ADR-005)"
    )
    assert isinstance(caught.value, ValueError)


def test_page_budget_density_requested_below_floor() -> None:
    # At 3 texels/m four 10 000 m² facades need 360 000 texels: one page of 512² (262 144) is not enough.
    areas = _areas(_quads("plaster_cream"))
    assert pages_at_least(areas, 3.0, max_size=512) == 2
    with pytest.raises(LightmapBudgetError, match="needs at least 2 pages at 3 texels/m, and the density never"):
        page_budget_density(areas, requested=3.0, pages_max=1, max_size=512)
    assert page_budget_density(areas, requested=3.0, pages_max=2, max_size=512) == 3.0
    # Just above the floor and over budget: nothing on the grid between 4.0 and 4.1 fits.
    with pytest.raises(LightmapBudgetError, match="at 4 texels/m"):
        page_budget_density(areas, requested=4.1, pages_max=1, max_size=512)
