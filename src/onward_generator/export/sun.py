"""The sun's azimuth across the streets of the playable core (plan E6, ADR-037).

The export does not see the street centrelines, but every street is lined with facades, so the street grid is the
orientation of the walls. The vertical facade and party wall triangles of the visual GLB whose centre lies in the
playable core vote for their normal's heading, modulo 90 degrees (a block's walls are two perpendicular pairs),
weighted by area, in 5-degree bins smoothed with their neighbours. The winning bin is refined by the circular mean of
its votes. The sun then stands 35 degrees off that axis, so neither direction of the grid runs parallel to it and
every street has a side in the sun and a side in the shade. Of the four such azimuths the one nearest the neutral
150 degrees is kept, so the sun stays where it was, only turned to the streets. Nothing is observed: the axis is
estimated from generated walls, and the azimuth is generated.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from onward_generator.export.atmosphere import NEUTRAL_SUN_AZIMUTH_DEG
from onward_generator.export.lightmap import surface_class
from onward_generator.generation.gltf import Primitive, scene_primitives

BIN_DEG = 5.0
PERIOD_DEG = 90.0
OFFSET_DEG = 35.0
# A triangle is a wall when its normal is within about 6 degrees of horizontal.
WALL_NORMAL_Z_MAX = 0.1
WALL_CLASSES = frozenset({"facade", "party"})


@dataclass(frozen=True, slots=True)
class StreetSun:
    axis_deg: float | None
    azimuth_deg: float
    wall_area_m2: float


def wall_votes(primitives: Iterable[Primitive], half_side_m: float | None) -> tuple[np.ndarray, np.ndarray]:
    """Heading of each wall triangle's normal modulo 90 degrees, and its area, for the walls inside the core square."""
    headings: list[np.ndarray] = []
    areas: list[np.ndarray] = []
    for primitive in primitives:
        if surface_class(primitive.material) not in WALL_CLASSES or primitive.material.startswith("prop_"):
            continue
        v = primitive.mesh.vertices[primitive.mesh.triangles]
        cross = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])  # pragma: no mutate: cross(b - a, c - b) equals cross(b - a, c - a)
        length = np.linalg.norm(cross, axis=1)
        keep = length > 0
        if half_side_m is not None:
            centre = v.mean(axis=1)
            keep &= (np.abs(centre[:, 0]) <= half_side_m) & (np.abs(centre[:, 1]) <= half_side_m)
        keep &= np.abs(cross[:, 2]) <= WALL_NORMAL_Z_MAX * length  # pragma: no mutate: a normal exactly on the bound is a measure-zero case
        if not keep.any():
            continue
        normal = cross[keep]
        headings.append(np.degrees(np.arctan2(normal[:, 0], normal[:, 1])) % PERIOD_DEG)
        areas.append(length[keep] * 0.5)
    if not headings:
        return np.zeros(0), np.zeros(0)
    return np.concatenate(headings), np.concatenate(areas)


def street_axis(headings: np.ndarray, areas: np.ndarray) -> float | None:
    """The dominant wall heading in [0, 90), or None without walls."""
    total = float(areas.sum())
    if total <= 0:
        return None
    bins = int(PERIOD_DEG / BIN_DEG)
    index = np.minimum((headings / BIN_DEG).astype(int), bins - 1)
    histogram = np.bincount(index, weights=areas, minlength=bins)
    smoothed = 2 * histogram + np.roll(histogram, 1) + np.roll(histogram, -1)
    best = int(np.argmax(smoothed))
    # The circular mean, on the 90-degree period, of the votes in the winning bin and its two neighbours.
    near = np.isin(index, [(best - 1) % bins, best, (best + 1) % bins])
    turn = np.radians(headings[near]) * (360.0 / PERIOD_DEG)
    mean = math.degrees(math.atan2(float(np.sum(areas[near] * np.sin(turn))), float(np.sum(areas[near] * np.cos(turn)))))
    # Rounded first, so 89.999 is 0.0 and the axis stays in [0, 90).
    return round(mean * PERIOD_DEG / 360.0, 2) % PERIOD_DEG


def _gap(a: float, b: float) -> float:
    turn = (a - b) % 360.0
    return min(turn, 360.0 - turn)


def azimuth_across(axis_deg: float) -> float:
    """Of the four azimuths 35 degrees off the grid, the one nearest the neutral sun; a tie keeps the smaller."""
    # The residues modulo 360 are the same whichever way k turns, and the one nearest 150 never wraps past 360.
    candidates = sorted(round((axis_deg + OFFSET_DEG + k * PERIOD_DEG) % 360.0, 2) for k in range(4))  # pragma: no mutate: the same four residues; the axis already has two decimals
    return min(candidates, key=lambda c: _gap(c, NEUTRAL_SUN_AZIMUTH_DEG))


def street_sun(visual: bytes, half_side_m: float | None) -> StreetSun:
    """The street axis of the core of ``visual`` and the sun's azimuth across it; the neutral azimuth without walls."""
    headings, areas = wall_votes(scene_primitives(visual), half_side_m)
    axis = street_axis(headings, areas)
    azimuth = NEUTRAL_SUN_AZIMUTH_DEG if axis is None else azimuth_across(axis)
    return StreetSun(axis, azimuth, round(float(areas.sum()), 1))


__all__ = ["OFFSET_DEG", "StreetSun", "azimuth_across", "street_axis", "street_sun", "wall_votes"]
