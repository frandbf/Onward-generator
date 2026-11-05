"""``building:part`` extrusion at its edges: area, height order, hidden parts and rounding, on exact local coordinates."""

from __future__ import annotations

from typing import Any

from shapely import box

from onward_generator.detail.config import load_detail_config
from onward_generator.detail.scene import _building_parts

CFG = load_detail_config()
TOP = 12.0


def _part(fid: str, x0: float, y0: float, x1: float, y1: float, **tags: str) -> dict[str, Any]:
    ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    return {"id": fid, "kind": "line", "tags": {"building:part": "yes", **tags},
            "geometry": {"type": "LineString", "coordinates": [*ring, ring[0]]}}


def _run(*features: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[tuple[str, str | None]]]:
    objects, issues = _building_parts(list(features), {"b": box(0, 0, 20, 20)}, CFG,
                                      storey_of=lambda b: (3.0, "unknown"), top_of={"b": TOP})
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
                                 {"b": box(0, 0, 20, 20)}, CFG, storey_of=lambda b: (3.0, "unknown"), top_of={"b": TOP})
    visual = {o.record["source_ids"][1]: o.visual for o in objects}
    assert [p.material for p in visual["cap"]] == ["roof_tile"]  # eaves at 12 m: only the roof
    assert len(visual["walled"]) == 2 and min(float(p.mesh.vertices[:, 2].min()) for p in visual["walled"]) == TOP
