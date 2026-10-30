"""A map texture plan is chosen from the library and can be replaced."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.ai.providers import StaticProvider
from onward_generator.maps.workspace import create_map, refresh_texture_plan
from onward_generator.materials.library_plan import (
    COARSE_CONTRAST_LIMIT,
    REPO_ROOT,
    coarse_contrast,
    deterministic_choices,
    is_calm,
    library_rows,
    select_texture_plan,
    texture_family,
    unfilled_slots,
)
from onward_generator.materials.plan import PlanError
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON


def test_a_rejected_model_reply_falls_back_to_the_library() -> None:
    plan = select_texture_plan(
        name="Murcia",
        map_id="murcia",
        latitude=MURCIA_CENTER_LAT,
        longitude=MURCIA_CENTER_LON,
        brief=None,
        seed=3,
        provider=StaticProvider("replay", '{"choices":[{"slot":"roofs","library_id":"acg_asphalt011"}]}', "test"),
    )
    assert plan["selector"] == "library"
    assert plan["provider"] == "library"
    roofs = next(row for row in plan["entries"] if row["id"] == "roofs")
    assert roofs["library_id"].startswith("acg_roofingt")


def test_refresh_replaces_only_the_texture_plan(tmp_path: Path) -> None:
    root = tmp_path / "murcia"
    create_map(
        root, map_id="murcia", name="Murcia",
        latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100, seed=4,
    )
    marker = root / "data" / "processed" / "keep.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")
    before = (root / "map.json").read_text(encoding="utf-8")
    reply = json.dumps({"choices": [
        {"slot": "historic_facades", "library_id": "acg_paintedplaster006"},
        {"slot": "ochre_facades", "library_id": "acg_paintedplaster010"},
        {"slot": "plain_facades", "library_id": "acg_paintedplaster014"},
        {"slot": "stone_monuments", "library_id": "acg_rock022"},
        {"slot": "streets_plazas", "library_id": "acg_pavingstones007"},
        {"slot": "roofs", "library_id": "acg_roofingtiles011b"},
        {"slot": "brick", "library_id": "acg_bricks001"},
        {"slot": "carriageway", "library_id": "acg_asphalt011"},
        {"slot": "sidewalks", "library_id": "acg_concrete012"},
        {"slot": "plain_pedestrian", "library_id": "acg_concrete047a"},
        {"slot": "flat_roofs", "library_id": "acg_concrete048"},
        {"slot": "doors", "library_id": "kenney_fantasy_door_wood"},
    ]})
    path = refresh_texture_plan(root, texture_provider=StaticProvider("replay", reply, "test"))
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert plan["selector"] == "ollama"
    assert next(row["library_id"] for row in plan["entries"] if row["id"] == "roofs") == "acg_roofingtiles011b"
    assert (root / "map.json").read_text(encoding="utf-8") == before
    assert marker.read_text(encoding="utf-8") == "keep"


def test_a_bark_texture_fills_the_trunks_slot() -> None:
    bark = {
        "id": "ph_bark_brown_02", "name": "bark_brown_02", "provider": "polyhaven",
        "path": "assets/textures/bark_brown_02/bark_brown_02_diff_1k.jpg", "license": "CC0-1.0",
    }
    rows = [*library_rows(), bark]
    assert texture_family(bark) == "bark"
    assert "trunks" not in unfilled_slots(rows)
    assert deterministic_choices(rows, 3)["trunks"] == "ph_bark_brown_02"


def _fixture_row(library_id: str, name: str) -> dict[str, str]:
    return {"id": library_id, "name": name, "provider": "ambientcg", "path": f"assets/textures/{library_id}.jpg", "license": "CC0-1.0"}


# One texture per family except bark and rock, so the real library can gain bark without breaking the test.
FIXTURE_LIBRARY = [
    _fixture_row("fx_plaster", "PaintedPlaster001"),
    _fixture_row("fx_paving", "PavingStones001"),
    _fixture_row("fx_roof", "RoofingTiles001"),
    _fixture_row("fx_brick", "Bricks001"),
    _fixture_row("fx_asphalt", "Asphalt001"),
    _fixture_row("fx_concrete", "Concrete001"),
    _fixture_row("fx_door", "door_wood"),
]


def test_a_slot_without_a_library_family_is_reported() -> None:
    rows = FIXTURE_LIBRARY
    assert unfilled_slots(rows) == ["stone_monuments", "trunks"]
    plan = select_texture_plan(
        name="Murcia", map_id="murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
        brief=None, seed=3, library=rows,
    )
    assert {row["id"] for row in plan["entries"]}.isdisjoint({"stone_monuments", "trunks"})
    assert "no texture for stone_monuments (rock), trunks (bark)" in plan["description"]


CALM_ASPHALT = {"acg_asphalt011", "acg_asphalt020s"}
CALM_CONCRETE = {"acg_concrete012", "acg_concrete047a", "acg_concrete048"}
BUSY_GROUND = {"acg_asphalt017", "acg_asphalt019", "acg_asphalt021", "acg_asphalt022", "acg_concrete047b"}


def _grey(tmp_path: Path, name: str, pixels: np.ndarray) -> Path:
    path = tmp_path / f"{name}.png"
    Image.fromarray(pixels.astype(np.uint8)).save(path)
    return path


def test_fine_grain_is_calm_and_a_centred_cover_is_not(tmp_path: Path) -> None:
    rng = np.random.default_rng(7)
    grain = 120 + rng.integers(-20, 21, size=(256, 256))
    cover = grain.copy()
    cover[112:144, 112:144] = 200
    assert coarse_contrast(_grey(tmp_path, "grain", grain)) < 0.005
    assert coarse_contrast(_grey(tmp_path, "cover", cover)) > 2 * COARSE_CONTRAST_LIMIT


def test_coarse_contrast_matches_the_measured_library() -> None:
    measured = {
        "acg_asphalt017": 0.0204,
        "acg_asphalt022": 0.0138,
        "acg_concrete012": 0.0105,
        "acg_concrete047b": 0.0261,
    }
    for library_id, expected in measured.items():
        path = REPO_ROOT / "assets" / "textures" / "ambientcg" / f"{library_id}.jpg"
        assert coarse_contrast(path) == pytest.approx(expected, abs=2e-4), library_id


def test_only_calm_asphalt_and_concrete_join_the_ground_pools() -> None:
    rows = {str(row["id"]): row for row in library_rows()}
    ground = {library_id for library_id, row in rows.items() if texture_family(row) in {"asphalt", "concrete"}}
    calm = {library_id for library_id in ground if is_calm(rows[library_id])}
    assert calm == CALM_ASPHALT | CALM_CONCRETE
    assert ground - calm == BUSY_GROUND


def test_the_limit_is_inclusive_and_other_families_are_never_measured() -> None:
    rows = {str(row["id"]): row for row in library_rows()}
    busy = rows["acg_concrete047b"]
    path = REPO_ROOT / str(busy["path"])
    assert is_calm(busy, limit=coarse_contrast(path))
    assert not is_calm(busy, limit=coarse_contrast(path) - 1e-6)
    brick = next(row for row in rows.values() if texture_family(row) == "brick")
    assert is_calm(brick, limit=-1.0)


def test_a_ground_row_without_a_file_is_left_to_the_record_check() -> None:
    row = _fixture_row("fx_asphalt", "Asphalt001")
    assert is_calm(row, limit=-1.0)


@pytest.mark.parametrize("seed", range(40))
def test_every_seed_fills_the_ground_slots_from_the_calm_pools(seed: int) -> None:
    rows = library_rows()
    choices = deterministic_choices(rows, seed)
    assert choices["carriageway"] in CALM_ASPHALT
    assert {choices["sidewalks"], choices["plain_pedestrian"], choices["flat_roofs"]} == CALM_CONCRETE
    assert [slot for slot in ("carriageway", "sidewalks", "plain_pedestrian", "flat_roofs") if slot not in choices] == []
    assert sum(1 for library_id in choices.values() if library_id.startswith("acg_asphalt")) == 1


def test_a_model_reply_with_a_busy_texture_is_rejected() -> None:
    reply = json.dumps({"choices": [{"slot": "sidewalks", "library_id": "acg_concrete047b"}]})
    plan = select_texture_plan(
        name="Murcia", map_id="murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
        brief=None, seed=3, provider=StaticProvider("replay", reply, "test"),
    )
    assert plan["selector"] == "library"
    assert "acg_concrete047b is not in the concrete pool" in plan["description"]


def test_an_existing_plan_with_a_busy_texture_still_validates() -> None:
    from onward_generator.materials.library_plan import assert_library_entries

    document = {"entries": [{"id": "sidewalks", "library_id": "acg_concrete047b"}]}
    assert_library_entries(document)
    with pytest.raises(PlanError, match="cannot use"):
        assert_library_entries({"entries": [{"id": "sidewalks", "library_id": "acg_asphalt017"}]})
