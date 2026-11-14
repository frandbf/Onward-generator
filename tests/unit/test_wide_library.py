"""Plan T1: the wide CC0 library joins the texture pools only for a map with the "wide_library" switch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from onward_generator.maps.workspace import MapError, create_map, refresh_texture_plan, wide_library_enabled
from onward_generator.materials.library_plan import (
    REPO_ROOT,
    SLOT_FAMILY,
    WIDE_LIBRARY_PATH,
    assert_library_entries,
    catalogue_rows,
    deterministic_choices,
    is_calm,
    library_rows,
    select_texture_plan,
    texture_family,
    texture_record,
)
from onward_generator.materials.variants import VARIANT_SLOTS
from tests.conftest import MURCIA_CENTER

WIDE = library_rows(WIDE_LIBRARY_PATH)
FAMILIES = {"plaster", "brick", "rock", "paving", "concrete", "asphalt", "roof", "bark", "earth"}


def test_every_wide_row_is_a_cached_cc0_poly_haven_file_that_matches_its_hash() -> None:
    assert len(WIDE) == 34
    for row in WIDE:
        path = REPO_ROOT / row["path"]
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == row["sha256"] and len(data) == row["bytes"], row["id"]
        side = json.loads(path.with_name(path.name + ".source.json").read_text(encoding="utf-8"))
        assert side["license"] == "CC0" and side["provider"] == "polyhaven" and side["sha256"] == row["sha256"], row["id"]
        assert (row["provider"], row["license"], row["source_url"]) == (
            "polyhaven", "CC0-1.0", f"https://polyhaven.com/a/{row['name']}")
        assert row["id"] == f"ph_{row['name']}" and "Poly Haven" in row["credit"] and row["family"] in FAMILIES


def test_every_wide_row_is_calm_by_its_family_s_extended_limit_and_named_once() -> None:
    assert all(is_calm(row, extended=True) for row in WIDE)
    ids = [row["id"] for row in catalogue_rows()]
    assert len(ids) == len(set(ids))  # no wide id shadows an imported one


def test_a_row_s_family_field_wins_over_its_file_name() -> None:
    assert texture_family({"name": "rock_wall_11", "family": "paving"}) == "paving"
    assert texture_family({"name": "rock_wall_11"}) == "rock"
    assert texture_family({"name": "x", "family": ""}) is None


def test_the_default_plan_does_not_change_and_the_wide_one_draws_on_both_libraries() -> None:
    plain = select_texture_plan(name="M", map_id="m", latitude=MURCIA_CENTER[1], longitude=MURCIA_CENTER[0], brief=None, seed=48152)
    assert plain == select_texture_plan(name="M", map_id="m", latitude=MURCIA_CENTER[1], longitude=MURCIA_CENTER[0], brief=None,
                                        seed=48152, wide=False)
    wide_ids = {row["id"] for row in WIDE}
    assert not {e["library_id"] for e in plain["entries"]} & wide_ids
    picked: set[str] = set()
    for seed in range(40):
        picked |= set(deterministic_choices(catalogue_rows(), seed).values())
    assert len(picked & wide_ids) >= 15  # forty maps reach most of the wide library
    wide = select_texture_plan(name="M", map_id="m", latitude=MURCIA_CENTER[1], longitude=MURCIA_CENTER[0], brief=None, seed=48152,
                               wide=True)
    assert {e["id"] for e in wide["entries"]} == set(SLOT_FAMILY)
    assert_library_entries(wide)  # a wide plan is checked against both libraries, switch or not
    for entry in wide["entries"]:
        assert texture_record(entry["library_id"]).sha256


def test_the_switch_is_read_from_map_json_and_refreshes_the_plan(tmp_path: Path) -> None:
    lon, lat = MURCIA_CENTER
    on = tmp_path / "on"
    create_map(on, map_id="on", name="On", latitude=lat, longitude=lon, seed=7, switches=["wide_library"])
    assert wide_library_enabled(on)
    created = (on / "config" / "texture_plan.json").read_bytes()
    refresh_texture_plan(on)
    assert (on / "config" / "texture_plan.json").read_bytes() == created
    expected = select_texture_plan(name="On", map_id="on", latitude=lat, longitude=lon, brief=None, seed=7, wide=True)
    assert json.loads(created) == expected
    off = tmp_path / "off"
    create_map(off, map_id="off", name="Off", latitude=lat, longitude=lon, seed=7)
    assert not wide_library_enabled(off)
    document = json.loads((off / "map.json").read_text(encoding="utf-8"))
    (off / "map.json").write_text(json.dumps({**document, "wide_library": "yes"}), encoding="utf-8")
    with pytest.raises(MapError, match='"wide_library" must be true or false'):
        wide_library_enabled(off)


def test_a_lookup_given_its_rows_reads_only_those() -> None:
    from onward_generator.materials.plan import PlanError

    row = next(r for r in WIDE if r["family"] == "plaster")
    assert texture_record(row["id"], [row]).sha256 == row["sha256"]
    with pytest.raises(PlanError, match="is not in the imported library"):
        texture_record(row["id"], library_rows())
    plan = {"entries": [{"id": "plain_facades", "library_id": row["id"]}]}
    assert_library_entries(plan, [row])
    with pytest.raises(PlanError, match="plain_facades cannot use"):
        assert_library_entries(plan, library_rows())


def test_every_wide_row_carries_its_cc0_opengl_normal_map() -> None:
    import numpy as np
    from PIL import Image

    for row in WIDE:
        normal = row["normal"]
        path = REPO_ROOT / normal["path"]
        data = path.read_bytes()
        assert normal["map"] == "nor_gl" and path.name == f"{row['name']}_nor_gl_1k.jpg", row["id"]
        assert hashlib.sha256(data).hexdigest() == normal["sha256"] and len(data) == normal["bytes"], row["id"]
        side = json.loads(path.with_name(path.name + ".source.json").read_text(encoding="utf-8"))
        assert (side["license"], side["map"], side["asset_id"], side["resolution"]) == ("CC0", "nor_gl", row["name"], "1k"), row["id"]
        with Image.open(path) as image:
            assert image.size == (1024, 1024), row["id"]
            mean = np.asarray(image.convert("RGB"), dtype=float).mean(axis=(0, 1)) / 255
        # A tangent-space normal map: mostly facing out (blue), tilted evenly around the middle (red, green).
        assert mean[2] > 0.85 and 0.4 < mean[0] < 0.6 and 0.4 < mean[1] < 0.6, (row["id"], mean.round(3).tolist())


def test_the_normal_maps_switch_is_read_from_map_json_and_reaches_materials_apply(tmp_path: Path) -> None:
    from onward_generator.maps.workspace import normal_maps_enabled
    from onward_generator.next_step import materials_command

    lon, lat = MURCIA_CENTER
    on = tmp_path / "on"
    create_map(on, map_id="on", name="On", latitude=lat, longitude=lon, switches=["normal_maps"])
    assert normal_maps_enabled(on)
    assert "--normal-maps" in materials_command(on / "data" / "processed" / "scene_dress").argv
    off = tmp_path / "off"
    create_map(off, map_id="off", name="Off", latitude=lat, longitude=lon)
    assert not normal_maps_enabled(off) and "--normal-maps" not in materials_command(off / "data" / "processed" / "scene_dress").argv
    document = json.loads((off / "map.json").read_text(encoding="utf-8"))
    (off / "map.json").write_text(json.dumps({**document, "normal_maps": 1}), encoding="utf-8")
    with pytest.raises(MapError, match='"normal_maps" must be true or false, got 1'):
        normal_maps_enabled(off)


def test_the_rows_added_for_mogadishu_join_every_map_and_the_distinctive_ones_wait_for_the_model() -> None:
    """14 November 2026: dirt, adobe and corrugated roofs join the wide library of every map. The seeded fallback keeps
    the corrugated, adobe and turquoise rows and the earth alternates out; the model, which reads the brief, gets them."""
    from onward_generator.materials.library_plan import (
        ALTERNATE_FAMILIES,
        EARTH_CONTRAST_LIMIT,
        _pools,
        choice_schema,
        contrast_limit,
        slot_families,
        slot_pool,
    )
    from onward_generator.materials.variants import variant_picks

    added = {"asbestos_sheet", "blue_plaster_weathered", "box_profile_metal_sheet", "brown_mud_dry", "chipped_concrete",
             "clay_block_wall", "clay_floor_001", "coast_sand_01", "coast_sand_02"}
    rows = {row["name"]: row for row in WIDE if row["name"] in added}
    assert set(rows) == added and all("normal" in row and is_calm(row, extended=True) for row in rows.values())
    distinctive = {name for name, row in rows.items() if row.get("distinctive")}
    assert distinctive == {"asbestos_sheet", "blue_plaster_weathered", "box_profile_metal_sheet", "clay_block_wall"}
    assert {name for name, row in rows.items() if row["family"] == "earth"} == {
        "brown_mud_dry", "clay_floor_001", "coast_sand_01", "coast_sand_02"}
    assert EARTH_CONTRAST_LIMIT == 0.02 and contrast_limit("earth") == 0.02 == contrast_limit("earth", extended=True)
    seeded = {row["id"] for pool in _pools(catalogue_rows(), extended=True).values() for row in pool}
    offered = {row["id"] for pool in _pools(catalogue_rows(), extended=True, model=True).values() for row in pool}
    assert not {f"ph_{name}" for name in distinctive} & seeded and {f"ph_{name}" for name in distinctive} <= offered
    assert "ph_chipped_concrete" in seeded and "ph_brown_mud_dry" in seeded  # pooled, but no slot's own family is earth
    for seed in range(40):
        picked = set(deterministic_choices(catalogue_rows(), seed, extended=True).values())
        assert not picked & ({f"ph_{name}" for name in distinctive} | {row["id"] for row in rows.values() if row["family"] == "earth"})
    pools = _pools(catalogue_rows(), extended=True, model=True)
    assert slot_families("streets_plazas") == ("paving", "earth") and slot_families("doors") == ("door",)
    assert "ph_brown_mud_dry" in {row["id"] for row in slot_pool("streets_plazas", pools)}
    assert ALTERNATE_FAMILIES["roofs"] == frozenset({"concrete"})
    branches = {b["properties"]["slot"]["const"]: b["properties"]["library_id"]["enum"]
                for b in choice_schema(pools)["properties"]["choices"]["items"]["anyOf"]}
    assert "ph_coast_sand_01" in branches["sidewalks"] and "ph_coast_sand_01" not in branches["carriageway"]
    assert "ph_asbestos_sheet" in branches["roofs"] and "ph_clay_block_wall" in branches["brick"]
    entries = [SimpleNamespace(id=slot, library_id="") for slot in sorted(VARIANT_SLOTS)]
    varied = {i for picks in variant_picks(entries, catalogue_rows(), 1, extended=True).values() for i in picks}
    assert not varied & {f"ph_{name}" for name in distinctive}


def test_the_model_is_offered_the_distinctive_rows_and_the_earth_alternates_and_may_choose_them() -> None:
    """14 November 2026: the model reads the brief, so it gets the corrugated, adobe and earth rows the seeded fallback skips."""
    from onward_generator.ai.providers import ProviderResult
    from onward_generator.materials.library_plan import _parse_choices

    rows = catalogue_rows()
    seeded = deterministic_choices(rows, 3, extended=True)
    reply = json.dumps({"choices": [{"slot": slot, "library_id": {"roofs": "ph_asbestos_sheet", "streets_plazas": "ph_brown_mud_dry",
                                                                  "brick": "ph_clay_block_wall"}.get(slot, library_id)}
                                    for slot, library_id in seeded.items()]})
    seen: dict[str, object] = {}

    class Recording:
        def complete(self, system: str, instruction: str, schema: dict[str, object]) -> ProviderResult:
            seen.update(system=system, instruction=json.loads(instruction), schema=schema)
            return ProviderResult(reply, {"provider": "replay", "source": "test", "model": None})

    plan = select_texture_plan(name="Mogadishu", map_id="m", latitude=2.05, longitude=45.33, brief="dirt streets, sheet roofs",
                               seed=3, provider=Recording(), extended=True, wide=True)  # type: ignore[arg-type]
    assert plan["selector"] == "ollama"
    chosen = {row["id"]: row["library_id"] for row in plan["entries"]}
    assert (chosen["roofs"], chosen["streets_plazas"], chosen["brick"]) == ("ph_asbestos_sheet", "ph_brown_mud_dry", "ph_clay_block_wall")
    instruction = seen["instruction"]
    assert instruction["slots"]["streets_plazas"] == ["paving", "earth"] and instruction["slots"]["roofs"] == ["roof", "concrete"]  # type: ignore[index]
    assert instruction["slots"]["doors"] == ["door"]  # type: ignore[index]
    assert "ph_asbestos_sheet" in instruction["ids_by_family"]["roof"] and "ph_brown_mud_dry" in instruction["ids_by_family"]["earth"]  # type: ignore[index]
    assert seen["system"] == (
        "Choose one tileable albedo for each slot of a playable city map. Keep the walls, roofs and ground in one calm palette. "
        "Use only listed ids, one id once, and a family listed for that slot. A slot's first family is its usual one: choose a "
        "later family (earth for dirt or sand streets, plaster on the brick slot, concrete roofs) or a corrugated sheet roof "
        "only when the brief or the place calls for it. Reply with JSON only.")
    assert _parse_choices(reply, rows, extended=True)["roofs"] == "ph_asbestos_sheet"


def test_the_seeded_variants_never_draw_a_distinctive_row() -> None:
    entries = [SimpleNamespace(id=slot, library_id="") for slot in sorted(VARIANT_SLOTS)]
    distinctive = {row["id"] for row in catalogue_rows() if row.get("distinctive")}
    assert distinctive
    from onward_generator.materials.variants import variant_picks

    for seed in range(40):
        picks = variant_picks(entries, catalogue_rows(), seed, extended=True)
        assert not distinctive & {i for ids in picks.values() for i in ids}, seed
