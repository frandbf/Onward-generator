"""The playable core bakes at the atmosphere preset's lightmap density (plan E10, ADR-037)."""

from __future__ import annotations

from pathlib import Path

import pytest

from onward_generator.export.lightmap import LightmapBudgetError, estimate, in_core, page_budget_density, pages_at_least, summarise
from onward_generator.export.package import _core_lightmap

ADAPTER_CS = Path(__file__).resolve().parents[2] / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"
CORE = {"half_side_m": 100.0, "scale": 1.4}


@pytest.mark.parametrize(("cell", "inside"), [
    ((0, 0), True), ((-1, -1), True), ((-1, 0), True), ((0, -1), True),
    ((1, 0), False), ((0, 1), False), ((-2, 0), False), ((0, -2), False), ((5,), False),
])
def test_a_cell_is_in_the_core_only_when_wholly_inside(cell: tuple[int, ...], inside: bool) -> None:
    assert in_core(cell, CORE, 100.0) is inside


def test_no_core_or_no_combining_is_never_in_the_core() -> None:
    assert in_core((0, 0), None, 100.0) is False
    assert in_core((0, 0), CORE, 0.0) is False
    assert in_core((0, 0), {"half_side_m": 150.0, "scale": 1.4}, 100.0) is True
    assert in_core((1, 0), {"half_side_m": 150.0, "scale": 1.4}, 100.0) is False
    assert in_core((1, 0), {"half_side_m": 200.0, "scale": 1.4}, 100.0) is True


def test_a_core_cell_bakes_at_the_scale_squared() -> None:
    areas = {((0, 0), "plaster"): 100.0, ((3, 0), "plaster"): 100.0}
    plain = summarise(areas, texels_per_m=10.0, max_size=2048)
    boosted = summarise(areas, texels_per_m=10.0, max_size=2048, core=CORE, cell_m=100.0)
    assert plain["texels"] == 20000
    assert boosted["texels"] == 10000 + round(100 * 14.0 * 14.0)
    assert pages_at_least(areas, 10.0, max_size=2048, core=CORE, cell_m=100.0) == 1


def test_the_page_budget_counts_the_core_and_keeps_its_ratio() -> None:
    """18 m² in the core and 18 m² outside: 3600 texels fit one 64 px page at 10 texels/m, 5328 do not with the core."""
    areas = {((0, 0), "plaster"): 18.0, ((3, 0), "plaster"): 18.0}
    assert page_budget_density(areas, requested=10.0, pages_max=1, max_size=64) == 10.0
    lowered = page_budget_density(areas, requested=10.0, pages_max=1, max_size=64, core=CORE, cell_m=100.0)
    assert lowered == 8.75
    assert pages_at_least(areas, lowered, max_size=64, core=CORE, cell_m=100.0) == 1
    assert pages_at_least(areas, lowered + 0.25, max_size=64, core=CORE, cell_m=100.0) == 2
    # 120 m² each at the 4 texels/m floor: 3840 texels fit one page alone, 5683 do not with the core.
    with pytest.raises(LightmapBudgetError, match=r"needs at least 2 pages at 4 texels/m"):
        page_budget_density({((0, 0), "plaster"): 120.0, ((3, 0), "plaster"): 120.0}, requested=10.0, pages_max=0,
                            max_size=64, core=CORE, cell_m=100.0)


def _options(texels: float = 10.0, cell: float = 100.0) -> dict[str, float]:
    return {"lightmap_texels_per_m": texels, "combine_cell_m": cell}


def test_the_core_option_is_the_preset_density_over_the_card() -> None:
    detail = {"core": {"half_side_m": 100.0}}
    assert _core_lightmap("golden_hour", _options(), detail) == {"half_side_m": 100.0, "scale": 1.4}
    assert _core_lightmap("golden_hour", _options(texels=12.0), detail) == {"half_side_m": 100.0, "scale": 1.1667}
    assert _core_lightmap("golden_hour", _options(cell=1.0), detail) == {"half_side_m": 100.0, "scale": 1.4}


@pytest.mark.parametrize(("preset", "options", "detail"), [
    (None, _options(), {"core": {"half_side_m": 100.0}}),
    ("golden_hour", _options(), {}),
    ("golden_hour", _options(), {"core": {}}),
    ("golden_hour", _options(cell=0.0), {"core": {"half_side_m": 100.0}}),
    ("golden_hour", {"lightmap_texels_per_m": 10.0}, {"core": {"half_side_m": 100.0}}),
    ("golden_hour", _options(texels=14.0), {"core": {"half_side_m": 100.0}}),
    ("golden_hour", _options(texels=20.0), {"core": {"half_side_m": 100.0}}),
    ("overcast", _options(), {"core": {"half_side_m": 100.0}}),
])
def test_no_core_option_without_a_preset_a_core_combining_or_a_higher_density(preset: str | None, options: dict[str, float], detail: dict[str, object]) -> None:
    assert _core_lightmap(preset, options, detail) is None


def test_the_adapter_scales_the_combined_core_cells() -> None:
    """No Unity here: this pins the wiring. The bake of Madrid Sol Arena is in docs/12-export.md."""
    source = ADAPTER_CS.read_text(encoding="utf-8")
    assert "public LightmapCore lightmap_core = new LightmapCore();" in source
    assert "private class LightmapCore { public float half_side_m; public float scale; }" in source
    assert "float core = furnitureGroups.Contains(g.Key) ? 1f : CoreScale(opt, cx, cz, cellM);" in source
    assert "opt.lightmap_texels_per_m * core * ScaleInLightmap(opt, visual.Doc.materials, mat)" in source
    assert "float coreScale = coreMeshes.TryGetValue(name, out float core) ? core : 1f;" in source
    assert "? ScaleInLightmap(opt, visual.Doc.materials, mat) * coreScale : 0f;" in source
    body = source.split("private static float CoreScale(", 1)[1].split("\n        }\n", 1)[0]
    assert "if (core == null || core.scale <= 1f || core.half_side_m <= 0f) return 1f;" in body
    assert "-h <= cx * cellM && (cx + 1) * cellM <= h && -h <= cz * cellM && (cz + 1) * cellM <= h" in body


def test_a_one_metre_cell_can_be_in_the_core() -> None:
    assert in_core((0, 0), CORE, 1.0) is True


def test_without_a_cell_size_the_core_counts_nothing() -> None:
    """The default cell_m 0 means no combining: no cell is in the core, as the adapter does without cells."""
    areas = {((0, 0), "plaster"): 18.0, ((3, 0), "plaster"): 18.0}
    assert summarise(areas, texels_per_m=10.0, max_size=64, core=CORE) == summarise(areas, texels_per_m=10.0, max_size=64)
    assert pages_at_least(areas, 10.0, max_size=64, core=CORE) == 1
    assert page_budget_density(areas, requested=10.0, pages_max=1, max_size=64, core=CORE) == 10.0


def test_the_estimate_of_a_glb_counts_the_core() -> None:
    from tests.unit.test_export_lightmap import _floor, _glb

    visual = _glb(("plaster_cream", _floor(10.0, 20.0, 10.0, 20.0)), ("plaster_ochre", _floor(310.0, 320.0, 10.0, 20.0)))
    plain = estimate(visual, texels_per_m=10.0, max_size=2048, cell_m=100.0)
    boosted = estimate(visual, texels_per_m=10.0, max_size=2048, cell_m=100.0, core=CORE)
    assert plain["texels"] == 20000 and boosted["texels"] == 10000 + 19600
