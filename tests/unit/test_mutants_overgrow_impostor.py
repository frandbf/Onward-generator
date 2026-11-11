"""Flat impostors (overgrow/impostor.py) pixel by pixel: the outline's tolerance ladder, the top view and the polygon."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image
from shapely import MultiPoint
from shapely.geometry.polygon import orient

from onward_generator.generation.mesh import Mesh
from onward_generator.overgrow.catalog import AssetSpec
from onward_generator.overgrow.impostor import flat_impostor, outline, top_view
from onward_generator.overgrow.library import Prop, PropPart

COVER = AssetSpec("cover", "barriers", "manhole", ("sidewalk",), "flat", "a cover")


def _png(pixels: np.ndarray) -> bytes:
    """A PNG whose mode follows the array: 2-D is greyscale, 4 channels RGBA."""
    out = io.BytesIO()
    Image.fromarray(pixels.astype(np.uint8)).save(out, format="PNG")
    return out.getvalue()


def _flat(colour: tuple[int, ...], size: int = 2) -> bytes:
    return _png(np.tile(np.array(colour, dtype=np.uint8), (size, size, 1)))


def _texture(h: int, w: int, mode: str = "RGB") -> np.ndarray:
    """Every texel its own colour: row and column can be read back from it."""
    rows, cols = np.mgrid[0:h, 0:w]
    channels = [rows * 40 + 5, cols * 30 + 7, (rows * w + cols) * 3 + 11]
    if mode == "RGBA":
        channels.append(np.full_like(rows, 255))
    return np.stack(channels, axis=-1)


# --- outline -------------------------------------------------------------------------------------------------------


def test_a_plan_without_an_area_says_so_exactly() -> None:
    with pytest.raises(ValueError, match="^a flat impostor needs a plan with an area$"):
        outline(np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]]))


def _ladder(xy: np.ndarray, sides: int):
    """The contract: the first tolerance of 0.2 % of the larger side, times 1.5 each step, that leaves ``sides`` corners."""
    hull = MultiPoint([tuple(p) for p in xy]).convex_hull
    x0, y0, x1, y1 = hull.bounds
    tolerance, shape = max(x1 - x0, y1 - y0) * 0.002, hull
    while len(shape.exterior.coords) - 1 > sides:
        shape = hull.simplify(tolerance, preserve_topology=True)
        tolerance *= 1.5
    return orient(shape, 1.0)


@pytest.mark.parametrize("centre", [(0.0, 0.0), (100.0, 200.0), (-40.0, 7.0)])
def test_the_outline_takes_the_first_tolerance_of_the_ladder_from_the_larger_side(centre: tuple[float, float]) -> None:
    angles = np.linspace(0, 2 * np.pi, 300, endpoint=False)
    ellipse = np.column_stack([centre[0] + 2.0 * np.cos(angles), centre[1] + 1.0 * np.sin(angles)])
    for sides in range(3, 17):
        shape = outline(ellipse, sides)
        assert list(shape.exterior.coords) == list(_ladder(ellipse, sides).exterior.coords), sides
        assert len(shape.exterior.coords) - 1 <= sides and shape.exterior.is_ccw


def test_a_square_cut_to_a_triangle_keeps_a_polygon() -> None:
    square = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    shape = outline(square, 3)
    assert shape.area > 0 and len(shape.exterior.coords) - 1 == 3
    assert list(shape.exterior.coords) == list(_ladder(square, 3).exterior.coords)


# --- top view: an independent per-pixel oracle -----------------------------------------------------------------------


def _oracle(prop: Prop, px: int) -> np.ndarray:
    """For each pixel centre, the albedo of the highest triangle over it, by solving its barycentric coordinates."""
    v = prop.mesh.vertices
    x0, y0, x1, y1 = v[:, 0].min(), v[:, 1].min(), v[:, 0].max(), v[:, 1].max()
    image = np.zeros((px, px, 3))
    depth = np.full((px, px), -np.inf)
    nearest = np.inf
    for primitive, part in zip(prop.primitives(), prop._parts(), strict=True):
        albedo = np.asarray(Image.open(io.BytesIO(part.albedo)).convert("RGB"), dtype=np.float64)
        h, w = albedo.shape[:2]
        pv, uvs = primitive.mesh.vertices, primitive.uvs
        assert uvs is not None
        for tri in primitive.mesh.triangles:
            p = np.column_stack([(pv[tri, 0] - x0) / (x1 - x0) * px, (y1 - pv[tri, 1]) / (y1 - y0) * px])
            m = np.array([[p[0, 0] - p[2, 0], p[1, 0] - p[2, 0]], [p[0, 1] - p[2, 1], p[1, 1] - p[2, 1]]])
            if abs(np.linalg.det(m)) < 1e-9:
                continue
            for r in range(px):
                for c in range(px):
                    l0, l1 = np.linalg.solve(m, [c + 0.5 - p[2, 0], r + 0.5 - p[2, 1]])
                    weights = np.array([l0, l1, 1.0 - l0 - l1])
                    if (weights > -1e-6).all():
                        nearest = min(nearest, float(np.abs(weights).min()))
                    if (weights < 0).any():
                        continue
                    z = float(weights @ pv[tri, 2])
                    if z <= depth[r, c]:
                        continue
                    depth[r, c] = z
                    u, vv = (weights @ uvs[tri, 0]) % 1.0, (weights @ uvs[tri, 1]) % 1.0
                    image[r, c] = albedo[int((1.0 - vv) * (h - 1)), int(u * (w - 1))]
    assert nearest > 1e-6  # no pixel centre sits on a triangle's edge: the oracle and the rasteriser cannot round apart
    covered = np.isfinite(depth)
    image[~covered] = image[covered].mean(axis=0)
    return image


def _scene() -> Prop:
    """Two parts over off-origin, non-square bounds: a sloped base, a crossing slope, a speck, and two skipped rows."""
    px = 16
    x0, y0, width, height = -3.7, 10.3, 5.8, 3.6

    def at(sx: float, sy: float, z: float) -> list[float]:  # pixel position to local metres
        return [x0 + sx / px * width, y0 + height - sy / px * height, z]

    vertices = np.array(
        [
            # part 0: a degenerate row and a sliver over no pixel centre come first, then the sloped base triangle.
            at(1.1, 1.1, 1.0),
            at(2.1, 2.1, 1.0),
            at(3.1, 3.1, 1.0),
            at(5.1, 5.1, 1.0),
            at(5.4, 5.15, 1.0),
            at(5.2, 5.4, 1.0),
            at(0.0, 0.0, 0.7),
            at(16.0, 2.3, 1.9),
            at(3.3, 16.0, 1.1),
            # part 1: a slope crossing the base, a speck above it all, and a corner that sets the bounds.
            at(2.37, 3.13, 0.2),
            at(14.71, 6.29, 2.6),
            at(6.83, 13.57, 1.4),
            at(10.21, 8.23, 5.0),
            at(10.83, 8.31, 5.0),
            at(10.41, 8.97, 5.0),
            at(16.0, 16.0, 0.1),
            at(15.2, 16.0, 0.1),
            at(16.0, 14.9, 0.1),
        ]
    )
    triangles = np.arange(18).reshape(6, 3)
    uvs = np.array(
        [
            [0.1, 0.2],
            [0.3, 0.4],
            [0.5, 0.6],
            [0.2, 0.2],
            [0.3, 0.2],
            [0.2, 0.3],
            [-0.23, 1.17],
            [1.61, 0.31],
            [0.47, -0.73],
            [0.13, 0.91],
            [2.77, 0.07],
            [0.59, 1.43],
            [0.33, 0.62],
            [0.36, 0.64],
            [0.34, 0.69],
            [0.9, 0.1],
            [0.8, 0.1],
            [0.9, 0.2],
        ],
        dtype=np.float32,
    )
    parts = (PropPart(3, _png(_texture(5, 7))), PropPart(6, _png(_texture(4, 6, "RGBA"))))
    return Prop(COVER, Mesh(vertices, triangles), uvs, b"", parts=parts)


def test_the_top_view_is_the_albedo_of_the_highest_triangle_at_each_pixel_centre() -> None:
    prop = _scene()
    image, bounds = top_view(prop, 16)
    assert image.shape == (16, 16, 3) and image.dtype == np.float32
    assert bounds == pytest.approx((-3.7, 10.3, 5.8, 3.6))
    expected = _oracle(prop, 16)
    assert np.isfinite(image).all()
    assert np.array_equal(image, expected.astype(np.float32))
    # The scene exercises what it claims: a speck pixel, both parts on top somewhere, and some pixels left to the mean.
    assert len({tuple(p) for p in expected.reshape(-1, 3)}) > 20


def _edges_prop() -> Prop:
    """8 m square at 8 px, so a pixel is a metre: a red floor, a blue triangle whose edges cross pixel centres, green slivers."""

    def at(sx: float, sy: float, z: float) -> list[float]:
        return [sx, 8.0 - sy, z]

    vertices = np.array(
        [
            at(0, 0, 0.0),
            at(8, 0, 0.0),
            at(8, 8, 0.0),
            at(0, 8, 0.0),  # floor
            at(1.5, 1.5, 1.0),
            at(5.5, 1.5, 1.0),
            at(1.5, 5.5, 1.0),  # its three edges run through pixel centres
            at(7.2, 0, 2.0),
            at(8, 0, 2.0),
            at(8, 8, 2.0),  # a sliver inside the last column
            at(0, 7.2, 2.0),
            at(0, 8, 2.0),
            at(8, 8, 2.0),  # a sliver inside the last row
        ],
        dtype=np.float64,
    )
    triangles = np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [7, 8, 9], [10, 11, 12]])
    uvs = np.full((len(vertices), 2), 0.5, dtype=np.float32)
    parts = (PropPart(2, _flat((200, 0, 0))), PropPart(3, _flat((0, 0, 200))), PropPart(5, _flat((0, 150, 0))))
    return Prop(COVER, Mesh(vertices, triangles), uvs, b"", parts=parts)


def test_pixel_centres_on_an_edge_are_inside_and_the_last_row_and_column_are_drawn() -> None:
    image, bounds = top_view(_edges_prop(), 8)
    assert bounds == (0.0, 0.0, 8.0, 8.0)
    red, blue, green = (200, 0, 0), (0, 0, 200), (0, 150, 0)
    for r in range(8):
        for c in range(8):
            if (c == 7 and r <= 2) or (r == 7 and c <= 2):
                want = green
            elif c >= 1 and r >= 1 and c + r <= 6:
                want = blue
            else:
                want = red
            assert tuple(image[r, c]) == want, (r, c)


def test_at_equal_height_the_triangle_drawn_first_keeps_the_pixel() -> None:
    square = [[0.0, 0.0, 0.5], [2.0, 0.0, 0.5], [2.0, 2.0, 0.5], [0.0, 2.0, 0.5]]
    mesh = Mesh(np.array(square * 2), np.array([[0, 1, 2], [0, 2, 3], [4, 5, 6], [4, 6, 7]]))
    uvs = np.full((8, 2), 0.5, dtype=np.float32)
    parts = (PropPart(2, _flat((200, 0, 0))), PropPart(4, _flat((0, 0, 200))))
    image, _ = top_view(Prop(COVER, mesh, uvs, b"", parts=parts), 4)
    assert {tuple(p) for p in image.reshape(-1, 3)} == {(200.0, 0.0, 0.0)}


def test_pixels_off_the_mesh_take_the_mean_colour_of_the_covered_ones() -> None:
    vertices = np.array([[0.0, 0.0, 0.1], [4.0, 0.0, 0.1], [0.0, 4.0, 0.1], [4.0, 4.0, 0.0]])
    triangles = np.array([[0, 1, 2], [3, 3, 3]])  # the second row only stretches the bounds
    uvs = np.array([[0.0, 0.5], [0.99, 0.5], [0.0, 0.5], [0.5, 0.5]], dtype=np.float32)
    albedo = _png(np.array([[[250, 10, 30], [10, 250, 90]]]))
    image, _ = top_view(Prop(COVER, Mesh(vertices, triangles), uvs, albedo), 4)
    # Rows run north to south: the triangle is the south-west half, its diagonal through the pixel centres included.
    covered = np.array([[c <= r for c in range(4)] for r in range(4)])
    assert not covered.all() and covered.any()
    mean = image[covered].mean(axis=0)
    assert not np.allclose(mean, mean[0])  # a colour, not a grey
    assert np.allclose(image[~covered], mean)
    assert {tuple(p) for p in image[covered]} <= {(250.0, 10.0, 30.0), (10.0, 250.0, 90.0)}


def test_a_degenerate_plan_and_a_small_prop_keep_their_real_bounds() -> None:
    small = Mesh(np.array([[0.25, 3.0, 0.0], [0.75, 3.0, 0.0], [0.25, 3.25, 0.1]]), np.array([[0, 1, 2]]))
    uvs = np.zeros((3, 2), dtype=np.float32)
    assert top_view(Prop(COVER, small, uvs, _flat((9, 9, 9))), 4)[1] == pytest.approx((0.25, 3.0, 0.5, 0.25))
    wall = Mesh(np.array([[2.0, 3.0, 0.0], [2.0, 3.0, 0.0], [2.0, 3.0, 1.0]]), np.array([[0, 1, 2]]))
    image, bounds = top_view(Prop(COVER, wall, uvs, _flat((9, 9, 9))), 4)
    assert bounds == (2.0, 3.0, 1e-6, 1e-6)
    assert not image.any()  # nothing seen from above, nothing to average


def test_a_greyscale_albedo_reads_as_rgb() -> None:
    mesh = Mesh(np.array([[0.0, 0.0, 0.1], [1.0, 0.0, 0.1], [1.0, 1.0, 0.1], [0.0, 1.0, 0.1]]), np.array([[0, 1, 2], [0, 2, 3]]))
    uvs = np.full((4, 2), 0.5, dtype=np.float32)
    image, _ = top_view(Prop(COVER, mesh, uvs, _png(np.full((2, 2), 77))), 4)
    assert (image == 77).all()


# --- the polygon -----------------------------------------------------------------------------------------------------


def _slab(albedo: bytes, parts: tuple[PropPart, ...] = ()) -> Prop:
    """A 0.8 x 0.4 m slab, 4 cm thick, off the origin: its outline is its four corners."""
    xs, ys = (0.2, 1.0), (0.1, 0.5)
    corners = [(x, y) for x in xs for y in ys]
    vertices = np.array([(x, y, z) for z in (0.0, 0.04) for x, y in corners])
    triangles = np.array([[4, 6, 7], [4, 7, 5], [0, 1, 3], [0, 3, 2]])
    uvs = np.column_stack([(vertices[:, 0] - 0.2) / 0.8, (vertices[:, 1] - 0.1) / 0.4]).astype(np.float32)
    return Prop(COVER, Mesh(vertices, triangles), uvs, albedo, parts=parts, source_triangles=0)


def test_the_impostor_is_a_fan_on_top_and_a_rim_to_the_ground_with_v_up_uvs() -> None:
    prop = _slab(_flat((0, 0, 0)))
    flat = flat_impostor(prop, px=24)
    ring = np.array(outline(prop.mesh.vertices[:, :2]).exterior.coords[:-1])
    assert len(ring) == 4
    assert np.allclose(
        flat.mesh.vertices,
        np.vstack([[0.6, 0.3, 0.04], np.column_stack([ring, np.full(4, 0.04)]), np.column_stack([ring, np.zeros(4)])]),
        rtol=0,
        atol=1e-12,
    )
    assert flat.mesh.triangles.tolist() == [
        [0, 1, 2],
        [0, 2, 3],
        [0, 3, 4],
        [0, 4, 1],
        [1, 5, 6],
        [1, 6, 2],
        [2, 6, 7],
        [2, 7, 3],
        [3, 7, 8],
        [3, 8, 4],
        [4, 8, 5],
        [4, 5, 1],
    ]
    assert flat.uvs.dtype == np.float32
    want = np.column_stack([(flat.mesh.vertices[:, 0] - 0.2) / 0.8, (flat.mesh.vertices[:, 1] - 0.1) / 0.4])
    assert np.allclose(flat.uvs, want, atol=1e-6)
    top = Image.open(io.BytesIO(flat.albedo)).convert("RGB")
    assert top.size == (24, 24)
    assert set(top.getdata()) == {(0, 0, 0)}  # black stays black
    assert flat.parts == () and flat.source_triangles == 4


def test_the_impostor_of_a_prop_with_parts_is_one_albedo_with_the_requested_sides() -> None:
    angles = np.linspace(0, 2 * np.pi, 40, endpoint=False)
    rim = np.column_stack([0.3 * np.cos(angles), 0.3 * np.sin(angles), np.full(40, 0.03)])
    vertices = np.vstack([[0.0, 0.0, 0.03], rim])
    triangles = np.array([(0, 1 + k, 1 + (k + 1) % 40) for k in range(40)])
    uvs = np.column_stack([vertices[:, 0] + 0.5, vertices[:, 1] + 0.5]).astype(np.float32)
    parts = (PropPart(20, _flat((10, 200, 10))), PropPart(40, _flat((10, 10, 200))))
    prop = Prop(COVER, Mesh(vertices, triangles), uvs, b"", parts=parts, source_triangles=900)
    flat = flat_impostor(prop, sides=5, px=8)
    corners = len(outline(vertices[:, :2], 5).exterior.coords) - 1
    assert corners <= 5 and flat.triangles == 3 * corners and flat.parts == () and flat.source_triangles == 900
    assert Image.open(io.BytesIO(flat.albedo)).size == (8, 8)
    assert len(flat.images()) == 1
