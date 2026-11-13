"""Plan T1: the wide CC0 library joins the texture pools only for a map with the "wide_library" switch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
from tests.conftest import MURCIA_CENTER

WIDE = library_rows(WIDE_LIBRARY_PATH)
FAMILIES = {"plaster", "brick", "rock", "paving", "concrete", "asphalt", "roof", "bark"}


def test_every_wide_row_is_a_cached_cc0_poly_haven_file_that_matches_its_hash() -> None:
    assert len(WIDE) == 25
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
