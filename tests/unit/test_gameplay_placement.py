"""Spawn and Uplink placement by the gameplay metrics (plan C6): scores, the choice and its tie-breaks.

Graphs are small grids in local metres around the MURCIA_CENTER origin.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from shapely import Polygon, box

from onward_generator.gameplay.metrics import MAX_OVERLAP, NavGraph, Route, balance, dijkstra, from_edges, nav_graph, snap
from onward_generator.gameplay.placement import (
    CONTACT_TOLERANCE_PCT,
    KEPT,
    Choice,
    Placement,
    Routes,
    Score,
    admissible,
    contact_score,
    even_contact_node,
    note,
    route_score,
    score_all,
    select,
    team_distances,
)
from onward_generator.gameplay.report import build_report


def _s(index: int, diff: float | None = 10.0, routes: int | None = 3, overlap: float | None = 0.2, reason: str | None = None) -> Score:
    return Score(index, diff, routes, overlap, reason)


def _chain(count: int, step_m: float = 2.0) -> NavGraph:
    """Nodes 0..count-1 on the x axis, ``step_m`` apart."""
    return from_edges([(i * step_m, 0.0) for i in range(count)], {(i, i + 1): round(step_m * 1000) for i in range(count - 1)})


def _place(attack: tuple[float, float], defend: tuple[float, float], uplink: tuple[float, float],
           attack_b: tuple[float, float] | None = None, defend_b: tuple[float, float] | None = None) -> Placement:
    return Placement(attack, defend, uplink, attack_b or attack, defend_b or defend)


# ------------------------------------------------------------------------------------------------ select


def test_select_prefers_the_lowest_contact_difference() -> None:
    choice = select([_s(0, 20.0), _s(1, 5.0), _s(2, 12.0)])
    assert choice == Choice(1, 3, _s(0, 20.0), _s(1, 5.0), None)


def test_select_breaks_ties_on_routes_then_overlap_then_index() -> None:
    assert select([_s(0, 20.0, 3), _s(1, 5.0, 3), _s(2, 5.0, 4)]).index == 2
    assert select([_s(0, 20.0, 3, 0.3), _s(1, 5.0, 3, 0.25), _s(2, 5.0, 3, 0.2)]).index == 2
    # Equal on every measure: the lower index wins, whatever order the scores come in.
    assert select([_s(3, 5.0), _s(2, 5.0), _s(0, 20.0), _s(1, 5.0)]).index == 1
    assert select([_s(0, 20.0), _s(1, 5.0), _s(2, 5.0)]).index == 1


def test_select_keeps_candidate_0_unless_another_is_strictly_better() -> None:
    choice = select([_s(0), _s(1), _s(2)])
    assert (choice.index, choice.count, choice.baseline, choice.chosen) == (0, 3, _s(0), _s(0))
    assert choice.reason == f"{KEPT}: no candidate beats the current placement"
    assert select([_s(0)]).reason == f"{KEPT}: no candidate beats the current placement"


def test_select_refuses_fewer_routes_or_a_higher_difference() -> None:
    # A perfect balance with fewer routes than today is not admissible ...
    assert select([_s(0, 20.0, 3), _s(1, 0.0, 2)]).index == 0
    # ... and neither are more routes with a worse balance.
    assert select([_s(0, 20.0, 3), _s(1, 20.01, 6)]).index == 0
    # The same balance with more routes is.
    assert select([_s(0, 20.0, 3), _s(1, 20.0, 4)]).index == 1
    assert admissible(_s(1, 20.0, 3), _s(0, 20.0, 3))
    assert not admissible(_s(1, 20.0, 2), _s(0, 20.0, 3))


def test_select_overlap_limit_is_the_larger_of_today_and_one_half() -> None:
    low = _s(0, 20.0, 3, 0.3)
    assert MAX_OVERLAP == 0.5
    assert admissible(_s(1, 5.0, 3, 0.5), low)
    assert not admissible(_s(1, 5.0, 3, 0.5001), low)
    high = _s(0, 20.0, 3, 0.6)
    assert admissible(_s(1, 5.0, 3, 0.6), high)
    assert not admissible(_s(1, 5.0, 3, 0.6001), high)
    assert select([low, _s(1, 5.0, 3, 0.5001)]).index == 0
    assert select([low, _s(1, 5.0, 3, 0.5)]).index == 1


@pytest.mark.parametrize("candidate", [_s(1, None, None, None, "off"), _s(1, 0.0, None, None), _s(1, 0.0, 6, 0.1, "no path")])
def test_an_invalid_or_unscored_candidate_is_never_admissible(candidate: Score) -> None:
    assert not admissible(candidate, _s(0, 20.0, 3))
    assert select([_s(0, 20.0, 3), candidate]).index == 0


def test_select_keeps_the_current_placement_when_candidate_0_cannot_be_scored() -> None:
    off = select([_s(0, None, None, None, "attackers is off the navmesh"), _s(1, 0.0)])
    assert (off.index, off.count) == (0, 2)
    assert off.baseline == off.chosen == _s(0, None, None, None, "attackers is off the navmesh")
    assert off.reason == f"{KEPT}: candidate 0 cannot be scored (attackers is off the navmesh)"
    unscored = select([_s(0, 20.0, None, None), _s(1, 0.0)])
    assert unscored.reason == f"{KEPT}: candidate 0 cannot be scored (it has no route score)"
    missing = select([_s(1, 0.0)])
    assert missing == Choice(0, 1, Score(0, reason="candidate 0 was not scored"), Score(0, reason="candidate 0 was not scored"),
                             f"{KEPT}: candidate 0 was not scored")
    assert select([]).reason == f"{KEPT}: candidate 0 was not scored"


def test_select_is_deterministic() -> None:
    scores = [_s(0, 20.0), _s(1, 7.5, 4, 0.3), _s(2, 7.5, 4, 0.2), _s(3, 9.0, 6)]
    assert select(scores) == select(list(scores)) == select(scores[::-1])
    assert select(scores).index == 3  # within 15 %, the most routes
    tied = [_s(0, 20.0), _s(1, 7.5, 4, 0.3), _s(2, 7.5, 4, 0.2), _s(3, 9.0, 4)]
    assert select(tied).index == 2  # the same routes: the lower difference, then the lower overlap


def test_within_the_contact_tolerance_more_routes_win_and_past_it_the_balance() -> None:
    """C6 amendment of 11 November 2026: ADR-032's 15 % proposal bounds where routes come first."""
    assert CONTACT_TOLERANCE_PCT == 15.0
    assert select([_s(0, 40.0, 2), _s(1, 0.5, 3), _s(2, 15.0, 5)]).index == 2  # 15 % is within
    assert select([_s(0, 40.0, 2), _s(1, 0.5, 3), _s(2, 15.01, 5)]).index == 1  # just past it: the balance first
    assert select([_s(0, 40.0, 2), _s(1, 20.0, 3), _s(2, 30.0, 5)]).index == 1  # both past it: the lower difference
    assert select([_s(0, 40.0, 2), _s(1, 20.0, 3), _s(2, 20.0, 5)]).index == 2  # past it, a tie: then the routes


# ------------------------------------------------------------------------------------------------ scores


def test_a_centre_snaps_up_to_2_m_from_a_node() -> None:
    graph = _chain(5)
    on = contact_score(graph, _place((0.0, 2.0), (8.0, 0.0), (4.0, 0.0)), 0)
    assert on == Score(0, diff_pct=0.0)
    off = contact_score(graph, _place((0.0, 2.01), (8.0, 0.0), (4.0, 0.0)), 1)
    assert off == Score(1, reason="attackers is off the navmesh") and not off.valid
    assert contact_score(graph, _place((0.0, 0.0), (8.0, 0.0), (4.0, 0.0), defend_b=(8.0, -2.01)), 2).reason == "defenders_b is off the navmesh"
    assert contact_score(graph, _place((0.0, 0.0), (8.0, 0.0), (4.0, 2.01)), 3).reason == "the Uplink is off the navmesh"


def test_an_uplink_on_another_island_has_no_path() -> None:
    graph = from_edges([(0.0, 0.0), (2.0, 0.0), (4.0, 0.0), (40.0, 0.0)], {(0, 1): 2000, (1, 2): 2000})
    assert contact_score(graph, _place((0.0, 0.0), (4.0, 0.0), (40.0, 0.0)), 2) == Score(2, reason="no path from the attackers to the Uplink")
    assert contact_score(graph, _place((0.0, 0.0), (40.0, 0.0), (2.0, 0.0)), 3) == Score(3, reason="no path from the defenders to the Uplink")
    split = route_score(graph, _place((0.0, 0.0), (40.0, 0.0), (2.0, 0.0)), Score(0, diff_pct=0.0))
    assert split.reason == "no path from attackers to defenders" and split.routes is None
    off = route_score(graph, _place((0.0, 9.0), (4.0, 0.0), (2.0, 0.0)), Score(0, diff_pct=0.0))
    assert off.reason == "attackers is off the navmesh"


def test_the_contact_difference_uses_each_teams_nearer_block() -> None:
    graph = _chain(11)  # 0 .. 20 m
    # The Uplink at 6 m: attackers 6 m (main block), defenders 14 m; their second block at 10 m is nearer.
    placement = _place((0.0, 0.0), (20.0, 0.0), (6.0, 0.0), attack_b=(0.0, 0.0), defend_b=(10.0, 0.0))
    assert contact_score(graph, placement, 0).diff_pct == balance(6.0, 4.0)["diff_pct"] == 33.33
    assert contact_score(graph, dataclasses.replace(placement, defend_b=(20.0, 0.0)), 0).diff_pct == balance(6.0, 14.0)["diff_pct"]
    # The attackers' second block nearer than their main one.
    nearer = _place((0.0, 0.0), (20.0, 0.0), (12.0, 0.0), attack_b=(8.0, 0.0), defend_b=(20.0, 0.0))
    assert contact_score(graph, nearer, 0).diff_pct == balance(4.0, 8.0)["diff_pct"] == 50.0


RING = Polygon([(0, 0), (60, 0), (60, 40), (0, 40)], [[(10, 10), (50, 10), (50, 30), (10, 30)]])


def _layout(placement: Placement) -> dict[str, Any]:
    return {
        "schema": "onward_generator.gameplay_layout", "schema_version": "0.3.0",
        **{block: {"centre_m": list(centre)} for block, centre in placement.blocks().items()},
        "uplink_m": list(placement.uplink),
    }


@pytest.mark.parametrize("uplink", [(30.0, 5.0), (5.0, 30.0), (55.0, 20.0)])
def test_the_score_is_the_reports_headline(uplink: tuple[float, float]) -> None:
    graph = nav_graph([RING])
    placement = Placement((5.0, 5.0), (55.0, 35.0), uplink, (5.0, 35.0), (55.0, 5.0))
    score = route_score(graph, placement, contact_score(graph, placement, 0))
    headline = build_report([RING], box(0, 0, 0, 0), [], _layout(placement))["headline"]
    assert score.valid
    assert (score.diff_pct, score.routes) == (headline["contact_diff_pct"], headline["distinct_routes"])
    assert score.max_overlap is not None and round(score.max_overlap * 100, 2) == headline["max_overlap_pct"]
    assert score.routes >= 2


def test_route_score_reuses_the_routes_of_a_pair() -> None:
    graph = nav_graph([RING])
    placement = Placement((5.0, 5.0), (55.0, 35.0), (30.0, 5.0), (5.0, 35.0), (55.0, 5.0))
    cache: Routes = {}
    first = route_score(graph, placement, Score(0, diff_pct=1.0), cache)
    assert len(cache) == 2
    pair = next(iter(cache))
    cache[pair] = [Route((pair[0], pair[1]), 1, 1)]
    again = route_score(graph, placement, Score(0, diff_pct=1.0), cache)
    assert again.routes == 1 and first.routes is not None and first.routes > 1


def test_route_score_rounds_the_overlap_to_4_places_and_is_0_with_one_route() -> None:
    # Route 1 runs 0 -> 1 (90 m). Route 2 runs 0 -> 2 -> 3 -> 1 in three 1 m-cost edges, the first beside route 1.
    graph = from_edges([(0.0, 0.0), (30.0, 0.0), (0.0, 5.0), (30.0, 40.0)], {(0, 1): 90000, (0, 2): 1000, (2, 3): 1000, (1, 3): 1000})
    long, short = Route((0, 1), 90000, 1), Route((0, 2, 3, 1), 3000, 2)
    placement = _place((0.0, 0.0), (30.0, 0.0), (0.0, 5.0))
    score = route_score(graph, placement, Score(0, diff_pct=1.0), {(0, 1): [long, short]})
    assert (score.routes, score.max_overlap) == (2, 0.3333)
    single = route_score(_chain(11), _place((0.0, 0.0), (20.0, 0.0), (10.0, 0.0)), Score(0, diff_pct=0.0))
    assert (single.routes, single.max_overlap, single.reason) == (1, 0.0, None)


# ------------------------------------------------------------------------------------------------ stage 2


def test_stage_2_scores_every_candidate_no_less_balanced_than_candidate_0() -> None:
    graph = _chain(11)
    calls: list[int] = []
    caches: list[Routes] = []

    def fake(graph: NavGraph, placement: Placement, score: Score, cache: Routes) -> Score:
        calls.append(score.index)
        caches.append(cache)
        return dataclasses.replace(score, routes=3, max_overlap=0.2)

    a, d = (0.0, 0.0), (20.0, 0.0)
    candidates: list[Placement | str] = [
        _place(a, d, (6.0, 0.0)),     # 0: 6 / 14 m
        _place(a, d, (2.0, 0.0)),     # 1: worse than 0, never scored
        "no second block fits",       # 2: invalid
        _place(a, d, (8.0, 0.0)),     # 3: 8 / 12 m
        _place(a, d, (10.0, 0.0)),    # 4: even
        _place(a, d, (12.0, 0.0)),    # 5: the mirror of 3
    ]
    scores = score_all(graph, candidates, routes=fake)
    # Within the tolerance a less balanced candidate with more routes may win, so 3 and 5 are scored too.
    assert calls == [0, 3, 4, 5]
    # One cache, shared by every candidate.
    assert caches[0] == {} and caches[0] is caches[1]
    assert [s.index for s in scores] == [0, 1, 2, 3, 4, 5]
    assert scores[1].routes is None and scores[1].diff_pct is not None and scores[1].diff_pct > scores[0].diff_pct  # type: ignore[operator]
    assert scores[2] == Score(2, reason="no second block fits")
    assert scores[3].routes == scores[5].routes == 3
    assert select(scores).index == 4  # the same routes everywhere: the even one


def test_stage_2_moves_on_while_the_better_balance_is_not_admissible() -> None:
    graph = _chain(11)
    calls: list[int] = []

    def fake(graph: NavGraph, placement: Placement, score: Score, cache: Routes) -> Score:
        calls.append(score.index)
        return dataclasses.replace(score, routes=1 if score.index == 2 else 3, max_overlap=0.2)

    a, d = (0.0, 0.0), (20.0, 0.0)
    candidates: list[Placement | str] = [_place(a, d, (6.0, 0.0)), _place(a, d, (8.0, 0.0)), _place(a, d, (10.0, 0.0)), _place(a, d, (12.0, 0.0))]
    scores = score_all(graph, candidates, routes=fake)
    # 2 is even but has fewer routes than 0, so it is not admissible; 1 and 3 tie on 20 %, past the tolerance,
    # and 1 wins on the index. Every candidate no less balanced than 0 is scored, in index order.
    assert calls == [0, 1, 2, 3]
    assert select(scores).index == 1


def test_stage_2_scores_a_candidate_as_balanced_as_candidate_0() -> None:
    graph = _chain(11)

    def fake(graph: NavGraph, placement: Placement, score: Score, cache: Routes) -> Score:
        return dataclasses.replace(score, routes=3 + score.index, max_overlap=0.2)

    a, d = (0.0, 0.0), (20.0, 0.0)
    scores = score_all(graph, [_place(a, d, (6.0, 0.0)), _place(a, d, (14.0, 0.0))], routes=fake)
    assert scores[0].diff_pct == scores[1].diff_pct
    assert scores[1].routes == 4 and select(scores).index == 1


def test_stage_2_stops_when_candidate_0_has_no_route() -> None:
    graph = _chain(11)

    def fake(graph: NavGraph, placement: Placement, score: Score, cache: Routes) -> Score:
        return dataclasses.replace(score, reason="no path from attackers to defenders")

    candidates: list[Placement | str] = [_place((0.0, 0.0), (20.0, 0.0), (6.0, 0.0)), _place((0.0, 0.0), (20.0, 0.0), (10.0, 0.0))]
    scores = score_all(graph, candidates, routes=fake)
    assert scores[1].routes is None
    assert select(scores).reason == f"{KEPT}: candidate 0 cannot be scored (no path from attackers to defenders)"
    off = score_all(graph, ["no block", *candidates[1:]])
    assert off == [Score(0, reason="no block"), Score(1, diff_pct=0.0)]
    assert score_all(graph, []) == []


def test_score_all_with_the_real_route_score_matches_route_score() -> None:
    graph = nav_graph([RING])
    candidates: list[Placement | str] = [
        Placement((5.0, 5.0), (55.0, 35.0), (5.0, 20.0), (5.0, 35.0), (55.0, 5.0)),
        Placement((5.0, 5.0), (55.0, 35.0), (30.0, 5.0), (5.0, 35.0), (55.0, 5.0)),
    ]
    scores = score_all(graph, candidates)
    for score, candidate in zip(scores, candidates, strict=True):
        assert isinstance(candidate, Placement)
        assert score == route_score(graph, candidate, contact_score(graph, candidate, score.index))


# ------------------------------------------------------------------------------------------------ even contact


def test_even_contact_node_is_the_middle_of_the_path() -> None:
    graph = _chain(5)
    a, _ = dijkstra(graph, 0)
    d, _ = dijkstra(graph, 4)
    assert even_contact_node(a, d, [0, 1, 2, 3, 4], set(range(5))) == 2
    # An even count: nodes 1 and 2 tie, the lower index wins.
    graph = _chain(4)
    a, _ = dijkstra(graph, 0)
    d, _ = dijkstra(graph, 3)
    assert even_contact_node(a, d, [0, 1, 2, 3], set(range(4))) == 1
    assert even_contact_node(a, d, [3, 2, 1, 0], set(range(4))) == 1
    assert even_contact_node(a, d, [0, 1, 2, 3], {0, 2, 3}) == 2
    assert even_contact_node(a, d, [0, 1, 2, 3], {0, 3}) == 0
    assert even_contact_node(a, d, [0, 1, 2, 3], set()) is None
    assert even_contact_node([0, None, 2000], [2000, 0, None], [0, 1, 2], {0, 1, 2}) == 0


def test_team_distances_take_the_nearer_block() -> None:
    graph = from_edges([(0.0, 0.0), (2.0, 0.0), (4.0, 0.0), (6.0, 0.0), (40.0, 0.0)], {(0, 1): 2000, (1, 2): 2000, (2, 3): 2000})
    assert team_distances(graph, [0, 3]) == [0, 2000, 2000, 0, None]
    assert team_distances(graph, [0]) == [0, 2000, 4000, 6000, None]
    assert team_distances(graph, []) == [None] * 5
    assert snap(graph, (40.0, 0.0)) == 4


# ------------------------------------------------------------------------------------------------ note


def test_note_names_the_candidate_and_the_measures() -> None:
    chosen = Choice(3, 8, _s(0, 29.48, 3, 0.4335), _s(3, 11.2, 4, 0.382))
    assert note(chosen) == (
        "placement: balanced, candidate 3 of 8, contact diff 29.48 -> 11.20 %, routes 3 -> 4, "
        "overlap 43.35 -> 38.20 % (estimated from the 2D navmesh)"
    )
    kept = select([_s(0)])
    assert note(kept) == f"placement: {KEPT}: no candidate beats the current placement (estimated from the 2D navmesh)"


def test_placement_blocks_use_the_layout_names() -> None:
    placement = Placement((1.0, 2.0), (3.0, 4.0), (5.0, 6.0), (7.0, 8.0), (9.0, 10.0))
    assert placement.blocks() == {"attackers": (1.0, 2.0), "defenders": (3.0, 4.0), "attackers_b": (7.0, 8.0), "defenders_b": (9.0, 10.0)}
    assert Score(0, diff_pct=0.0).valid and not Score(0).valid and not Score(0, diff_pct=0.0, reason="x").valid
