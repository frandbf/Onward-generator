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


def _hash(meshes) -> str:
    import hashlib

    return hashlib.sha256(b"".join(np.round(m.vertices, 4).tobytes() + np.asarray(m.triangles, np.int64).tobytes() for m in meshes)).hexdigest()[:12]


def test_a_neighbour_in_the_middle_of_the_street_wall_gives_two_steps_and_the_dormers_are_pinned() -> None:
    refined, segments = refine_exposure(box(0, 0, 12, 10), box(4, -5, 8, 0), CFG)
    south = [s for s in segments if abs(s.a[1]) < 1e-9 and abs(s.b[1]) < 1e-9 and s.exposed]
    east = next(s for s in segments if abs(s.a[0] - 12) < 1e-9 and abs(s.b[0] - 12) < 1e-9)
    holes = [Hole("window", south[0].index, 1.0, 15.4, 1.2, 1.4, CFG.recess_depth_m, "glass_dark", 0, 5),
             Hole("window", east.index, 4.0, 15.4, 1.2, 1.4, CFG.recess_depth_m, "glass_dark", 1, 5)]
    shell = build_shell(refined, segments, Layout(holes=holes), 17.2, TEMPLATE, "plaster_ochre", "roof_flat_grey", "b1", CFG, Mansard(2.2))
    assert shell.defects == 0 and shell.roof_area_error == 0.0
    assert shell.roof == {"type": "mansard", "rise_m": 2.2, "inset_m": 1.0, "slope_material": "roof_zinc", "material": "roof_flat_grey"}
    assert dict(shell.counts) == {"dormer": 2}
    assert {k: (len(v), _hash(v)) for k, v in sorted(shell.parts.items())} == {
        "frame_stone": (2, "6ee133291949"), "glass_dark": (2, "a808fa43e75b"), "plaster_ochre": (9, "6f9f3e0c1fc8"),
        "roof_flat_grey": (1, "70e7753298cb"), "roof_zinc": (5, "5625ae023c4d")}
    assert [[np.round(a, 4).tolist(), np.round(b, 4).tolist()] for a, b in mansard_ring(segments, 1.0)] == [
        [[11.0, 1.0], [11.0, 9.0]], [[11.0, 9.0], [1.0, 9.0]], [[1.0, 9.0], [1.0, 1.0]], [[1.0, 1.0], [4.0, 1.0]],
        [[4.0, 0.0], [8.0, 0.0]], [[8.0, 1.0], [11.0, 1.0]]]


def test_each_step_faces_the_exposed_piece_below_it() -> None:
    from onward_generator.detail.facades import _step

    refined, segments = refine_exposure(box(0, 0, 12, 10), box(4, -5, 8, 0), CFG)
    party = next(s for s in segments if not s.exposed)
    after = next(s for s in segments if s.a == party.b)
    for seg, start, previous_end, side in (
        (party, np.array([4.0, 0.0]), np.array([4.0, 1.0]), -1.0),  # the exposed piece lies west, toward -x
        (after, np.array([8.0, 1.0]), np.array([8.0, 0.0]), 1.0),  # the exposed piece lies east
    ):
        step = _step(seg, 15.0, 18.0, start, previous_end)
        v = step.vertices
        normal = np.cross(v[step.triangles[0][1]] - v[step.triangles[0][0]], v[step.triangles[0][2]] - v[step.triangles[0][0]])
        assert np.sign(normal[0]) == side and abs(normal[1]) < 1e-9 and abs(normal[2]) < 1e-9
        assert sorted(map(tuple, v.round(6).tolist())) == sorted([(*seg.a, 15.0), (*start, 18.0), (*previous_end, 18.0)])


def test_a_triangle_and_a_courtyard_ring_and_a_rise_to_the_millimetre() -> None:
    from shapely.geometry import Polygon as P

    _, tri = refine_exposure(P([(0, 0), (20, 0), (10, 15)]), Polygon(), CFG)
    triangle = mansard_ring(tri, 1.0)  # slanted corners: the moved edges meet where their lines cross
    assert triangle is not None and [[np.round(a, 4).tolist(), np.round(b, 4).tolist()] for a, b in triangle] == [
        [[1.8685, 1.0], [18.1315, 1.0]], [[18.1315, 1.0], [10.0, 13.1972]], [[10.0, 13.1972], [1.8685, 1.0]]]
    court = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(5, 5), (15, 5), (15, 15), (5, 15)]])
    _, segments = refine_exposure(court, Polygon(), CFG)
    ring = mansard_ring(segments, 1.0)
    assert ring is not None and [[a.tolist(), b.tolist()] for a, b in ring] == [
        [[1.0, 1.0], [19.0, 1.0]], [[19.0, 1.0], [19.0, 19.0]], [[19.0, 19.0], [1.0, 19.0]], [[1.0, 19.0], [1.0, 1.0]]]  # the outer ring only
    shell, _ = _shell(Polygon(), mansard=Mansard(3.12345))
    assert shell.roof["rise_m"] == 3.123


def test_only_a_tall_flat_haussmann_building_without_a_landmark_gets_a_mansard() -> None:
    from types import SimpleNamespace

    from onward_generator.detail.scene import mansard_for

    five = SimpleNamespace(count=5, height_m=3.1)
    flat = SimpleNamespace(pitched=None, hip=None)
    assert mansard_for(True, False, five, flat) == Mansard(3.1)
    assert mansard_for(True, False, SimpleNamespace(count=4, height_m=3.1), flat) is None
    assert mansard_for(False, False, five, flat) is None
    assert mansard_for(True, True, five, flat) is None
    assert mansard_for(True, False, five, SimpleNamespace(pitched=object(), hip=None)) is None
    assert mansard_for(True, False, five, SimpleNamespace(pitched=None, hip=object())) is None


def test_a_deck_under_a_quarter_of_the_footprint_keeps_the_roof() -> None:
    for width, kind in ((2.6, "flat"), (3.0, "mansard")):  # decks of 0.6 and 1 m: 21 % and 31 % of the footprint
        refined, segments = refine_exposure(box(0, 0, width, 30), Polygon(), CFG)
        shell = build_shell(refined, segments, Layout(), 18.0, TEMPLATE, "plaster_ochre", "roof_flat_grey", "b1", CFG, STOREY)
        assert (shell.roof["type"] == "mansard") == (kind == "mansard")


def test_a_window_is_a_dormer_only_when_it_rises_past_the_eaves() -> None:
    _, segments = _shell(Polygon())
    south = next(s for s in segments if abs(s.a[1]) < 1e-9 and abs(s.b[1]) < 1e-9)

    def dormers(v0: float) -> int:
        hole = Hole("window", south.index, 3.0, v0, 1.2, 1.4, CFG.recess_depth_m, "glass_dark", 0, 4)
        return _shell(Polygon(), Layout(holes=[hole]))[0].counts.get("dormer", 0)

    assert dormers(13.6) == 0  # its top exactly at the eaves, 15 m
    assert dormers(13.6001) == 1 and dormers(14.0) == 1
