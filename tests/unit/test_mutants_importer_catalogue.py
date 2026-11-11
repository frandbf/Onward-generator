"""What ``build_catalogue``, ``blank_albedo`` and ``unit_factor`` give a caller, case by case (ADR-035)."""

from __future__ import annotations

import datetime
import io
import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from onward_generator.assetstore import importer
from onward_generator.assetstore.classify import Decision, classify
from onward_generator.assetstore.importer import blank_albedo, build_catalogue, unit_factor
from onward_generator.assetstore.packages import Package
from onward_generator.generation.gltf import pack_glb, read_glb
from onward_generator.overgrow.library_catalog import LibraryError

GREY = "newmtl m\nKd 0.5 0.2 0.2\n"
WHITE = "newmtl m\nKd 1 1 1\n"


def _box(x: tuple[float, float], y: tuple[float, float], z: tuple[float, float]) -> str:
    """An OBJ box: x is the length, y the height and z the depth once the library loads it."""
    corners = "".join(f"v {a} {b} {c}\n" for a in x for b in y for c in z)
    return corners + "usemtl m\nf 1 2 4 3\nf 5 6 8 7\nf 1 2 6 5\n"


def _package(tmp_path: Path, name: str = "Junk Pack", category: str = "3D/Props/Exterior") -> Package:
    return Package(tmp_path / f"{name}.unitypackage", "Pub", category, name)


def _write(folder: Path, stem: str, obj: str | bytes, mtl: str | bytes = GREY) -> None:
    for suffix, text in (("obj", obj), ("mtl", mtl)):
        path = folder / f"{stem}.{suffix}"
        path.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))


def _report(folder: Path, items: list[dict[str, Any]]) -> None:
    (folder / "report.json").write_text(json.dumps({"items": items}), encoding="utf-8")


def _exported(stem: str, **extra: Any) -> dict[str, Any]:
    return {"file": f"{stem}.obj", "status": "exported", **extra}


def _manifest(assets: Path, catalogue: str) -> dict[str, Any]:
    return json.loads((assets / catalogue / "manifest.json").read_text(encoding="utf-8"))


# ---- one package with every kind of item -----------------------------------------------------------------------------


def _mixed_export(tmp_path: Path) -> Path:
    exported = tmp_path / "export" / "junk_pack"
    exported.mkdir(parents=True)
    _write(exported, "Bench_Wide", _box((0, 1), (0, 1), (0, 1.5)))
    _write(exported, "Broken_Crate", "v 0 0 0\n")
    _write(exported, "Broken_Barrel", "v 0 0 0\n")
    _write(exported, "White_Bench", _box((0, 1), (0, 1), (0, 0.5)), WHITE)
    _write(exported, "White_Cone", _box((0, 1), (0, 1), (0, 0.5)), WHITE)
    _write(exported, "Mystery_Thing", _box((0, 1), (0, 1), (0, 0.5)))
    _write(exported, "Odd_Blob", _box((0, 1), (0, 1), (0, 0.5)))
    # A byte that is not UTF-8 in a comment of each file: read with replacement, the model converts.
    _write(exported, "Bench_Square", b"# caf\xe9\n" + _box((0, 1), (0, 1), (0, 1.2)).encode(), b"# caf\xe9\n" + GREY.encode())
    _write(exported, "Bench_Long", _box((1, 3), (0, 1.23456), (0, 2)))
    _write(
        exported,
        "Bench_Textured",
        _box((0, 1), (0, 1), (0, 0.5)).replace("f 1 2 4 3", "vt 0 0\nvt 1 1\nf 1/1 2/2 4/2 3/1"),
        "newmtl m\nKd 1 1 1\nmap_Kd t.png\n",
    )
    Image.fromarray(np.array([[[200, 20, 20], [20, 200, 20]], [[20, 20, 200], [200, 200, 20]]], dtype=np.uint8)).save(exported / "t.png")
    _write(exported, "Chair_Deep", _box((0, 0.5), (0, 0.9), (0, 1.0)))
    _write(exported, "Barrel_Rusty", _box((0, 0.6), (0, 0.9), (0, 0.6)))
    _report(
        exported,
        [
            {"prefab": "Assets/P/Big.prefab", "status": "too_many_triangles: 12000"},
            {"status": "too_many_triangles: 9000"},
            {"prefab": "Assets/P/NoStatus.prefab"},
            _exported("Bench_Wide", prefab="Assets/P/Bench_Wide.prefab"),
            _exported("Bench_Wide", prefab="Assets/P/Bench_Wide 1.prefab"),
            _exported("Bench_Wide", prefab="Assets/P/Bench_Wide 2.prefab"),
            _exported("Broken_Crate"),
            _exported("Broken_Barrel"),
            _exported("White_Bench"),
            _exported("White_Cone"),
            _exported("Mystery_Thing"),
            _exported("Odd_Blob"),
            _exported("Bench_Square"),
            _exported("Bench_Long", prefab="Assets/P/Bench_Long.prefab"),
            _exported("Bench_Textured"),
            _exported("Chair_Deep"),
            _exported("Barrel_Rusty"),
        ],
    )
    return exported


ROW_KEYS = (
    "id",
    "file",
    "placement",
    "category",
    "role",
    "triangles",
    "length_m",
    "depth_m",
    "height_m",
    "collider",
    "license",
    "source_prefab",
)


def test_every_item_becomes_a_row_or_a_counted_reason(tmp_path: Path) -> None:
    package = _package(tmp_path)
    assets = tmp_path / "assets"
    result = build_catalogue(package, _mixed_export(tmp_path), assets)
    assert result.package is package and result.status == "ok" and result.rows == 6 and result.error == ""
    skipped = {"convert: ObjError": 2, "duplicate_name": 2, "too_many_triangles": 2, "unclassified": 2, "unknown": 1, "untextured_white": 2}
    assert dict(result.skipped) == skipped
    manifest = _manifest(assets, "asset_store_junk_pack")
    rows = {row["id"]: row for row in manifest["assets"]}
    assert list(rows) == [
        f"as_junk_pack_{s}" for s in ("Bench_Wide", "Bench_Square", "Bench_Long", "Bench_Textured", "Chair_Deep", "Barrel_Rusty")
    ]
    lic = "LicenseRef-Unity-Asset-Store-EULA"
    assert {k: rows["as_junk_pack_Bench_Wide"][k] for k in ROW_KEYS} == {
        "id": "as_junk_pack_Bench_Wide",
        "file": "Bench_Wide.glb",
        "placement": "kerb",
        "category": "barriers",
        "role": "bench",
        "triangles": 6,
        "length_m": 1.0,
        "depth_m": 1.5,
        "height_m": 1.0,
        "collider": "none",
        "license": lic,
        "source_prefab": "Assets/P/Bench_Wide.prefab",
    }
    assert {k: rows["as_junk_pack_Bench_Long"][k] for k in ROW_KEYS} == {
        "id": "as_junk_pack_Bench_Long",
        "file": "Bench_Long.glb",
        "placement": "kerb",
        "category": "barriers",
        "role": "bench",
        "triangles": 6,
        "length_m": 2.0,
        "depth_m": 2.0,
        "height_m": 1.235,
        "collider": "none",
        "license": lic,
        "source_prefab": "Assets/P/Bench_Long.prefab",
    }
    assert rows["as_junk_pack_Bench_Square"]["source_prefab"] == ""  # no prefab in the report
    assert rows["as_junk_pack_Bench_Square"]["depth_m"] == 1.2 and rows["as_junk_pack_Bench_Square"]["length_m"] == 1.0
    # A street row deeper than 1.2 times its length is turned a quarter; a square, a 1.2 one or an interior one is not.
    assert {i: r.get("yaw_deg") for i, r in rows.items()} == {
        "as_junk_pack_Bench_Wide": 90,
        "as_junk_pack_Bench_Square": None,
        "as_junk_pack_Bench_Long": None,
        "as_junk_pack_Bench_Textured": None,
        "as_junk_pack_Chair_Deep": None,
        "as_junk_pack_Barrel_Rusty": None,
    }
    assert rows["as_junk_pack_Chair_Deep"]["placement"] == "interior" and rows["as_junk_pack_Chair_Deep"]["depth_m"] == 1.0
    # The package category decides an ambiguous storage word: outdoor waste in an exterior package.
    assert rows["as_junk_pack_Barrel_Rusty"]["placement"] == "wall_foot" and "from Junk Pack" in rows["as_junk_pack_Barrel_Rusty"]["use"]
    # Each GLB is written under its own name, and a textured model keeps its texture.
    folder = assets / "asset_store_junk_pack"
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [f"{s}.glb" for s in ("Bench_Wide", "Bench_Square", "Bench_Long", "Bench_Textured", "Chair_Deep", "Barrel_Rusty")]
        + ["License.txt", "manifest.json"]
    )
    doc, _ = read_glb((folder / "Bench_Long.glb").read_bytes())
    assert doc["meshes"][0]["name"] == "Bench_Long"
    assert not blank_albedo((folder / "Bench_Textured.glb").read_bytes())
    head = {k: v for k, v in manifest.items() if k != "assets"}
    assert head == {
        "schema": "onward_generator.abandoned_prefabs",
        "schema_version": "0.2.0",
        "unit": "m",
        "collider": "none",
        "shader": "PP_Opaque_Simple_Lit albedo only",
        "source": "Unity Asset Store EULA (Extension Asset), local only: Pub / Junk Pack. Converted on this machine; "
        "never committed or redistributed outside a built map.",
        "semantics": {**importer.SEMANTICS, "reviewed_on": datetime.date.today().isoformat()},
        "package": {"publisher": "Pub", "category": "3D/Props/Exterior", "name": "Junk Pack"},
        "skipped": skipped,
        "unit_factor": 1.0,
    }
    assert list(manifest["skipped"]) == sorted(skipped)
    assert (folder / "License.txt").read_text(encoding="utf-8") == (
        "Junk Pack by Pub, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
        "Local copy for this user's maps only. Do not commit, share or publish these files.\n"
    )


def test_a_centimetre_package_is_scaled_a_hundred_times(tmp_path: Path) -> None:
    exported = tmp_path / "export"
    exported.mkdir()
    _write(exported, "Bench_Tiny", _box((0, 0.012), (0, 0.009), (0, 0.004)))
    _report(exported, [_exported("Bench_Tiny", size=[0.012, 0.004, 0.009])])
    assets = tmp_path / "assets"
    assert build_catalogue(_package(tmp_path), exported, assets).rows == 1
    manifest = _manifest(assets, "asset_store_junk_pack")
    row = manifest["assets"][0]
    assert manifest["unit_factor"] == 100.0
    assert (row["length_m"], row["depth_m"], row["height_m"]) == (1.2, 0.4, 0.9)


def test_ids_take_twenty_characters_of_the_package_and_stop_at_eighty(tmp_path: Path) -> None:
    exported = tmp_path / "export"
    exported.mkdir()
    stem = "Bench_" + "Long" * 16
    _write(exported, stem, _box((0, 1), (0, 1), (0, 0.5)))
    _report(exported, [_exported(stem)])
    assets = tmp_path / "assets"
    package = _package(tmp_path, "Abcdefghij Klmnopqrst Uvwxyz Pack")
    build_catalogue(package, exported, assets)
    row = _manifest(assets, package.catalogue)["assets"][0]
    assert row["id"] == f"as_abcdefghij_klmnopqrs_{stem}"[:80] and len(row["id"]) == 80 and row["file"] == f"{stem}.glb"


def test_nothing_placeable_is_an_empty_result_and_replaces_the_old_catalogue(tmp_path: Path) -> None:
    exported = tmp_path / "export"
    exported.mkdir()
    _write(exported, "Mystery_Thing", _box((0, 1), (0, 1), (0, 0.5)))
    _report(exported, [_exported("Mystery_Thing"), {"status": "no_mesh"}])
    assets = tmp_path / "assets"
    (assets / "asset_store_junk_pack").mkdir(parents=True)
    (assets / "asset_store_junk_pack" / "old.glb").write_bytes(b"old")
    result = build_catalogue(_package(tmp_path), exported, assets)
    assert (result.status, result.rows, dict(result.skipped)) == ("empty", 0, {"unclassified": 1, "no_mesh": 1})
    assert not (assets / "asset_store_junk_pack").exists()


def test_a_manifest_the_library_would_refuse_is_never_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def facade(*args: Any) -> Decision:
        decision = classify(*args)
        assert decision.row is not None
        return Decision({**decision.row, "anchor": "facade"}, decision.reason)

    monkeypatch.setattr(importer, "classify", facade)
    exported = tmp_path / "export"
    exported.mkdir()
    _write(exported, "Bench_A", _box((0, 1), (0, 1), (0, 0.5)))
    _report(exported, [_exported("Bench_A")])
    assets = tmp_path / "assets"
    with pytest.raises(LibraryError) as caught:
        build_catalogue(_package(tmp_path), exported, assets)
    assert str(caught.value).startswith("asset_store_junk_pack")
    assert "facade" in str(caught.value)
    assert not (assets / "asset_store_junk_pack" / "manifest.json").exists()


# ---- blank_albedo -----------------------------------------------------------------------------------------------------


def _png(pixels: np.ndarray, mode: str | None = None) -> bytes:
    out = io.BytesIO()
    image = Image.fromarray(pixels.astype(np.uint8))
    (image.convert(mode) if mode else image).save(out, format="PNG")
    return out.getvalue()


def _glb(png: bytes | None, *, offset: int | None = 8) -> bytes:
    """A GLB with one image (or none) in its buffer; ``offset`` None leaves ``byteOffset`` out (glTF's default 0)."""
    if png is None:
        return pack_glb({"asset": {"version": "2.0"}}, b"")
    pad = b"\x89PNG" * ((offset or 0) // 4)  # bytes that are not an image: reading from 0 would fail
    view: dict[str, Any] = {"buffer": 0, "byteLength": len(png)}
    if offset is not None:
        view["byteOffset"] = offset
    return pack_glb({"asset": {"version": "2.0"}, "bufferViews": [view], "images": [{"bufferView": 0, "mimeType": "image/png"}]}, pad + png)


def _uniform(rgb: tuple[int, int, int], size: int = 4) -> np.ndarray:
    return np.tile(np.array(rgb, dtype=np.uint8), (size, size, 1))


def test_a_model_without_images_is_blank() -> None:
    assert blank_albedo(_glb(None)) is True


def test_the_image_is_read_at_its_byte_offset_or_at_zero_without_one() -> None:
    white = _png(_uniform((250, 250, 250)))
    assert blank_albedo(_glb(white, offset=8)) is True
    assert blank_albedo(_glb(white, offset=None)) is True
    assert blank_albedo(_glb(_png(_uniform((20, 120, 20))), offset=None)) is False


def test_one_flat_colour_per_channel_is_blank_even_when_the_channels_differ() -> None:
    assert blank_albedo(_glb(_png(_uniform((250, 230, 240))))) is True


def test_a_grey_image_is_read_as_rgb() -> None:
    rows = np.tile(np.array([[240, 250, 230]], dtype=np.uint8), (3, 1))  # columns of one value, pixels that differ
    assert blank_albedo(_glb(_png(rows, "L"))) is False


def test_the_thresholds_are_a_spread_of_two_and_a_mean_of_225() -> None:
    two = np.array([[[240, 240, 240], [244, 244, 244]]], dtype=np.uint8)  # standard deviation exactly 2 in every channel
    assert blank_albedo(_glb(_png(two))) is False
    assert blank_albedo(_glb(_png(_uniform((225, 225, 225))))) is True
    assert blank_albedo(_glb(_png(_uniform((224, 224, 224))))) is False


# ---- unit_factor ------------------------------------------------------------------------------------------------------


def test_unit_factor_reads_only_exported_items_with_a_size() -> None:
    tiny = {"status": "exported", "size": [0.01, 0.01, 0.01]}
    assert unit_factor([{"status": "no_mesh", "size": [1.0, 1.0, 1.0]}, tiny]) == 100.0
    assert unit_factor([{"status": "exported"}, tiny]) == 100.0


def test_unit_factor_keeps_a_package_of_exactly_five_centimetres() -> None:
    assert unit_factor([{"status": "exported", "size": [0.05, 0.01, 0.01]}]) == 1.0
    assert unit_factor([{"status": "exported", "size": [0.049, 0.01, 0.01]}]) == 100.0
