# ADR-002 — Coordinate system

Status: accepted · 18 October 2026

## Context

Input is WGS84, longitude then latitude. Geometry runs in a metric projection that fits the map. The scene is stored in local coordinates around a known origin.

Degrees are never treated as metres, and large projected coordinates never go into the 3D scene.

## Decision

1. **Default projected CRS: EPSG:25830, ETRS89 / UTM zone 30N.** Official grid for peninsular Spain between 6° W and 0°, which includes Murcia (about 1.13° W). Axes are metres.
2. **Transform from WGS84.** pyproj 3.8.0 (PROJ 9.8.1) uses `Inverse of ETRS89 to WGS 84 (1) + UTM zone 30N`. That operation treats ETRS89 and WGS84 as identical, with a stated accuracy of about 1 m. That is enough for a game scene. Geodetic accuracy would need a coordinate epoch and a transformation grid.
3. **Local coordinates** (`coordinate_system: "local_grid"`):
   - `x = E − E₀` and `y = N − N₀`, in metres.
   - `x` grows east and `y` grows north.
   - The origin `(E₀, N₀)` is the projection of a fixed WGS84 point. The default is the centroid of the area of interest, computed in the projected CRS. `--origin` can set it.
4. **No altitude in this stage.** Coordinates are 2D. Building height is a later stage.
5. **JSON output is rounded to millimetres**, three decimal places.
6. **Checks:**
   - The CRS must be projected and in metres.
   - The origin must lie inside the CRS area of use.
   - Input coordinates must be valid longitudes and latitudes.
   - Clipping rejects an area of interest under 1 m², because that means the polygon is still in degrees.

## Consequences

- Coordinates are a shifted UTM grid, not a pure geodetic ENU frame:
  - The scale factor in Murcia is about 0.99994, roughly 6 cm per kilometre.
  - Meridian convergence is about 1.1°: grid north is not exactly geographic north.
  - Over a few hundred metres both effects are negligible for play. They are recorded rather than corrected.
- Conversion to the Onward SDK axes (Unity: `y` up, left-handed) belongs to the export adapter, not to this layer.
- Outside 6° W to 0°, EPSG:25830 is the wrong grid. `map create` then picks the WGS84 UTM zone of the centre (`EPSG:326xx` north, `EPSG:327xx` south) and stores it in `map.json`. `geo` uses that CRS only when `--crs` is passed.

## Verification

`tests/unit/test_projection.py` compares pyproj with an independent third-order Krüger series (GRS80). Five points between 36° and 40° N differ by less than 0.04 mm. It also checks the false origin (500 000, 0), a 0.001° meridian arc of about 110.97 m, and a 100 m square that round-trips without changing area.
