"""The Panoramax decal atlas per combine cell, the photo tint and the coverage (map.json "panoramax_atlas")."""

from __future__ import annotations

import io
import json
import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh, quad
from onward_generator.materials.trim_style import TrimLayout, load_style_table, trim_layout
from onward_generator.panoramax.apply import (
    _CELL_SIDE,
    Patch,
    TintAtlas,
    _pack_cell,
    _pack_patches,
    _rows,
    cell_material,
    cell_row,
    coverage,
    current_strip,
    dominant_rgb,
    group_cells,
    nearest_strip,
    retint,
    strip_colours,
    tint_atlas_for,
    tint_node,
)
from onward_generator.panoramax.cache import PanoramaxError
from onward_generator.panoramax.project import BASE_COLOR, FacadeImage
from onward_generator.tiers import combine_cell

STYLE = "mediterranean"


def _png(width: int, height: int, color: tuple[int, int, int] = (90, 120, 150)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _facade(width: int, height: int, color: tuple[int, int, int] = (90, 120, 150)) -> FacadeImage:
    return FacadeImage(_png(width, height, color), width, height, BASE_COLOR)


# --- coverage ---


def _face(building: str, index: int, length: float) -> dict[str, object]:
    return {"building_id": building, "segment_index": index, "length_m": length}


def _span(building: str, index: int, s0: float, s1: float) -> dict[str, object]:
    return {"building_id": building, "segment_index": index, "s0_m": s0, "s1_m": s1}


def test_coverage_merges_overlapping_and_touching_spans_on_one_face() -> None:
    faces = [_face("a", 0, 20.0)]
    result = coverage(faces, [_span("a", 0, 0.0, 5.0), _span("a", 0, 4.0, 8.0), _span("a", 0, 8.0, 10.0)])
    assert result["photo_m"] == 10.0
    assert result["percent"] == 50.0


def test_coverage_keeps_a_gap_between_spans() -> None:
    result = coverage([_face("a", 0, 20.0)], [_span("a", 0, 0.0, 4.0), _span("a", 0, 6.0, 9.0)])
    assert result["photo_m"] == 7.0


def test_coverage_clips_a_span_to_the_face() -> None:
    result = coverage([_face("a", 0, 10.0)], [_span("a", 0, -2.0, 4.0), _span("a", 0, 8.0, 14.0)])
    assert result["photo_m"] == 6.0
    assert result["exposed_m"] == 10.0


def test_coverage_adds_faces_and_ignores_rows_without_a_face() -> None:
    faces = [_face("b", 0, 10.0), _face("a", 0, 10.0), _face("a", 1, 5.0)]
    rows = [_span("a", 7, 0.0, 5.0), _span("c", 0, 0.0, 5.0), _span("a", 0, 0.0, 3.0), _span("a", 1, 0.0, 2.0)]
    result = coverage(faces, rows)
    assert result["exposed_m"] == 25.0
    assert result["photo_m"] == 5.0
    assert result["percent"] == 20.0
    assert result["buildings"] == [
        {"building_id": "a", "exposed_m": 15.0, "photo_m": 5.0, "percent": 33.3},
        {"building_id": "b", "exposed_m": 10.0, "photo_m": 0.0, "percent": 0.0},
    ]


def test_coverage_rounds_the_percentage_to_a_tenth() -> None:
    result = coverage([_face("a", 0, 3.0)], [_span("a", 0, 0.0, 2.0)])
    assert result["percent"] == 66.7
    assert coverage([_face("a", 0, 7.0)], [_span("a", 0, 0.0, 1.0)])["percent"] == 14.3


def test_coverage_without_a_photo_and_without_frontage() -> None:
    assert coverage([_face("a", 0, 12.0)], [])["photo_m"] == 0.0
    assert coverage([_face("a", 0, 12.0)], [])["percent"] == 0.0
    empty = coverage([], [])
    assert empty == {"exposed_m": 0.0, "photo_m": 0.0, "percent": None, "buildings": []}
    assert coverage([_face("a", 0, 0.0)], [])["buildings"][0]["percent"] is None


def test_coverage_adds_every_separate_span() -> None:
    result = coverage([_face("a", 0, 20.0)], [_span("a", 0, 1.0, 4.0), _span("a", 0, 6.0, 9.0), _span("a", 0, 11.0, 12.0)])
    assert result["photo_m"] == 7.0


def test_coverage_rounds_metres_to_the_millimetre() -> None:
    result = coverage([_face("a", 0, 10.12345)], [_span("a", 0, 0.0, 3.33333)])
    assert result["exposed_m"] == 10.123
    assert result["photo_m"] == 3.333
    assert result["buildings"][0]["exposed_m"] == 10.123
    assert result["buildings"][0]["photo_m"] == 3.333
    assert result["percent"] == 32.9


def test_coverage_of_frontage_under_a_metre() -> None:
    result = coverage([_face("a", 0, 0.5)], [_span("a", 0, 0.0, 0.25)])
    assert result["percent"] == 50.0
    assert result["buildings"][0]["percent"] == 50.0


def test_coverage_ignores_a_span_entirely_past_the_face() -> None:
    result = coverage([_face("a", 0, 10.0)], [_span("a", 0, 10.0, 12.0)])
    assert result["photo_m"] == 0.0


# --- packing ---


def test_legacy_pack_keeps_one_patch_as_it_is_with_inset_uvs() -> None:
    patch = _facade(64, 32)
    png, uvs = _pack_patches([patch])
    assert png == patch.png
    assert np.allclose(uvs[0], [[0.001, 0.001], [0.999, 0.001], [0.999, 0.999], [0.001, 0.999]])


def test_legacy_pack_places_three_patches_on_one_1024_row() -> None:
    png, uvs = _pack_patches([_facade(64, 32), _facade(64, 32), _facade(64, 32)])
    assert Image.open(io.BytesIO(png)).size == (1024, 1024)
    assert np.allclose(uvs[1][0], [(66 + 0.5) / 1024, 1.0 - (32 - 0.5) / 1024])


def test_pack_cell_uses_the_smallest_side_that_holds_the_crops() -> None:
    png, uvs, side, scale = _pack_cell([_facade(64, 32)])
    assert (side, scale) == (256, 1.0)
    assert Image.open(io.BytesIO(png)).size == (256, 256)
    half = 0.5 / 256
    assert np.allclose(uvs[0], [[half, 1 - 32 / 256 + half], [64 / 256 - half, 1 - 32 / 256 + half], [64 / 256 - half, 1 - half], [half, 1 - half]])
    assert pack_side([_facade(256, 100)]) == 256
    assert pack_side([_facade(256, 256)]) == 256
    assert pack_side([_facade(100, 257)]) == 512
    assert pack_side([_facade(257, 100)]) == 512
    assert pack_side([_facade(512, 512), _facade(512, 512)]) == 2048


def pack_side(patches: list[FacadeImage]) -> int:
    return _pack_cell(patches)[2]


def test_pack_cell_rows_leave_a_two_pixel_gap() -> None:
    _png_bytes, uvs, side, _scale = _pack_cell([_facade(200, 200), _facade(100, 200)])
    assert side == 512
    assert uvs[1][0][0] == pytest.approx((202 + 0.5) / 512)
    _png_bytes, uvs, side, _scale = _pack_cell([_facade(128, 200), _facade(126, 200)])
    assert side == 256
    assert uvs[1][0][0] == pytest.approx((130 + 0.5) / 256)
    _png_bytes, uvs, side, _scale = _pack_cell([_facade(128, 200), _facade(127, 200)])
    assert side == 512


def test_pack_cell_wraps_to_a_new_row_below_the_tallest() -> None:
    _png_bytes, uvs, side, _scale = _pack_cell([_facade(200, 50), _facade(40, 30), _facade(200, 20)])
    assert side == 256
    assert uvs[2][2][1] == pytest.approx(1.0 - (52 + 0.5) / 256)
    assert uvs[2][0][0] == pytest.approx(0.5 / 256)


def test_pack_cell_shrinks_crops_that_overflow_and_keeps_the_uvs_inside() -> None:
    crops = [_facade(512, 512) for _ in range(20)]
    png, uvs, side, scale = _pack_cell(crops)
    assert side == _CELL_SIDE
    assert scale == pytest.approx(0.75)
    assert Image.open(io.BytesIO(png)).size == (2048, 2048)
    for item in uvs:
        assert np.all((item > 0.0) & (item < 1.0))
        width_px = (item[1, 0] - item[0, 0]) * side + 1.0
        assert width_px == pytest.approx(384.0)


def _paletted(width: int, height: int, mode: str) -> FacadeImage:
    buffer = io.BytesIO()
    Image.new(mode, (width, height), 200).save(buffer, format="PNG")
    return FacadeImage(buffer.getvalue(), width, height, BASE_COLOR)


def test_pack_cell_places_each_crop_and_leaves_the_rest_black() -> None:
    png, uvs, side, _scale = _pack_cell([_facade(100, 50, (250, 0, 0)), _facade(60, 40, (0, 250, 0))])
    canvas = np.asarray(Image.open(io.BytesIO(png)))
    assert side == 256
    assert tuple(canvas[10, 10]) == (250, 0, 0)
    assert tuple(canvas[10, 110]) == (0, 250, 0)
    assert tuple(canvas[10, 101]) == (0, 0, 0)
    assert tuple(canvas[200, 200]) == (0, 0, 0)
    assert all(item.dtype == np.float32 for item in uvs)
    gray, _uvs, _side, _scale = _pack_cell([_paletted(8, 8, "L")])
    assert tuple(np.asarray(Image.open(io.BytesIO(gray)))[2, 2]) == (200, 200, 200)


def test_pack_cell_resamples_a_shrunk_crop_with_lanczos(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onward_generator.panoramax.apply._CELL_SIDE", 300)
    rng = np.random.default_rng(7)
    noise = rng.integers(0, 256, size=(512, 512, 3), dtype=np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(noise).save(buffer, format="PNG")
    png, uvs, side, scale = _pack_cell([FacadeImage(buffer.getvalue(), 512, 512, BASE_COLOR), _facade(2, 2, (255, 255, 255))])
    canvas = np.asarray(Image.open(io.BytesIO(png)))
    expected = np.asarray(Image.fromarray(noise).resize((288, 288), Image.Resampling.LANCZOS))
    assert (side, scale) == (300, 0.5625)
    assert np.array_equal(canvas[:288, :288], expected)
    # The second crop lands right of the first, shrunk to 1 px, and the gap stays black.
    assert tuple(canvas[0, 288]) == (0, 0, 0)
    assert tuple(canvas[0, 290]) == (255, 255, 255)
    assert tuple(canvas[0, 291]) == (0, 0, 0)
    assert tuple(canvas[1, 290]) == (0, 0, 0)
    width_px = (uvs[1][1, 0] - uvs[1][0, 0]) * side + 1.0
    height_px = (uvs[1][2, 1] - uvs[1][0, 1]) * side + 1.0
    assert width_px == pytest.approx(1.0) and height_px == pytest.approx(1.0)


def test_pack_cell_counts_its_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onward_generator.panoramax.apply._CELL_SIDE", 300)
    crops = [_facade(512, 512)]
    # 512 * 0.75 ** 2 = 288 fits a 300 px side on the third attempt.
    assert _pack_cell(crops)[3] == pytest.approx(0.5625)
    monkeypatch.setattr("onward_generator.panoramax.apply._CELL_ATTEMPTS", 2)
    with pytest.raises(PanoramaxError, match="^the facade photographs of one combine cell do not fit in 300 px$"):
        _pack_cell(crops)


def test_pack_cell_refuses_a_set_that_never_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("onward_generator.panoramax.apply._CELL_SIDE", 4)
    with pytest.raises(PanoramaxError, match="do not fit"):
        _pack_cell([_facade(512, 512)])
    with pytest.raises(PanoramaxError, match="^a decal cell needs at least one photograph$"):
        _pack_cell([])


def _patch(node: str, segment: int, s0: float, picture: str, mesh: Mesh, color: tuple[int, int, int] = (90, 120, 150)) -> Patch:
    note = {"segment": segment, "s0_m": s0, "picture_id": picture, "author": f"author-{picture}", "license": "CC-BY-SA-4.0", "origin": "observed"}
    return Patch(node, mesh, _facade(64, 32, color), note)


def _box(x0: float, y0: float, x1: float, y1: float) -> Mesh:
    return quad((x0, y0, 0.0), (x1, y0, 0.0), (x1, y1, 3.0), (x0, y1, 3.0))


def test_shuffled_patches_pack_to_the_same_bytes() -> None:
    mesh = _box(0, 0, 1, 1)
    patches = [_patch("b", 1, 0.0, "p2", mesh), _patch("a", 0, 3.0, "p1", mesh), _patch("a", 0, 1.0, "p3", mesh), _patch("a", 2, 0.0, "p0", mesh)]
    patches = [Patch(p.node, p.mesh, _facade(40 + 10 * i, 30), p.note) for i, p in enumerate(patches)]
    nodes = [Node("a", (Primitive(_box(10, 10, 20, 20), "wall"),)), Node("b", (Primitive(_box(30, 10, 40, 20), "wall"),))]
    first = group_cells(nodes, {"a": patches[1:], "b": patches[:1]}, 100.0)
    shuffled = patches[:]
    random.Random(4).shuffle(shuffled)
    second = group_cells(nodes, {"a": [p for p in shuffled if p.node == "a"], "b": [p for p in shuffled if p.node == "b"]}, 100.0)
    assert [p.key for p in first[(0, -1)]] == [("a", 0, 1.0, "p3"), ("a", 0, 3.0, "p1"), ("a", 2, 0.0, "p0"), ("b", 1, 0.0, "p2")]
    assert _pack_cell([p.image for p in first[(0, -1)]])[0] == _pack_cell([p.image for p in second[(0, -1)]])[0]


# --- cells ---


def test_cell_row_names_the_cell_and_its_buildings() -> None:
    mesh = _box(0, 0, 1, 1)
    ordered = [_patch("b", 0, 0.0, "p1", mesh), _patch("a", 1, 0.0, "p2", mesh), _patch("b", 2, 0.0, "p3", mesh)]
    assert cell_row((3, -2), ordered, 1024, 0.421875) == {
        "cell": [3, -2],
        "material": "pano-cell-3_-2",
        "decals": 3,
        "buildings": ["a", "b"],
        "side_px": 1024,
        "scale": 0.4219,
    }


def test_two_buildings_in_one_cell_share_one_cell_and_neighbours_do_not() -> None:
    nodes = [
        Node("a", (Primitive(_box(10, -20, 20, -10), "wall"),)),
        Node("b", (Primitive(_box(40, -20, 50, -10), "wall"),)),
        Node("c", (Primitive(_box(110, -20, 120, -10), "wall"),)),
        Node("d", (Primitive(_box(60, -60, 70, -50), "wall"),)),
    ]
    patches = {name: [_patch(name, 0, 0.0, "p", _box(*nodes[i].primitives[0].mesh.vertices[[0, 2], :2].ravel()))] for i, name in enumerate("abc")}
    patches["d"] = []
    cells = group_cells(nodes, patches, 100.0)
    assert sorted(cells) == [(0, 0), (1, 0)]
    assert [p.node for p in cells[(0, 0)]] == ["a", "b"]
    assert [p.node for p in cells[(1, 0)]] == ["c"]
    assert cell_material((1, 0)) == "pano-cell-1_0"
    assert cell_material((-1, 2)) == "pano-cell--1_2"


def test_a_decal_that_moves_the_centre_across_a_cell_line_moves_the_cell() -> None:
    # The wall spans x 80..119.98: its centre is 99.99, cell 0. The decal reaches 120.1, so the node's centre crosses 100.
    wall = Primitive(_box(80.0, -20.0, 119.98, -10.0), "wall")
    decal = _box(80.0, -20.0, 120.1, -19.96)
    node = Node("a", (wall,))
    assert combine_cell(node.primitives, 100.0) == (0, 0)
    cells = group_cells([node], {"a": [_patch("a", 0, 0.0, "p", decal)]}, 100.0)
    assert list(cells) == [(1, 0)]
    assert combine_cell((wall, Primitive(decal, "pano-cell-1_0")), 100.0) == (1, 0)


# --- tint ---


@pytest.fixture(scope="module")
def layout() -> TrimLayout:
    return trim_layout(load_style_table().styles[STYLE].atlas)


@pytest.fixture(scope="module")
def families() -> dict[str, str]:
    return {str(k): str(v) for k, v in load_style_table().styles[STYLE].atlas["walls"].items()}


def _solid_atlas(layout: TrimLayout) -> tuple[np.ndarray, dict[tuple[str, int], tuple[int, int, int]]]:
    rgb = np.zeros((layout.height, layout.size, 3), dtype=np.uint8)
    rgb[:, :] = (1, 2, 3)
    colours: dict[tuple[str, int], tuple[int, int, int]] = {}
    for index, (wall, strips) in enumerate(sorted(layout.tones.items())):
        for tone, strip in enumerate(strips):
            colour = (20 * index + 10, 30 * tone + 40, 200 - 7 * index)
            rgb[strip.row0 + layout.gutter : strip.row1 - layout.gutter] = colour
            colours[(wall, tone)] = colour
    return rgb, colours


def test_strip_colours_reads_each_strip_inside_its_gutters(layout: TrimLayout) -> None:
    rgb, colours = _solid_atlas(layout)
    # Paint the gutters: the mean must not see them.
    for strips in layout.tones.values():
        for strip in strips:
            rgb[strip.row0 : strip.row0 + layout.gutter] = (255, 255, 255)
            rgb[strip.row1 - layout.gutter : strip.row1] = (0, 0, 0)
    found = strip_colours(rgb.astype(np.float64), layout)
    assert set(found) == set(colours)
    for key, colour in colours.items():
        assert found[key] == pytest.approx(colour)


def test_dominant_rgb_is_a_median_an_outlier_block_does_not_move() -> None:
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    image[:, :] = (200, 150, 100)
    image[:1, :] = (0, 0, 255)
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG")
    other = _png(4, 5, (190, 140, 90))
    assert dominant_rgb([buffer.getvalue()]) == (200, 150, 100)
    assert dominant_rgb([buffer.getvalue(), other]) == dominant_rgb([other, buffer.getvalue()])
    assert dominant_rgb([_png(2, 1, (10, 20, 30)), _png(2, 1, (11, 21, 33))]) == (10, 20, 32)


def test_nearest_strip_stays_in_the_wall_family(families: dict[str, str]) -> None:
    colours = {("brick_red", 0): (150, 60, 50), ("brick_red", 1): (120, 40, 30), ("plaster_white", 0): (230, 230, 230)}
    # Plaster is far nearer the pale photo; a brick wall still takes a brick tone.
    assert nearest_strip((228, 228, 228), colours, families, "brick_red") == ("brick_red", 0)
    assert nearest_strip((121, 41, 31), colours, families, "brick_red") == ("brick_red", 1)
    assert nearest_strip((10, 10, 10), colours, families, "plaster_cream") == ("plaster_white", 0)


def test_nearest_strip_breaks_a_tie_by_the_sorted_key(families: dict[str, str]) -> None:
    colours = {("plaster_white", 0): (110, 100, 100), ("plaster_cream", 1): (90, 100, 100), ("plaster_cream", 0): (100, 110, 100)}
    assert nearest_strip((100, 100, 100), colours, families, "plaster_white") == ("plaster_cream", 0)
    colours[("plaster_cream", 0)] = (100, 111, 100)
    assert nearest_strip((100, 100, 100), colours, families, "plaster_white") == ("plaster_cream", 1)


def test_nearest_strip_uses_the_squared_distance(families: dict[str, str]) -> None:
    # Squared: 18 against 16, so the second; the largest channel difference (3 against 4) would pick the first.
    colours = {("plaster_cream", 0): (103, 103, 100), ("plaster_white", 0): (104, 100, 100)}
    assert nearest_strip((100, 100, 100), colours, families, "plaster_cream") == ("plaster_white", 0)
    colours = {("plaster_cream", 0): (105, 100, 100), ("plaster_white", 0): (103, 103, 103)}
    # Squared: 25 against 27; absolute: 5 against 9.
    assert nearest_strip((100, 100, 100), colours, families, "plaster_cream") == ("plaster_cream", 0)


def _tint_atlas(layout: TrimLayout, families: dict[str, str], colours: dict[tuple[str, int], tuple[float, float, float]] | None = None) -> TintAtlas:
    if colours is None:
        colours = {key: (float(k), 0.0, 0.0) for k, key in enumerate(sorted((w, t) for w, s in layout.tones.items() for t in range(len(s))))}
    names = (f"facade_trim_{STYLE}", f"scenic_trim_{STYLE}")
    return TintAtlas(STYLE, layout, families, colours, frozenset([*names, *(f"party_{n}" for n in names)]))


def _strip_mesh(vs: list[float]) -> tuple[Mesh, np.ndarray]:
    """One separate triangle per three V values."""
    count = len(vs)
    vertices = np.column_stack([np.arange(count, dtype=np.float64), np.zeros(count), np.arange(count, dtype=np.float64) % 3])
    triangles = np.arange(count, dtype=np.int64).reshape(-1, 3)
    uvs = np.column_stack([np.linspace(0.1, 0.9, count), vs]).astype(np.float32)
    return Mesh(vertices, triangles), uvs


def test_retint_moves_only_the_source_strip_and_keeps_its_fraction(layout: TrimLayout, families: dict[str, str]) -> None:
    source = layout.tones["plaster_cream"][1]
    target = layout.tones["plaster_white"][2]
    mid = source.v0 + 0.25 * (source.v1 - source.v0)
    zocalo, cornice, stone = layout.zocalo, layout.cornice, layout.stone
    cell = next(iter(layout.cells.values()))
    other = layout.tones["plaster_ochre"][0]
    wall_vs = [source.v0, mid, source.v1]
    keep_vs = [zocalo.v0, zocalo.v1, cornice.v0, cornice.v1, stone.v0, stone.v1, cell.v0, cell.v1, other.v1]
    mesh, uvs = _strip_mesh(wall_vs + keep_vs)
    scenic_mesh, scenic_uvs = _strip_mesh(wall_vs)
    node = Node("b", (
        Primitive(scenic_mesh, "roof_tile", scenic_uvs),
        Primitive(mesh, f"facade_trim_{STYLE}", uvs),
        Primitive(scenic_mesh, f"party_scenic_trim_{STYLE}", scenic_uvs),
    ), {"type": "building"})
    atlas = _tint_atlas(layout, families)
    assert current_strip(node, atlas) == ("plaster_cream", 1)
    out = retint(node, atlas, ("plaster_cream", 1), ("plaster_white", 2))
    moved = out.primitives[1].uvs
    assert moved is not None
    assert moved.dtype == np.float32
    expected = np.array([target.v0, target.v0 + 0.25 * (target.v1 - target.v0), target.v1], dtype=np.float32)
    assert np.allclose(moved[:3, 1], expected, atol=1e-6)
    assert moved[3:].tobytes() == uvs[3:].tobytes()
    assert moved[:, 0].tobytes() == uvs[:, 0].tobytes()
    assert out.primitives[1].mesh is mesh
    assert out.primitives[1].material == f"facade_trim_{STYLE}"
    assert np.allclose(out.primitives[2].uvs[:, 1], expected, atol=1e-6)
    assert out.primitives[0].uvs.tobytes() == scenic_uvs.tobytes()
    assert scenic_uvs[0, 1] == np.float32(source.v0)
    assert out.extras == node.extras and out.name == "b"


def test_retint_counts_half_a_pixel_past_the_strip_edges(layout: TrimLayout, families: dict[str, str]) -> None:
    source = layout.tones["plaster_cream"][1]
    pixel = 1.0 / layout.height
    inside = [source.v0 - 0.5 * pixel, source.v1 + 0.5 * pixel, source.v0]
    outside = [source.v0 - 0.6 * pixel, source.v1 + 0.6 * pixel, source.v1 + 0.7 * pixel]
    mesh, uvs = _strip_mesh(inside + outside)
    node = Node("b", (Primitive(mesh, f"facade_trim_{STYLE}", uvs),))
    out = retint(node, _tint_atlas(layout, families), ("plaster_cream", 1), ("plaster_white", 0))
    assert out.primitives[0].uvs is not None
    assert not np.allclose(out.primitives[0].uvs[:3, 1], uvs[:3, 1])
    assert out.primitives[0].uvs[3:].tobytes() == uvs[3:].tobytes()


def test_retint_refuses_a_triangle_across_the_strip_edge(layout: TrimLayout, families: dict[str, str]) -> None:
    source = layout.tones["plaster_cream"][1]
    mesh, uvs = _strip_mesh([source.v0, source.v1, layout.zocalo.v0])
    node = Node("b", (Primitive(mesh, f"scenic_trim_{STYLE}", uvs),))
    with pytest.raises(PanoramaxError, match="^b: a scenic_trim_mediterranean triangle straddles the plaster_cream strip$"):
        retint(node, _tint_atlas(layout, families), ("plaster_cream", 1), ("plaster_white", 0))


def test_current_strip_counts_vertices_and_breaks_ties_by_key(layout: TrimLayout, families: dict[str, str]) -> None:
    cream, white = layout.tones["plaster_cream"][1], layout.tones["plaster_white"][0]
    mesh, uvs = _strip_mesh([white.v0, white.v0, white.v1, cream.v0, cream.v1, cream.v1])
    atlas = _tint_atlas(layout, families)
    assert current_strip(Node("b", (Primitive(mesh, f"facade_trim_{STYLE}", uvs),)), atlas) == ("plaster_cream", 1)
    mesh, uvs = _strip_mesh([white.v0, white.v0, white.v1, cream.v0, cream.v1, cream.v1, white.v1, white.v1, white.v1])
    assert current_strip(Node("b", (Primitive(mesh, f"facade_trim_{STYLE}", uvs),)), atlas) == ("plaster_white", 0)
    assert current_strip(Node("b", (Primitive(mesh, "plaster_white", uvs),)), atlas) is None
    # Counts add across primitives, and a primitive outside the trim materials is skipped, not the end.
    first, first_uvs = _strip_mesh([white.v0, white.v0, white.v1, cream.v0, cream.v1, cream.v1])
    second, second_uvs = _strip_mesh([cream.v0, cream.v0, cream.v1])
    assert current_strip(Node("b", (
        Primitive(first, "roof_tile", first_uvs),
        Primitive(first, f"facade_trim_{STYLE}", first_uvs),
        Primitive(second, f"party_facade_trim_{STYLE}", second_uvs),
    )), atlas) == ("plaster_cream", 1)
    # Each primitive adds to the strips' counts: 4 + 1 white against 1 + 2 cream.
    first, first_uvs = _strip_mesh([white.v0, white.v0, white.v1, white.v1, cream.v0, layout.stone.v0])
    second, second_uvs = _strip_mesh([cream.v0, cream.v1, white.v0])
    assert current_strip(Node("b", (
        Primitive(first, f"facade_trim_{STYLE}", first_uvs),
        Primitive(second, f"scenic_trim_{STYLE}", second_uvs),
    )), atlas) == ("plaster_white", 0)
    stone, stone_uvs = _strip_mesh([layout.stone.v0, layout.stone.v0, layout.stone.v1])
    assert current_strip(Node("b", (Primitive(stone, f"facade_trim_{STYLE}", stone_uvs),)), atlas) is None
    assert current_strip(Node("b", (Primitive(mesh, f"facade_trim_{STYLE}", None),)), atlas) is None


def _photographed(layout: TrimLayout, wall: str, tone: int, extra: tuple[Primitive, ...] = ()) -> Node:
    strip = layout.tones[wall][tone]
    mesh, uvs = _strip_mesh([strip.v0, (strip.v0 + strip.v1) / 2, strip.v1])
    return Node("building/1", (Primitive(mesh, f"facade_trim_{STYLE}", uvs), *extra), {"type": "building"})


def test_tint_node_records_an_observed_panoramax_colour(layout: TrimLayout, families: dict[str, str]) -> None:
    colours = {key: (0.0, 0.0, 0.0) for key in [(w, t) for w, s in layout.tones.items() for t in range(len(s))]}
    colours[("plaster_ochre", 2)] = (200.0, 100.0, 50.0)
    node = _photographed(layout, "plaster_cream", 0)
    decal = _box(0, 0, 1, 1)
    patches = [_patch("building/1", 0, 0.0, "pic-b", decal, (200, 100, 50)), _patch("building/1", 1, 0.0, "pic-a", decal, (200, 100, 50))]
    out, record = tint_node(node, patches, _tint_atlas(layout, families, colours))
    assert record == {
        "rgb": [200, 100, 50],
        "from": {"wall": "plaster_cream", "tone": 0},
        "to": {"wall": "plaster_ochre", "tone": 2},
        "picture_ids": ["pic-a", "pic-b"],
        "authors": ["author-pic-a", "author-pic-b"],
        "origin": "observed",
        "source": "panoramax",
    }
    target = layout.tones["plaster_ochre"][2]
    assert out.primitives[0].uvs is not None
    assert out.primitives[0].uvs[0, 1] == pytest.approx(target.v0)


def test_tint_node_skips(layout: TrimLayout, families: dict[str, str]) -> None:
    patches = [_patch("building/1", 0, 0.0, "p", _box(0, 0, 1, 1), (0, 0, 0))]
    atlas = _tint_atlas(layout, families, {key: (0.0, 0.0, 0.0) if key == ("plaster_cream", 0) else (255.0, 255.0, 255.0) for key in [(w, t) for w, s in layout.tones.items() for t in range(len(s))]})
    node = _photographed(layout, "plaster_cream", 0)
    for reason in ("no_trim_atlas", "layout_mismatch"):
        assert tint_node(node, patches, reason) == (node, {"reason": reason})
    tinted = _photographed(layout, "plaster_cream", 0, (Primitive(_box(0, 0, 1, 1), "plaster_white__tint_ab12"),))
    assert tint_node(tinted, patches, atlas) == (tinted, {"reason": "osm_colour"})
    bare = Node("building/1", (Primitive(_box(0, 0, 1, 1), "roof_tile"),))
    assert tint_node(bare, patches, atlas) == (bare, {"reason": "no_wall_strip"})
    assert tint_node(node, patches, atlas) == (node, {"reason": "unchanged"})


def test_tint_atlas_for_reads_the_materials_scene(layout: TrimLayout) -> None:
    table = load_style_table()
    rgb, colours = _solid_atlas(layout)
    buffer = io.BytesIO()
    Image.fromarray(rgb).convert("RGBA").save(buffer, format="PNG")
    images = {f"facade_trim_{STYLE}": buffer.getvalue()}
    facades = {"mode": "trim", "style": STYLE, "style_table_sha256": table.sha256}
    atlas = tint_atlas_for(facades, images)
    assert isinstance(atlas, TintAtlas)
    assert atlas.style == STYLE
    assert atlas.colours[("brick_red", 1)] == pytest.approx(colours[("brick_red", 1)])
    assert atlas.families["brick_red"] == "brick"
    assert atlas.materials == {
        "facade_trim_mediterranean", "scenic_trim_mediterranean",
        "party_facade_trim_mediterranean", "party_scenic_trim_mediterranean",
    }
    assert tint_atlas_for(None, images) == "no_trim_atlas"
    assert tint_atlas_for({**facades, "mode": "legacy"}, images) == "no_trim_atlas"
    assert tint_atlas_for(facades, {}) == "no_trim_atlas"
    assert tint_atlas_for({**facades, "style": "generic"}, {"facade_trim_generic": images[f"facade_trim_{STYLE}"]}) == "no_trim_atlas"
    assert tint_atlas_for(facades, {f"facade_trim_{STYLE}": _png(2048, 2048)}) == "layout_mismatch"
    assert tint_atlas_for(facades, {f"facade_trim_{STYLE}": _png(4096, 4096)}) == "layout_mismatch"
    with pytest.raises(PanoramaxError, match="^the materials scene was built with another facade style table; rerun materials$"):
        tint_atlas_for({**facades, "style_table_sha256": "0" * 64}, images)
    assert tint_atlas_for({key: value for key, value in facades.items() if key != "style"}, {"facade_trim_None": images[f"facade_trim_{STYLE}"]}) == "no_trim_atlas"


def test_tint_atlas_for_reads_a_grey_atlas_as_rgb(layout: TrimLayout) -> None:
    grey = np.zeros((layout.height, layout.size), dtype=np.uint8)
    strip = layout.tones["brick_red"][0]
    grey[strip.row0 : strip.row1] = 90
    buffer = io.BytesIO()
    Image.fromarray(grey).save(buffer, format="PNG")
    facades = {"mode": "trim", "style": STYLE, "style_table_sha256": load_style_table().sha256}
    atlas = tint_atlas_for(facades, {f"facade_trim_{STYLE}": buffer.getvalue()})
    assert isinstance(atlas, TintAtlas)
    assert atlas.colours[("brick_red", 0)] == pytest.approx((90.0, 90.0, 90.0))
    assert atlas.colours[("brick_red", 1)] == pytest.approx((0.0, 0.0, 0.0))


def test_a_legacy_scene_from_another_table_is_not_refused() -> None:
    assert tint_atlas_for({"mode": "legacy", "style_table_sha256": "0" * 64}, {}) == "no_trim_atlas"


# --- run_apply ---


def _map_with(tmp_path: Path, **values: object) -> Path:
    from onward_generator.maps.workspace import create_map
    from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON

    root = tmp_path / "murcia"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=100, panoramax=True)
    document = json.loads((root / "map.json").read_text(encoding="utf-8"))
    document.update(values)
    (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    return root


class _Offline:
    def get_json(self, url: str) -> dict[str, object]:
        raise AssertionError("no search with a bad switch")

    def get_bytes(self, url: str) -> bytes:
        raise AssertionError("no download with a bad switch")


@pytest.mark.parametrize("value", ["yes", 1, {}])
def test_run_apply_refuses_a_switch_that_is_not_a_bool(tmp_path: Path, capsys: pytest.CaptureFixture[str], value: object) -> None:
    from onward_generator.panoramax.__main__ import run_apply

    root = _map_with(tmp_path, panoramax_atlas=value)
    base = root / "data" / "processed" / "scene_materials"
    out = root / "data" / "processed" / "scene_panoramax"
    assert run_apply(base, out, client=_Offline()) == 1
    assert capsys.readouterr().err.startswith('panoramax: map.json "panoramax_atlas" must be true or false')
    assert not out.exists()


def test_run_apply_stays_off_without_panoramax_even_with_the_atlas_switch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator.panoramax.__main__ import run_apply

    root = _map_with(tmp_path, panoramax=False, panoramax_atlas=True)
    out = root / "data" / "processed" / "scene_panoramax"
    assert run_apply(root / "data" / "processed" / "scene_materials", out, client=_Offline()) == 0
    assert capsys.readouterr().out == "panoramax off\n"
    assert not out.exists()


def test_run_apply_passes_the_switch_and_prints_coverage(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.panoramax.__main__ import run_apply

    root = _map_with(tmp_path, panoramax_atlas=True)
    card_path = root / "config" / "export.json"
    card = json.loads(card_path.read_text(encoding="utf-8"))
    card["build"]["combine_cell_m"] = 50
    card_path.write_text(json.dumps(card), encoding="utf-8")
    calls: list[dict[str, object]] = []
    written: dict[str, object] = {"assignments": [1, 2], "coverage": {"percent": 12.5}, "draws_added": 3}

    class _Cached:
        kept = 4

    monkeypatch.setattr("onward_generator.panoramax.__main__.cache_pictures", lambda root, client: _Cached())

    def fake_apply(base: Path, out: Path, **options: object) -> dict[str, object]:
        calls.append(options)
        return written

    monkeypatch.setattr("onward_generator.panoramax.__main__.apply_pictures", fake_apply)
    base = root / "data" / "processed" / "scene_materials"
    out = root / "data" / "processed" / "scene_panoramax"
    assert run_apply(base, out, overwrite=True, client=_Offline()) == 0
    assert calls == [{"overwrite": True, "atlas": True, "cell_m": 50.0}]
    assert capsys.readouterr().out.splitlines()[0] == "panoramax: kept=4 assignments=2 coverage=12.5% draws_added=3"
    written["coverage"] = {"percent": None}
    assert run_apply(base, out, client=_Offline()) == 0
    assert calls[-1] == {"overwrite": False, "atlas": True, "cell_m": 50.0}
    assert capsys.readouterr().out.splitlines()[0] == "panoramax: kept=4 assignments=2 coverage=n/a draws_added=3"
    del written["coverage"]
    assert run_apply(base, out, client=_Offline()) == 0
    assert capsys.readouterr().out.splitlines()[0] == "panoramax: kept=4 assignments=2"


def test_rows_keeps_the_dict_rows_and_refuses_another_type() -> None:
    assert _rows([{"a": 1}, "x", {"b": 2}]) == [{"a": 1}, {"b": 2}]
    with pytest.raises(TypeError, match="^match_facades gave str, not a list$"):
        _rows("x")
