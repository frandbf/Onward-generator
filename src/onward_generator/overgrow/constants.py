"""Shared limits for abandoned-city dressing. Metres, never degrees."""

from __future__ import annotations

LANE_M = 4.2
# No grass tufts: the export is opaque only, so a tuft is a solid green 0.55 m square, single-sided, that read
# as litter on the headset and baked black (Barcelona Sarria, 5 November 2026). Was 480.
MAX_TUFTS = 0
MAX_TREES = 96
MAX_WRECKS = 28
MAX_ADDED_TRIS = 80_000
MAX_LIBRARY_TRIS = 60_000
MAX_VINES = 16
SEED = 48152
BLOCK_HALF_M = 55.0
SPAWN_CLEAR_M = 5.0
VINE_WIDTH_M = 0.35
VINE_HEIGHT_M = 3.2
TARGET_ASSET_TRIS = 1800
# Load-time level of detail of a library prop (library.level_of_detail): triangles
# per square metre of its largest bounding-box face, at least LOD_MIN_TRIS. A prop
# lower than FLAT_PROP_M lies flat on the ground and takes LOD_MIN_TRIS.
LOD_TRIS_PER_M2 = 600
LOD_MIN_TRIS = 128
FLAT_PROP_M = 0.1
# A reduced copy whose bounding box moved more than this (street metres) lost a part; the target doubles.
LOD_BOX_TOLERANCE_M = 0.02
# A prop lower than FLAT_PROP_M with more triangles than this is drawn as a flat impostor (overgrow/impostor.py).
IMPOSTOR_MIN_TRIS = 64
# Decorative vegetation (not a tree): at most DECOR_MAX_TRIS, and its box may move by DECOR_BOX_FRACTION of its size.
DECOR_MAX_TRIS = 300
DECOR_BOX_FRACTION = 0.1
ALBEDO_PX = 512
# A flat-anchored vegetation mesh taller than this is a slope tile, not ground cover.
FLAT_VEGETATION_MAX_M = 0.05
# Sidewalk manhole covers: candidates every COVER_STEP_M × [0.6, 1.4) along a
# street, never closer than COVER_SPACING_M to another cover nor within
# COVER_CLEAR_M of a wall.
COVER_STEP_M = 9.0
COVER_SPACING_M = 8.0
COVER_CLEAR_M = 0.6
