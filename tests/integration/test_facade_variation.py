"""Per-building facades and a door on every building, on two synthetic blocks around MURCIA_CENTER."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from onward_generator.detail import config_from_document, detail_scene, load_base, load_detail_config
from onward_generator.detail.config import DEFAULT_CONFIG_PATH
from onward_generator.detail.facades import DOOR_KINDS
from onward_generator.generation import load_generation_config

GEN = load_generation_config()
CFG = load_detail_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def _buildings(inputs: tuple[Path, Path, Path], cfg=CFG) -> tuple[dict[str, dict], dict]:
    scene, heights, base = inputs
    generated, ref = load_base(base, scene, heights, GEN)
    detailed = detail_scene(generated, ref, heights, scene, cfg)
    assert not detailed.critical
    records = {}
    for o in detailed.objects:
        if o.type == "building":
            doors = [p.mesh.vertices for p in o.visual if p.material == "door_wood"]
            records[o.id.split("-")[-1]] = o.record | {"door_box": [doors[0].min(axis=0), doors[0].max(axis=0)] if doors else None}
    return records, detailed.stats


@pytest.fixture(scope="module")
def row(row_inputs: tuple[Path, Path, Path]) -> tuple[dict[str, dict], dict]:
    return _buildings(row_inputs)


@pytest.fixture(scope="module")
def block(detail_inputs: tuple[Path, Path, Path]) -> tuple[dict[str, dict], dict]:
    return _buildings(detail_inputs)


def _wall(record: dict) -> str:
    return str(record["facade"]["materials"]["wall"])


def _doors(record: dict) -> int:
    return sum(n for kind, n in record["facade"]["openings"].items() if kind in DOOR_KINDS)


def test_buildings_in_one_block_get_different_walls(row: tuple[dict[str, dict], dict], block: tuple[dict[str, dict], dict]) -> None:
    records, stats = row
    order = ["601", "602", "603", "604", "605", "606"]
    walls = [_wall(records[b]) for b in order]
    assert all(left != right for left, right in zip(walls, walls[1:], strict=False))
    assert stats["wall_materials"] == {"brick_red": 2, "plaster_cream": 3, "stone_warm": 1}
    # The touching row 311 | 312 | 313 used to be plaster_ochre three times.
    first = block[0]
    assert len({_wall(first["311"]), _wall(first["312"]), _wall(first["313"])}) == 3


def test_osm_evidence_picks_the_palette_and_is_recorded(row: tuple[dict[str, dict], dict], block: tuple[dict[str, dict], dict]) -> None:
    records, stats = row
    uses = {b: r["facade"]["use"] for b, r in records.items()}
    assert uses["601"] == {"use": "residential", "origin": "observed",
                           "evidence": [{"source_id": "osm-way-601", "tag": "building=apartments", "match": "building"}]}
    assert uses["602"]["use"] == "institutional" and uses["602"]["origin"] == "observed"
    assert uses["602"]["evidence"][0]["tag"] == "amenity=school" and uses["602"]["evidence"][0]["match"] == "poi_inside"
    assert uses["603"]["use"] == "office" and uses["603"]["evidence"][0]["tag"] == "amenity=bank"
    assert uses["606"]["use"] == "retail" and uses["606"]["evidence"][0]["tag"] == "shop=bakery"
    assert uses["604"] == {"use": "residential", "origin": "estimated", "evidence": []}
    for b, record in records.items():
        assert _wall(record) in CFG.facade_palettes[uses[b]["use"]]
        assert record["facade"]["materials"]["wall_origin"] == "generated"
    assert stats["facade_uses"] == {"institutional": 1, "office": 1, "residential": 3, "retail": 1}
    church = block[0]["314"]["facade"]
    assert church["use"] == {"use": "landmark", "origin": "observed",
                             "evidence": [{"source_id": "osm-way-314", "tag": None, "match": "landmark"}]}
    assert church["materials"]["wall"] == "stone_warm"


def test_every_building_gets_a_door_except_where_the_shopfront_rules_decide(
    row: tuple[dict[str, dict], dict], block: tuple[dict[str, dict], dict],
) -> None:
    records, stats = row
    rules = {b: r["facade"]["door"] for b, r in records.items()}
    # 604's storeys are too low for the 2.4 m door; 605 has no 2.5 m street-facing wall.
    assert rules == {"601": "template", "602": "template", "603": "template", "604": "fallback", "605": "fallback", "606": "none"}
    assert stats["doors"] == {"fallback": 2, "none": 1, "template": 3}
    for b, record in records.items():
        assert _doors(record) == (0 if b == "606" else 1)
    # 606 is a shop: its door keeps the shopfront rules, which find no room under 2.67 m storeys.
    assert records["606"]["facade"]["ground_floor"] == "shopfront"
    assert records["604"]["facade"]["openings"]["door"] == 1
    # 605's door is on its 2 m street front (y = 6), clear of the party walls at x = 0 and x = 2.
    low, high = records["605"]["door_box"]
    assert low[1] == pytest.approx(6.15) and high[1] == pytest.approx(6.15)
    assert (low[0], high[0], high[2]) == pytest.approx((0.5, 1.5, 2.4))
    for record in block[0].values():
        assert _doors(record) == 1 and record["facade"]["door"] == "template"
    assert block[1]["doors"] == {"template": 5}


def test_a_building_the_interior_budget_turns_down_still_gets_its_fallback_door(detail_inputs: tuple[Path, Path, Path]) -> None:
    # A 2.7 m main door does not fit 3 m storeys; the 2.4 m interior door does, so 311 and 313
    # plan an interior that a zero triangle budget then turns down.
    doc = copy.deepcopy(DOC)
    doc["doors"]["main"]["height_m"] = 2.7
    doc["interiors"]["max_triangles"] = 0
    declined, stats = _buildings(detail_inputs, config_from_document(doc))
    doc["interiors"]["enabled"] = False
    plain, _ = _buildings(detail_inputs, config_from_document(doc))
    for b in ("311", "313"):
        assert declined[b]["facade"]["door"] == "fallback"
        assert declined[b]["door_box"][1][2] == pytest.approx(3.0 - CFG.head_clearance_m)
        assert {k: v for k, v in declined[b].items() if k != "door_box"} == {k: v for k, v in plain[b].items() if k != "door_box"}
    assert stats["interiors"] == []
