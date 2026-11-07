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
    WALL_CONTRAST_LIMIT,
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
        {"slot": "ochre_facades", "library_id": "ph_painted_plaster_wall"},
        {"slot": "plain_facades", "library_id": "ph_plastered_wall"},
        {"slot": "stone_monuments", "library_id": "acg_rock030"},
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
    roof = next(row for row in rows.values() if texture_family(row) == "roof")
    assert is_calm(roof, limit=-1.0)


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


@pytest.mark.parametrize(("slot", "library_id"), [
    ("sidewalks", "acg_concrete047b"),
    ("historic_facades", "acg_paintedplaster016"),
    ("brick", "acg_bricks100"),
    ("stone_monuments", "acg_rock055"),
])
def test_an_existing_plan_with_a_busy_texture_is_refused(slot: str, library_id: str) -> None:
    """A plan saved before the calm rule would keep the repeated cover or stain on every tile."""
    from onward_generator.materials.library_plan import assert_library_entries

    with pytest.raises(PlanError, match=rf"{slot} cannot use {library_id}: its tile repeats visibly\. Run `map create --name <map> --textures`"):
        assert_library_entries({"entries": [{"id": slot, "library_id": library_id}]})


def test_a_plan_with_calm_textures_validates_and_a_wrong_or_unknown_id_does_not() -> None:
    from onward_generator.materials.library_plan import assert_library_entries

    assert_library_entries({"entries": [
        {"id": "sidewalks", "library_id": "acg_concrete012"},
        {"id": "historic_facades", "library_id": "ph_plastered_wall"},
        {"id": "brick", "library_id": "acg_bricks001"},
    ]})
    with pytest.raises(PlanError, match="sidewalks cannot use acg_asphalt017$"):
        assert_library_entries({"entries": [{"id": "sidewalks", "library_id": "acg_asphalt017"}]})
    with pytest.raises(PlanError, match="sidewalks cannot use acg_missing$"):
        assert_library_entries({"entries": [{"id": "sidewalks", "library_id": "acg_missing"}]})


CALM_PLASTER = {"acg_paintedplaster006", "ph_painted_plaster_wall", "ph_plastered_wall"}
CALM_BRICK = {"acg_bricks001", "acg_bricks059", "acg_bricks082b", "acg_bricks105"}
CALM_ROCK = {"acg_rock030"}


def test_only_calm_plaster_brick_and_rock_join_the_wall_pools() -> None:
    """Large stains, pale stones or dark bands repeat every tile on a facade, so those images stay out."""
    rows = {str(row["id"]): row for row in library_rows()}
    walls = {"plaster": CALM_PLASTER, "brick": CALM_BRICK, "rock": CALM_ROCK}
    for family, expected in walls.items():
        members = {library_id for library_id, row in rows.items() if texture_family(row) == family}
        assert {library_id for library_id in members if is_calm(rows[library_id])} == expected, family
    assert WALL_CONTRAST_LIMIT > COARSE_CONTRAST_LIMIT


def test_wall_contrast_matches_the_measured_library() -> None:
    measured = {
        "acg_paintedplaster006": 0.0214,
        "acg_paintedplaster010": 0.0254,
        "acg_paintedplaster016": 0.0656,
        "acg_bricks003": 0.0260,
        "acg_rock022": 0.0267,
    }
    for library_id, expected in measured.items():
        path = REPO_ROOT / "assets" / "textures" / "ambientcg" / f"{library_id}.jpg"
        assert coarse_contrast(path) == pytest.approx(expected, abs=2e-4), library_id


def test_the_poly_haven_plasters_are_plaster() -> None:
    assert texture_family({"name": "painted_plaster_wall"}) == "plaster"
    assert texture_family({"name": "plastered_wall"}) == "plaster"
    assert texture_family({"name": "beige_wall_001"}) is None


@pytest.mark.parametrize("seed", range(40))
def test_every_seed_fills_the_wall_slots_from_the_calm_pools(seed: int) -> None:
    choices = deterministic_choices(library_rows(), seed)
    plaster = {choices["historic_facades"], choices["ochre_facades"], choices["plain_facades"]}
    assert plaster == CALM_PLASTER
    assert choices["brick"] in CALM_BRICK
    assert choices["stone_monuments"] in CALM_ROCK


# Plan C1: roofs and paving are measured only with the map.json "repetition" switch (extended=True).
CALM_PAVING = {"acg_pavingstones151", "acg_pavingstones007", "acg_pavingstones008"}
BUSY_PAVING = {"acg_pavingstones050", "acg_pavingstones141"}
CALM_ROOF = {"acg_roofingtiles014a", "acg_roofingtiles014b", "acg_roofingtiles011b", "acg_roofingtiles011c"}
BUSY_ROOF = {"acg_roofingtiles012c"}
# deterministic_choices(library_rows(), seed) before plan C1, for seeds 0..20 and 48152 (roofs, streets_plazas).
DEFAULT_ROOF_PAVING = {
    0: ("acg_roofingtiles012c", "acg_pavingstones050"), 1: ("acg_roofingtiles011b", "acg_pavingstones007"),
    2: ("acg_roofingtiles011b", "acg_pavingstones151"), 3: ("acg_roofingtiles011c", "acg_pavingstones007"),
    4: ("acg_roofingtiles014a", "acg_pavingstones007"), 5: ("acg_roofingtiles011b", "acg_pavingstones151"),
    6: ("acg_roofingtiles011b", "acg_pavingstones141"), 7: ("acg_roofingtiles014b", "acg_pavingstones008"),
    8: ("acg_roofingtiles011c", "acg_pavingstones050"), 9: ("acg_roofingtiles011c", "acg_pavingstones007"),
    10: ("acg_roofingtiles011c", "acg_pavingstones050"), 11: ("acg_roofingtiles012c", "acg_pavingstones050"),
    12: ("acg_roofingtiles014a", "acg_pavingstones008"), 13: ("acg_roofingtiles012c", "acg_pavingstones151"),
    14: ("acg_roofingtiles014a", "acg_pavingstones050"), 15: ("acg_roofingtiles014b", "acg_pavingstones008"),
    16: ("acg_roofingtiles011b", "acg_pavingstones007"), 17: ("acg_roofingtiles011b", "acg_pavingstones050"),
    18: ("acg_roofingtiles011c", "acg_pavingstones008"), 19: ("acg_roofingtiles014b", "acg_pavingstones050"),
    20: ("acg_roofingtiles012c", "acg_pavingstones008"), 48152: ("acg_roofingtiles011b", "acg_pavingstones141"),
}
# The sha256 of every default pick for those seeds, taken before plan C1.
DEFAULT_PICKS_SHA256 = "e9c372941b1fa06ecb57cc6db4d4e4e4b44aaef8cbb3ab5fcdb2fa966a093d1c"


def test_roof_and_paving_contrast_matches_the_measured_library() -> None:
    measured = {
        "acg_pavingstones007": 0.0126, "acg_pavingstones008": 0.0152, "acg_pavingstones050": 0.0165,
        "acg_pavingstones141": 0.0188, "acg_pavingstones151": 0.0073,
        "acg_roofingtiles011b": 0.0168, "acg_roofingtiles011c": 0.0183, "acg_roofingtiles012c": 0.0211,
        "acg_roofingtiles014a": 0.0109, "acg_roofingtiles014b": 0.0156,
    }
    for library_id, expected in measured.items():
        path = REPO_ROOT / "assets" / "textures" / "ambientcg" / f"{library_id}.jpg"
        assert coarse_contrast(path) == pytest.approx(expected, abs=2e-4), library_id


def test_the_roof_and_paving_limits_and_the_extended_table() -> None:
    from onward_generator.materials.library_plan import (
        CONTRAST_LIMITS,
        EXTENDED_CONTRAST_LIMITS,
        NOT_APPLICABLE_FAMILIES,
        PAVING_CONTRAST_LIMIT,
        ROOF_CONTRAST_LIMIT,
        contrast_limit,
    )

    assert PAVING_CONTRAST_LIMIT == 0.016
    assert ROOF_CONTRAST_LIMIT == 0.019
    assert set(CONTRAST_LIMITS) == {"asphalt", "concrete", "plaster", "brick", "rock"}
    assert EXTENDED_CONTRAST_LIMITS == {**CONTRAST_LIMITS, "paving": 0.016, "roof": 0.019}
    assert NOT_APPLICABLE_FAMILIES == frozenset({"door"})
    assert contrast_limit("roof") is None
    assert contrast_limit("roof", extended=True) == 0.019
    assert contrast_limit("paving", extended=True) == 0.016
    assert contrast_limit("asphalt") == contrast_limit("asphalt", extended=True) == COARSE_CONTRAST_LIMIT
    assert contrast_limit("door", extended=True) is None
    assert contrast_limit("bark", extended=True) is None


def test_extended_pools_keep_only_calm_roofs_and_paving() -> None:
    rows = {str(row["id"]): row for row in library_rows()}
    for family, calm, busy in (("paving", CALM_PAVING, BUSY_PAVING), ("roof", CALM_ROOF, BUSY_ROOF)):
        members = {library_id for library_id, row in rows.items() if texture_family(row) == family}
        assert members == calm | busy, family
        assert {library_id for library_id in members if is_calm(rows[library_id], extended=True)} == calm, family
        assert {library_id for library_id in members if is_calm(rows[library_id])} == members, family


def test_the_extended_limit_is_inclusive() -> None:
    rows = {str(row["id"]): row for row in library_rows()}
    for library_id in ("acg_roofingtiles012c", "acg_pavingstones141"):
        row = rows[library_id]
        value = coarse_contrast(REPO_ROOT / str(row["path"]))
        assert is_calm(row, limit=value, extended=True)
        assert not is_calm(row, limit=value - 1e-6, extended=True)


def test_the_default_picks_are_the_ones_before_plan_c1() -> None:
    import hashlib

    rows = library_rows()
    seeds = [*range(21), 48152]
    picks = {seed: deterministic_choices(rows, seed) for seed in seeds}
    assert {seed: (picks[seed]["roofs"], picks[seed]["streets_plazas"]) for seed in seeds} == DEFAULT_ROOF_PAVING
    digest = hashlib.sha256(json.dumps({str(k): v for k, v in picks.items()}, sort_keys=True).encode()).hexdigest()
    assert digest == DEFAULT_PICKS_SHA256


@pytest.mark.parametrize("seed", [*range(21), 48152])
def test_extended_picks_change_only_roofs_and_paving_and_stay_calm(seed: int) -> None:
    rows = library_rows()
    default, extended = deterministic_choices(rows, seed), deterministic_choices(rows, seed, extended=True)
    assert {slot for slot in default if default[slot] != extended[slot]} <= {"roofs", "streets_plazas"}
    assert set(default) == set(extended)
    assert extended["roofs"] in CALM_ROOF
    assert extended["streets_plazas"] in CALM_PAVING


def test_extended_pools_are_never_empty() -> None:
    rows = library_rows()
    assert unfilled_slots(rows, extended=True) == unfilled_slots(rows)


@pytest.mark.parametrize(("slot", "library_id"), [("roofs", "acg_roofingtiles012c"), ("streets_plazas", "acg_pavingstones141"),
                                                  ("streets_plazas", "acg_pavingstones050")])
def test_an_over_limit_roof_or_paving_is_refused_only_when_extended(slot: str, library_id: str) -> None:
    from onward_generator.materials.library_plan import assert_library_entries

    document = {"entries": [{"id": slot, "library_id": library_id}]}
    assert_library_entries(document)
    with pytest.raises(PlanError) as caught:
        assert_library_entries(document, extended=True)
    assert str(caught.value) == (
        f"{slot} cannot use {library_id}: its tile repeats visibly. "
        "Run `map create --name <map> --textures` to choose this map's textures again"
    )


def _reply_with(rows: list[dict], slot: str, library_id: str) -> str:
    choices = deterministic_choices(rows, 0, extended=True)
    choices[slot] = library_id
    return json.dumps({"choices": [{"slot": key, "library_id": value} for key, value in choices.items()]})


@pytest.mark.parametrize(("slot", "library_id"), [("roofs", "acg_roofingtiles012c"), ("streets_plazas", "acg_pavingstones141")])
def test_a_model_reply_with_a_busy_roof_or_paving_is_rejected_only_when_extended(slot: str, library_id: str) -> None:
    from onward_generator.materials.library_plan import _parse_choices

    rows = library_rows()
    reply = _reply_with(rows, slot, library_id)
    assert _parse_choices(reply, rows)[slot] == library_id
    family = "roof" if slot == "roofs" else "paving"
    with pytest.raises(PlanError, match=rf"^{library_id} is not in the {family} pool$"):
        _parse_choices(reply, rows, extended=True)
    common = {"name": "Murcia", "map_id": "murcia", "latitude": MURCIA_CENTER_LAT, "longitude": MURCIA_CENTER_LON,
              "brief": None, "seed": 5}
    accepted = select_texture_plan(**common, provider=StaticProvider("replay", reply, "test"))
    assert accepted["selector"] == "ollama"
    assert next(row for row in accepted["entries"] if row["id"] == slot)["library_id"] == library_id
    rejected = select_texture_plan(**common, provider=StaticProvider("replay", reply, "test"), extended=True)
    assert rejected["selector"] == "library"
    assert f"{library_id} is not in the {family} pool" in rejected["description"]
    picks = deterministic_choices(rows, 5, extended=True)
    assert {row["id"]: row["library_id"] for row in rejected["entries"]} == picks


def test_an_extended_model_reply_with_calm_tiles_is_accepted() -> None:
    rows = library_rows()
    choices = deterministic_choices(rows, 2, extended=True)
    reply = json.dumps({"choices": [{"slot": key, "library_id": value} for key, value in choices.items()]})
    plan = select_texture_plan(name="Murcia", map_id="murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                               brief=None, seed=2, provider=StaticProvider("replay", reply, "test"), extended=True)
    assert plan["selector"] == "ollama"


def test_the_extended_plan_description_names_only_the_extended_gaps() -> None:
    rows = [_fixture_row("fx_roof_busy", "RoofingTiles012C")]
    rows[0]["path"] = "assets/textures/ambientcg/acg_roofingtiles012c.jpg"
    plan = select_texture_plan(name="Murcia", map_id="murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                               brief=None, seed=2, library=rows, extended=True)
    assert "roofs (roof)" in plan["description"]
    plain = select_texture_plan(name="Murcia", map_id="murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON,
                                brief=None, seed=2, library=rows)
    assert "roofs (roof)" not in plain["description"]


def _applied(slot: str, asset_id: str, color_id: str = "c") -> dict[str, str]:
    return {"id": slot, "asset_id": asset_id, "color_id": color_id}


def test_repetition_rows_measure_roofs_and_paving_and_skip_doors(monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.materials import library_plan

    rows = {str(row["id"]): row for row in library_rows()}
    paths = {library_id: REPO_ROOT / str(row["path"]) for library_id, row in rows.items()}
    measured: list[Path] = []
    real = library_plan.coarse_contrast

    def spy(path: Path) -> float:
        measured.append(path)
        return real(path)

    monkeypatch.setattr(library_plan, "coarse_contrast", spy)
    out = library_plan.repetition_rows([
        _applied("roofs", "acg_roofingtiles012c", "roof_tile"),
        _applied("streets_plazas", "acg_pavingstones151", "cobblestone_paving"),
        _applied("doors", "kenney_fantasy_door_wood", "door_wood"),
        _applied("trunks", "kenney_fantasy_door_wood", "tree_trunk"),
        _applied("plain_facades", "ph_plastered_wall", "plaster_white"),
    ], paths)
    assert out[0] == {"slot": "roofs", "color_id": "roof_tile", "asset_id": "acg_roofingtiles012c", "family": "roof",
                      "coarse_contrast": round(real(paths["acg_roofingtiles012c"]), 4), "limit": 0.019,
                      "over_limit": True, "status": "measured"}
    assert out[0]["coarse_contrast"] == pytest.approx(0.0211, abs=2e-4)
    assert out[1]["over_limit"] is False and out[1]["limit"] == 0.016 and out[1]["status"] == "measured"
    assert out[2] == {"slot": "doors", "color_id": "door_wood", "asset_id": "kenney_fantasy_door_wood", "family": "door",
                      "coarse_contrast": None, "limit": None, "over_limit": False, "status": "not_applicable"}
    assert out[3]["status"] == "unmeasured" and out[3]["limit"] is None and out[3]["coarse_contrast"] is None
    assert out[3]["family"] == "bark" and out[3]["over_limit"] is False
    assert out[4]["status"] == "measured" and out[4]["limit"] == 0.025
    assert paths["kenney_fantasy_door_wood"] not in measured
    assert measured == [paths["acg_roofingtiles012c"], paths["acg_pavingstones151"], paths["ph_plastered_wall"]]


def test_repetition_rows_flag_a_busy_tile_and_round_to_four_places(tmp_path: Path) -> None:
    from onward_generator.materials.library_plan import repetition_rows

    rng = np.random.default_rng(7)
    grain = 120 + rng.integers(-20, 21, size=(256, 256))
    cover = grain.copy()
    cover[112:144, 112:144] = 200
    paths = {"busy": _grey(tmp_path, "cover", cover), "calm": _grey(tmp_path, "grain", grain)}
    busy, calm = repetition_rows([_applied("streets_plazas", "busy"), _applied("streets_plazas", "calm")], paths)
    assert busy["over_limit"] is True and calm["over_limit"] is False
    assert busy["coarse_contrast"] == round(coarse_contrast(paths["busy"]), 4)
    assert calm["coarse_contrast"] == round(coarse_contrast(paths["calm"]), 4)


def test_the_over_limit_verdict_is_on_the_unrounded_value(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.materials import library_plan

    path = tmp_path / "x.png"
    monkeypatch.setattr(library_plan, "coarse_contrast", lambda _path: 0.01600004)
    (row,) = library_plan.repetition_rows([_applied("streets_plazas", "x")], {"x": path})
    assert row["coarse_contrast"] == 0.016
    assert row["over_limit"] is True
    monkeypatch.setattr(library_plan, "coarse_contrast", lambda _path: 0.016)
    (row,) = library_plan.repetition_rows([_applied("streets_plazas", "x")], {"x": path})
    assert row["over_limit"] is False
