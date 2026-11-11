"""Local CC0 library: albedo and GLB only, buildings do not replace OSM."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from onward_generator.overgrow.manifest import LOAD_FIELDS

REPO = Path(__file__).resolve().parents[2]
# Per-row provenance of a downloaded model. Kenney records its licence in each pack's License.txt instead.
PROVENANCE = ("license", "license_url", "author", "source_url", "source")
CATALOG = REPO / "config" / "dress" / "imported_library.json"


def _load() -> dict:
    document = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert document["schema"] == "onward_generator.imported_library"
    assert document["schema_version"] == "0.1.0"
    return document


def test_imported_library_keeps_albedo_and_does_not_place_buildings() -> None:
    document = _load()
    rows = document["textures"] + document["objects"]
    ids = [row["id"] for row in rows]
    assert ids
    assert len(ids) == len(set(ids))
    surfaces = {row["surface"] for row in document["textures"]}
    assert {"wall", "roof", "floor", "sidewalk", "street"} <= surfaces
    for row in document["textures"]:
        assert row["license"] == "CC0-1.0"
        assert row["bound"] is False
        assert row["path"].startswith("assets/textures/")
        assert "Normal" not in row["path"]
        assert "Roughness" not in row["path"]
    for row in document["objects"]:
        assert row["license"] == "CC0-1.0"
        assert row["replaces_osm"] is False
        assert row["placed"] is False
        assert row["path"].endswith(".glb")
        assert row["path"].startswith("assets/kenney/")


def test_kenney_review_describes_every_mesh_and_drops_buildings() -> None:
    catalogue = _load()
    review = json.loads((REPO / "assets" / "kenney_review.json").read_text(encoding="utf-8"))
    assert review["schema"] == "onward_generator.kenney_review"
    rows = review["assets"]
    assert len(rows) == 272
    installed = [row for row in rows if row["installed"] is True]
    removed = [row for row in rows if row["installed"] is False]
    assert len(installed) == 177
    assert len(removed) == 95
    catalog_ids = {row["id"] for row in catalogue["objects"]}
    assert catalog_ids == {row["id"] for row in installed}
    assert all(row["role"] != "building" for row in catalogue["objects"])
    for row in rows:
        assert row["author"] == "Kenney"
        assert row["license"] == "CC0"
        assert row["reason"]
        assert row["usable"] is row["installed"]
    for row in installed:
        assert (REPO / "assets" / "kenney" / row["normalized_file"]).is_file()
    for row in removed:
        assert "Removed on 2026-10-26" in row["reason"] or "Removed on 2026-10-29" in row["reason"]
        assert "normalized_file" not in row
        pack = {"City Kit Suburban": "suburban", "Modular Buildings": "modular", "Retro Urban Kit": "urban"}[row["group"]]
        assert not (REPO / "assets" / "kenney" / pack / f"{row['name']}.glb").exists()


def test_kenney_manifest_matches_the_normalized_shape_and_the_files() -> None:
    normalized = json.loads((REPO / "assets" / "normalized" / "manifest.json").read_text(encoding="utf-8"))
    document = json.loads((REPO / "assets" / "kenney" / "manifest.json").read_text(encoding="utf-8"))
    assert list(document) == list(normalized)
    assert document["schema"] == normalized["schema"]
    assert document["schema_version"] == normalized["schema_version"]
    assert document["unit"] == "m"
    assert document["collider"] == "none"
    sample = [key for key in normalized["assets"][0] if key not in PROVENANCE]
    files = sorted(path.relative_to(REPO / "assets" / "kenney").as_posix() for path in (REPO / "assets" / "kenney").rglob("*.glb"))
    listed = [row["file"] for row in document["assets"]]
    assert sorted(listed) == files
    assert len(listed) == len(set(row["id"] for row in document["assets"]))
    for row in document["assets"]:
        assert [key for key in row if key not in LOAD_FIELDS] == sample
        assert row["use"]
        assert row["collider"] == "none"
        assert (REPO / "assets" / "kenney" / row["file"]).is_file()


def test_overgrow_selection_includes_every_asset_catalogue() -> None:
    from onward_generator.overgrow.library import LibraryReject, load_library, read_library
    from onward_generator.overgrow.library_catalog import choose_ids, installed_rows, library_directories

    directories = library_directories(REPO / "assets")
    assert {path.name for path in directories} == {"kenney", "kenney_food", "kenney_furniture", "kenney_mini_shop", "normalized", "polyhaven_shop",
                                                  "quaternius_city_props", "quaternius_furniture", "quaternius_home_interior",
                                                  "quaternius_nature", "quaternius_ultimate_furniture"}
    rows = installed_rows(REPO / "assets")
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids)) == 771
    assert "covered_car" in ids
    assert "kenney_suburban_driveway_long" in ids
    assert choose_ids(
        REPO / "assets",
        ["covered_car", "kenney_suburban_driveway_long"],
        all_installed=False,
    ) == ["covered_car", "kenney_suburban_driveway_long"]
    props, rejects = read_library(REPO / "assets")
    kit = {row["id"] for row in rows if row["placement"] == "kit_only"}
    assert len(kit) == 128
    interior = {row["id"] for row in rows if row["placement"] == "interior"}
    assert len(interior) == 505
    refused = {row["id"]: row["refused"] for row in rows if "refused" in row}
    assert refused == {"watchtower": "unknown_scale", "warning_signboard": "unknown_scale",
                       "kenney_modular_detail_ac_a": "low_detail", "kenney_modular_detail_ac_b": "low_detail"}
    expected = [LibraryReject(ident, "kit_only") for ident in kit]
    expected += [LibraryReject(ident, "interior") for ident in interior]
    expected += [LibraryReject(ident, reason) for ident, reason in refused.items()]
    assert sorted(rejects, key=lambda item: item.asset_id) == sorted(expected, key=lambda item: item.asset_id)
    assert {prop.spec.id for prop in load_library(REPO / "assets")} == set(ids) - kit - interior - set(refused)
    loaded = {prop.spec.id for prop in props}
    assert loaded == set(ids) - kit - interior - set(refused)
    trucks = [prop for prop in props if prop.spec.id.startswith("kenney_urban_truck_")]
    assert len(trucks) == 5 and all(prop.length_m > prop.half_y * 2 for prop in trucks)
    bench = next(prop for prop in props if prop.spec.id == "kenney_urban_detail_bench")
    assert bench.triangles == 84 and len(bench.parts) == 2
    driveway = next(prop for prop in props if prop.spec.id == "kenney_suburban_driveway_long")
    texture = (REPO / "assets/kenney/suburban/Textures/colormap.png").read_bytes()
    assert driveway.albedo == texture
    from onward_generator.generation.gltf import read_glb
    for glb in (REPO / "assets/kenney").rglob("*.glb"):
        doc, _blob = read_glb(glb.read_bytes())
        for image in doc.get("images") or []:
            uri = image.get("uri")
            if isinstance(uri, str):
                assert (glb.parent / uri).is_file(), f"{glb} -> {uri}"


def test_two_catalogues_cannot_share_an_id(tmp_path: Path) -> None:
    from onward_generator.overgrow.library_catalog import LibraryError, installed_rows

    for name in ("one", "two"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "manifest.json").write_text(
            json.dumps({"assets": [{"id": "shared", "file": "shared.glb"}]}),
            encoding="utf-8",
        )
    with pytest.raises(LibraryError, match="shared"):
        installed_rows(tmp_path)


def test_imported_files_match_the_catalogue_when_present() -> None:
    document = _load()
    rows = document["textures"] + document["objects"]
    first = REPO / rows[0]["path"]
    if not first.is_file():
        return
    for row in rows:
        path = REPO / row["path"]
        assert path.is_file(), row["id"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == row["sha256"]
