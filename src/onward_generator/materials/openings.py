"""Project-generated window albedo. One quad maps to 0–1, so the frame sits on the pane.

This is not a photograph and not an Onward texture.
"""

from __future__ import annotations

import numpy as np

from onward_generator.dress.atlas import encode_png_rgba

# Dark fill only. Frame and mullions stay opaque. PP_Sprite_Lit multiplies this by vertex alpha.
GLASS_ALPHA = 0.35


def window_rgba(size: int = 256) -> np.ndarray:
    image = np.zeros((size, size, 4), dtype=np.uint8)
    image[..., 0] = 28
    image[..., 1] = 34
    image[..., 2] = 42
    image[..., 3] = int(round(GLASS_ALPHA * 255))
    border = max(size // 10, 4)
    frame = np.array([176, 168, 154, 255], dtype=np.uint8)
    image[:border, :] = frame
    image[-border:, :] = frame
    image[:, :border] = frame
    image[:, -border:] = frame
    mullion = max(size // 28, 2)
    centre = size // 2
    image[centre - mullion : centre + mullion, border:-border] = frame
    image[border:-border, centre - mullion : centre + mullion] = frame
    return image


def window_png(size: int = 256) -> bytes:
    return encode_png_rgba(window_rgba(size))


def boarded_rgba(size: int = 256) -> np.ndarray:
    """Plywood sheets over the same frame. Project-generated, not a photograph."""
    image = window_rgba(size)
    board = np.array([92, 74, 48, 255], dtype=np.uint8)
    gap = np.array([48, 40, 32, 255], dtype=np.uint8)
    border = max(size // 10, 4)
    image[border:-border, border:-border] = board
    seam = max(size // 18, 3)
    for row in range(border, size - border, seam * 3):
        image[row : row + seam, border:-border] = gap
    return image


def boarded_png(size: int = 256) -> bytes:
    return encode_png_rgba(boarded_rgba(size))
