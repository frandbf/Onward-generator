"""Striped awnings under the shops' fascia signs (overgrow/awnings.py; plan 2026-11-16, increment 3)."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from shapely import STRtree
from shapely.geometry import box as shapely_box

from onward_generator.detail.facades import stable_unit
from onward_generator.overgrow.awnings import (
    AWNINGS_PATH,
    NODE_PREFIX,
    SCHEMA_PATH,
    AwningConfig,
    AwningError,
    fascias,
    load_awnings,
    parse_awnings,
    place_awnings,
    wall_things,
)
from onward_generator.overgrow.cloth import FABRIC_MATERIAL, band_v
from onward_generator.overgrow.zone import square_zone

DOC = json.loads(AWNINGS_PATH.read_text(encoding="utf-8"))
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
ALL = replace(parse_awnings(DOC, SCHEMA), share=1.0)
SHOP = shapely_box(-10, 0, 10, 10)


def _fascia(x: float = 0.0, z: float = 3.08, width: float = 3.0, height: float = 0.36) -> dict[str, Any]:
    return {"kind": "fascia", "centre_m": [x, -0.03, z], "normal": [0.0, -1.0], "width_m": width, "height_m": height, "depth_m": 0.06}


def _manifest(*signs: dict[str, Any] | None, extra: list[Any] | None = None) -> dict[str, Any]:
    shops = [{"id": f"shop/A/{n}", "sign": sign} for n, sign in enumerate(signs)]
    return {"objects": [{"type": "building", "source_ids": ["A"], "facade": {"shops": shops}}, *(extra or [])]}


def _run(manifest: dict[str, Any], buildings: list[Any] | None = None, **kwargs: Any):  # type: ignore[no-untyped-def]
    options: dict[str, Any] = {"budget": 10_000, "config": ALL}
    options.update(kwargs)
    return place_awnings(manifest, STRtree(buildings or [SHOP]), square_zone(100.0), 7, **options)


def test_the_configuration_is_read_whole(tmp_path: Path) -> None:
    cfg = parse_awnings(DOC, SCHEMA)
    assert cfg == AwningConfig(share=0.7, depth_m=0.9, max_drop_m=0.35, min_drop_m=0.1, valance_m=0.2, head_clear_m=2.2,
                               under_fascia_m=0.02, free_m=0.5)
    assert all(isinstance(value, float) for value in (cfg.share, cfg.depth_m, cfg.free_m))
    assert parse_awnings({**DOC, "share": 1, "free_m": 2}, SCHEMA).free_m == 2.0
    assert load_awnings() == cfg
    bad = tmp_path / "awnings.json"
    bad.write_text("{", encoding="utf-8")
    with pytest.raises(AwningError, match="awnings.json"):
        load_awnings(bad)


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.update(schema="other"), "^awnings: schema: 'onward_generator.awnings' was expected$"),
    (lambda d: d.pop("depth_m"), "^awnings: 'depth_m' is a required property$"),
    (lambda d: d.update(head_clear_m=1.5), "^awnings: head_clear_m: "),
])
def test_a_bad_configuration_is_refused(change, message: str) -> None:  # type: ignore[no-untyped-def]
    doc = copy.deepcopy(DOC)
    change(doc)
    with pytest.raises(AwningError, match=message):
        parse_awnings(doc, SCHEMA)


def test_the_fascias_and_the_wall_things_are_read() -> None:
    blade = {**_fascia(), "kind": "blade"}
    manifest = _manifest(_fascia(), blade, None, extra=[
        "not a record",
        {"type": "building", "facade": {"shops": ["odd", {"sign": _fascia(5.0)}]}},
        {"type": "interior", "facade": {"shops": [{"id": "x", "sign": _fascia()}]}},
        {"type": "street_sign", "position_m": [1.0, -0.1, 2.55]},
        {"type": "wall_decal", "position_m": [2.0, -0.1, 3.0], "size_m": [1.2, 0.8]},
        {"type": "wall_decal", "position_m": [2.0, -0.1, 3.0]},
        {"type": "street_sign", "position_m": [1.0, -0.1]},
        {"type": "shop_sign", "position_m": [1.0, -0.1, 2.0]},
    ])
    assert [(b, s, sign["centre_m"][0]) for b, s, sign in fascias(manifest)] == [("A", "shop/A/0", 0.0), ("", "", 5.0)]
    things = wall_things(manifest)
    assert things == [(1.0, -0.1, 0.7, pytest.approx(2.37), pytest.approx(2.73)), (2.0, -0.1, 0.6, pytest.approx(2.6), pytest.approx(3.4))]


def test_an_awning_hangs_under_its_fascia() -> None:
    result = _run(_manifest(_fascia()))
    assert result.report() == {"fascias": 1, "awnings": 1, "triangles": 12, "skipped": {}}
    node = result.nodes[0]
    assert node.name == NODE_PREFIX + "0" and node.matrix is None and node.mesh_key is None  # drawn where it hangs
    assert node.extras == {"type": "overgrowth", "origin": "generated", "attribute_origin": "generated", "kind": "awning",
                           "building_id": "A", "shop": "shop/A/0", "solid": False}
    primitive = node.primitives[0]
    assert primitive.material == FABRIC_MATERIAL
    vertices = primitive.mesh.vertices
    assert np.allclose(vertices[:6], [[-1.5, -0.01, 2.88], [1.5, -0.01, 2.88], [1.5, -0.91, 2.53], [-1.5, -0.91, 2.53],
                                      [-1.5, -0.91, 2.33], [1.5, -0.91, 2.33]])
    band = int(stable_unit(7, "shop/A/0", "colour") * 3)
    assert np.allclose(primitive.uvs[0], [0.0, band_v(band, 0.0)]) and np.allclose(primitive.uvs[1], [3.0, band_v(band, 0.0)])
    assert result.records == [{"kind": "awning", "origin": "generated", "source_id": "A", "role": "awning", "position_m": [0.0, -0.46, 2.33],
                               "size_m": [3.0, 0.9, 0.55], "solid": False}]
    plan, bottom, top = result.footprints[0]
    assert (round(bottom, 3), round(top, 3)) == (2.33, 2.88) and plan.bounds == pytest.approx((-1.5, -0.91, 1.5, -0.01))
    turned = _run({"objects": [{"type": "building", "source_ids": ["A"], "facade": {"shops": [{"id": "s", "sign": {
        **_fascia(), "centre_m": [10.03, 2.0, 3.08], "normal": [1.0, 0.0]}}]}}]})
    assert np.allclose(turned.nodes[0].primitives[0].mesh.vertices[2], [10.91, 3.5, 2.53])  # out along the wall's own normal


def test_a_low_fascia_slopes_less_or_gets_none() -> None:
    exact = replace(ALL, valance_m=0.25, head_clear_m=2.0, min_drop_m=0.25, under_fascia_m=0.0)
    kept = _run(_manifest(_fascia(z=2.75, height=0.5)), config=exact)  # its canopy can slope exactly min_drop_m
    assert kept.records[0]["size_m"][2] == 0.5 and kept.records[0]["position_m"][2] == 2.0
    gentle = _run(_manifest(_fascia(z=2.8)))  # 2.6 m under the board: slopes 0.2 m, not 0.35
    assert gentle.records[0]["size_m"][2] == pytest.approx(0.4) and gentle.records[0]["position_m"][2] == pytest.approx(2.2)
    low = _run(_manifest(_fascia(z=2.68), _fascia(z=2.68)))
    assert low.records == [] and low.skipped == {"low": 2}


def test_an_awning_keeps_off_the_zone_edge_a_near_building_and_the_signs() -> None:
    outside = place_awnings(_manifest(_fascia(x=5.0)), STRtree([SHOP]), square_zone(4.0), 7, budget=10_000, config=ALL)
    assert outside.skipped == {"outside_zone": 1}
    assert _run(_manifest(_fascia()), [SHOP, shapely_box(-10, -20, 10, -1.42)]).skipped == {"narrow": 1}  # 1.41 m free, 1.4 m needed
    assert _run(_manifest(_fascia()), [SHOP, shapely_box(-10, -20, 10, -1.5)]).skipped == {}
    plate = {"type": "street_sign", "position_m": [1.0, -0.06, 2.55]}
    assert _run(_manifest(_fascia(), extra=[plate])).skipped == {"covers_sign": 1}
    assert _run(_manifest(_fascia(), extra=[{**plate, "position_m": [1.0, -0.06, 3.15]}])).skipped == {"covers_sign": 1}  # 2.97 < 2.98
    assert _run(_manifest(_fascia(), extra=[{**plate, "position_m": [1.0, -0.06, 3.17]}])).skipped == {}
    assert _run(_manifest(_fascia(), extra=[{**plate, "position_m": [1.0, -0.06, 2.1]}])).skipped == {"covers_sign": 1}  # 2.28 > 2.23
    assert _run(_manifest(_fascia(), extra=[{**plate, "position_m": [1.0, -0.06, 2.0]}])).skipped == {}  # 2.18 < 2.23
    assert _run(_manifest(_fascia(), extra=[{**plate, "position_m": [2.25, -0.06, 2.55]}])).skipped == {"covers_sign": 1}  # 1.55 < 1.6
    assert _run(_manifest(_fascia(), extra=[{**plate, "position_m": [2.35, -0.06, 2.55]}])).skipped == {}  # 1.65 > 1.6
    decal = {"type": "wall_decal", "position_m": [-2.1, -0.02, 2.5], "size_m": [1.2, 0.6]}  # reaches x -1.5, inside the 10 cm round the awning
    assert _run(_manifest(_fascia(), extra=[decal])).skipped == {"covers_sign": 1}
    assert _run(_manifest(_fascia(), extra=[{**decal, "position_m": [-2.3, -0.02, 2.5]}])).skipped == {}  # ends at x -1.7
    blade = {"kind": "blade", "centre_m": [1.2, -0.5, 2.6], "normal": [-1.0, 0.0], "width_m": 0.7, "height_m": 0.5, "depth_m": 0.04}
    assert _run(_manifest(_fascia(), blade)).skipped == {"covers_sign": 1}
    high = {**blade, "centre_m": [1.2, -0.5, 3.2]}
    assert _run(_manifest(_fascia(), high)).skipped == {}


def test_the_share_and_the_budget() -> None:
    none = _run(_manifest(_fascia(), _fascia(5.0)), config=replace(ALL, share=0.0))
    assert none.skipped == {"share": 2} and none.fascias == 2
    real = _run(_manifest(*(_fascia(x) for x in (-8.0, -4.0, 0.0, 4.0, 8.0))), config=parse_awnings(DOC, SCHEMA))
    chosen = [stable_unit(7, f"shop/A/{n}", "awning") < 0.7 for n in range(5)]
    assert len(real.nodes) == sum(chosen) and real.skipped.get("share", 0) == 5 - sum(chosen)
    tight = _run(_manifest(_fascia(-5.0), _fascia(5.0)), budget=12)
    assert len(tight.nodes) == 1 and tight.skipped == {"budget": 1} and tight.triangles == 12
    assert _run(_manifest(_fascia()), budget=11).skipped == {"budget": 1}
