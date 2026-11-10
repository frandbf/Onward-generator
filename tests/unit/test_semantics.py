"""The semantic block of every library row (overgrow/semantics.py, ADR-036)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from onward_generator.overgrow.semantics import HOME, KINDS, MOUNTS, SETTINGS, SHOPS, annotate, counts, describe, is_part, opaque, words_of

ESTIMATED = "name, catalogue, role, placement and size (overgrow/semantics.py)"


def _row(asset_id: str, size: tuple[float, float, float], placement: str = "interior", **extra: object) -> dict[str, object]:
    length, depth, height = size
    return {"id": asset_id, "file": f"{asset_id}.glb", "placement": placement, "role": "shelf", "category": "furniture",
            "length_m": length, "depth_m": depth, "height_m": height, **extra}


def _glb(materials: list[dict[str, object]]) -> bytes:
    body = json.dumps({"asset": {"version": "2.0"}, "materials": materials}).encode()
    body += b" " * (-len(body) % 4)
    blob = b"\0" * 4
    return (struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(body) + 8 + len(blob)) + struct.pack("<I4s", len(body), b"JSON") + body
            + struct.pack("<I4s", len(blob), b"BIN\0") + blob)


@pytest.mark.parametrize(("asset_id", "size", "kind", "mount", "settings"), [
    ("kenney_furniture_chair", (0.5, 0.5, 0.9), "chair", "seat", ["dining", "bar", "bakery", "office", "kitchen"]),
    # The model's own name names its room, which wins over the kind's rooms.
    ("kenney_furniture_bathroomCabinet", (0.6, 0.4, 0.8), "cabinet_low", "wall", ["bathroom"]),
    # A table lamp is a lamp, not a table; a tall floor lamp goes in a corner.
    ("kenney_furniture_lampSquareTable", (0.3, 0.3, 0.5), "lamp_small", "table_top", ["living", "bedroom", "office"]),
    ("kenney_furniture_lampSquareFloor", (0.4, 0.4, 1.6), "lamp_floor", "corner", ["living", "bedroom"]),
    ("kenney_food_croissant", (0.2, 0.1, 0.08), "decor_food", "counter_top", ["bakery", "bar", "grocery", "kitchen", "dining"]),
])
def test_a_row_is_described_from_its_name_and_size(asset_id, size, kind, mount, settings) -> None:
    block = describe(_row(asset_id, size), "kenney_furniture")
    assert block == {"kind": kind, "mount": mount, "settings": settings, "origin": "estimated", "basis": ESTIMATED}
    assert block["kind"] in KINDS and block["mount"] in MOUNTS and set(block["settings"]) <= set(SETTINGS)


def test_a_low_shelf_and_a_low_table_change_kind_by_height() -> None:
    assert describe(_row("qt_home_Bookshelf", (1.0, 0.4, 1.0)), "c")["kind"] == "shelf_low"
    assert describe(_row("qt_home_Bookshelf", (1.0, 0.4, 2.0)), "c")["kind"] == "shelf_tall"
    low = describe(_row("qt_home_Table", (1.0, 0.6, 0.45)), "c")
    assert (low["kind"], low["mount"], low["settings"]) == ("coffee_table", "center", ["living"])


def test_a_large_kind_too_small_to_be_one_is_a_part_and_never_placed() -> None:
    block = describe(_row("qt_home_Table", (0.3, 0.3, 0.3)), "c")
    assert block["mount"] == "none" and block.get("part") is True


def test_a_split_child_named_as_a_part_is_a_part() -> None:
    row = _row("as_kitchen_Cabinet_handle", (0.1, 0.02, 0.02), source_prefab="Kitchen.fbx#Cabinet_Handle")
    assert is_part(row, words_of("Cabinet_Handle"))
    assert describe(row, "asset_store_kitchen")["mount"] == "none"
    assert not is_part(_row("x", (1, 1, 1), source_prefab="Sofa.fbx"), words_of("handle"))  # no split, no part


def test_a_hung_model_is_never_placed_on_the_floor() -> None:
    block = describe(_row("qt_home_WallShelf", (0.8, 0.2, 0.3)), "c")
    assert block["mount"] == "none" and block["hung"] is True


def test_an_outdoor_row_gets_a_street_setting() -> None:
    block = describe(_row("qt_city_Bench", (1.5, 0.5, 0.8), placement="street"), "quaternius_city_props")
    assert block["kind"] == "bench" and block["mount"] == "ground" and block["settings"] == ["street"]


def test_a_reviewed_block_is_kept_as_it_is() -> None:
    reviewed = {"origin": "reviewed", "kind": "bed", "mount": "wall", "settings": ["bedroom"]}
    assert describe({"id": "qt_home_Table", "semantic": reviewed}, "c") == reviewed


def test_an_as_id_is_read_from_its_file_name_only() -> None:
    # Every row of a kitchen pack carries "kitchen" in its id; the model's own name is its file name.
    row = _row("as_kitchen_pack_Chair", (0.5, 0.5, 0.9)) | {"file": "Chair.glb"}
    assert describe(row, "asset_store_kitchen_pack")["kind"] == "chair"


def test_words_of_splits_camel_case_digits_and_separators() -> None:
    assert words_of("kitchenFridgeSmall_02") == {"kitchen", "fridge", "small", "kitchenfridge", "fridgesmall"}
    assert words_of("Shelves") == {"shelves", "shelv", "shelve"}


def test_opaque_reads_every_material_alpha_mode() -> None:
    assert opaque(_glb([{"name": "a"}, {"name": "b", "alphaMode": "OPAQUE"}]))
    assert not opaque(_glb([{"name": "a"}, {"name": "b", "alphaMode": "BLEND"}]))
    assert opaque(_glb([]))


def test_annotate_marks_a_transparent_model_unplaceable_and_keeps_other_rows(tmp_path: Path) -> None:
    (tmp_path / "glass.glb").write_bytes(_glb([{"alphaMode": "BLEND"}]))
    (tmp_path / "chair.glb").write_bytes(_glb([{}]))
    document = {"catalogue": "c", "assets": [_row("glass", (0.5, 0.5, 0.9)) | {"file": "glass.glb"},
                                            _row("kenney_furniture_chair", (0.5, 0.5, 0.9)) | {"file": "chair.glb"},
                                            "not a row"]}
    rows = annotate(document, "kenney_furniture", tmp_path)["assets"]
    assert rows[0]["semantic"]["mount"] == "none" and rows[0]["semantic"]["transparent"] is True
    assert rows[1]["semantic"]["kind"] == "chair" and "transparent" not in rows[1]["semantic"]
    assert rows[2] == "not a row"
    # Without a folder no GLB is opened.
    assert "transparent" not in annotate(document, "kenney_furniture")["assets"][0]["semantic"]


def test_counts_tally_kinds() -> None:
    rows = [{"semantic": {"kind": "chair"}}, {"semantic": {"kind": "chair"}}, {"semantic": {"kind": "bed"}}]
    assert list(counts([*rows, {}]).items()) == [("chair", 2), ("bed", 1), ("none", 1)]


def test_the_shop_and_home_settings_are_settings() -> None:
    assert set(SHOPS) <= set(SETTINGS) and set(HOME) <= set(SETTINGS)


def _tracked_manifests() -> list[Path]:
    from onward_generator.detail.config import REPO_ROOT

    # The local Asset Store catalogues are gitignored, one machine's own (ADR-035).
    return [m for m in sorted((REPO_ROOT / "assets").glob("*/manifest.json")) if not m.parent.name.startswith("asset_store_")]


def test_every_tracked_manifest_is_what_annotate_writes() -> None:
    """The committed blocks are the rules' output, row for row: ``tools/describe_library.py --check`` as a test. A rule
    change that moves a block must come with the rewritten manifests."""
    for manifest in _tracked_manifests():
        document = json.loads(manifest.read_text(encoding="utf-8"))
        assert annotate(document, manifest.parent.name, manifest.parent) == document, manifest.parent.name


def test_every_tracked_manifest_row_has_a_valid_semantic_block() -> None:
    for manifest in _tracked_manifests():
        for row in json.loads(manifest.read_text(encoding="utf-8")).get("assets") or []:
            block = row["semantic"]
            assert block["kind"] in KINDS and block["mount"] in MOUNTS, (manifest.parent.name, row["id"])
            assert set(block["settings"]) <= set(SETTINGS), (manifest.parent.name, row["id"])
            assert block["origin"] in ("estimated", "reviewed")
