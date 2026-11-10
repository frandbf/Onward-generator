"""``overgrow place`` builds the shop furniture ``detail`` planned (ADR-031), on the street block around MURCIA_CENTER."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from onward_generator.detail.__main__ import main as detail_main
from onward_generator.detail.config import FURNISH_CATALOGUES, REPO_ROOT
from onward_generator.generation.gltf import nodes_from_glb, read_glb
from onward_generator.overgrow.atlas import ATLAS_PREFIX, PROP_ATLAS_EXTRA
from onward_generator.overgrow.fit import check_scene
from onward_generator.overgrow.place import OvergrowError, overgrow
from tests.integration.test_shop_interiors import DOC, SHOP

ASSETS = REPO_ROOT / "assets"


def _library(root: Path, *names: str) -> Path:
    root.mkdir(parents=True)
    for name in names:
        (root / name).symlink_to(ASSETS / name, target_is_directory=True)
    return root


def _detail_out(inputs: tuple[Path, Path, Path], tmp: Path, enabled: bool) -> Path:
    scene, heights, base = inputs
    doc = copy.deepcopy(DOC)
    doc["interiors"]["shops"]["furnish"]["enabled"] = enabled
    config = tmp / f"detail_{enabled}.json"
    config.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp / f"detail_{enabled}"
    assert detail_main(["--scene", str(scene), "--heights", str(heights), "--base", str(base), "--config", str(config),
                        "--no-preview", "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def bases(detail_inputs, tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    tmp = tmp_path_factory.mktemp("furnish")
    return {"on": _detail_out(detail_inputs, tmp, True), "off": _detail_out(detail_inputs, tmp, False),
            "library": _library(tmp / "library", *FURNISH_CATALOGUES)}


@pytest.fixture(scope="module")
def placed(detail_inputs, bases, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict]:
    out = tmp_path_factory.mktemp("placed") / "out"
    report = overgrow(bases["on"], detail_inputs[0], out, library=bases["library"])
    return out, report


def _pieces(base: Path) -> list[dict]:
    manifest = json.loads((base / "scene_detail.json").read_text(encoding="utf-8"))
    interior = next(o for o in manifest["objects"] if o["id"] == f"interior/{SHOP}")
    return interior["rooms"][0]["furnishing"]["pieces"]


def test_overgrow_builds_every_planned_piece_with_an_albedo_prop_material(bases, placed) -> None:
    out, report = placed
    pieces = _pieces(bases["on"])
    nodes, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    built = {n.name: n for n in nodes if n.name.startswith("furnish/")}
    assert sorted(built) == sorted(p["id"] for p in pieces)
    for piece in pieces:
        node = built[piece["id"]]
        assert node.extras["type"] == "furnishing" and node.extras["attribute_origin"] == "generated"
        assert node.extras["asset_id"] == piece["asset_id"] and node.extras["licence"] == "CC0-1.0"
        # Its own prop_<id> material, or the shared prop atlas that holds it (overgrow/atlas.py).
        assert node.primitives and all(p.material.startswith((f"prop_{piece['asset_id']}", ATLAS_PREFIX)) for p in node.primitives)
        low = min(float(v[2]) for p in node.world_primitives() for v in p.mesh.vertices)
        assert low == pytest.approx(piece["position_m"][2], abs=1e-4)
    doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    materials = {m["name"]: m for m in doc["materials"]}
    furniture = {p.material for n in built.values() for p in n.primitives}
    for name in furniture:
        pbr = materials[name]["pbrMetallicRoughness"]
        assert "baseColorTexture" in pbr and pbr["baseColorFactor"] == [1, 1, 1, 1] and pbr["roughnessFactor"] == 0.9
    meshes = [doc["meshes"][n["mesh"]] for n in doc["nodes"] if n.get("name", "").startswith("furnish/")]
    assert all("TEXCOORD_0" in prim["attributes"] for mesh in meshes for prim in mesh["primitives"])
    cells = doc.get("extras", {}).get(PROP_ATLAS_EXTRA, {})
    own = sorted(report["furnishing"]["materials"])
    assert all(name in cells or name in furniture for name in own)  # each model's material: kept, or in the atlas
    assert {cells[name][0] for name in own if name in cells} | {name for name in own if name not in cells} == furniture
    assert report["furnishing"] == {"pieces": len(pieces), "triangles": sum(p["triangles"] for p in pieces),
                                    "colliders": sum(p["solid"] for p in pieces), "materials": own}


def test_only_solid_pieces_get_a_collider_and_only_in_the_collision_glb(bases, placed) -> None:
    out, _ = placed
    pieces = _pieces(bases["on"])
    collision, _ = nodes_from_glb((out / "scene_collision.glb").read_bytes())
    boxes = sorted(n.name for n in collision if n.name.startswith("furnish/collider/"))
    assert boxes == sorted(p["id"].replace("furnish/", "furnish/collider/", 1) for p in pieces if p["solid"])
    assert any(not p["solid"] for p in pieces)  # the decor on the counter stays visual
    visual, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    assert not any(n.name.startswith("furnish/collider/") or "collider" in {p.material for p in n.primitives} for n in visual)


def test_overgrow_fit_passes_with_no_finding_on_the_furniture(detail_inputs, bases, placed, tmp_path: Path) -> None:
    out, _ = placed
    checked, problems = check_scene(out, detail_inputs[0])
    bare = overgrow(bases["off"], detail_inputs[0], tmp_path / "bare", library=bases["library"])
    assert bare is not None
    checked_off, problems_off = check_scene(tmp_path / "bare", detail_inputs[0])
    assert problems == problems_off and checked == checked_off
    assert not any("furnish/" in p for p in problems)


def test_with_the_option_off_overgrow_writes_the_same_files(detail_inputs, bases, tmp_path: Path) -> None:
    with_shop_catalogues = overgrow(bases["off"], detail_inputs[0], tmp_path / "a", library=bases["library"])
    without = overgrow(bases["off"], detail_inputs[0], tmp_path / "b", library=_library(tmp_path / "empty"))
    assert "furnishing" not in with_shop_catalogues and "furnishing" not in without
    for name in ("scene_visual.glb", "scene_collision.glb"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_a_tampered_glb_or_a_missing_catalogue_stops_overgrow(detail_inputs, bases, tmp_path: Path) -> None:
    piece = _pieces(bases["on"])[0]
    with pytest.raises(OvergrowError, match=f"interior furniture {piece['asset_id']}: not installed"):
        overgrow(bases["on"], detail_inputs[0], tmp_path / "missing", library=_library(tmp_path / "lib_missing"))
    tampered = tmp_path / "lib_tampered"
    shutil.copytree(bases["library"], tampered, symlinks=False)
    manifest = json.loads((tampered / piece["catalogue"] / "manifest.json").read_text(encoding="utf-8"))
    row = next(r for r in manifest["assets"] if r["id"] == piece["asset_id"])
    glb = tampered / piece["catalogue"] / row["file"]
    data = bytearray(glb.read_bytes())
    at = data.index(b'"name":"') + len(b'"name":"')  # one letter of a name in the JSON chunk: same length, still a GLB
    data[at] = ord("Z") if data[at] != ord("Z") else ord("Y")
    glb.write_bytes(bytes(data))
    assert hashlib.sha256(glb.read_bytes()).hexdigest() != piece["glb_sha256"]
    with pytest.raises(OvergrowError, match=f"interior furniture {piece['asset_id']}: the GLB is not the one detail planned"):
        overgrow(bases["on"], detail_inputs[0], tmp_path / "tampered", library=tampered)
    with pytest.raises(OvergrowError, match="needs --library"):
        overgrow(bases["on"], detail_inputs[0], tmp_path / "nolib")


def test_a_selection_of_another_prop_keeps_the_furniture(detail_inputs, bases, tmp_path: Path) -> None:
    library = _library(tmp_path / "lib", *FURNISH_CATALOGUES, "kenney")
    street = json.loads((ASSETS / "kenney" / "manifest.json").read_text(encoding="utf-8"))["assets"]
    chosen = next(r["id"] for r in street if r.get("placement") not in ("kit_only", "interior") and not r.get("refused"))
    selection = tmp_path / "prop_selection.json"
    selection.write_text(json.dumps({"ids": [chosen]}), encoding="utf-8")
    report = overgrow(bases["on"], detail_inputs[0], tmp_path / "out", library=library, selection=selection)
    assert report["furnishing"]["pieces"] == len(_pieces(bases["on"]))


@pytest.fixture(scope="module")
def floors(detail_inputs, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """``detail`` with ``interiors.furnish`` on (ADR-034) and the shop block off, then ``overgrow place``."""
    tmp = tmp_path_factory.mktemp("floors")
    scene, heights, base = detail_inputs
    doc = copy.deepcopy(DOC)
    doc["interiors"]["furnish"]["enabled"] = True
    config = tmp / "detail_floors.json"
    config.write_text(json.dumps(doc), encoding="utf-8")
    out = tmp / "detail"
    assert detail_main(["--scene", str(scene), "--heights", str(heights), "--base", str(base), "--config", str(config),
                        "--no-preview", "--out", str(out)]) == 0
    overgrow(out, scene, tmp / "placed", library=_library(tmp / "library", *FURNISH_CATALOGUES))
    return out, tmp / "placed"


def test_upper_floor_pieces_built_at_their_z_with_one_box_collider_each_solid(floors) -> None:
    detail, placed = floors
    manifest = json.loads((detail / "scene_detail.json").read_text(encoding="utf-8"))
    pieces = {p["id"]: (room["floor_z_m"], p) for o in manifest["objects"] if o.get("type") == "interior" and o["kind"] == "residential"
              for room in o["rooms"] for p in (room.get("furnishing") or {}).get("pieces", [])}
    assert pieces and any(z > 0 for z, _ in pieces.values())
    visual, _ = nodes_from_glb((placed / "scene_visual.glb").read_bytes())
    collision, _ = nodes_from_glb((placed / "scene_collision.glb").read_bytes())
    built = {n.name: n for n in visual if n.name.startswith("furnish/")}
    boxes = {n.name: n for n in collision if n.name.startswith("furnish/collider/")}
    assert sorted(built) == sorted(pieces)
    assert sorted(boxes) == sorted(i.replace("furnish/", "furnish/collider/", 1) for i, (_, p) in pieces.items() if p["solid"])
    assert not any(n.name.startswith("furnish/") for n in collision if not n.name.startswith("furnish/collider/"))
    for piece_id, (floor_z, piece) in pieces.items():
        node = built[piece_id]
        assert node.matrix[2, 3] == pytest.approx(floor_z) and node.extras["slot"] == "wall"
        low = min(float(v[2]) for p in node.world_primitives() for v in p.mesh.vertices)
        assert low == pytest.approx(floor_z, abs=1e-4)
        if piece["solid"]:
            collider = boxes[piece_id.replace("furnish/", "furnish/collider/", 1)]
            meshes = [p.mesh for p in collider.world_primitives()]
            assert sum(m.triangle_count for m in meshes) == 12
            assert min(float(v[2]) for m in meshes for v in m.vertices) == pytest.approx(floor_z, abs=1e-4)
