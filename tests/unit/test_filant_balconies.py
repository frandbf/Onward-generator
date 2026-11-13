"""Plan Q3b: Haussmann's balcons filants, one balcony along the street wall of the 2e and 5e étage."""

from __future__ import annotations

from types import SimpleNamespace

from shapely.geometry import Polygon, box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import (
    FILANT_DEPTH_M,
    FILANT_MIN_M,
    FILANT_RAILING_M,
    FILANT_SLAB_M,
    FILANT_STOREYS,
    Layout,
    Segment,
    Storeys,
    entrance,
    filant_storeys,
    layout_segment,
)

CFG = load_detail_config()
TEMPLATE = CFG.template_for("residential_multi", False)


def test_the_second_and_fifth_storeys_carry_the_filants() -> None:
    assert (FILANT_STOREYS, FILANT_DEPTH_M, FILANT_SLAB_M, FILANT_RAILING_M, FILANT_MIN_M) == ((2, 5), 0.5, 0.15, 1.0, 3.0)
    assert filant_storeys(3) == frozenset()
    assert filant_storeys(4) == frozenset({2, 3})  # the fifth falls to the top storey
    assert filant_storeys(6) == frozenset({2, 5})
    assert filant_storeys(8) == frozenset({2, 5})


def _layout(length: float, *, filants: frozenset[int] = frozenset({2, 5}), hidden: bool = False, others: Polygon | None = None,
            floor_m: float = 0.0) -> Layout:
    seg = Segment(0, 0, 0, (0.0, 0.0), (length, 0.0), True)  # outward normal -y
    layout = Layout()
    kind, _ = entrance(False, False, CFG)
    layout_segment(seg, Storeys(7, 3.0, "heights_levels"), TEMPLATE, "residential", None, kind, CFG.recess_depth_m, "b1",
                   box(0, 0, length, 10), others if others is not None else Polygon(), CFG, layout, floor_m, hidden=hidden, filants=filants)
    return layout


def test_a_street_wall_gets_one_balcony_along_each_filant_storey_and_no_bay_balcony_there() -> None:
    layout = _layout(12.0)
    margin = CFG.edge_margin_m
    assert layout.filants == [(0, margin, 12.0 - margin, 6.0), (0, margin, 12.0 - margin, 15.0)]
    assert not any(v0 in (6.0, 15.0) for _, _, v0 in layout.balconies)
    assert all(h.kind != "balcony_door" or h.storey not in (2, 5) for h in layout.holes)
    # The windows of those storeys stay.
    assert any(h.storey == 2 for h in layout.holes) and any(h.storey == 5 for h in layout.holes)


def test_no_filant_on_a_hidden_short_or_crowded_wall_or_below_the_setback() -> None:
    assert _layout(12.0, hidden=True).filants == []
    margin = CFG.edge_margin_m
    assert _layout(FILANT_MIN_M + 2 * margin - 0.01).filants == []
    assert len(_layout(FILANT_MIN_M + 2 * margin).filants) == 2
    assert _layout(12.0, others=box(4.0, -0.4, 5.0, -0.2)).filants == []  # a neighbour in front of the slab
    assert len(_layout(12.0, others=box(4.0, -0.6, 5.0, -0.52)).filants) == 2  # just past the 0.5 m slab
    assert len(_layout(12.0, others=box(4.0, -0.6, 5.0, -0.5)).filants) == 2  # touching the slab's edge is not meeting it
    assert [v0 for *_, v0 in _layout(12.0, floor_m=9.0).filants] == [15.0]  # the 6 m storey is under the setback
    assert [v0 for *_, v0 in _layout(12.0, floor_m=6.0).filants] == [6.0, 15.0]  # a storey starting on the setback keeps it
    assert [v0 for *_, v0 in _layout(12.0, filants=frozenset({1})).filants] == [3.0]  # any storey above the ground floor
    assert _layout(12.0, filants=frozenset({0, 7})).filants == []  # never the ground floor, never above the top
    assert _layout(12.0, filants=frozenset()).filants == []


def test_the_map_centre_names_the_style() -> None:
    from onward_generator.detail.scene import regional_style

    def base(origin: object) -> SimpleNamespace:
        return SimpleNamespace(manifest=lambda: {"frame": {"origin_wgs84": origin}})

    assert regional_style(base([2.34981, 48.84045])) == "haussmann"  # Rue Mouffetard
    assert regional_style(base([-1.12863, 37.98407])) == "mediterranean"  # Murcia
    assert regional_style(base(None)) == "generic"
    assert regional_style(SimpleNamespace(manifest=lambda: {})) == "generic"


def test_the_shell_bakes_each_filant_as_a_slab_and_a_railing_with_a_collider() -> None:
    import numpy as np

    from onward_generator.detail.facades import build_shell, refine_exposure

    footprint = box(0, 0, 12, 8)
    refined, segments = refine_exposure(footprint, Polygon(), CFG)
    south = next(s for s in segments if abs(s.a[1]) < 1e-6 and abs(s.b[1]) < 1e-6)
    layout = Layout(filants=[(south.index, 0.3, 11.7, 6.0), (south.index, 0.3, 11.7, 15.0)])
    shell = build_shell(refined, segments, layout, 21.0, TEMPLATE, "plaster_ochre", "roof_flat_grey", "b1", CFG)
    plain = build_shell(refined, segments, Layout(), 21.0, TEMPLATE, "plaster_ochre", "roof_flat_grey", "b1", CFG)
    assert shell.counts["balcony_filant"] == 2 and "balcony_filant" not in plain.counts
    assert len(shell.balcony_colliders) == len(plain.balcony_colliders) + 2
    collider = shell.balcony_colliders[-1]
    lo, hi = collider.vertices.min(axis=0), collider.vertices.max(axis=0)
    span = sorted(hi - lo)
    assert np.allclose(span, sorted([11.4, FILANT_DEPTH_M, FILANT_SLAB_M + FILANT_RAILING_M]), atol=1e-6)
    assert len(shell.parts["railing_metal"]) == len(plain.parts.get("railing_metal", [])) + 2


def test_the_bay_balconies_are_spans_from_their_start() -> None:
    from types import SimpleNamespace

    from onward_generator.detail.scene import _balcony_columns

    layout = Layout(balconies=[(0, 4.0, 3.0), (1, 2.0, 6.0)])
    assert _balcony_columns(layout, SimpleNamespace(width_m=1.5)) == [(0, 4.0, 5.5), (1, 2.0, 3.5)]
    assert _balcony_columns(Layout(), None) == []
