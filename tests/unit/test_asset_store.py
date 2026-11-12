"""Local Asset Store catalogues (ADR-035): safe unpacking, OBJ conversion, classification, merge and isolation."""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.assetstore import local
from onward_generator.assetstore.classify import classify, words_of
from onward_generator.assetstore.importer import blank_albedo, build_catalogue, unit_factor
from onward_generator.assetstore.objglb import ObjError, obj_to_glb, parse_mtl, parse_obj
from onward_generator.assetstore.packages import Package, find_packages, slug, unpack
from onward_generator.detail.config import load_detail_config, with_local_furnishing
from onward_generator.generation.gltf import read_glb, scene_primitives
from onward_generator.overgrow.library import read_interior_catalogue
from onward_generator.overgrow.library_catalog import library_directories

REPO = Path(__file__).resolve().parents[2]
ON = {local.ENV: "1"}


# ---- packages -------------------------------------------------------------------------------------------------------

def _package(path: Path, entries: dict[str, tuple[str, bytes | None]]) -> Path:
    """A .unitypackage: per GUID a pathname, and an asset unless the value's bytes are None (a folder)."""
    with tarfile.open(path, "w:gz") as tar:
        for guid, (pathname, data) in entries.items():
            for name, payload in ((f"{guid}/pathname", pathname.encode()), (f"{guid}/asset", data), (f"{guid}/asset.meta", b"guid: x")):
                if payload is None:
                    continue
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return path


def test_unpack_writes_assets_and_meta_and_skips_code_traversal_and_editor(tmp_path: Path) -> None:
    pkg = _package(tmp_path / "p.unitypackage", {
        "a1": ("Assets/Pack/Models/chair.fbx", b"fbx"),
        "a2": ("Assets/Pack/Scripts/Evil.cs", b"class Evil {}"),
        "a3": ("Assets/Pack/Plugins/native.dll", b"MZ"),
        "a4": ("../outside.txt", b"x"),
        "a5": ("Assets/../../escape.fbx", b"x"),
        "a6": ("Assets/Editor/Tool.fbx", b"x"),
        "a7": ("Assets/Pack/Textures", None),
        "a8": ("ProjectSettings/TagManager.asset", b"x"),
    })
    project = tmp_path / "project"
    assert unpack(pkg, project) == 1
    assert (project / "Assets/Pack/Models/chair.fbx").read_bytes() == b"fbx"
    assert (project / "Assets/Pack/Models/chair.fbx.meta").is_file()
    assert (project / "Assets/Pack/Textures").is_dir()
    files = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert files == ["p.unitypackage", "project/Assets/Pack/Models/chair.fbx", "project/Assets/Pack/Models/chair.fbx.meta",
                     "project/Assets/Pack/Textures.meta"]


def test_find_packages_reads_publisher_and_category_and_a_missing_cache_is_empty(tmp_path: Path) -> None:
    assert find_packages(tmp_path / "none") == []
    for rel in ("Pub B/3D ModelsPropsInterior/Low Poly Storage Pack.unitypackage", "Pub A/3D ModelsVehiclesLand/Mini Truck.unitypackage"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    found = find_packages(tmp_path)
    assert [(p.id, p.publisher, p.category, p.catalogue) for p in found] == [
        ("low_poly_storage_pack", "Pub B", "3D ModelsPropsInterior", "asset_store_low_poly_storage_pack"),
        ("mini_truck", "Pub A", "3D ModelsVehiclesLand", "asset_store_mini_truck")]
    assert slug("  ---  ") == "package" and slug("A" * 80) == "a" * 48


# ---- OBJ to GLB -------------------------------------------------------------------------------------------------------

OBJ = """mtllib m.mtl
v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
vt 0 0
vt 1 0
vt 1 1
vt 0 1
vn 0 0 1
usemtl Tex
f 1/1/1 2/2/1 3/3/1 4/4/1
usemtl Flat
f 1 2 3
"""
MTL = "newmtl Tex\nKd 1 1 1\nmap_Kd -s 2 2 1 -o 0.5 0 0 albedo.png\nnewmtl Flat\nKd 0.8 0.8 0.8\n"


def test_parse_mtl_reads_texture_options_and_parse_obj_fans_and_keeps_uvs() -> None:
    materials = parse_mtl(MTL)
    assert materials["Tex"].texture == "albedo.png" and materials["Tex"].scale == (2.0, 2.0) and materials["Tex"].offset == (0.5, 0.0)
    assert materials["Flat"].texture is None and materials["Flat"].kd == (0.8, 0.8, 0.8)
    groups = parse_obj(OBJ)
    assert len(groups["Tex"].positions) == 6 and len(groups["Flat"].positions) == 3
    assert groups["Tex"].uvs[:3].tolist() == [[0, 0], [1, 0], [1, 1]]
    assert np.allclose(groups["Flat"].normals, [[0, 0, 1]] * 3)
    assert groups["Flat"].uvs.tolist() == [[0.5, 0.5]] * 3


def test_obj_to_glb_flips_v_applies_the_map_options_scales_and_embeds_the_texture(tmp_path: Path) -> None:
    Image.new("RGB", (8, 4), (200, 100, 50)).save(tmp_path / "albedo.png")
    data = obj_to_glb(OBJ, MTL, "quad", tmp_path, factor=2.0)
    doc, _ = read_glb(data)
    assert [m["alphaMode"] for m in doc["materials"]] == ["OPAQUE", "OPAQUE"] and len(doc["images"]) == 2
    vertices = np.concatenate([p.mesh.vertices for p in scene_primitives(data)])
    assert np.allclose(vertices.max(axis=0) - vertices.min(axis=0), [2.0, 0.0, 2.0]) or np.allclose(vertices.max(axis=0) - vertices.min(axis=0), [2.0, 2.0, 0.0])
    tex = next(p for p in scene_primitives(data) if p.mesh.triangle_count == 2)
    assert tex is not None


def test_obj_errors_name_what_is_wrong(tmp_path: Path) -> None:
    with pytest.raises(ObjError, match="missing texture albedo.png"):
        obj_to_glb(OBJ, MTL, "quad", tmp_path)
    with pytest.raises(ObjError, match="out of range"):
        parse_obj("v 0 0 0\nf 1 2 3\n")
    with pytest.raises(ObjError, match="three corners"):
        parse_obj("v 0 0 0\nv 1 0 0\nf 1 2\n")
    with pytest.raises(ObjError, match="no faces"):
        obj_to_glb("v 0 0 0\n", "", "empty")


# ---- classification ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("name", "size", "package", "category", "placement", "reason"), [
    ("SM_Wall_Corner_01", (2.0, 0.2, 3.0), "Apartment Kit", "3D ModelsEnvironments", None, "kit_piece"),
    ("Police_Car", (4.6, 1.9, 1.5), "Police Car", "3D ModelsVehiclesLand", "street", "vehicle"),
    ("Toy_Car", (0.3, 0.1, 0.1), "Toys", "3D ModelsProps", None, "vehicle_too_small"),
    ("SA_TrafficCone_01", (0.5, 0.5, 0.9), "Street Assets", "3D ModelsPropsExterior", "kerb", "street"),
    ("Barrel_Rusty", (0.6, 0.6, 0.9), "Barrels", "3D ModelsEnvironmentsIndustrial", "wall_foot", "storage_outdoor"),
    ("Kitchen_Cabinet_Wall", (0.8, 0.5, 0.9), "Kitchen Pack", "3D ModelsPropsInterior", "interior", "interior"),
    ("Bread_Baguette", (0.5, 0.07, 0.07), "CozyShops Bakery", "3D ModelsPropsFood", "interior", "interior"),
    ("Thingamajig", (1.0, 1.0, 1.0), "Misc", "3D ModelsProps", None, "unclassified"),
    ("Barrels_1", (0.6, 0.6, 0.9), "Barrels", "3D ModelsEnvironmentsIndustrial", "wall_foot", "storage_outdoor"),
    ("Chair", (0.5, 0.5, 20.0), "Furniture", "3D ModelsPropsFurniture", None, "size"),
])
def test_classify(name: str, size: tuple, package: str, category: str, placement: str | None, reason: str) -> None:
    decision = classify(name, size, package, category)
    assert decision.reason == reason
    assert (decision.row or {}).get("placement") == placement


def test_furnish_hints_name_the_slot_the_sets_and_the_floors() -> None:
    shelf = classify("Shelf_Wood", (1.2, 0.4, 1.8), "Coffee Shop Props", "3D ModelsProps", ).row
    assert shelf is not None and shelf["furnish"] == {"slot": "wall", "shop_sets": ["bar", "generic"], "floors": True}
    counter = classify("Cashier_Counter", (1.6, 0.7, 1.0), "Lite Grocery Store", "3D ModelsEnvironments").row
    assert counter is not None and counter["furnish"] == {"slot": "counter", "shop_sets": ["generic"], "floors": False}
    deep = classify("Wardrobe_Big", (1.8, 0.95, 2.0), "Bedroom", "3D ModelsPropsFurniture").row
    assert deep is not None and "furnish" not in deep
    assert words_of("SM_KitchenCabinet02_Blue") == ["sm", "kitchen", "cabinet", "blue"]


def test_a_blank_white_model_is_refused_and_a_coloured_one_kept(tmp_path: Path) -> None:
    quad = "v 0 0 0\nv 1 0 0\nv 1 1 0\nusemtl m\nf 1 2 3\n"
    assert blank_albedo(obj_to_glb(quad, "newmtl m\nKd 1 1 1\n", "white"))
    assert not blank_albedo(obj_to_glb(quad, "newmtl m\nKd 0.8 0.1 0.1\n", "red"))
    Image.fromarray(np.tile(np.array([[0, 255]], dtype=np.uint8), (8, 4)).astype(np.uint8)).convert("RGB").save(tmp_path / "t.png")
    assert not blank_albedo(obj_to_glb("v 0 0 0\nv 1 0 0\nv 1 1 0\nvt 0 0\nvt 1 0\nvt 1 1\nusemtl t\nf 1/1 2/2 3/3\n",
                                       "newmtl t\nKd 1 1 1\nmap_Kd t.png\n", "textured", tmp_path))


def test_unit_factor_fixes_a_centimetre_package_only() -> None:
    tiny = [{"status": "exported", "size": [0.003, 0.006, 0.003]}, {"status": "exported", "size": [0.01, 0.008, 0.005]}]
    assert unit_factor(tiny) == 100.0
    assert unit_factor([*tiny, {"status": "exported", "size": [0.4, 0.2, 0.9]}]) == 1.0
    assert unit_factor([{"status": "no_mesh"}]) == 1.0


# ---- catalogue, merge and isolation -----------------------------------------------------------------------------------

def _export(tmp_path: Path) -> tuple[Package, Path]:
    exported = tmp_path / "export" / "shop_props"
    exported.mkdir(parents=True)
    quad = "v -0.5 0 -0.2\nv 0.5 0 -0.2\nv 0.5 1.6 -0.2\nv -0.5 1.6 -0.2\nv -0.5 0 0.2\nv 0.5 0 0.2\nusemtl m\nf 1 2 3 4\nf 5 6 2 1\n"
    for stem in ("Shelf_Tall", "Mystery_Thing", "SM_Wall_Plain"):
        (exported / f"{stem}.obj").write_text(quad, encoding="utf-8")
        (exported / f"{stem}.mtl").write_text("newmtl m\nKd 0.5 0.5 0.5\n", encoding="utf-8")
    items = [{"prefab": f"Assets/P/{s}.prefab", "file": f"{s}.obj", "status": "exported", "size": [1, 0.4, 1.6]}
             for s in ("Shelf_Tall", "Mystery_Thing", "SM_Wall_Plain")]
    items.append({"prefab": "Assets/P/Big.prefab", "status": "too_many_triangles"})
    (exported / "report.json").write_text(json.dumps({"id": "shop_props", "items": items}), encoding="utf-8")
    cache = tmp_path / "cache" / "Pub" / "3D ModelsPropsInterior"
    cache.mkdir(parents=True)
    (cache / "Shop Props.unitypackage").write_bytes(b"x")
    return find_packages(tmp_path / "cache")[0], exported


def test_build_catalogue_writes_the_rows_it_can_place_and_counts_the_rest(tmp_path: Path) -> None:
    package, exported = _export(tmp_path)
    assets = tmp_path / "assets"
    result = build_catalogue(package, exported, assets)
    assert result.status == "ok" and result.rows == 1
    assert dict(result.skipped) == {"kit_piece": 1, "too_many_triangles": 1, "unclassified": 1}
    manifest = json.loads((assets / "asset_store_shop_props" / "manifest.json").read_text(encoding="utf-8"))
    row = manifest["assets"][0]
    assert row["id"] == "as_shop_props_Shelf_Tall" and row["placement"] == "interior" and row["furnish"]["slot"] == "wall"
    assert row["license"] == local.LICENCE_SPDX and local.LICENCE_NAME in manifest["source"] and "Do not commit" in (
        assets / "asset_store_shop_props" / "License.txt").read_text(encoding="utf-8")
    assert 1.5 < row["height_m"] < 1.7 and row["depth_m"] <= 0.8


def test_local_rows_join_furnishing_only_when_enabled_and_never_from_a_shared_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package, exported = _export(tmp_path)
    assets = tmp_path / "assets"
    build_catalogue(package, exported, assets)
    cfg = load_detail_config()
    monkeypatch.setenv(local.ENV, "0")
    assert with_local_furnishing(cfg, assets) is cfg
    assert [p.name for p in library_directories(assets)] == []
    monkeypatch.setenv(local.ENV, "1")
    merged = with_local_furnishing(cfg, assets)
    assert "as_shop_props_Shelf_Tall" in {e.id for e in merged.interiors.shops.furnish.sets["generic"].wall}
    assert "as_shop_props_Shelf_Tall" in {e.id for e in merged.interiors.furnish.sets["generic"].wall}
    assert "asset_store_shop_props" in merged.interiors.shops.furnish.catalogues
    assert [p.name for p in library_directories(assets)] == ["asset_store_shop_props"]
    props, rejects = read_interior_catalogue(assets, ["asset_store_shop_props"])
    assert list(props) == ["as_shop_props_Shelf_Tall"] and rejects == []
    # Each row's semantic kind reaches the size cap of small table things (interior_size).
    manifest_path = assets / "asset_store_shop_props" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["assets"][0]["semantic"] = {**(manifest["assets"][0].get("semantic") or {}), "kind": "decor_tableware"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    shrunk, _ = read_interior_catalogue(assets, ["asset_store_shop_props"])
    piece = shrunk["as_shop_props_Shelf_Tall"].prop
    assert max(np.ptp(piece.mesh.vertices, axis=0)) == pytest.approx(0.3) and piece.fit_scale < 1.0
    # The same EULA manifest in a folder that is not a local catalogue is refused whole.
    (assets / "asset_store_shop_props").rename(assets / "shared_props")
    props, rejects = read_interior_catalogue(assets, ["shared_props"])
    assert props == {} and [r.reason for r in rejects] == ["licence"]


def test_local_rows_per_slot_are_capped_lightest_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = load_detail_config()  # ONWARD_LOCAL_CATALOGUES=0 here: this machine's own catalogues stay out
    monkeypatch.setenv(local.ENV, "1")
    folder = tmp_path / "asset_store_many"
    folder.mkdir(parents=True)
    rows = [{"id": f"as_many_shelf_{k}", "file": "x.glb", "placement": "interior", "triangles": 1000 - k, "length_m": 1.0,
             "depth_m": 0.4, "height_m": 1.5, "furnish": {"slot": "wall", "shop_sets": ["generic"], "floors": False}} for k in range(7)]
    (folder / "manifest.json").write_text(json.dumps({"source": f"{local.LICENCE_NAME} local", "assets": rows}), encoding="utf-8")
    merged = with_local_furnishing(base, tmp_path)
    ids = [e.id for e in merged.interiors.shops.furnish.sets["generic"].wall if e.id.startswith("as_")]
    assert ids == ["as_many_shelf_6", "as_many_shelf_5", "as_many_shelf_4", "as_many_shelf_3"][:local.ROWS_PER_SLOT] and local.ROWS_PER_SLOT == 4
    assert merged.interiors.furnish == base.interiors.furnish  # floors: False


def test_no_tracked_file_carries_the_asset_store_licence_or_a_local_catalogue() -> None:
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=REPO, capture_output=True, text=True, check=False)
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != REPO.resolve():
        pytest.skip("REPO is not the root of a git checkout (a copy such as the mutation sandbox)")
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    names = tracked.stdout.splitlines()
    assert not [n for n in names if n.startswith("assets/asset_store_")]
    for name in names:
        if name.startswith("assets/") and name.endswith("manifest.json"):
            assert local.LICENCE_NAME not in (REPO / name).read_text(encoding="utf-8"), name
    assert "assets/asset_store_*/" in (REPO / ".gitignore").read_text(encoding="utf-8")
