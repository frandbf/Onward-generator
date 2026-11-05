"""``building:part`` extrusion at its edges: area, height order, hidden parts and rounding, on exact local coordinates."""

from __future__ import annotations

from typing import Any

import numpy as np
from shapely import box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.scene import PART_OUTLINE_M, _building_parts
from onward_generator.generation.mesh import Mesh

CFG = load_detail_config()
TOP = 12.0


def _part(fid: str, x0: float, y0: float, x1: float, y1: float, **tags: str) -> dict[str, Any]:
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return {"id": fid, "kind": "line", "tags": {"building:part": "yes", **tags},
            "geometry": {"type": "LineString", "coordinates": [*ring, ring[0]]}}


def _run(*features: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str | None]]]:
    objects, issues = _building_parts(list(features), {"b": box(0, 0, 20, 20)}, CFG,
                                      storey_of=lambda b: (3.0, "unknown"), top_of={"b": TOP}, parapet_of={})
    return {o.record["source_ids"][1]: o.record for o in objects}, [(i.code, i.object_id) for i in issues]


def test_a_part_of_one_square_metre_is_drawn_and_a_smaller_one_is_not() -> None:
    kept, issues = _run(_part("one", 1, 1, 2, 2, height="14"), _part("small", 4, 4, 4.9, 5, height="14"),
                        _part("one_and_a_half", 6, 6, 7, 7.5, height="14"))
    assert set(kept) == {"one", "one_and_a_half"} and issues == []


def test_a_part_whose_base_is_its_top_is_refused() -> None:
    kept, issues = _run(_part("flat", 1, 1, 5, 5, height="14", min_height="14"))
    assert kept == {} and issues == [("building_part_height_order", "flat")]


def test_a_part_as_high_as_its_building_is_hidden_and_a_hair_higher_is_too() -> None:
    kept, issues = _run(_part("level", 1, 1, 5, 5, height="12"), _part("hair", 6, 6, 9, 9, height="12.0000005"),
                        _part("above", 10, 10, 14, 14, height="12.00001"))
    assert set(kept) == {"above"}
    assert issues == [("building_part_hidden", "level"), ("building_part_hidden", "hair")]


def test_the_eave_is_rounded_to_the_millimetre() -> None:
    kept, _ = _run(_part("roofed", 1, 1, 7, 5, height="20", **{"roof:shape": "gabled", "roof:height": "3.33333"}))
    assert kept["roofed"]["height"]["eave_m"] == 16.667


def test_a_part_whose_eaves_are_its_building_top_draws_no_wall() -> None:
    objects, _ = _building_parts([_part("cap", 1, 1, 5, 5, height="16", **{"roof:shape": "pyramidal", "roof:height": "4"}),
                                  _part("walled", 6, 6, 10, 10, height="17", **{"roof:shape": "pyramidal", "roof:height": "4"})],
                                 {"b": box(0, 0, 20, 20)}, CFG, storey_of=lambda b: (3.0, "unknown"), top_of={"b": TOP},
                                 parapet_of={})
    visual = {o.record["source_ids"][1]: o.visual for o in objects}
    assert [p.material for p in visual["cap"]] == ["roof_tile"]  # eaves at 12 m: only the roof
    assert len(visual["walled"]) == 2 and min(float(p.mesh.vertices[:, 2].min()) for p in visual["walled"]) == TOP


PARAPET = 0.7


def _walls_of(*features: dict[str, Any], parapet: float | None = PARAPET) -> dict[str, Mesh]:
    """Walls of each part on a 20 m square building; ``parapet=None`` leaves the building out of ``parapet_of``."""
    objects, _ = _building_parts(list(features), {"b": box(0, 0, 20, 20)}, CFG, storey_of=lambda b: (3.0, "unknown"),
                                 top_of={"b": TOP}, parapet_of={} if parapet is None else {"b": parapet})
    return {o.record["source_ids"][1]: p.mesh for o in objects for p in o.visual if p.material != "roof_tile"}


def _base_on(mesh: Mesh, axis: int, at: float) -> float | None:
    """Lowest z of the wall triangles lying in the vertical plane ``axis = at``, or None when there is none."""
    tris = mesh.vertices[mesh.triangles]
    on = np.all(np.abs(tris[:, :, axis] - at) < 1e-9, axis=1)
    return float(tris[on][:, :, 2].min()) if on.any() else None


def test_a_part_on_the_outline_of_a_parapet_building_rises_from_the_parapet_top_there() -> None:
    """Barcelona Sarria: the part walls and the building's 0.7 m of parapet z-fought in the same plane."""
    mesh = _walls_of(_part("corner", 0, 0, 5, 5, height="18"))["corner"]
    assert _base_on(mesh, 0, 0.0) == _base_on(mesh, 1, 0.0) == TOP + PARAPET  # on the outline
    assert _base_on(mesh, 0, 5.0) == _base_on(mesh, 1, 5.0) == TOP  # over the roof deck: no gap under the part
    assert float(mesh.vertices[:, 2].max()) == 18.0


def test_a_part_inside_the_outline_or_without_a_parapet_keeps_its_walls_from_the_building_top() -> None:
    inside = _walls_of(_part("inside", 3, 3, 8, 8, height="18"))["inside"]
    assert {_base_on(inside, axis, at) for axis in (0, 1) for at in (3.0, 8.0)} == {TOP}
    for parapet in (0.0, None):
        flat = _walls_of(_part("flat", 0, 0, 5, 5, height="18"), parapet=parapet)["flat"]
        assert _base_on(flat, 0, 0.0) == _base_on(flat, 0, 5.0) == TOP and flat.triangle_count == 8


def test_an_edge_is_on_the_outline_within_one_centimetre_along_its_whole_length() -> None:
    near = _walls_of(_part("near", PART_OUTLINE_M / 2, 2, 5, 7, height="18"))["near"]
    off = _walls_of(_part("off", 2 * PART_OUTLINE_M, 2, 5, 7, height="18"))["off"]
    assert _base_on(near, 0, PART_OUTLINE_M / 2) == TOP + PARAPET
    assert _base_on(off, 0, 2 * PART_OUTLINE_M) == TOP
    assert PART_OUTLINE_M == 0.01
    # Ends on the outline, middle 2 cm inside it: the outline bulges out past the part's straight edge.
    from shapely import Polygon

    building = Polygon([(0, 0), (20, 0), (20, 20), (10, 20 + 2 * PART_OUTLINE_M), (0, 20)])
    objects, _ = _building_parts([_part("chord", 0, 15, 20, 20, height="18")], {"b": building}, CFG,
                                 storey_of=lambda b: (3.0, "unknown"), top_of={"b": TOP}, parapet_of={"b": PARAPET})
    mesh = next(p.mesh for o in objects for p in o.visual)
    assert _base_on(mesh, 0, 20.0) == _base_on(mesh, 0, 0.0) == TOP + PARAPET
    assert _base_on(mesh, 1, 20.0) == TOP


def test_an_edge_that_only_touches_the_outline_at_its_ends_is_not_on_it() -> None:
    """An L-shaped building: the part's diagonal edge runs from one outline corner to another, across the roof."""
    from shapely import Polygon

    building = Polygon([(0, 0), (20, 0), (20, 10), (10, 10), (10, 20), (0, 20)])
    part = {"id": "tri", "kind": "line", "tags": {"building:part": "yes", "height": "18"},
            "geometry": {"type": "LineString", "coordinates": [[10, 10], [20, 10], [20, 0], [10, 10]]}}
    objects, _ = _building_parts([part], {"b": building}, CFG, storey_of=lambda b: (3.0, "unknown"),
                                 top_of={"b": TOP}, parapet_of={"b": PARAPET})
    mesh = next(p.mesh for o in objects for p in o.visual)
    tris = mesh.vertices[mesh.triangles]
    diagonal = np.all(np.abs(tris[:, :, 0] + tris[:, :, 1] - 20) < 1e-9, axis=1)
    assert float(tris[diagonal][:, :, 2].min()) == TOP


def test_a_part_whose_eaves_are_the_parapet_top_draws_only_its_edges_over_the_roof_as_plain_quads() -> None:
    mesh = _walls_of(_part("level", 0, 0, 5, 5, height=str(TOP + PARAPET)))["level"]
    assert _base_on(mesh, 0, 0.0) is None and _base_on(mesh, 1, 0.0) is None
    assert mesh.triangle_count == 4 and float(mesh.vertices[:, 2].max()) == TOP + PARAPET


def test_a_part_whose_eaves_are_below_the_parapet_top_draws_only_its_edges_over_the_roof() -> None:
    mesh = _walls_of(_part("low", 0, 0, 5, 5, height="12.5"))["low"]
    assert _base_on(mesh, 0, 0.0) is None and _base_on(mesh, 1, 0.0) is None
    assert _base_on(mesh, 0, 5.0) == TOP and float(mesh.vertices[:, 2].max()) == 12.5


def _outward(mesh: Mesh, centre: tuple[float, float]) -> bool:
    tris = mesh.vertices[mesh.triangles]
    normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    out = tris.mean(axis=1)[:, :2] - np.array(centre)
    return bool((np.einsum("ij,ij->i", normals[:, :2], out) > 0).all())


def _has(mesh: Mesh, point: tuple[float, float, float]) -> bool:
    return bool(np.any(np.all(np.abs(mesh.vertices - np.array(point)) < 1e-9, axis=1)))


def test_an_inner_wall_beside_a_raised_one_shares_its_corner_at_the_parapet_top() -> None:
    """One raised neighbour: a five-sided face; both neighbours raised: two quads split at the parapet top."""
    rim = TOP + PARAPET
    corner = _walls_of(_part("corner", 0, 0, 5, 5, height="18"))["corner"]
    assert _has(corner, (5, 0, rim)) and _has(corner, (0, 5, rim)) and not _has(corner, (5, 5, rim))
    assert corner.triangle_count == 2 * 2 + 2 * 3 and _outward(corner, (2.5, 2.5))
    band = _walls_of(_part("band", 0, 0, 20, 5, height="18"))["band"]
    assert _has(band, (0, 5, rim)) and _has(band, (20, 5, rim))
    assert band.triangle_count == 3 * 2 + 4 and _outward(band, (10, 2.5))
    inside = _walls_of(_part("inside", 3, 3, 8, 8, height="18"))["inside"]
    assert inside.triangle_count == 8 and _outward(inside, (5.5, 5.5))
