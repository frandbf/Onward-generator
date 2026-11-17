"""Procedural cloth: the awning and parasol meshes and their striped image (overgrow/cloth.py; plan 2026-11-16, increment 3)."""

from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from onward_generator.overgrow.cloth import (
    BANDS,
    CREAM,
    FABRIC_MATERIAL,
    POLE_BAND,
    POLE_GREY,
    STRIPE_COLOURS,
    STRIPE_REPEAT_M,
    awning_mesh,
    band_v,
    fabric_png,
    parasol_mesh,
    two_sided,
)


def test_the_fabric_image_has_three_striped_bands_and_a_grey_one() -> None:
    image = Image.open(io.BytesIO(fabric_png())).convert("RGB")
    assert image.size == (256, 256) and (FABRIC_MATERIAL, BANDS, POLE_BAND, STRIPE_REPEAT_M) == ("awning_fabric", 4, 3, 1.0)
    for band, colour in enumerate(STRIPE_COLOURS):
        top, bottom = band * 64, band * 64 + 63
        assert image.getpixel((0, top)) == colour and image.getpixel((15, bottom)) == colour
        assert image.getpixel((16, top)) == CREAM and image.getpixel((31, bottom)) == CREAM
        assert image.getpixel((32, top + 30)) == colour and image.getpixel((255, top + 30)) == CREAM  # eight stripe pairs across
    assert image.getpixel((0, 192)) == POLE_GREY and image.getpixel((255, 255)) == POLE_GREY and image.getpixel((100, 220)) == POLE_GREY


def test_a_band_s_v_keeps_off_its_edges() -> None:
    assert band_v(0, 0.0) == pytest.approx(0.075) and band_v(0, 1.0) == pytest.approx(0.175)
    assert band_v(2, 0.5) == pytest.approx(0.625) and band_v(3, 1.0) == pytest.approx(0.925)


def test_each_face_is_drawn_both_ways() -> None:
    mesh, uvs = two_sided(np.zeros((3, 3)), np.array([[0, 1, 2]]), np.zeros((3, 2)))
    assert mesh.triangles.tolist() == [[0, 1, 2], [2, 1, 0]] and mesh.vertices.dtype == np.float64 and uvs.shape == (3, 2)
    assert mesh.triangles.dtype == np.int64


def test_an_awning_slopes_out_of_the_wall_and_hangs_a_valance() -> None:
    mesh, uvs = awning_mesh(np.array([0.0, 0.0]), np.array([1.0, 0.0]), np.array([0.0, -1.0]), 2.0, 3.0, 0.9, 0.3, 0.2, 1)
    assert np.allclose(mesh.vertices, [[-1, 0, 3], [1, 0, 3], [1, -0.9, 2.7], [-1, -0.9, 2.7], [-1, -0.9, 2.5], [1, -0.9, 2.5]])
    assert mesh.triangles[:6].tolist() == [[0, 1, 2], [0, 2, 3], [3, 2, 5], [3, 5, 4], [0, 3, 4], [1, 5, 2]] and mesh.triangle_count == 12
    assert np.allclose(uvs[:, 0], [0, 2, 2, 0, 0, 2])  # one image width a metre along the awning
    assert np.allclose(uvs[:, 1], [band_v(1, s) for s in (0.0, 0.0, 0.7, 0.7, 1.0, 1.0)])


def test_a_parasol_is_a_cone_of_cloth_on_a_pole() -> None:
    mesh, uvs = parasol_mesh(5.0, -2.0, 0.25, 1.1, 2.15, 2.5, 0.05, 2)
    assert mesh.vertex_count == 1 + 8 + 8 and mesh.triangle_count == 2 * (8 + 8)
    assert np.allclose(mesh.vertices[0], [5.0, -2.0, 2.75])
    rim = mesh.vertices[1:9]
    assert np.allclose(np.hypot(rim[:, 0] - 5.0, rim[:, 1] + 2.0), 1.1) and np.allclose(rim[:, 2], 2.4)
    assert np.allclose(rim[0], [6.1, -2.0, 2.4]) and np.allclose(rim[2], [5.0, -0.9, 2.4])  # a quarter turn every two sides
    pole = mesh.vertices[9:]
    assert np.allclose(pole[0], [4.975, -2.025, 0.25]) and np.allclose(pole[1], [4.975, -2.025, 2.75])
    assert np.allclose(pole[6], [4.975, -1.975, 0.25])
    assert np.allclose(np.ptp(pole[:, :2], axis=0), [0.05, 0.05]) and set(np.round(pole[:, 2], 6)) == {0.25, 2.75}
    assert mesh.triangles[:8].tolist() == [[0, 1 + (k + 1) % 8, 1 + k] for k in range(8)]
    assert mesh.triangles[8:16].tolist() == [[9, 11, 12], [9, 12, 10], [11, 13, 14], [11, 14, 12], [13, 15, 16], [13, 16, 14],
                                             [15, 9, 10], [15, 10, 16]]
    assert np.allclose(uvs[0], [0.5, band_v(2, 0.0)]) and np.allclose(uvs[3], [1.0, band_v(2, 1.0)])
    assert np.allclose(uvs[9:, 1], [band_v(POLE_BAND, s) for s in (0.0, 1.0) * 4]) and np.allclose(uvs[9:, 0], [0, 0, 0.25, 0.25, 0.5, 0.5, 0.75, 0.75])
    six = parasol_mesh(0.0, 0.0, 0.0, 1.0, 2.0, 2.5, 0.05, 0, segments=6)[0]
    assert six.vertex_count == 1 + 6 + 8 and np.allclose(six.vertices[2], [0.5, np.sqrt(0.75), 2.0])
