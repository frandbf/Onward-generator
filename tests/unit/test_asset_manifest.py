"""Curated placement semantics in the prop catalogues (manifest 0.2.0)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import box
from onward_generator.overgrow.catalog import ALL_ASSETS, AssetSpec
from onward_generator.overgrow.library import Prop, read_library
from onward_generator.overgrow.library_catalog import LibraryError
from onward_generator.overgrow.manifest import (
    KIT_ONLY,
    LEGACY_VERSION,
    MANIFEST_VERSION,
    SEMANTIC_FIELDS,
    check_manifest,
    keep_semantics,
)
from onward_generator.overgrow.prompt import PROMPT_VERSION, SYSTEM_PROMPT, library_rows
from onward_generator.overgrow.sketchfab_fetch import SPECS

REPO = Path(__file__).resolve().parents[2]
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 16
SEMANTICS = {
    "origin": "estimated",
    "basis": "rendered views",
    "reviewed_on": "2026-10-30",
    "fields": list(SEMANTIC_FIELDS),
}


def _repo(name: str) -> dict:
    return json.loads((REPO / "assets" / name / "manifest.json").read_text(encoding="utf-8"))


def _row(**changes: object) -> dict:
    row = {
        "id": "crate", "file": "crate.glb", "category": "waste", "role": "debris",
        "surfaces": ["sidewalk", "carriageway"], "anchor": "ground", "lane": "forbid", "clearance_m": 0.0,
        "use": "test", "description": "A test crate on the ground.", "placement": "street",
        "grouping": "cluster", "condition": "worn", "tags": ["wood"],
    }
    row.update(changes)
    return row


def _document(*rows: dict) -> dict:
    return {
        "schema": "onward_generator.abandoned_prefabs",
        "schema_version": MANIFEST_VERSION,
        "semantics": copy.deepcopy(SEMANTICS),
        "assets": list(rows) or [_row()],
    }


@pytest.mark.parametrize("name", ["kenney", "normalized"])
def test_installed_manifests_are_curated_and_valid(name: str) -> None:
    document = _repo(name)
    assert document["schema_version"] == MANIFEST_VERSION
    assert document["semantics"]["origin"] == "estimated"
    check_manifest(document, name)


def test_code_specs_match_the_corrected_manifest_rows() -> None:
    rows = {row["id"]: row for row in _repo("normalized")["assets"]}
    specs = [
        {"id": spec.id, "category": spec.category, "role": spec.role, "surfaces": list(spec.surfaces),
         "anchor": spec.anchor, "use": spec.use}
        for spec in ALL_ASSETS
    ] + list(SPECS.values())
    for spec in specs:
        row = rows[spec["id"]]
        for key in ("category", "role", "surfaces", "anchor", "use"):
            assert row[key] == spec[key], (spec["id"], key)


def test_a_valid_manifest_passes() -> None:
    check_manifest(_document(), "m")
    check_manifest(_document(_row(placement=KIT_ONLY, anchor="facade", surfaces=["facade"])), "m")
    check_manifest(_document(_row(placement="wall_mounted", anchor="facade", surfaces=["facade"])), "m")


def test_a_legacy_manifest_is_not_checked() -> None:
    check_manifest({"assets": [{"id": "x"}]}, "m")
    check_manifest({"schema_version": LEGACY_VERSION, "assets": [{"id": "x"}]}, "m")
    check_manifest([], "m")


@pytest.mark.parametrize("field", SEMANTIC_FIELDS)
def test_a_missing_semantic_field_fails(field: str) -> None:
    row = _row()
    del row[field]
    with pytest.raises(LibraryError, match=rf"m/assets/0: '{field}' is a required property"):
        check_manifest(_document(row), "m")


@pytest.mark.parametrize("changes", [
    {"placement": "roof"},
    {"grouping": "pile"},
    {"condition": "new"},
    {"tags": ["Not A Tag"]},
    {"description": "short"},
])
def test_an_unknown_value_fails(changes: dict) -> None:
    with pytest.raises(LibraryError, match=r"^m/assets/0/"):
        check_manifest(_document(_row(**changes)), "m")


def test_a_missing_semantics_block_fails() -> None:
    document = _document()
    del document["semantics"]
    with pytest.raises(LibraryError, match="'semantics' is a required property"):
        check_manifest(document, "m")


@pytest.mark.parametrize("changes", [
    {"anchor": "facade"},
    {"placement": "wall_mounted", "surfaces": ["facade"]},
])
def test_anchor_and_placement_must_agree(changes: dict) -> None:
    with pytest.raises(LibraryError, match="crate: anchor .* does not match placement"):
        check_manifest(_document(_row(**changes)), "m")


def test_a_street_object_needs_the_carriageway() -> None:
    with pytest.raises(LibraryError, match="crate: placement street needs the carriageway surface"):
        check_manifest(_document(_row(surfaces=["sidewalk"])), "m")
    check_manifest(_document(_row(placement="kerb", surfaces=["sidewalk"])), "m")


def _write_library(root: Path, document: dict) -> None:
    root.mkdir()
    for row in document["assets"]:
        (root / row["file"]).write_bytes(build_glb(
            [Node(row["id"], (Primitive(box(-0.3, 0.3, -0.3, 0.3, 0.0, 0.6), "albedo"),), {})],
            {"albedo": Material((1, 1, 1, 1), 1)},
            repeat_images={"albedo": PNG},
        ))
    (root / "manifest.json").write_text(json.dumps(document), encoding="utf-8")


def test_the_loader_reads_semantics_and_refuses_kit_pieces(tmp_path: Path) -> None:
    document = _document(_row(), _row(id="roof", file="roof.glb", placement=KIT_ONLY))
    _write_library(tmp_path / "lib", document)
    (tmp_path / "lib" / "roof.glb").unlink()
    props, rejects = read_library(tmp_path / "lib")
    assert [reject.as_dict() for reject in rejects] == [{"asset_id": "roof", "reason": "kit_only"}]
    assert len(props) == 1
    spec = props[0].spec
    assert (spec.description, spec.placement, spec.grouping, spec.condition, spec.tags) == (
        "A test crate on the ground.", "street", "cluster", "worn", ("wood",),
    )


def test_the_loader_refuses_an_invalid_curated_manifest(tmp_path: Path) -> None:
    _write_library(tmp_path / "lib", _document(_row(grouping="pile")))
    with pytest.raises(LibraryError, match="manifest.json/assets/0/grouping"):
        read_library(tmp_path / "lib")


def test_a_legacy_row_has_no_semantics(tmp_path: Path) -> None:
    row = {key: value for key, value in _row().items() if key not in SEMANTIC_FIELDS}
    _write_library(tmp_path / "lib", {"assets": [row]})
    props, rejects = read_library(tmp_path / "lib")
    assert rejects == []
    spec = props[0].spec
    assert (spec.description, spec.placement, spec.grouping, spec.condition, spec.tags) == ("", "", "", "", ())


def _prop(spec: AssetSpec) -> Prop:
    mesh = box(-0.5, 0.5, -0.25, 0.25, 0.0, 1.0)
    return Prop(spec, mesh, np.zeros((mesh.vertex_count, 2), dtype=np.float32), PNG)


def test_the_prompt_sends_the_curated_hints_only_when_present() -> None:
    curated = AssetSpec(
        "crate", "waste", "debris", ("sidewalk",), "ground", "test",
        description="A crate.", placement="open_ground", grouping="cluster", condition="worn", tags=("wood", "box"),
    )
    legacy = AssetSpec("old", "waste", "debris", ("sidewalk",), "ground", "test")
    rows = {row["id"]: row for row in library_rows([_prop(curated), _prop(legacy)])}
    assert rows["crate"]["description"] == "A crate."
    assert rows["crate"]["placement"] == "open_ground"
    assert rows["crate"]["grouping"] == "cluster"
    assert rows["crate"]["condition"] == "worn"
    assert rows["crate"]["tags"] == ["wood", "box"]
    assert set(rows["old"]) == {"id", "role", "category", "anchor", "surfaces", "length_m", "width_m", "height_m"}
    assert PROMPT_VERSION == "0.3.0"
    assert "a span whose asset is not a run" in SYSTEM_PROMPT
    assert "a cover whose asset is not a grid" in SYSTEM_PROMPT


def test_rewriting_a_manifest_keeps_the_curated_fields() -> None:
    previous = _document(_row(), _row(id="gone"))
    previous["assets"].append("not a row")
    fresh = {key: value for key, value in _row(use="fresh").items() if key not in SEMANTIC_FIELDS}
    fresh["description"] = "stale text before use"
    fresh = {"description": fresh.pop("description"), **fresh}
    written = keep_semantics({"schema": "s", "schema_version": LEGACY_VERSION, "assets": [fresh]}, previous)
    assert written["schema_version"] == MANIFEST_VERSION
    assert written["semantics"] == SEMANTICS
    assert list(written) == ["schema", "schema_version", "semantics", "assets"]
    row = written["assets"][0]
    assert row["use"] == "fresh"
    assert row["description"] == "A test crate on the ground."
    keys = list(row)
    assert keys[keys.index("use") + 1:keys.index("use") + 6] == list(SEMANTIC_FIELDS)
    assert keys[0] == "id"


def test_an_uncurated_row_makes_the_manifest_legacy() -> None:
    bare = {key: value for key, value in _row(id="new").items() if key not in SEMANTIC_FIELDS}
    previous = _document(_row())
    written = keep_semantics({"schema": "s", "semantics": SEMANTICS, "assets": [_row(), bare]}, previous)
    assert written["schema_version"] == LEGACY_VERSION
    assert "semantics" not in written
    assert written["assets"][1] == bare
    assert keep_semantics({"assets": [_row()]}, None)["schema_version"] == LEGACY_VERSION == "0.1.0"
    unmarked = _document(_row())
    del unmarked["semantics"]
    assert keep_semantics({"assets": [_row()]}, unmarked)["schema_version"] == LEGACY_VERSION
    assert keep_semantics({"assets": [_row()]}, previous)["schema_version"] == MANIFEST_VERSION


def test_a_sketchfab_download_keeps_the_curated_fields(tmp_path: Path) -> None:
    from types import SimpleNamespace

    from onward_generator.overgrow.sketchfab_fetch import _append_manifest

    (tmp_path / "manifest.json").write_text(json.dumps(_document(_row())), encoding="utf-8")
    spec = {"id": "crate", "category": "waste", "role": "debris", "surfaces": ["sidewalk", "carriageway"],
            "anchor": "ground", "use": "again"}
    shape = SimpleNamespace(triangles_after=12, length_m=1.0, depth_m=1.0, height_m=1.0)
    _append_manifest(tmp_path, spec, {"url": "u", "author": "a", "license_url": "l"}, shape)
    written = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert written["schema_version"] == MANIFEST_VERSION
    [row] = written["assets"]
    assert row["use"] == "again"
    assert row["placement"] == "street"
    check_manifest(written, "m")
