"""The triangle audit of 10 November 2026: flat impostors, decorative vegetation caps, one band for the background rings."""

from __future__ import annotations

import io
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from onward_generator.generation.mesh import Mesh, box
from onward_generator.materials.trim import Bands
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.constants import DECOR_MAX_TRIS, FLAT_PROP_M, IMPOSTOR_MIN_TRIS
from onward_generator.overgrow.impostor import IMPOSTOR_SIDES, flat_impostor, outline
from onward_generator.overgrow.library import Prop, is_decorative, level_of_detail


def _png(colour: tuple[int, int, int], size: int = 8) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (size, size), colour).save(out, format="PNG")
    return out.getvalue()


def _disc(sides: int = 200, radius: float = 0.35, height: float = 0.05) -> Mesh:
    angles = np.linspace(0, 2 * np.pi, sides, endpoint=False)
    ring = np.column_stack([radius * np.cos(angles), radius * np.sin(angles)])
    vertices = np.vstack([[0, 0, height], np.column_stack([ring, np.full(sides, height)])])
    triangles = np.array([(0, 1 + k, 1 + (k + 1) % sides) for k in range(sides)])
    return Mesh(vertices, triangles)


def _prop(mesh: Mesh, spec: AssetSpec, albedo: bytes) -> Prop:
    uvs = np.column_stack([mesh.vertices[:, 0] + 0.5, mesh.vertices[:, 1] + 0.5]).astype(np.float32)
    return Prop(spec, mesh, uvs, albedo)


COVER = AssetSpec("cover", "barriers", "manhole", ("sidewalk",), "flat", "a cover")
FERN = AssetSpec("fern", "vegetation", "plant", ("park",), "ground", "a fern")
TREE = AssetSpec("tree", "vegetation", "tree", ("park",), "ground", "a tree")


def test_a_flat_prop_becomes_a_textured_polygon_with_a_rim_on_the_ground() -> None:
    prop = _prop(_disc(), COVER, _png((200, 40, 10)))
    flat = flat_impostor(prop)
    assert flat.triangles == 3 * IMPOSTOR_SIDES and flat.source_triangles == prop.triangles == 200
    z = flat.mesh.vertices[:, 2]
    assert z.min() == 0.0 and z.max() == pytest.approx(0.05)
    top = Image.open(io.BytesIO(flat.albedo)).convert("RGB")
    assert top.getpixel((top.width // 2, top.height // 2)) == (200, 40, 10)
    assert 0.0 <= flat.uvs.min() and flat.uvs.max() <= 1.0
    normals = np.cross(*(flat.mesh.vertices[flat.mesh.triangles[:IMPOSTOR_SIDES, i]] - flat.mesh.vertices[flat.mesh.triangles[:IMPOSTOR_SIDES, 0]] for i in (1, 2)))
    assert (normals[:, 2] > 0).all()  # the top faces up


def test_the_outline_is_convex_counter_clockwise_and_capped() -> None:
    angles = np.linspace(0, 2 * np.pi, 300, endpoint=False)
    shape = outline(np.column_stack([np.cos(angles), np.sin(angles)]))
    assert len(shape.exterior.coords) - 1 <= IMPOSTOR_SIDES and shape.exterior.is_ccw
    with pytest.raises(ValueError, match="area"):
        outline(np.array([[0, 0], [1, 1], [2, 2]]))


def test_level_of_detail_uses_the_impostor_only_for_flat_props_above_its_floor() -> None:
    cover = _prop(_disc(), COVER, _png((90, 90, 90)))
    assert level_of_detail(cover).triangles == 3 * IMPOSTOR_SIDES
    small = _prop(_disc(sides=IMPOSTOR_MIN_TRIS), COVER, _png((90, 90, 90)))
    assert level_of_detail(small) is small  # 64 triangles: already cheap
    crate = AssetSpec("crate", "waste", "crate", ("sidewalk",), "ground", "a crate")  # no street fit rescales a crate
    tall = _prop(_subdivide(box(0, 0.6, 0, 0.6, 0, FLAT_PROP_M + 0.3), 2), crate, _png((90, 90, 90)))
    assert level_of_detail(tall).triangles != 3 * IMPOSTOR_SIDES


def test_decorative_vegetation_is_capped_and_trees_and_props_are_not() -> None:
    assert is_decorative(FERN) and not is_decorative(TREE) and not is_decorative(COVER)
    bush = box(0, 1, 0, 1, 0, 1)
    fine = _subdivide(bush, 5)
    fern = level_of_detail(_prop(fine, FERN, _png((30, 120, 30))))
    assert fern.triangles <= DECOR_MAX_TRIS < fine.triangle_count
    tree = level_of_detail(_prop(fine, TREE, _png((30, 120, 30))))
    assert tree.triangles > DECOR_MAX_TRIS


def _subdivide(mesh: Mesh, times: int) -> Mesh:
    vertices, triangles = mesh.vertices.copy(), mesh.triangles.copy()
    for _ in range(times):
        new_tris = []
        verts = list(vertices)
        for a, b, c in triangles:
            ab, bc, ca = len(verts), len(verts) + 1, len(verts) + 2
            verts += [(vertices[a] + vertices[b]) / 2, (vertices[b] + vertices[c]) / 2, (vertices[c] + vertices[a]) / 2]
            new_tris += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
        vertices, triangles = np.array(verts), np.array(new_tris)
    return Mesh(vertices, triangles)


def test_one_band_has_no_storey_or_zocalo_cut() -> None:
    one = Bands(0.0, 12.0, 1, 12.0, zocalo=False)
    assert one.levels == [12.0] and one.plain_levels == [12.0]
    storeyed = Bands(0.9, 3.0, 4, 12.0)
    assert storeyed.levels == [0.9, 3.0, 6.0, 9.0, 12.0]
    assert replace(storeyed, storeys=1, zocalo=False).levels == [12.0]
