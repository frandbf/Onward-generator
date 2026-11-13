"""Plan T4: per-building texture variants of roofs and plaster walls."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh
from onward_generator.maps.workspace import MapError, create_map, texture_variants_enabled
from onward_generator.materials.library_plan import WIDE_LIBRARY_PATH, catalogue_rows, library_rows
from onward_generator.materials.variants import (
    BUILDING_TYPES,
    VARIANT_SEP,
    VARIANT_SLOTS,
    VARIANTS,
    assign_variants,
    variant_base,
    variant_index,
    variant_picks,
    varied_materials,
)
from tests.conftest import MURCIA_CENTER

QUAD = Mesh(np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0]], float), np.array([[0, 1, 2]]))


def test_the_constants_and_the_base_of_a_variant() -> None:
    assert (VARIANT_SEP, VARIANTS, BUILDING_TYPES) == ("__var", 2, frozenset({"building", "building_part"}))
    assert VARIANT_SLOTS == ("roofs", "flat_roofs", "historic_facades", "ochre_facades", "plain_facades", "brick")
    assert variant_base("roof_tile__var2") == "roof_tile" and variant_base("roof_tile") == "roof_tile"


def test_the_picks_are_calm_unused_and_seeded() -> None:
    rows = catalogue_rows()
    entries = [SimpleNamespace(id="roofs", library_id="ph_clay_roof_tiles"), SimpleNamespace(id="carriageway", library_id="ph_clean_asphalt"),
               SimpleNamespace(id="flat_roofs", library_id="ph_concrete_floor_worn_001"),
               SimpleNamespace(id="sidewalks", library_id="ph_brushed_concrete")]
    picks = variant_picks(entries, rows, 48152, extended=True)
    assert sorted(picks) == ["flat_roofs", "roofs"]  # ground slots are not varied
    assert all(len(ids) == VARIANTS for ids in picks.values())
    taken = {"ph_clay_roof_tiles", "ph_clean_asphalt", "ph_concrete_floor_worn_001", "ph_brushed_concrete"}
    chosen = [i for ids in picks.values() for i in ids]
    assert not set(chosen) & taken and len(chosen) == len(set(chosen))  # never the plan's, never twice
    assert picks == variant_picks(entries, rows, 48152, extended=True) != variant_picks(entries, rows, 7, extended=True)
    from onward_generator.materials.library_plan import SLOT_FAMILY, texture_family

    by_id = {row["id"]: row for row in rows}
    assert all(texture_family(by_id[i]) == SLOT_FAMILY[slot] for slot, ids in picks.items() for i in ids)
    narrow = variant_picks([SimpleNamespace(id="roofs", library_id="x")], [], 1, extended=False)
    assert narrow == {"roofs": []}  # no other photograph of the family: no variant


def test_a_building_keeps_its_variant_and_the_ground_does_not_change() -> None:
    assert variant_index("building/b1", "roof_tile", 2) == variant_index("building/b1", "roof_tile", 2)
    assert {variant_index(f"building/b{i}", "roof_tile", 2) for i in range(40)} == {0, 1, 2}
    assert variant_index("building/b1", "roof_tile", 0) == 0
    nodes = [Node(f"building/b{i}", (Primitive(QUAD, "roof_tile"), Primitive(QUAD, "roof_tile"), Primitive(QUAD, "plaster_white")),
                  {"type": "building"}) for i in range(12)]
    nodes.append(Node("surface/carriageway", (Primitive(QUAD, "roof_tile"),), {"type": "surface"}))
    out, drawn = assign_variants(nodes, {"roof_tile": 2})
    for node in out[:-1]:
        k = variant_index(node.name, "roof_tile", 2)
        expected = "roof_tile" if k == 0 else f"roof_tile__var{k}"
        assert [p.material for p in node.primitives] == [expected, expected, "plaster_white"]
    assert out[-1] is nodes[-1]
    assert sum(drawn.values()) == 12 and set(drawn) <= {"roof_tile", "roof_tile__var1", "roof_tile__var2"}


def test_only_applied_materials_of_varied_slots_outside_the_trim_are_varied() -> None:
    applied = [{"id": "roofs", "color_id": "roof_tile"}, {"id": "historic_facades", "color_id": "facade_trim_x"},
               {"id": "carriageway", "color_id": "carriageway"}, {"id": "brick", "color_id": "brick_red"}]
    picks = {"roofs": ["a", "b"], "historic_facades": ["c", "d"], "brick": []}
    assert varied_materials(applied, picks, {"facade_trim_x"}) == {"roof_tile": ["a", "b"]}


def test_the_switch_is_read_from_map_json(tmp_path: Path) -> None:
    from onward_generator.next_step import materials_command

    lon, lat = MURCIA_CENTER
    on = tmp_path / "on"
    create_map(on, map_id="on", name="On", latitude=lat, longitude=lon, switches=["texture_variants"])
    assert texture_variants_enabled(on) and "--texture-variants" in materials_command(on / "data" / "processed" / "scene_dress").argv
    off = tmp_path / "off"
    create_map(off, map_id="off", name="Off", latitude=lat, longitude=lon)
    assert not texture_variants_enabled(off)
    document = json.loads((off / "map.json").read_text(encoding="utf-8"))
    (off / "map.json").write_text(json.dumps({**document, "texture_variants": "yes"}), encoding="utf-8")
    with pytest.raises(MapError, match='"texture_variants" must be true or false'):
        texture_variants_enabled(off)


def test_the_wide_library_widens_the_variant_pool() -> None:
    entries = [SimpleNamespace(id="roofs", library_id="acg_roofingtiles014b")]
    narrow = variant_picks(entries, library_rows(), 3, extended=True)["roofs"]
    wide = variant_picks(entries, library_rows() + library_rows(WIDE_LIBRARY_PATH), 3, extended=True)["roofs"]
    assert len(narrow) <= VARIANTS and len(wide) == VARIANTS


def test_the_variant_index_is_pinned() -> None:
    assert [variant_index(f"building/b{i}", "roof_tile", 2) for i in range(8)] == [1, 2, 1, 2, 2, 2, 1, 2]


def test_a_building_keeps_its_uvs_and_every_primitive_and_node_is_seen() -> None:
    uvs = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]])
    nodes = [Node("surface/a", (Primitive(QUAD, "roof_tile"),), {"type": "surface"}),
             Node("building/b0", (Primitive(QUAD, "plaster_white", uvs), Primitive(QUAD, "roof_tile", uvs)), {"type": "building_part"})]
    out, drawn = assign_variants(nodes, {"roof_tile": 2})
    k = variant_index("building/b0", "roof_tile", 2)
    assert out[0] is nodes[0] and [p.material for p in out[1].primitives] == ["plaster_white", "roof_tile" if k == 0 else f"roof_tile__var{k}"]
    assert all(np.array_equal(p.uvs, uvs) for p in out[1].primitives) and sum(drawn.values()) == 1


def test_the_extended_limits_keep_a_busy_roof_out_of_the_variants() -> None:
    from onward_generator.materials.library_plan import library_rows

    entries = [SimpleNamespace(id="roofs", library_id="acg_roofingtiles014b")]
    for seed in range(30):
        assert "acg_roofingtiles012c" not in variant_picks(entries, library_rows(), seed, extended=True)["roofs"]  # 0.0211 over 0.019
    assert any("acg_roofingtiles012c" in variant_picks(entries, library_rows(), seed, extended=False)["roofs"] for seed in range(30))


def test_the_cli_takes_the_flag_and_prints_the_variants(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
                                                        tmp_path: Path) -> None:
    import onward_generator.materials.__main__ as cli

    seen: dict[str, object] = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return {"applied": [], "skipped": [], "brick_nodes": [], "cobble_applied": False,
                "texture_variants": {"added_materials": 2, "materials": {"roof_tile__var1": "a", "roof_tile__var2": "b"}}}

    monkeypatch.setattr(cli, "apply_materials", fake)
    monkeypatch.setattr(cli, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "after_materials", lambda out: None)
    assert cli.main(["apply", "--base", str(tmp_path), "--out", str(tmp_path / "o"), "--texture-variants"]) == 0
    assert seen["texture_variants"] is True
    assert "texture variants: 2 materials added (roof_tile__var1, roof_tile__var2)\n" in capsys.readouterr().out
    monkeypatch.setattr(cli, "apply_materials", lambda *args, **kwargs: {"applied": [], "skipped": [], "brick_nodes": [], "cobble_applied": False,
                                                                         "texture_variants": {"added_materials": 0, "materials": {}}})
    assert cli.main(["apply", "--base", str(tmp_path), "--out", str(tmp_path / "o")]) == 0
    assert "texture variants: 0 materials added (none)\n" in capsys.readouterr().out


def test_the_plan_s_own_photograph_is_never_a_variant() -> None:
    rows = [{"id": i, "family": "roof", "path": "missing.jpg"} for i in ("a", "b", "c")]
    for seed in range(20):
        picks = variant_picks([SimpleNamespace(id="roofs", library_id="b")], rows, seed, extended=True)["roofs"]
        assert sorted(picks) == ["a", "c"]


def test_the_repetition_switch_reaches_the_variant_picks(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import onward_generator.materials.apply as apply_module

    seen: list[object] = []

    def spy(entries, rows, seed, *, extended):
        seen.append(extended)
        raise RuntimeError("stop")

    monkeypatch.setattr(apply_module, "variant_picks", spy)
    plan = SimpleNamespace(entries=[])
    with pytest.raises(RuntimeError, match="stop"):
        apply_module._apply_variants([], plan, [], {}, {}, [], seed=1, wide=False, extended=True)
    with pytest.raises(RuntimeError, match="stop"):
        apply_module._apply_variants([], plan, [], {}, {}, [], seed=1, wide=False, extended=False)
    assert seen == [True, False]
