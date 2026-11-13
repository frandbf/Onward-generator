"""Plan Q3b, option B: Haussmann's zinc mansard on the top storey; the building keeps its OSM height."""

from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon, box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.facades import (
    DORMER_WIDTH_M,
    MANSARD_INSET_M,
    MANSARD_MATERIAL,
    MANSARD_MIN_STOREYS,
    Hole,
    Layout,
    Mansard,
    build_shell,
    mansard_ring,
    refine_exposure,
)

CFG = load_detail_config()
TEMPLATE = CFG.template_for("residential_multi", False)
STOREY = Mansard(3.0)


def _shell(neighbour: Polygon, layout: Layout | None = None, mansard: Mansard | None = STOREY):
    refined, segments = refine_exposure(box(0, 0, 12, 10), neighbour, CFG)
    return build_shell(refined, segments, layout or Layout(), 18.0, TEMPLATE, "plaster_ochre", "roof_flat_grey", "b1", CFG, mansard), segments


def test_the_constants() -> None:
    assert (MANSARD_INSET_M, MANSARD_MIN_STOREYS, MANSARD_MATERIAL, DORMER_WIDTH_M) == (1.0, 5, "roof_zinc", 1.0)


def test_a_free_standing_building_gets_four_sloped_zinc_faces_and_a_deck_at_its_height() -> None:
    shell, _ = _shell(Polygon())
    assert shell.defects == 0  # watertight: walls to the eaves, the slopes, the deck
    assert abs(shell.roof_area_error) < 1e-6  # the slopes and the deck cover the footprint
    assert shell.roof["type"] == "mansard" and shell.roof["rise_m"] == 3.0 and shell.roof["inset_m"] == 1.0
    zinc = np.vstack([m.vertices for m in shell.parts[MANSARD_MATERIAL]])
    assert zinc[:, 2].min() == 15.0 and zinc[:, 2].max() == 18.0
    deck = shell.roof_deck
    assert deck is not None and deck.equals(box(1, 1, 11, 9))
    walls = np.vstack([m.vertices for m in shell.parts["plaster_ochre"]])
    assert walls[:, 2].max() == 15.0  # the walls stop under the mansard
    assert shell.roof_z is None or shell.roof_z <= 18.0


def test_a_party_side_stays_upright_and_the_step_closes_the_shell() -> None:
    # A neighbour along the west wall: that side rises straight to the deck.
    shell, segments = _shell(box(-5, -5, 0, 15))
    assert shell.defects == 0
    deck = shell.roof_deck
    assert deck is not None and abs(deck.bounds[0]) < 1e-9 and abs(deck.bounds[2] - 11.0) < 1e-9
    upright = [m for m in shell.parts["plaster_ochre"] if np.ptp(m.vertices[:, 0]) < 1e-9 and m.vertices[:, 2].max() == 18.0]
    assert upright  # the party wall's top storey
    # Half the south wall against a neighbour: an exposed and a party piece on one line, joined by an upright step.
    stepped, _ = _shell(box(0, -5, 6, 0))
    assert stepped.defects == 0 and stepped.roof["type"] == "mansard"


def test_top_storey_windows_become_dormers() -> None:
    _, segments = _shell(Polygon())
    south = next(s for s in segments if abs(s.a[1]) < 1e-9 and abs(s.b[1]) < 1e-9)
    window = Hole("window", south.index, 3.0, 15.9, 1.2, 1.4, CFG.recess_depth_m, "glass_dark", 0, 5)
    lower = Hole("window", south.index, 3.0, 12.9, 1.2, 1.4, CFG.recess_depth_m, "glass_dark", 0, 4)
    shell, _ = _shell(Polygon(), Layout(holes=[window, lower]))
    assert shell.counts["dormer"] == 1 and shell.counts["window"] == 1
    plain, _ = _shell(Polygon(), Layout(holes=[window, lower]), mansard=None)
    assert "dormer" not in plain.counts and plain.counts["window"] == 2 and plain.roof["type"] != "mansard"


def test_a_ragged_or_courtyard_footprint_keeps_its_roof() -> None:
    refined, segments = refine_exposure(box(0, 0, 1.5, 10), Polygon(), CFG)
    assert mansard_ring(segments, 1.0) is None  # 1.5 m wide: the moved edges fold
    shell = build_shell(refined, segments, Layout(), 18.0, TEMPLATE, "plaster_ochre", "roof_flat_grey", "b1", CFG, Mansard(3.0))
    assert shell.roof["type"] != "mansard"
    court = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(5, 5), (15, 5), (15, 15), (5, 15)]])
    refined, segments = refine_exposure(court, Polygon(), CFG)
    assert build_shell(refined, segments, Layout(), 18.0, TEMPLATE, "plaster_ochre", "roof_flat_grey", "b1", CFG, Mansard(3.0)).roof["type"] != "mansard"


def test_the_wall_bands_and_their_cornice_end_under_the_mansard() -> None:
    from onward_generator.materials.trim import Bands, building_bands

    record = {"facade": {"storeys": {"count": 6, "height_m": 3.0}}, "roof": {"type": "mansard"}}
    assert building_bands(record, 18.0, 0.9, 4.5) == Bands(0.9, 3.0, 5, 15.0)
    assert building_bands({**record, "roof": {"type": "flat"}}, 18.0, 0.9, 4.5) == Bands(0.9, 3.0, 6, 18.0)
