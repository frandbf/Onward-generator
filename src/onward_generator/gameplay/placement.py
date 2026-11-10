"""Spawn and Uplink placement chosen by the gameplay metrics (plan C6, ADR-032 C6 amendment, off by default).

Pure functions on the B4 navigation graph. ``perf.layout.choose_placement`` builds a few
candidate placements in a fixed order; this module scores them with the same definitions
the gameplay report uses (``BLOCKS``, ``PAIRS``, ``TEAMS``, ``snap``, ``dijkstra``,
``distinct_routes``, ``overlap``, ``balance``) and picks the most balanced one.

Every score is estimated on the 2D navmesh grid, not on the mesh Unity bakes, and is
uncalibrated until the playtest (plan 4.2). No randomness: integer millimetre costs and
the candidate index break every tie, so two runs choose the same placement.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Container, Sequence
from dataclasses import dataclass

from onward_generator.gameplay.metrics import MAX_OVERLAP, NavGraph, Route, balance, dijkstra, distinct_routes, overlap, snap
from onward_generator.gameplay.report import PAIRS, TEAMS

Point = tuple[float, float]
KEPT = "kept the current placement"


@dataclass(frozen=True, slots=True)
class Placement:
    """The four spawn block centres and the Uplink, local metres rounded to the centimetre."""

    attack: Point
    defend: Point
    uplink: Point
    attack_b: Point
    defend_b: Point

    def blocks(self) -> dict[str, Point]:
        """The centres by the layout's block names (``report.BLOCKS``)."""
        return {"attackers": self.attack, "defenders": self.defend, "attackers_b": self.attack_b, "defenders_b": self.defend_b}


@dataclass(frozen=True, slots=True)
class Score:
    """A candidate's measures; ``reason`` names why it is invalid, and ``routes`` is None until stage 2 ran.

    ``diff_pct`` is the report's ``headline.contact_diff_pct``, ``routes`` its ``distinct_routes`` and
    ``max_overlap`` the largest pairwise overlap as a fraction with 4 decimals (``max_overlap_pct`` / 100).
    """

    index: int
    diff_pct: float | None = None
    routes: int | None = None
    max_overlap: float | None = None
    reason: str | None = None

    @property
    def valid(self) -> bool:
        return self.reason is None and self.diff_pct is not None


@dataclass(frozen=True, slots=True)
class Choice:
    """The chosen candidate (0 is the current placement), how many were scored, and why candidate 0 was kept."""

    index: int
    count: int
    baseline: Score
    chosen: Score
    reason: str | None = None


def _nodes(graph: NavGraph, placement: Placement) -> tuple[dict[str, int], int] | str:
    """The snapped node of each block and of the Uplink, or the reason one is off the navmesh."""
    nodes: dict[str, int] = {}
    for block, centre in placement.blocks().items():
        node = snap(graph, centre)
        if node is None:
            return f"{block} is off the navmesh"
        nodes[block] = node
    uplink = snap(graph, placement.uplink)
    if uplink is None:
        return "the Uplink is off the navmesh"
    return nodes, uplink


def contact_score(graph: NavGraph, placement: Placement, index: int) -> Score:
    """Stage 1: the difference between the teams' times to the Uplink, as the report's ``balance``.

    A team's distance is the shorter of its two blocks' distances.
    """
    found = _nodes(graph, placement)
    if isinstance(found, str):
        return Score(index, reason=found)
    nodes, uplink = found
    best, _ = dijkstra(graph, uplink)
    distances: dict[str, int] = {}
    for team, blocks in TEAMS.items():
        known = [value for value in (best[nodes[block]] for block in blocks) if value is not None]
        if not known:
            return Score(index, reason=f"no path from the {team} to the Uplink")
        distances[team] = min(known)
    diff = balance(distances["attackers"] / 1000.0, distances["defenders"] / 1000.0)["diff_pct"]
    return Score(index, diff_pct=diff)


Routes = dict[tuple[int, int], list[Route]]


def route_score(graph: NavGraph, placement: Placement, score: Score, cache: Routes | None = None) -> Score:
    """Stage 2: the distinct routes of the poorer spawn pair and the largest overlap, as the report's headline.

    ``cache`` keeps the routes of each (start, goal) node pair: candidates that differ only in the Uplink share them.
    """
    found = _nodes(graph, placement)
    if isinstance(found, str):
        return dataclasses.replace(score, reason=found)
    nodes, _ = found
    known: Routes = {} if cache is None else cache
    counts: list[int] = []
    shares: list[float] = []
    for a, b in PAIRS:
        pair = (nodes[a], nodes[b])
        if pair not in known:
            known[pair] = distinct_routes(graph, *pair)
        routes = known[pair]
        if not routes:
            return dataclasses.replace(score, reason=f"no path from {a} to {b}")
        counts.append(len(routes))
        shares.extend(round(overlap(graph, routes[i], routes[j]), 4) for i in range(len(routes)) for j in range(i + 1, len(routes)))
    return dataclasses.replace(score, routes=min(counts), max_overlap=max(shares, default=0.0))


RouteScorer = Callable[[NavGraph, Placement, Score, Routes], Score]


def score_all(graph: NavGraph, candidates: Sequence[Placement | str], *, routes: RouteScorer = route_score) -> list[Score]:
    """Stage 1 for every candidate, then stage 2 where it can change the choice.

    Stage 2 runs for candidate 0, then for the others whose contact difference is not above candidate 0's,
    lowest difference first (then lowest index). Once one is admissible, a candidate with a higher difference
    can no longer win, so it keeps its stage 1 score. A candidate given as a string is invalid for that reason.
    """
    scores = [
        Score(index, reason=candidate) if isinstance(candidate, str) else contact_score(graph, candidate, index)
        for index, candidate in enumerate(candidates)
    ]
    if not scores or not scores[0].valid:
        return scores
    cache: Routes = {}
    first = candidates[0]
    assert isinstance(first, Placement)  # pragma: no mutate: a valid candidate is a placement
    baseline = scores[0] = routes(graph, first, scores[0], cache)
    if not baseline.valid:
        return scores
    limit = baseline.diff_pct
    assert limit is not None  # pragma: no mutate: a valid score has a contact difference
    # Stage 1 leaves diff_pct None exactly when the candidate is invalid.
    order = sorted((s for s in scores[1:] if s.diff_pct is not None and s.diff_pct <= limit), key=lambda s: (s.diff_pct, s.index))
    best: float | None = None
    for score in order:
        assert score.diff_pct is not None  # pragma: no mutate: order holds only scored candidates
        if best is not None and score.diff_pct > best:
            break
        candidate = candidates[score.index]
        assert isinstance(candidate, Placement)  # pragma: no mutate: a scored candidate is a placement
        scores[score.index] = routes(graph, candidate, score, cache)
        if admissible(scores[score.index], baseline):
            best = score.diff_pct
    return scores


def _key(score: Score) -> tuple[float, int, float, int]:
    assert score.diff_pct is not None and score.routes is not None and score.max_overlap is not None  # pragma: no mutate: only admissible scores reach the key
    return (score.diff_pct, -score.routes, score.max_overlap, score.index)


def admissible(score: Score, baseline: Score) -> bool:
    """Valid, scored in stage 2, and no worse than candidate 0 in contact difference, routes or overlap.

    The overlap may rise up to ``MAX_OVERLAP`` when candidate 0 is below it: a route that shares at most
    half of another is still distinct (ADR-032).
    """
    if not score.valid or score.routes is None:
        return False
    assert score.diff_pct is not None and score.max_overlap is not None  # pragma: no mutate: stage 2 sets routes and max_overlap together
    assert baseline.diff_pct is not None and baseline.routes is not None and baseline.max_overlap is not None  # pragma: no mutate: select checks the baseline first
    return (
        score.diff_pct <= baseline.diff_pct
        and score.routes >= baseline.routes
        and score.max_overlap <= max(baseline.max_overlap, MAX_OVERLAP)
    )


def select(scores: Sequence[Score]) -> Choice:
    """The lowest (diff_pct, -routes, max_overlap, index) among the admissible candidates.

    Candidate 0 is the current placement. It stays unless another one is strictly lower on that key,
    and it stays with a reason when it cannot be scored itself.
    """
    baseline = next((score for score in scores if score.index == 0), None)
    if baseline is None:
        missing = Score(0, reason="candidate 0 was not scored")
        return Choice(0, len(scores), missing, missing, f"{KEPT}: {missing.reason}")
    if not baseline.valid or baseline.routes is None:
        why = baseline.reason or "it has no route score"
        return Choice(0, len(scores), baseline, baseline, f"{KEPT}: candidate 0 cannot be scored ({why})")
    best = min((score for score in scores if admissible(score, baseline)), key=_key)
    if best.index == 0:
        return Choice(0, len(scores), baseline, baseline, f"{KEPT}: no candidate beats the current placement")
    return Choice(best.index, len(scores), baseline, best)


def team_distances(graph: NavGraph, sources: Sequence[int]) -> list[int | None]:
    """Per node, the shortest distance in millimetres from any of ``sources`` (None when unreachable)."""
    result: list[int | None] = [None] * graph.size
    for source in sources:
        best, _ = dijkstra(graph, source)
        for node, value in enumerate(best):
            known = result[node]
            if value is not None and (known is None or value < known):  # pragma: no mutate: replacing an equal distance keeps it
                result[node] = value
    return result


def even_contact_node(
    attackers: Sequence[int | None], defenders: Sequence[int | None], path: Sequence[int], allowed: Container[int],
) -> int | None:
    """The node of ``path`` in ``allowed`` where the teams' distances differ least; the lower index on a tie."""
    best: tuple[int, int] | None = None
    for node in path:
        a, d = attackers[node], defenders[node]
        if node not in allowed or a is None or d is None:
            continue
        key = (abs(a - d), node)
        if best is None or key < best:  # pragma: no mutate: keys end with the node, so two are equal only for a node listed twice
            best = key
    return None if best is None else best[1]


def note(choice: Choice) -> str:
    """The ``placement:`` line that ``perf layout --balanced-spawns`` prints. Estimated, not measured."""
    if choice.reason is not None:
        return f"placement: {choice.reason} (estimated from the 2D navmesh)"
    old, new = choice.baseline, choice.chosen
    assert old.max_overlap is not None and new.max_overlap is not None  # pragma: no mutate: a chosen candidate has both
    return (
        f"placement: balanced, candidate {choice.index} of {choice.count}, "
        f"contact diff {old.diff_pct:.2f} -> {new.diff_pct:.2f} %, routes {old.routes} -> {new.routes}, "
        f"overlap {old.max_overlap * 100:.2f} -> {new.max_overlap * 100:.2f} % (estimated from the 2D navmesh)"
    )
