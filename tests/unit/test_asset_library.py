"""The enrichment library only selects meshes that are installed and licensed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.overgrow.library_catalog import (
    REVIEW_FILE,
    LibraryError,
    choose_ids,
    read_selection,
    review_rows,
    write_selection,
)
from onward_generator.overgrow.sketchfab_fetch import SPECS, _pick

LIBRARY = Path(__file__).resolve().parents[2] / "assets" / "normalized"


def test_download_pick_prefers_gltf_over_a_bare_glb() -> None:
    kind, item = _pick({
        "latest": {
            "gltf": [{"url": "https://example.invalid/model.zip", "size": 1000}],
            "glb": [{"url": "https://example.invalid/bare.glb", "size": 100, "textureMaxResolution": 0}],
        }
    })
    assert kind == "gltf"
    assert item["size"] == 1000


def test_download_list_is_only_the_usable_review() -> None:
    rows = review_rows(REVIEW_FILE)
    usable = {row["uid"] for row in rows if row["usable"] is True}
    blocked = {row["uid"] for row in rows if row["usable"] is not True}
    assert set(SPECS) == usable
    assert not set(SPECS) & blocked


def test_sketchfab_review_rejects_noncommercial_and_game_extracts() -> None:
    rows = review_rows(REVIEW_FILE)
    assert len(rows) == 46
    by_name = {row["name"]: row for row in rows}
    assert by_name["Post-Apoc Civilian Cars"]["usable"] is False
    assert by_name["Collection of oil barrels."]["usable"] is False
    assert by_name["Military Barricade"]["usable"] is False
    assert by_name["Pubg Building / Low Poly Building"]["usable"] is False
    assert by_name["Car (Slendrina the Forest)"]["usable"] is False
    assert by_name["Ruined buildings pack - Free download"]["usable"] is False
    assert by_name["M725 Military Ambulance"]["usable"] is False
    assert by_name["Grass free download"]["usable"] is False
    assert by_name["realistics grass 10"]["usable"] is False
    assert by_name["Low Poly Tree"]["usable"] is False
    assert by_name["Tree Elm"]["usable"] is False
    assert all(row["installed"] is False for row in rows if row["usable"] is not True)
    assert sum(1 for row in rows if row["usable"]) == 24
    for row in rows:
        if row.get("installed"):
            assert (LIBRARY / f"{row['id']}.glb").is_file()


def test_selection_refuses_a_model_that_is_not_installed(tmp_path: Path) -> None:
    with pytest.raises(LibraryError, match="not downloaded"):
        choose_ids(LIBRARY, ["Rusty Abandoned Truck with Interior - Game Ready"], all_installed=False)
    with pytest.raises(LibraryError, match="CC-BY-NC"):
        choose_ids(LIBRARY, ["Van Shell"], all_installed=False)
    ids = choose_ids(LIBRARY, ["covered_car"], all_installed=False)
    assert ids == ["covered_car"]
    out = tmp_path / "prop_selection.json"
    write_selection(out, ids)
    assert read_selection(out) == ["covered_car"]
    with pytest.raises(FileExistsError):
        write_selection(out, ids)


def test_installed_selection_matches_the_normalized_manifest() -> None:
    ids = choose_ids(LIBRARY, [], all_installed=True)
    manifest = json.loads((LIBRARY / "manifest.json").read_text(encoding="utf-8"))
    assert ids == [row["id"] for row in manifest["assets"]]
    assert "covered_car" in ids
    assert len(ids) >= 27
