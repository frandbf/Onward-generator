"""Models drawn in flat colours (ADR-036 amendment of 12 November 2026): a pale bookcase and a white fridge on Rue Mouffetard."""

from __future__ import annotations

import io
import json
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.overgrow.flatness import FLAT_SHARE, SOLID_IMAGE_PX, SWATCH_TEXELS, flat_colour, flat_share, is_flat

QUAD = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [2.0, 0.0, 1.0], [0.0, 0.0, 1.0]])
TRIS = np.array([[0, 1, 2], [0, 2, 3]])


def _uvs(span: float) -> np.ndarray:
    return np.array([[0.25, 0.25], [0.25 + span, 0.25], [0.25 + span, 0.25 + span], [0.25, 0.25 + span]])


def write_glb(path: Path, *, uv_span: float | None = 1.0, image_px: int = 64, external: bool = False, image_first: bool = False) -> Path:
    """A 2 x 1 m quad; ``uv_span`` None leaves the material untextured. ``external`` writes the image beside the GLB;
    ``image_first`` puts an embedded image at the start of the buffer, its view without a ``byteOffset``."""
    positions = QUAD.astype(np.float32).tobytes()
    indices = TRIS.astype(np.uint16).tobytes()
    head = b""
    if image_first:
        png = io.BytesIO()
        Image.new("RGB", (image_px, image_px), (120, 90, 60)).save(png, format="PNG")
        head = png.getvalue() + b"\0" * (-len(png.getvalue()) % 4)
    blob = head + positions + indices
    views = [{"buffer": 0, "byteOffset": len(head), "byteLength": len(positions)},
             {"buffer": 0, "byteOffset": len(head) + len(positions), "byteLength": len(indices)}]
    accessors = [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
                 {"bufferView": 1, "componentType": 5123, "count": 6, "type": "SCALAR"}]
    prim: dict = {"attributes": {"POSITION": 0}, "indices": 1, "material": 0}
    doc: dict = {"asset": {"version": "2.0"}, "meshes": [{"primitives": [prim]}], "materials": [{"pbrMetallicRoughness": {}}]}
    if uv_span is not None:
        uv = _uvs(uv_span).astype(np.float32).tobytes()
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(uv)})
        blob += uv
        accessors.append({"bufferView": 2, "componentType": 5126, "count": 4, "type": "VEC2"})
        prim["attributes"]["TEXCOORD_0"] = 2
        png = io.BytesIO()
        Image.new("RGB", (image_px, image_px), (120, 90, 60)).save(png, format="PNG")
        if image_first:
            views.append({"buffer": 0, "byteLength": len(head)})
            doc["images"] = [{"bufferView": 3, "mimeType": "image/png"}]
        elif external:
            (path.parent / "albedo.png").write_bytes(png.getvalue())
            doc["images"] = [{"uri": "albedo.png"}]
        else:
            views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(png.getvalue())})
            doc["images"] = [{"bufferView": 3, "mimeType": "image/png"}]
            blob += png.getvalue()
        doc["textures"] = [{"source": 0}]
        doc["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"] = {"index": 0}
    blob += b"\0" * (-len(blob) % 4)
    doc.update(bufferViews=views, accessors=accessors, buffers=[{"byteLength": len(blob)}])
    text = json.dumps(doc).encode()
    text += b" " * (-len(text) % 4)
    body = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(blob), 0x004E4942) + blob
    path.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    return path


def test_a_face_spanning_less_than_two_texels_samples_a_swatch() -> None:
    assert (SWATCH_TEXELS, SOLID_IMAGE_PX, FLAT_SHARE) == (2.0, 8, 0.9)
    assert flat_share(QUAD, TRIS, _uvs(1.0 / 512), (512, 512)) == (pytest.approx(2.0), pytest.approx(2.0))
    assert flat_share(QUAD, TRIS, _uvs(1.0), (64, 64)) == (0.0, pytest.approx(2.0))
    # Exactly two texels is an image, not a swatch; the wider side of the image counts.
    assert flat_share(QUAD, TRIS, _uvs(2.0 / 64), (64, 64))[0] == 0.0
    assert flat_share(QUAD, TRIS, _uvs(1.9 / 64), (64, 64))[0] == pytest.approx(2.0)
    assert flat_share(QUAD, TRIS, _uvs(1.9 / 64), (64, 4))[0] == pytest.approx(2.0)
    assert flat_share(QUAD, TRIS, _uvs(1.9 / 64), (4, 64))[0] == pytest.approx(2.0)
    assert flat_share(QUAD, TRIS, np.array([[0.25, 0.25], [0.5, 0.25], [0.25, 0.25], [0.25, 0.25]]), (64, 64))[0] == pytest.approx(1.0)


def test_no_uvs_no_image_or_a_solid_image_is_all_swatch() -> None:
    assert flat_share(QUAD, TRIS, None, (64, 64)) == (pytest.approx(2.0), pytest.approx(2.0))
    assert flat_share(QUAD, TRIS, _uvs(1.0), None) == (pytest.approx(2.0), pytest.approx(2.0))
    assert flat_share(QUAD, TRIS, _uvs(1.0), (8, 8))[0] == pytest.approx(2.0)
    assert flat_share(QUAD, TRIS, _uvs(1.0), (9, 8))[0] == 0.0


def test_a_model_is_flat_from_nine_tenths_of_its_surface() -> None:
    assert is_flat(9.0, 10.0) and is_flat(10.0, 10.0) and is_flat(0.5, 0.5)
    assert not is_flat(8.99, 10.0) and not is_flat(0.0, 0.0)


def test_flat_colour_reads_a_glb_file(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert flat_colour(write_glb(tmp_path / "palette.glb", uv_span=1.0 / 512, image_px=512))
    assert not flat_colour(write_glb(tmp_path / "photo.glb", uv_span=1.0, image_px=64))
    assert flat_colour(write_glb(tmp_path / "untextured.glb", uv_span=None))
    assert flat_colour(write_glb(tmp_path / "solid.glb", uv_span=1.0, image_px=4))
    assert not flat_colour(write_glb(tmp_path / "a" / "outside.glb", uv_span=1.0, image_px=64, external=True))
    assert flat_colour(write_glb(tmp_path / "b" / "outside.glb", uv_span=1.0, image_px=4, external=True))
    (tmp_path / "broken.glb").write_bytes(b"glTF")
    assert not flat_colour(tmp_path / "broken.glb") and not flat_colour(tmp_path / "missing.glb")


def test_a_quad_away_from_the_origin_has_the_same_area() -> None:
    moved = QUAD + np.array([5.0, 3.0, 1.0])
    assert flat_share(moved, TRIS, None, None) == (pytest.approx(2.0), pytest.approx(2.0))
    triangle = np.array([[1.0, 1.0, 0.0], [3.0, 1.0, 0.0], [1.0, 4.0, 0.0]])
    assert flat_share(triangle, np.array([[0, 1, 2]]), None, None)[1] == pytest.approx(3.0)


def test_an_image_at_the_start_of_the_buffer_is_read(tmp_path: Path) -> None:
    assert flat_colour(write_glb(tmp_path / "solid_first.glb", uv_span=1.0, image_px=4, image_first=True))
    assert not flat_colour(write_glb(tmp_path / "photo_first.glb", uv_span=1.0, image_px=64, image_first=True))


def test_a_rewritten_file_is_read_again(tmp_path: Path) -> None:
    import os

    path = tmp_path / "model.glb"
    write_glb(path, uv_span=1.0 / 512, image_px=512)
    assert flat_colour(path)
    stamp = path.stat().st_mtime_ns
    write_glb(path, uv_span=1.0, image_px=512)  # the same size: only the time tells
    os.utime(path, ns=(stamp + 10**9, stamp + 10**9))
    assert not flat_colour(path)
    write_glb(path, uv_span=1.0 / 512, image_px=64)  # another size at the same time
    os.utime(path, ns=(stamp + 10**9, stamp + 10**9))
    assert flat_colour(path)


def test_each_face_spans_its_own_uvs() -> None:
    # Two swatches far apart in the palette: each face is still one swatch.
    uvs = np.array([[0.1, 0.1], [0.1005, 0.1], [0.1005, 0.1005], [0.9, 0.9], [0.9005, 0.9], [0.9005, 0.9005]])
    faces = np.array([[0, 1, 2], [3, 4, 5]])
    corners = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 1.0], [2.0, 0.0, 0.0], [3.0, 0.0, 0.0], [3.0, 0.0, 1.0]])
    assert flat_share(corners, faces, uvs, (512, 512)) == (pytest.approx(1.0), pytest.approx(1.0))


def _png(px: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (px, px), (120, 90, 60)).save(out, format="PNG")
    return out.getvalue()


def test_a_loaded_prop_is_read_part_by_part() -> None:
    from onward_generator.generation.mesh import Mesh
    from onward_generator.overgrow.catalog import ASSETS_BY_ID
    from onward_generator.overgrow.flatness import prop_flat
    from onward_generator.overgrow.library import Prop, PropPart

    spec = ASSETS_BY_ID["concrete_road_barrier"]
    mesh = Mesh(QUAD, TRIS)
    assert prop_flat(Prop(spec, mesh, _uvs(1.0 / 512), _png(512)))
    assert not prop_flat(Prop(spec, mesh, _uvs(1.0), _png(64)))
    assert prop_flat(Prop(spec, mesh, _uvs(1.0), b""))  # an albedo that is no image is no texture
    assert prop_flat(Prop(spec, mesh, _uvs(1.0), _png(4)))
    # Two parts: the first triangle on a photo, the second on a 4 px colour: half the surface, not flat.
    assert not prop_flat(Prop(spec, mesh, _uvs(1.0), _png(64), parts=(PropPart(1, _png(64)), PropPart(2, _png(4)))))
    assert prop_flat(Prop(spec, mesh, _uvs(1.0), _png(64), parts=(PropPart(1, _png(4)), PropPart(2, _png(4)))))


def test_each_part_counts_its_own_triangles_by_area() -> None:
    from onward_generator.generation.mesh import Mesh
    from onward_generator.overgrow.catalog import ASSETS_BY_ID
    from onward_generator.overgrow.flatness import prop_flat
    from onward_generator.overgrow.library import Prop, PropPart

    spec = ASSETS_BY_ID["concrete_road_barrier"]

    def tri(x: float, area: float) -> np.ndarray:
        return np.array([[x, 0.0, 0.0], [x + 2 * area, 0.0, 0.0], [x, 0.0, 1.0]])

    def prop(areas: tuple[float, float], solid_first: bool) -> Prop:
        vertices = np.vstack([tri(0.0, areas[0]), tri(10.0, areas[1])])
        uvs = np.tile(np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]]), (2, 1))
        first, second = (_png(4), _png(64)) if solid_first else (_png(64), _png(4))
        return Prop(spec, Mesh(vertices, np.array([[0, 1, 2], [3, 4, 5]])), uvs, first, parts=(PropPart(1, first), PropPart(2, second)))

    # A large solid part and a small photo one: 1 of 1.05 m2 is swatch, flat; the photo's triangle is not counted twice.
    assert prop_flat(prop((1.0, 0.05), solid_first=True))
    # 0.8 of 1 m2: not flat, by a tenth.
    assert not prop_flat(prop((0.8, 0.2), solid_first=True))
    assert prop_flat(prop((0.9, 0.1), solid_first=True))
    # The photo part first: its triangle stays its own.
    assert prop_flat(prop((0.05, 1.0), solid_first=False))
    assert not prop_flat(prop((0.2, 0.8), solid_first=False))
