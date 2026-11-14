"""The maintainer's own wall decals, on every map (signs/user_decals.py, ADR-038 amendment of 14 November 2026)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from shapely import STRtree

from onward_generator.signs import place as signs_place
from onward_generator.signs.decals import Decal, Wall, place_decals
from onward_generator.signs.place import SignsError, place_signs
from onward_generator.signs.user_decals import (
    MANIFEST,
    MAX_FILE_BYTES,
    MAX_PX,
    MAX_SIZE_M,
    MIN_SIZE_M,
    USER_DECALS_DIR,
    UserDecalError,
    read_user_decals,
)
from tests.unit.test_street_signs import _base, _scene


def _folder(assets: Path, rows: object, images: dict[str, tuple[int, int, str]] | None = None) -> Path:
    folder = assets / USER_DECALS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for name, (w, h, mode) in (images or {}).items():
        Image.new(mode, (w, h), (200, 40, 40, 128) if mode == "RGBA" else (200, 40, 40)).save(folder / name)
    (folder / MANIFEST).write_text(json.dumps(rows), encoding="utf-8")
    return folder


def test_the_settings() -> None:
    assert (USER_DECALS_DIR, MANIFEST, MAX_PX, MAX_FILE_BYTES, MIN_SIZE_M, MAX_SIZE_M) == ("user_decals", "decals.json", 512, 16 * 1024 * 1024, 0.2, 3.0)


def test_no_folder_or_no_manifest_gives_no_decal(tmp_path: Path) -> None:
    assert read_user_decals(tmp_path) == []
    (tmp_path / USER_DECALS_DIR).mkdir()
    assert read_user_decals(tmp_path) == []


def test_each_row_is_one_decal_sized_from_the_image_aspect(tmp_path: Path) -> None:
    _folder(tmp_path, {"decals": [{"file": "Mural Calle.png", "width_m": 1.2, "description": "Mi mural"},
                                  {"file": "plain.png", "height_m": 0.5}]},
            {"Mural Calle.png": (1000, 500, "RGBA"), "plain.png": (300, 600, "RGB")})
    mural, plain = read_user_decals(tmp_path)
    assert (mural.id, mural.kind, mural.origin, mural.description) == ("user_mural_calle", "user", "user", "Mi mural")
    assert (mural.width_m, mural.height_m) == (1.2, 0.6)
    assert mural.rgba.shape == (256, 512, 4) and int(mural.rgba[0, 0, 3]) == 128  # scaled to 512 px, alpha kept
    assert (plain.id, plain.width_m, plain.height_m, plain.description) == ("user_plain", 0.25, 0.5, "")
    assert plain.rgba.shape == (512, 256, 4)
    assert int(plain.rgba[0, 0, 3]) == 255  # no alpha: an opaque rectangle


def test_a_small_image_keeps_its_size(tmp_path: Path) -> None:
    _folder(tmp_path, {"decals": [{"file": "a.png", "width_m": 1.0}]}, {"a.png": (MAX_PX, 128, "RGBA")})
    assert read_user_decals(tmp_path)[0].rgba.shape == (128, MAX_PX, 4)


@pytest.mark.parametrize(("rows", "images", "message"), [
    ([], {}, r'needs \{"decals": \[...\]\}$'),
    ({"decals": "x"}, {}, r'needs \{"decals": \[...\]\}$'),
    ({"decals": [{"width_m": 1}]}, {}, r"decals\[0\]: needs a file name$"),
    ({"decals": ["a.png"]}, {}, r"decals\[0\]: needs a file name$"),
    ({"decals": [{"file": "../a.png", "width_m": 1}]}, {}, r"file must be a .png in .*, by its plain name, got '../a.png'$"),
    ({"decals": [{"file": "a.jpg", "width_m": 1}]}, {}, r"file must be a .png"),
    ({"decals": [{"file": "missing.png", "width_m": 1}]}, {}, r"missing.png does not exist$"),
    ({"decals": [{"file": "a.png"}]}, {"a.png": (10, 10, "RGBA")}, "give width_m or height_m, not both and not neither$"),
    ({"decals": [{"file": "a.png", "width_m": 1, "height_m": 1}]}, {"a.png": (10, 10, "RGBA")}, "not both"),
    ({"decals": [{"file": "a.png", "width_m": "1"}]}, {"a.png": (10, 10, "RGBA")}, "the size must be a number of metres, got '1'$"),
    ({"decals": [{"file": "a.png", "width_m": True}]}, {"a.png": (10, 10, "RGBA")}, "got True$"),
    ({"decals": [{"file": "a.png", "width_m": 0.19}]}, {"a.png": (10, 10, "RGBA")}, "the width on the wall is 0.19 m; it must lie between 0.2 and 3 m$"),
    ({"decals": [{"file": "a.png", "width_m": 3.01}]}, {"a.png": (10, 10, "RGBA")}, "the width on the wall is 3.01 m"),
    ({"decals": [{"file": "a.png", "width_m": 2.0}]}, {"a.png": (10, 40, "RGBA")}, "the height on the wall is 8.00 m"),
    ({"decals": [{"file": "a.png", "width_m": 1.0}, {"file": "A.png", "width_m": 1.0}]},
     {"a.png": (10, 10, "RGBA"), "A.png": (10, 10, "RGBA")}, "two files give the same decal id: user_a$"),
])
def test_a_bad_manifest_or_image_is_refused_with_its_reason(tmp_path: Path, rows: object, images: dict, message: str) -> None:
    _folder(tmp_path, rows, images)
    with pytest.raises(UserDecalError, match=message):
        read_user_decals(tmp_path)


def test_bad_bytes_are_refused(tmp_path: Path) -> None:
    folder = _folder(tmp_path, {"decals": [{"file": "a.png", "width_m": 1.0}]})
    (folder / "a.png").write_bytes(b"not an image")
    with pytest.raises(UserDecalError, match="a.png cannot be read"):
        read_user_decals(tmp_path)
    Image.new("RGB", (10, 10)).save(folder / "a.png", format="JPEG")
    with pytest.raises(UserDecalError, match="a.png is not a PNG image$"):
        read_user_decals(tmp_path)
    (folder / MANIFEST).write_text("{", encoding="utf-8")
    with pytest.raises(UserDecalError, match="not JSON"):
        read_user_decals(tmp_path)


def test_a_file_over_the_limit_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.signs import user_decals

    _folder(tmp_path, {"decals": [{"file": "a.png", "width_m": 1.0}]}, {"a.png": (10, 10, "RGBA")})
    size = (tmp_path / USER_DECALS_DIR / "a.png").stat().st_size
    monkeypatch.setattr(user_decals, "MAX_FILE_BYTES", size)
    assert len(read_user_decals(tmp_path)) == 1
    monkeypatch.setattr(user_decals, "MAX_FILE_BYTES", size - 1)
    with pytest.raises(UserDecalError, match=r"a.png is over 0 MiB$"):
        read_user_decals(tmp_path)


def _decal(name: str, w: float = 1.0, origin: str = "generated") -> Decal:
    return Decal(name, "stain", w, 0.8, np.zeros((4, 4, 4), dtype=np.uint8), origin)


def test_the_required_decals_go_first_each_once_on_top_of_the_count() -> None:
    walls = [Wall(f"w{i}", np.array([0.0, 10.0 * i]), np.array([8.0, 10.0 * i]), np.array([0.0, -1.0]), 12.0) for i in range(4)]
    required = [_decal("user_a", origin="user"), _decal("user_b", origin="user")]
    placed = place_decals(walls, STRtree([]), lambda plan, z0, z1: False, [_decal("gen_x")], 3, 4, required=required)
    assert [p.decal.id for p in placed] == ["user_a", "user_b", "gen_x", "gen_x", "gen_x", "gen_x"]
    assert [p.decal.id for p in place_decals(walls, STRtree([]), lambda plan, z0, z1: False, [_decal("gen_x")], 3, 2)] == ["gen_x", "gen_x"]


def test_signs_place_puts_every_user_decal_on_the_map_or_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    base = tmp_path / "base"
    _base(base)
    assets = tmp_path / "repo" / "assets"
    _folder(assets, {"decals": [{"file": "mural.png", "width_m": 0.8}, {"file": "badge.png", "width_m": 0.5}]},
            {"mural.png": (200, 100, "RGBA"), "badge.png": (100, 100, "RGBA")})
    monkeypatch.setattr(signs_place, "REPO_ROOT", tmp_path / "repo")
    # The tests turn the local catalogues off; the user decals are local too.
    report = place_signs(base, scene, tmp_path / "off")
    assert all(row["origin"] != "user" for row in report["wall_decals"]["decals"])
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    report = place_signs(base, scene, tmp_path / "on")
    users = [row for row in report["wall_decals"]["decals"] if row["origin"] == "user"]
    assert [row["decal"] for row in users] == ["user_mural", "user_badge"] and users[0]["size_m"] == [0.8, 0.4]
    real = signs_place.place_decals
    monkeypatch.setattr(signs_place, "place_decals", lambda *a, required=(), **k: real(*a, **k))  # no wall takes it
    with pytest.raises(SignsError, match=r"^required decals that fit no exposed wall of the playable core: user_mural, user_badge \(make them smaller in their decals.json\)$"):
        place_signs(base, scene, tmp_path / "big")
    monkeypatch.setattr(signs_place, "place_decals", real)
    _folder(assets, {"decals": [{"file": "missing.png", "width_m": 1.0}]})
    with pytest.raises(SignsError, match="missing.png does not exist$"):
        place_signs(base, scene, tmp_path / "missing")


def test_the_size_bounds_are_inclusive_and_sizes_round_to_the_millimetre(tmp_path: Path) -> None:
    _folder(tmp_path, {"decals": [{"file": "a.png", "width_m": MIN_SIZE_M}, {"file": "b.png", "width_m": MAX_SIZE_M},
                                  {"file": "c.png", "width_m": 1.23456}]},
            {"a.png": (10, 10, "RGBA"), "b.png": (10, 10, "RGBA"), "c.png": (20, 10, "RGBA")})
    a, b, c = read_user_decals(tmp_path)
    assert (a.width_m, a.height_m, b.width_m, b.height_m) == (0.2, 0.2, 3.0, 3.0)
    assert (c.width_m, c.height_m) == (1.235, 0.617)


def test_an_error_names_the_row_it_comes_from(tmp_path: Path) -> None:
    _folder(tmp_path, {"decals": [{"file": "a.png", "width_m": 1.0}, {"file": "b.png", "width_m": 9.0}]},
            {"a.png": (10, 10, "RGBA"), "b.png": (10, 10, "RGBA")})
    with pytest.raises(UserDecalError, match=r"user_decals/decals.json decals\[1\]: the width on the wall is 9.00 m"):
        read_user_decals(tmp_path)


def test_ids_come_from_the_letters_and_digits_of_the_name_or_the_row_number(tmp_path: Path) -> None:
    _folder(tmp_path, {"decals": [{"file": "_Mi Mural_.png", "width_m": 1.0}, {"file": "___.png", "width_m": 1.0}]},
            {"_Mi Mural_.png": (10, 10, "RGBA"), "___.png": (10, 10, "RGBA")})
    assert [d.id for d in read_user_decals(tmp_path)] == ["user_mi_mural", "user_1"]


def test_only_the_duplicated_ids_are_named(tmp_path: Path) -> None:
    images = {name: (10, 10, "RGBA") for name in ("a.png", "A.png", "b.png", "B.png", "c.png")}
    _folder(tmp_path, {"decals": [{"file": name, "width_m": 1.0} for name in images]}, images)
    with pytest.raises(UserDecalError, match=r"two files give the same decal id: user_a, user_b$"):
        read_user_decals(tmp_path)


def test_a_large_image_is_scaled_smoothly(tmp_path: Path) -> None:
    rng = np.random.default_rng(3)
    picture = Image.fromarray(rng.integers(0, 255, (300, 1030, 4), dtype=np.uint8), "RGBA")
    folder = _folder(tmp_path, {"decals": [{"file": "a.png", "width_m": 2.0}]})
    picture.save(folder / "a.png")
    expected = picture.copy()
    expected.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
    assert np.array_equal(read_user_decals(tmp_path)[0].rgba, np.asarray(expected))


def test_the_size_limit_is_named_in_mebibytes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.signs import user_decals

    _folder(tmp_path, {"decals": [{"file": "a.png", "width_m": 1.0}]}, {"a.png": (10, 10, "RGBA")})
    monkeypatch.setattr(user_decals, "MAX_FILE_BYTES", 2 * 1024 * 1024)
    monkeypatch.setattr(user_decals.Path, "stat", lambda self, **k: type("S", (), {"st_size": 3 * 1024 * 1024, "st_mode": 0o100644})())
    with pytest.raises(UserDecalError, match=r"a.png is over 2 MiB$"):
        read_user_decals(tmp_path)


def test_a_map_can_leave_the_user_decals_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A map for the Onward Workshop, whose rules refuse the likeness of a real person: map.json "user_decals": false."""
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    root = tmp_path / "map"
    base = root / "data" / "processed" / "scene_materials"
    base.parent.mkdir(parents=True)
    _base(base)
    assets = tmp_path / "repo" / "assets"
    _folder(assets, {"decals": [{"file": "mural.png", "width_m": 0.8}]}, {"mural.png": (200, 100, "RGBA")})
    monkeypatch.setattr(signs_place, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    monkeypatch.setattr(signs_place, "_ambient", lambda base: ("", False))

    def users(out: str) -> list[str]:
        report = place_signs(base, scene, tmp_path / out)
        return [row["decal"] for row in report["wall_decals"]["decals"] if row["origin"] == "user"]

    root.mkdir(parents=True, exist_ok=True)
    for value, expected in ((None, ["user_mural"]), (True, ["user_mural"]), (False, [])):
        document = {} if value is None else {"user_decals": value}
        (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
        monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda r, d=document: d)
        assert users(f"out_{value}") == expected
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda r: {"user_decals": "no"})
    with pytest.raises(SignsError, match='^map.json "user_decals" must be true or false, got \'no\'$'):
        place_signs(base, scene, tmp_path / "bad")
    monkeypatch.setattr("onward_generator.maps.workspace.load_map", lambda r: (_ for _ in ()).throw(ValueError("broken")))
    assert users("unreadable") == ["user_mural"]  # an unreadable map.json reads as no switch


def test_the_project_decals_ship_with_the_repository_and_read_whole() -> None:
    """assets/decals/: committed, MIT, text and symbols only (ADR-038 amendment of 14 November 2026)."""
    from onward_generator.config.validation import REPO_ROOT
    from onward_generator.signs.user_decals import PROJECT_DECALS_DIR, PROJECT_DECALS_ENV, PROJECT_ORIGIN, read_project_decals

    assert (PROJECT_DECALS_DIR, PROJECT_ORIGIN, PROJECT_DECALS_ENV) == ("decals", "project", "ONWARD_PROJECT_DECALS")
    decals = read_project_decals(REPO_ROOT / "assets")
    assert [d.id for d in decals] == [
        "project_ai_code_it_you_own_it", "project_written_by_ai_tested_by_bullets", "project_your_neighborhood_our_battlefield",
        "project_from_openstreetmap_to_open_warfare", "project_if_its_on_the_map_we_fight_on_it",
        "project_no_mapper_needed_just_coordinates", "project_open_source_open_fire", "project_los_ohculos_team_plate",
        "project_los_ohculos_team_shield"]
    assert all(d.origin == d.kind == "project" and max(d.rgba.shape[:2]) == MAX_PX and d.description for d in decals)
    assert all(MIN_SIZE_M <= d.width_m <= MAX_SIZE_M and MIN_SIZE_M <= d.height_m <= MAX_SIZE_M for d in decals)
    assert "MIT" in (REPO_ROOT / "assets" / PROJECT_DECALS_DIR / "README.md").read_text(encoding="utf-8")


def test_the_project_decals_switch() -> None:
    from onward_generator.signs.user_decals import project_decals_enabled

    assert project_decals_enabled({}) is True and project_decals_enabled({"ONWARD_PROJECT_DECALS": "1"}) is True
    assert project_decals_enabled({"ONWARD_PROJECT_DECALS": "0"}) is False
    assert project_decals_enabled() is False  # tests/conftest.py turns them off


def test_signs_place_puts_the_project_decals_first_then_the_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    scene = tmp_path / "geo_scene.json"
    _scene(scene)
    base = tmp_path / "base"
    _base(base)
    assets = tmp_path / "repo" / "assets"
    _folder(assets, {"decals": [{"file": "mural.png", "width_m": 0.8}]}, {"mural.png": (200, 100, "RGBA")})
    project = assets / "decals"
    project.mkdir()
    Image.new("RGBA", (100, 100), (10, 120, 40, 255)).save(project / "sign.png")
    (project / MANIFEST).write_text(json.dumps({"decals": [{"file": "sign.png", "width_m": 0.6}]}), encoding="utf-8")
    monkeypatch.setattr(signs_place, "REPO_ROOT", tmp_path / "repo")
    monkeypatch.setattr(signs_place, "_ambient", lambda base: ("", False))

    def required(out: str) -> list[str]:
        report = place_signs(base, scene, tmp_path / out)
        return [row["decal"] for row in report["wall_decals"]["decals"] if row["origin"] in ("project", "user")]

    assert required("off") == []  # both off in the tests
    monkeypatch.setenv("ONWARD_PROJECT_DECALS", "1")
    assert required("project") == ["project_sign"]
    monkeypatch.setenv("ONWARD_LOCAL_CATALOGUES", "1")
    assert required("both") == ["project_sign", "user_mural"]
    monkeypatch.setattr(signs_place, "_map_switch", lambda base, key: key != "project_decals")
    assert required("no_project") == ["user_mural"]
