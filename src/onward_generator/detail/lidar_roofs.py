"""Flat or pitched roof from the p25-p75 spread of a LiDAR height model (nDSM), plan C3, off by default.

A raster source plugin (``es.pnoa_ndsm``, ``fr.lidar_hd``; ADR-018) already writes, per footprint, the 25th and
75th percentiles of the valid cells inside it (``<id>_report.json``, ``onward_generator.source_heights_report``).
Over a gabled roof the cells spread from the eaves to the ridge, so ``p75 - p25`` is about half the rise; over a
flat roof they hardly spread. ``classify`` turns that spread into ``flat``, ``pitched`` or the reason it does not
decide. The result is an estimate (``origin: estimated``) and never wins over an OSM ``roof:*`` tag. No roof is
reconstructed from raw LiDAR (no LoD2): a pitched roof is a gabled roof whose ridge runs along the long side of the
footprint's minimum rotated rectangle (``detail/roofs.py``).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry

from onward_generator.detail.config import PitchedRoof
from onward_generator.detail.roofs import DEFAULT_MAX_RISE_M, MIN_RISE_M

RULE = "ndsm_spread"
# Below this many valid cells the percentiles say little: PNOA cells are 2.5 m, so a 6 x 10 m footprint holds about 4
# after the 1 m inner buffer.
MIN_CELLS = 8
FLAT_MAX_SPREAD_M = 0.6  # p75 - p25 at most this is a flat roof (a parapet, plant or noise)
MIN_FILL = 0.85  # footprint area / minimum rotated rectangle area: an L or a mixed outline is not read as one gable
# s = spread / half span. A gable of pitch a gives s ~ tan(a) / 2: 0.1 is about 11 degrees, 0.5 about 45 degrees.
PITCH_S_MIN = 0.1  # under this a wide footprint with a small spread is not clearly pitched
PITCH_S_MAX = 0.5  # over this the spread is a step between two heights (a setback or a tower), not a slope

FLAT = "flat"
PITCHED = "pitched"
# Why a building's roof was not classified, in the order the checks run. ``fallback`` is a pitched roof the builder
# could not draw on the footprint (it is drawn flat, ``roof.fallback``).
REASONS = ("tagged", "scenic", "terrace", "no_data", "too_few_cells", "not_rectangular", "mixed", "ambiguous", "fallback")
# A tag that says anything about the roof's form wins over the estimate (ADR-023 decision 4).
ROOF_TAG_KEYS = ("roof:shape", "roof:height", "roof:levels", "roof:angle")


@dataclass(frozen=True, slots=True)
class RoofSpread:
    """One footprint of a raster plugin's report: its status and its cell percentiles (metres above ground)."""

    status: str  # measured | out_of_bounds | no_data
    p25_m: float | None
    p75_m: float | None
    valid: int
    cells: int


@dataclass(frozen=True, slots=True)
class SpreadReport:
    """The spreads of one ``<id>_report.json`` and where they came from."""

    path: str
    sha256: str
    source: str  # the plugin id
    provider: str
    license: str
    attribution: str
    buildings: Mapping[str, RoofSpread]

    def evidence(self, spread: RoofSpread, outcome: str) -> dict[str, object]:
        """``roof.lidar``: the measurement that decided a classified roof, with its source and licence."""
        return {
            "source": self.source, "provider": self.provider, "license": self.license,
            "p25_m": spread.p25_m, "p75_m": spread.p75_m, "valid": spread.valid, "cells": spread.cells,
            "rule": RULE, "class": outcome, "origin": "estimated",
        }


def _rectangle_sides(footprint: BaseGeometry) -> tuple[float, float]:
    """The short and long sides of the footprint's minimum rotated rectangle."""
    corners = list(footprint.minimum_rotated_rectangle.exterior.coords)
    a = math.dist(corners[0], corners[1])
    b = math.dist(corners[1], corners[2])
    return min(a, b), max(a, b)


def half_span(footprint: BaseGeometry) -> float:
    """Half the short side of the minimum rotated rectangle: the run of a gable's slope from eave to ridge."""
    return _rectangle_sides(footprint)[0] / 2


def fill(footprint: BaseGeometry) -> float:
    """Footprint area over the area of its minimum rotated rectangle (1 for a rectangle)."""
    short, long = _rectangle_sides(footprint)
    return float(footprint.area) / (short * long)


def is_roof_tagged(tags: Mapping[str, str]) -> bool:
    """True when an OSM tag describes the roof's form (``ROOF_TAG_KEYS``)."""
    return any(str(tags.get(key) or "").strip() for key in ROOF_TAG_KEYS)


def classify(spread: RoofSpread | None, footprint: BaseGeometry, *, tagged: bool, scenic: bool, terrace: bool) -> str:
    """``flat``, ``pitched``, or the first of ``REASONS`` that stops the estimate.

    The checks run in order: an OSM roof tag, a scenic shell (ADR-017, its flat cap stays), the terrace under a
    taller part of its own parcel, a footprint the plugin did not measure, too few valid cells, a small spread
    (``flat``), a footprint far from a rectangle, a spread too steep for a slope, and one too shallow to be sure.
    """
    if tagged:
        return "tagged"
    if scenic:
        return "scenic"
    if terrace:
        return "terrace"
    if spread is None or spread.status != "measured" or spread.p25_m is None or spread.p75_m is None:
        return "no_data"
    if spread.valid < MIN_CELLS:
        return "too_few_cells"
    width = spread.p75_m - spread.p25_m
    if width <= FLAT_MAX_SPREAD_M:
        return FLAT
    if fill(footprint) < MIN_FILL:
        return "not_rectangular"
    s = width / half_span(footprint)
    if s > PITCH_S_MAX:
        return "mixed"
    if s < PITCH_S_MIN:
        return "ambiguous"
    return PITCHED


def spread_spec(spread: RoofSpread, material: str) -> PitchedRoof:
    """The gabled roof of a footprint classified ``pitched``: rise ``p75 - p25``, within ``[MIN_RISE_M, DEFAULT_MAX_RISE_M]``.

    The recorded height is the median, which lies between eave and ridge; the roof stands on it (ADR-023 decision 5).
    A rise of the spread, about half the real rise, puts the drawn ridge near the measured one, so the building does
    not grow by a whole default roof. With no direction the ridge runs along the long side.
    """
    assert spread.p25_m is not None and spread.p75_m is not None  # classify returns pitched only with both
    rise = min(max(spread.p75_m - spread.p25_m, MIN_RISE_M), DEFAULT_MAX_RISE_M)
    return PitchedRoof(shape="gabled", tag="gabled", material=material, rise_m=rise, rise_rule=RULE, angle_deg=None,
                       direction_deg=None, across=False)
