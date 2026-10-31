"""The interior budgets: drawn triangles counted before the shell is built, and collision triangles.

The fixtures are the street block (``detail_inputs``: two stair interiors and a bakery), the same
block without its bakery (``residential_inputs``: three stair interiors) and the row of six
(``row_inputs``: two shops of the same size).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from onward_generator.detail import config_from_document, detail_scene, load_base
from onward_generator.detail import scene as scene_module
from onward_generator.detail.config import DEFAULT_CONFIG_PATH
from onward_generator.detail.scene import InteriorBudget
from onward_generator.generation import load_generation_config

GEN = load_generation_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def _cfg(**changes: object):
    doc = copy.deepcopy(DOC)
    for path, value in changes.items():
        target = doc
        keys = path.split("__")
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
    return config_from_document(doc)


def _detail(inputs: tuple[Path, Path, Path], cfg):
    scene, heights, base = inputs
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, cfg)


def _interior(d, bid: str) -> dict[str, Any]:
    return next(o.record for o in d.objects if o.id == f"interior/{bid}")


def _outcome(d) -> tuple[Any, ...]:
    """Everything the run writes about its buildings and interiors, meshes included."""
    objects = [(o.id, o.record, [p.mesh.triangle_count for p in o.visual], [m.triangle_count for m in o.colliders],
                [m.triangle_count for m in o.glass_colliders]) for o in d.objects if o.type in ("building", "interior")]
    issues = [(i.code, i.severity, i.object_id, i.message) for i in d.issues]
    return objects, issues, d.stats


@pytest.fixture(scope="module")
def unlimited(detail_inputs: tuple[Path, Path, Path], residential_inputs: tuple[Path, Path, Path], row_inputs: tuple[Path, Path, Path]):
    cfg = _cfg(interiors__max_triangles=100000, interiors__shops__max_triangles=100000)
    return {name: _detail(inputs, cfg) for name, inputs in
            (("block", detail_inputs), ("residential", residential_inputs), ("row", row_inputs))}


def _budgets(unlimited) -> list[tuple[str, dict[str, object]]]:
    """Budgets that turn some interiors down on each fixture: one short of everything, each interior's own
    cost with nothing for the other kind, and nothing at all."""
    cases: list[tuple[str, dict[str, object]]] = []
    for name, d in unlimited.items():
        stairs = [_interior(d, b)["visual_triangles"] for b in d.stats["interiors"]]
        shops = [_interior(d, b)["visual_triangles"] for b in d.stats["shop_interiors"]]
        cases.append((name, {"interiors__max_triangles": max(0, sum(stairs) - 1), "interiors__shops__max_triangles": max(0, sum(shops) - 1)}))
        cases += [(name, {"interiors__max_triangles": cost, "interiors__shops__max_triangles": 0}) for cost in stairs]
        cases += [(name, {"interiors__max_triangles": 0, "interiors__shops__max_triangles": cost}) for cost in shops]
        cases.append((name, {"interiors__max_triangles": 0, "interiors__shops__max_triangles": 0}))
    return cases


def test_counting_before_the_shell_selects_what_building_everything_selected(
    detail_inputs: tuple[Path, Path, Path], residential_inputs: tuple[Path, Path, Path], row_inputs: tuple[Path, Path, Path],
    unlimited, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The early stop against the loop it replaced, which built every interior and its shell and then counted.

    ``build_then_check`` restores that loop: it passes every drawn-triangle check and counts the drawn
    triangles only after the shell and the colliders were built. Both runs must write the same scene.
    """
    inputs = {"block": detail_inputs, "residential": residential_inputs, "row": row_inputs}
    assembled: list[str] = []
    assemble = scene_module.assemble_interior

    def counting_assemble(plan, *args, **kwargs):
        assembled.append(plan.building_id)
        return assemble(plan, *args, **kwargs)

    monkeypatch.setattr(scene_module, "assemble_interior", counting_assemble)
    refuse = InteriorBudget.refuse
    pending: dict[int, int] = {}

    def build_then_check(self: InteriorBudget, kind: str, cost: int) -> str | None:
        if kind == "triangles":
            pending[id(self)] = cost
            return None
        return refuse(self, "triangles", pending.pop(id(self))) or refuse(self, kind, cost)

    cases = _budgets(unlimited)
    for name, changes in cases:
        cfg = _cfg(**changes)
        assembled.clear()
        early = _detail(inputs[name], cfg)
        early_assembled = list(assembled)
        with monkeypatch.context() as m:
            m.setattr(InteriorBudget, "refuse", build_then_check)
            assembled.clear()
            late = _detail(inputs[name], cfg)
        assert _outcome(early) == _outcome(late), (name, changes)
        refused = [i.object_id for i in early.issues if i.code == "interior_not_generated" and i.message.startswith("triangle_budget")]
        assert refused, (name, changes)  # every case turns at least one interior down
        # The early stop never assembles an interior it turns down; the old loop assembled every one.
        assert sorted(early_assembled) == sorted([*early.stats["interiors"], *early.stats["shop_interiors"]])
        assert sorted(assembled) == sorted([*early_assembled, *refused])


def test_each_building_shell_is_built_once_when_the_budget_turns_its_interior_down(
    residential_inputs: tuple[Path, Path, Path], unlimited, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shells: list[str] = []
    build_shell = scene_module.build_shell

    def counting_shell(*args, **kwargs):
        shells.append(args[7])
        return build_shell(*args, **kwargs)

    monkeypatch.setattr(scene_module, "build_shell", counting_shell)
    d = _detail(residential_inputs, _cfg(interiors__max_triangles=0))
    assert d.stats["interiors"] == []
    assert sorted(shells) == sorted(o.record["source_ids"][0] for o in d.objects if o.type == "building")


def test_interiors_report_their_collision_triangles(unlimited) -> None:
    for d in unlimited.values():
        stairs, shops = d.stats["interiors"], d.stats["shop_interiors"]
        assert stairs or shops
        for bid in [*stairs, *shops]:
            record = _interior(d, bid)
            interior = next(o for o in d.objects if o.id == f"interior/{bid}")
            assert interior.colliders == []  # the interior's colliders are on its building's collision node
            building = next(o for o in d.objects if o.id == f"building/{bid}")
            own = sum(m.triangle_count for m in building.colliders) + sum(m.triangle_count for m in building.glass_colliders)
            # The building also carries its balcony boxes and, with roof access, the parapet.
            assert 0 < record["collision_triangles"] <= own
        assert d.stats["interior_collision_triangles"] == sum(_interior(d, b)["collision_triangles"] for b in stairs)
        assert d.stats["shop_interior_collision_triangles"] == sum(_interior(d, b)["collision_triangles"] for b in shops)


def test_the_collision_budget_turns_an_interior_down_after_it_is_built(
    residential_inputs: tuple[Path, Path, Path], row_inputs: tuple[Path, Path, Path], unlimited,
) -> None:
    order = unlimited["residential"].stats["interiors"]
    collision = {b: _interior(unlimited["residential"], b)["collision_triangles"] for b in order}
    first, second, third = order
    limit = collision[first] + collision[second] + collision[third] - 1
    cut = _detail(residential_inputs, _cfg(interiors__max_triangles=100000, interiors__max_collision_triangles=limit))
    assert not cut.critical
    assert cut.stats["interiors"] == [first, second]
    assert cut.stats["interior_collision_triangles"] == limit - collision[third] + 1
    skipped = [(i.object_id, i.severity, i.message) for i in cut.issues if i.code == "interior_not_generated"]
    assert skipped == [(third, "info",
                        f"triangle_budget: needs {collision[third]} collision triangles, {collision[third] - 1} left of max_collision_triangles {limit}")]
    # Turned down after its shell was built: the building is the one it is with interiors off.
    plain = _detail(residential_inputs, _cfg(interiors__enabled=False))
    rejected = next(o for o in cut.objects if o.id == f"building/{third}")
    untouched = next(o for o in plain.objects if o.id == f"building/{third}")
    assert rejected.record == untouched.record
    assert [p.mesh.triangle_count for p in rejected.visual] == [p.mesh.triangle_count for p in untouched.visual]
    assert [m.triangle_count for m in rejected.colliders] == [m.triangle_count for m in untouched.colliders]
    assert not rejected.glass_colliders

    # 603 is nearer the map centre than 602, so it is tried first.
    assert unlimited["row"].stats["shop_interiors"] == ["osm-way-602", "osm-way-603"]
    cost = {b: _interior(unlimited["row"], b)["collision_triangles"] for b in ("osm-way-602", "osm-way-603")}
    limit = cost["osm-way-603"]
    one = _detail(row_inputs, _cfg(interiors__shops__max_triangles=100000, interiors__shops__max_collision_triangles=limit))
    assert one.stats["shop_interiors"] == ["osm-way-603"]
    assert one.stats["shop_interior_collision_triangles"] == limit
    turned = [(i.object_id, i.message) for i in one.issues if i.code == "interior_not_generated" and i.message.startswith("triangle_budget")]
    assert turned == [("osm-way-602", f"triangle_budget: needs {cost['osm-way-602']} collision triangles, 0 left of shops.max_collision_triangles {limit}")]


def test_the_default_collision_budgets_leave_the_fixtures_their_interiors(unlimited) -> None:
    # The drawn-triangle limits bind first: the defaults change no interior on any fixture.
    for name, d in unlimited.items():
        assert d.stats["interior_collision_triangles"] <= DOC["interiors"]["max_collision_triangles"], name
        assert d.stats["shop_interior_collision_triangles"] <= DOC["interiors"]["shops"]["max_collision_triangles"], name


def test_the_counted_interior_is_the_one_drawn_with_its_window_cuts(residential_inputs: tuple[Path, Path, Path],
                                                                     detail_inputs: tuple[Path, Path, Path]) -> None:
    # Built here, not in a fixture: the visual pass the budget counts is the mesh the scene writes.
    for inputs in (residential_inputs, detail_inputs):
        d = _detail(inputs, _cfg())
        assert [*d.stats["interiors"], *d.stats["shop_interiors"]]
        for bid in [*d.stats["interiors"], *d.stats["shop_interiors"]]:
            record = _interior(d, bid)
            node = next(o for o in d.objects if o.id == f"interior/{bid}")
            assert record["visual_triangles"] == sum(p.mesh.triangle_count for p in node.visual)
            building = next(o for o in d.objects if o.id == f"building/{bid}")
            assert record["through_windows"] == len(building.glass_colliders) + record["open_shopfronts"] > 0
            heights = {round(float(z), 6) for p in node.visual for z in p.mesh.vertices[:, 2]}
            for pane in building.glass_colliders:
                # The room wall is cut where each pane is: its sill and its head are vertices of the interior.
                assert round(float(pane.vertices[:, 2].min()), 6) in heights, bid
                assert round(float(pane.vertices[:, 2].max()), 6) in heights, bid
