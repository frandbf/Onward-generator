"""Opaque alpha cut-outs through the Asset Store converter (ADR-040): the threshold, the alpha and the back faces."""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.assetstore.classify import BUSH_WORDS, TREE_MIN_HEIGHT_M, TREE_WORDS, classify
from onward_generator.assetstore.objglb import MtlMaterial, ObjError, ObjGroup, obj_to_glb, parse_mtl, two_sided
from onward_generator.generation.gltf import read_glb

OBJ = """v 0 0 0
v 2 0 0
v 0 2 0
vt 0 0
vt 1 0
vt 0 1
vn 0 0 1
usemtl Leaves
f 1/1/1 2/2/1 3/3/1
usemtl Bark
f 1/1/1 3/3/1 2/2/1
"""
MTL = "newmtl Leaves\nKd 1 1 1\nmap_Kd leaves.png\nonward_cutoff 0.35\nnewmtl Bark\nKd 1 1 1\nmap_Kd bark.png\n"


def _textures(directory: Path) -> None:
    leaves = Image.new("RGBA", (4, 4), (30, 120, 40, 255))
    leaves.putpixel((0, 0), (30, 120, 40, 0))  # a hole between the leaves
    leaves.save(directory / "leaves.png")
    Image.new("RGBA", (4, 4), (90, 60, 30, 10)).save(directory / "bark.png")


def test_the_threshold_is_read_and_checked() -> None:
    assert parse_mtl(MTL)["Leaves"] == MtlMaterial((1.0, 1.0, 1.0), "leaves.png", cutoff=0.35)
    assert parse_mtl(MTL)["Bark"].cutoff is None
    for bad in ("0", "1", "1.5", "-0.2"):
        with pytest.raises(ObjError, match=f"^Leaves: onward_cutoff must lie between 0 and 1, got {bad}$"):
            parse_mtl(f"newmtl Leaves\nonward_cutoff {bad}\n")
    assert parse_mtl("newmtl Leaves\nonward_cutoff\n")["Leaves"].cutoff is None  # no value: not a cut-out
    assert parse_mtl("onward_cutoff 0.5\nnewmtl Leaves\n")["Leaves"].cutoff is None  # before any material


def test_two_sided_adds_each_triangle_turned_with_its_normals() -> None:
    group = ObjGroup(np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5], [6, 5, 5], [5, 6, 5]], dtype=np.float32),
                     np.array([[0, 0, 1]] * 3 + [[0, 1, 0]] * 3, dtype=np.float32),
                     np.array([[0, 0], [1, 0], [0, 1], [0.5, 0.5], [1, 1], [0, 0.5]], dtype=np.float32))
    both = two_sided(group)
    assert np.array_equal(both.positions[:6], group.positions)
    assert np.array_equal(both.positions[6:], group.positions[[0, 2, 1, 3, 5, 4]])
    assert np.array_equal(both.normals[6:], -group.normals[[0, 2, 1, 3, 5, 4]])
    assert np.array_equal(both.uvs[6:], group.uvs[[0, 2, 1, 3, 5, 4]])
    a, b, c = both.positions[6:9]
    assert float(np.cross(b - a, c - a)[2]) < 0  # it faces down; the first faced up


def test_a_cutout_material_is_an_alpha_mask_with_its_alpha_and_back_faces(tmp_path: Path) -> None:
    _textures(tmp_path)
    doc, blob = read_glb(obj_to_glb(OBJ, MTL, "holly", tmp_path))
    leaves, bark = doc["materials"]
    assert leaves == {"name": "Leaves", "alphaMode": "MASK", "alphaCutoff": 0.35, "doubleSided": False,
                      "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}, "metallicFactor": 0.0, "roughnessFactor": 1.0}}
    assert bark["alphaMode"] == "OPAQUE" and "alphaCutoff" not in bark
    counts = [doc["accessors"][p["indices"]]["count"] for p in doc["meshes"][0]["primitives"]]
    assert counts == [6, 3]  # the leaves twice, the bark once

    def image(index: int) -> Image.Image:
        view = doc["bufferViews"][doc["images"][index]["bufferView"]]
        return Image.open(io.BytesIO(blob[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]]))

    assert image(0).mode == "RGBA" and image(0).getpixel((0, 0))[3] == 0 and image(0).getpixel((1, 1))[3] == 255
    assert image(1).mode == "RGB"  # an opaque material drops the alpha it had


def test_a_cutoff_without_a_texture_stays_opaque(tmp_path: Path) -> None:
    doc, _ = read_glb(obj_to_glb(OBJ.replace("usemtl Bark\nf 1/1/1 3/3/1 2/2/1\n", ""), "newmtl Leaves\nKd 0.2 0.5 0.2\nonward_cutoff 0.5\n", "flat", tmp_path))
    assert doc["materials"][0]["alphaMode"] == "OPAQUE"
    assert doc["accessors"][doc["meshes"][0]["primitives"][0]["indices"]]["count"] == 3


def test_trees_and_bushes_are_vegetation() -> None:
    assert TREE_WORDS == frozenset({"tree"}) and BUSH_WORDS == frozenset({"bush", "shrub", "hedge"}) and TREE_MIN_HEIGHT_M == 2.0
    tree = classify("HornedHollyTree_1", (5.0, 5.0, 7.0), "Horned Holly Tree", "3D ModelsEnvironments")
    assert tree.reason == "tree"
    assert tree.row is not None and (tree.row["category"], tree.row["role"], tree.row["placement"], tree.row["grouping"]) == (
        "vegetation", "tree", "open_ground", "single")
    assert tree.row["surfaces"] == ["park", "plaza", "sidewalk", "courtyard", "yard", "empty_lot"]
    assert tree.row["use"] == "Tree (horned holly tree) from Horned Holly Tree, about 7.0 m tall."
    assert (tree.row["anchor"], tree.row["lane"]) == ("ground", "forbid")
    exact = classify("OakTree", (3.0, 3.0, 2.0), "Trees", "3D ModelsVegetation")
    assert exact.reason == "tree"
    for name, size in (("Simple_Bushes", (1.5, 1.5, 1.0)), ("OakTree", (1.0, 1.0, 1.99)), ("Hedge", (3.0, 0.8, 1.2))):
        bush = classify(name, size, "Horned Holly Tree", "3D ModelsEnvironments")
        assert bush.reason == "bush", name
        assert bush.row is not None and (bush.row["role"], bush.row["grouping"]) == ("bush", "cluster")
        assert bush.row["surfaces"] == ["courtyard", "yard", "plaza", "park"]
        assert (bush.row["category"], bush.row["placement"], bush.row["anchor"]) == ("vegetation", "open_ground", "ground")
    # The package's name lends "tree"; the model's own "bushes" decides.
    assert classify("Simple_Bushes_1", (5.7, 5.0, 2.4), "Horned Holly Tree", "3D ModelsEnvironments").reason == "bush"
    assert classify("Simple_Bushes", (1.5, 1.2, 1.0), "Horned Holly Tree", "x").row["use"] == "Bush (simple bushes) from Horned Holly Tree, about 1.5 m."  # type: ignore[index]
