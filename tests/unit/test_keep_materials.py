"""One part and albedo per glTF material (Sketchfab ``keep_materials``) and a spec's own triangle budget (14 November 2026)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.generation.gltf import nodes_from_glb, read_glb
from onward_generator.overgrow.normalize import (
    TARGET_ASSET_TRIS,
    _grey_png,
    gltf_to_local_parts,
    normalize_arrays,
    normalize_parts,
    write_normalized_glb,
)


def _png(color: tuple[int, int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (4, 4), color).save(out, format="PNG")
    return out.getvalue()


def _grid(n: int, x0: float = 0.0, y0: float = 0.0, size: float = 1.0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """An n × n quad grid of ``size`` metres on the glTF ground (x, z), 2 n² triangles."""
    steps = np.linspace(0.0, size, n + 1)
    xs, zs = np.meshgrid(steps, steps)
    positions = np.stack([xs.ravel() + x0, np.zeros(xs.size), zs.ravel() + y0], axis=1).astype(np.float32)
    uvs = np.stack([xs.ravel() / size, zs.ravel() / size], axis=1).astype(np.float32)
    quads = [(j * (n + 1) + i, j * (n + 1) + i + 1, (j + 1) * (n + 1) + i + 1, (j + 1) * (n + 1) + i) for j in range(n) for i in range(n)]
    indices = np.array([t for a, b, c, d in quads for t in ((a, b, c), (a, c, d))], dtype=np.uint32).ravel()
    return positions, uvs, indices


def _write_gltf(folder: Path, prims: list[tuple[np.ndarray, np.ndarray, np.ndarray, int | None]], images: dict[str, bytes],
                materials: list[dict], extra: dict | None = None) -> Path:
    """A glTF with one node and one primitive per ``prims`` row (positions, uvs, uint32 indices, material or None)."""
    folder.mkdir(parents=True, exist_ok=True)
    blob = b""
    views, accessors, primitives = [], [], []
    for positions, uvs, indices, material in prims:
        first = len(accessors)
        for array, kind, component in ((positions, "VEC3", 5126), (uvs, "VEC2", 5126), (indices, "SCALAR", 5125)):
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": array.nbytes})
            accessor: dict = {"bufferView": len(views) - 1, "componentType": component, "count": len(array), "type": kind}
            if kind == "VEC3":
                accessor |= {"min": positions.min(axis=0).tolist(), "max": positions.max(axis=0).tolist()}
            accessors.append(accessor)
            blob += array.tobytes()
        prim: dict = {"attributes": {"POSITION": first, "TEXCOORD_0": first + 1}, "indices": first + 2}
        if material is not None:
            prim["material"] = material
        primitives.append(prim)
    names = sorted(images)
    for name, data in images.items():
        (folder / name).write_bytes(data)
    doc = {"asset": {"version": "2.0"}, "scene": 0, "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0}],
           "meshes": [{"primitives": primitives}], "materials": materials,
           "textures": [{"source": i} for i in range(len(names))], "images": [{"uri": n} for n in names],
           "buffers": [{"uri": "scene.bin", "byteLength": len(blob)}], "bufferViews": views, "accessors": accessors, **(extra or {})}
    (folder / "scene.bin").write_bytes(blob)
    (folder / "scene.gltf").write_text(json.dumps(doc), encoding="utf-8")
    return folder


def _two_materials(folder: Path) -> Path:
    """Material 0 (red image, 8 triangles), material 1 (glossy green image, 2), and a primitive without material (2)."""
    return _write_gltf(folder, [(*_grid(2), 0), (*_grid(1, 3.0), 1), (*_grid(1, 6.0), None)],
                       {"a_red.png": _png((200, 0, 0)), "b_green.png": _png((0, 200, 0))},
                       [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                        {"extensions": {"KHR_materials_pbrSpecularGlossiness": {"diffuseTexture": {"index": 1}}}}])


def test_each_triangle_keeps_its_material_and_each_material_its_image(tmp_path: Path) -> None:
    vertices, triangles, uvs, owners, albedos = gltf_to_local_parts(_two_materials(tmp_path / "model"))
    assert owners.tolist() == [0] * 8 + [1] * 2 + [-1] * 2  # no material: -1
    assert owners.dtype == np.int64 and len(triangles) == 12 and len(vertices) == len(uvs) == 9 + 4 + 4
    assert albedos == [_png((200, 0, 0)), _png((0, 200, 0))]


def test_the_grey_stand_in_is_a_small_flat_image() -> None:
    image = Image.open(io.BytesIO(_grey_png()))
    assert image.size == (8, 8) and image.convert("RGB").getextrema() == ((90, 90), (90, 90), (90, 90))


def _parts(owners: list[int], albedos: list[bytes | None], *, anchor: str = "ground", target: int = 1000,
           degenerate_first: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Unit quads side by side on the local ground (x, y, z up), one per owner, 2 m apart in x, 1 m deep, 0.5 m tall."""
    vertices, triangles = [], []
    for k, _owner in enumerate(owners):
        base = len(vertices)
        x = 2.0 * k
        vertices += [(x, 0.0, 0.0), (x + 1.0, 0.0, 0.0), (x + 1.0, 1.0, 0.5), (x, 1.0, 0.5)]
        if degenerate_first and k == 0:
            triangles += [(base, base, base + 1), (base + 2, base + 2, base + 3)]
        else:
            triangles += [(base, base + 1, base + 2), (base, base + 2, base + 3)]
    v = np.array(vertices, dtype=np.float64)
    return v, np.array(triangles, dtype=np.int64), (v[:, :2] / 8.0), np.repeat(np.array(owners, dtype=np.int64), 2)


def test_parts_follow_the_materials_and_take_grey_out_of_range() -> None:
    red, green = _png((200, 0, 0)), _png((0, 200, 0))
    v, t, uv, owners = _parts([0, 1, -1, 2], [red, green])
    normalized = normalize_parts(v, t, uv.astype(np.float64), owners, [red, green], anchor="ground", target_tris=1000)
    colours = [Image.open(io.BytesIO(png)).convert("RGB").getpixel((0, 0)) for _end, png in normalized.parts]
    assert colours == [(90, 90, 90), (200, 0, 0), (0, 200, 0), (90, 90, 90)]  # owners sorted: -1, 0, 1, 2 (past the list)
    assert [end for end, _ in normalized.parts] == [2, 4, 6, 8]
    assert normalized.albedo_png == normalized.parts[0][1] != normalized.parts[1][1]
    assert normalized.uvs.dtype == np.float32 and len(normalized.uvs) == normalized.mesh.vertex_count
    # One scale, orientation and ground for the whole model: the 7 m × 1 m × 0.5 m row of quads.
    assert (normalized.length_m, normalized.depth_m, normalized.height_m) == pytest.approx((7.0, 1.0, 0.5))
    plain = normalize_arrays(v.copy(), t, uv.astype(np.float32), red, anchor="ground", api_dimensions_mm=None, target_tris=1000)
    assert (normalized.scale_note, normalized.orientation) == (plain.scale_note, plain.orientation)
    assert normalized.triangles_before == 8 and normalized.triangles_after == 8


def test_parts_turn_with_the_anchor() -> None:
    v, t, uv, owners = _parts([0], [None])
    facade = normalize_parts(v, t, uv, owners, [None], anchor="facade", target_tris=1000)
    ground = normalize_parts(v, t, uv, owners, [None], anchor="ground", target_tris=1000)
    assert facade.orientation != ground.orientation and facade.orientation.startswith("thinnest horizontal axis")


def test_a_material_left_without_triangles_is_skipped_and_the_rest_kept() -> None:
    red, green = _png((200, 0, 0)), _png((0, 200, 0))
    v, t, uv, owners = _parts([0, 1], [red, green], degenerate_first=True)
    normalized = normalize_parts(v, t, uv, owners, [red, green], anchor="ground", target_tris=1000)
    assert len(normalized.parts) == 1 and normalized.parts[0][0] == 2
    assert Image.open(io.BytesIO(normalized.parts[0][1])).convert("RGB").getpixel((0, 0)) == (0, 200, 0)
    v, t, uv, owners = _parts([0], [red], degenerate_first=True)
    with pytest.raises(ValueError, match=r"^no material kept a triangle$"):
        normalize_parts(v, t, uv, owners, [red], anchor="ground", target_tris=1000)


def test_each_material_gets_its_share_of_the_budget() -> None:
    big_v, big_u, big_t = _grid(20)  # 800 triangles
    small_v, small_u, small_t = _grid(10, 3.0)  # 200 triangles
    vertices = np.vstack([big_v, small_v]).astype(np.float64)[:, [0, 2, 1]]
    triangles = np.vstack([big_t.reshape(-1, 3), small_t.reshape(-1, 3) + len(big_v)]).astype(np.int64)
    uvs = np.vstack([big_u, small_u]).astype(np.float64)
    owners = np.array([0] * 800 + [1] * 200, dtype=np.int64)
    normalized = normalize_parts(vertices, triangles, uvs, owners, [None, None], anchor="ground", target_tris=100)
    first, second = normalized.parts[0][0], normalized.parts[1][0] - normalized.parts[0][0]
    assert 20 <= first <= 80 and 5 <= second <= 20  # shares 80 and 20 of 100, each clustered to at most its own
    assert normalized.triangles_before == 1000 and normalized.triangles_after == first + second <= 100


def _read(path: Path) -> tuple[dict, list]:
    data = path.read_bytes()
    return read_glb(data)[0], nodes_from_glb(data)[0]


def test_a_normalized_glb_keeps_one_material_per_part_with_its_uvs(tmp_path: Path) -> None:
    red, green = _png((200, 0, 0)), _png((0, 200, 0))
    v, t, uv, owners = _parts([0, 1], [red, green])
    normalized = normalize_parts(v, t, uv, owners, [red, green], anchor="ground", target_tris=1000)
    write_normalized_glb(tmp_path / "deep" / "multi.glb", "multi", normalized)
    doc, (node,) = _read(tmp_path / "deep" / "multi.glb")
    assert node.name == "multi" and dict(node.extras) == {"type": "prop", "asset_id": "multi", "anchor": normalized.orientation}
    assert doc["meshes"][0]["name"] == "prop:multi"
    assert [p.material for p in node.primitives] == ["prop_multi_0", "prop_multi_1"]
    assert [p.mesh.triangle_count for p in node.primitives] == [2, 2]
    for material in doc["materials"]:
        pbr = material["pbrMetallicRoughness"]
        assert pbr["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0] and pbr["roughnessFactor"] == pytest.approx(0.9)
    uvs = np.vstack([p.uvs for p in node.primitives])
    assert uvs.shape == (8, 2) and np.allclose(np.sort(uvs, axis=0), np.sort(normalized.uvs, axis=0), atol=1e-6)


def test_a_normalized_glb_without_parts_is_one_primitive(tmp_path: Path) -> None:
    v, t, uv, _owners = _parts([0, 1], [None, None])
    plain = normalize_arrays(v, t, uv.astype(np.float32), _png((10, 20, 30)), anchor="ground", api_dimensions_mm=None, target_tris=1000)
    write_normalized_glb(tmp_path / "plain.glb", "plain", plain)
    _doc, (node,) = _read(tmp_path / "plain.glb")
    (primitive,) = node.primitives
    assert primitive.material == "prop_plain" and primitive.mesh.triangle_count == 4 and primitive.uvs is not None


def _spec(**extra: object) -> dict:
    return {"id": "multi", "category": "vehicles", "role": "wreck", "surfaces": ["yard"], "anchor": "ground", "use": "test", **extra}


ROW = {"license_url": "http://creativecommons.org/licenses/by/4.0/", "author": "someone", "url": "https://sketchfab.com/3d-models/x"}


def test_a_cached_download_is_imported_with_its_materials_and_its_budget(tmp_path: Path) -> None:
    """``_one`` reads a download already unpacked under ``raw/<id>``: no token, no request."""
    from onward_generator.overgrow.sketchfab_fetch import _one

    raw, library = tmp_path / "raw", tmp_path / "library"
    library.mkdir()
    _write_gltf(raw / "multi" / "unpacked", [(*_grid(40), 0), (*_grid(10, 3.0), 1)],
                {"a_red.png": _png((200, 0, 0)), "b_green.png": _png((0, 200, 0))},
                [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}, {"pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}}])
    entry = _one("uid", _spec(keep_materials=True, max_triangles=600, anchor="facade"), ROW, raw, library, "")
    assert entry["id"] == "multi" and entry["installed"] is True and entry["error"] is None
    assert entry["normalized_file"] == "multi.glb" and entry["triangles_before"] == 3400
    assert 150 <= entry["triangles_after"] <= 600
    _doc, (node,) = _read(library / "multi.glb")
    assert [p.material for p in node.primitives] == ["prop_multi_0", "prop_multi_1"]
    assert not str(node.extras["anchor"]).startswith("longest horizontal axis")  # the spec's facade anchor, not the default
    (row,) = json.loads((library / "manifest.json").read_text(encoding="utf-8"))["assets"]
    assert row["triangles"] == entry["triangles_after"] and row["length_m"] == entry["length_m"]
    assert (entry["length_m"], entry["depth_m"], entry["height_m"]) == (row["length_m"], row["depth_m"], row["height_m"])
    assert entry["length_m"] == pytest.approx(4.0, abs=0.1) and entry["depth_m"] == pytest.approx(1.0, abs=0.1)  # clustering trims the edges


def test_a_cached_download_without_keep_materials_is_one_part_at_the_default_budget(tmp_path: Path) -> None:
    from onward_generator.overgrow.sketchfab_fetch import _one

    raw, library = tmp_path / "raw", tmp_path / "library"
    library.mkdir()
    _write_gltf(raw / "multi" / "unpacked", [(*_grid(40), 0)], {"a_red.png": _png((200, 0, 0))},
                [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}])
    entry = _one("uid", _spec(anchor="facade"), ROW, raw, library, "")
    assert entry["triangles_before"] == 3200 and 600 < entry["triangles_after"] <= TARGET_ASSET_TRIS
    _doc, (node,) = _read(library / "multi.glb")
    assert [p.material for p in node.primitives] == ["prop_multi"]
    assert not str(node.extras["anchor"]).startswith("longest horizontal axis")
    from onward_generator.generation.gltf import images_by_material

    (image,) = images_by_material((library / "multi.glb").read_bytes()).values()
    assert Image.open(io.BytesIO(image)).convert("RGB").getpixel((0, 0)) == (200, 0, 0)  # the file's albedo, not a flat one
    small = _one("uid", _spec(max_triangles=300), ROW, raw, library, "")
    assert small["triangles_after"] <= 300


def test_a_draco_download_is_refused(tmp_path: Path) -> None:
    from onward_generator.overgrow.sketchfab_fetch import SketchfabError, _one

    raw, library = tmp_path / "raw", tmp_path / "library"
    library.mkdir()
    _write_gltf(raw / "multi" / "unpacked", [(*_grid(1), 0)], {"a_red.png": _png((200, 0, 0))},
                [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}], {"extensionsRequired": ["KHR_draco_mesh_compression"]})
    with pytest.raises(SketchfabError, match=r"^glTF requires Draco; not imported$"):
        _one("uid", _spec(), ROW, raw, library, "")
    assert not (library / "multi.glb").exists()
