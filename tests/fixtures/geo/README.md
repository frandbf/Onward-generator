# Geo test fixtures

Everything in this folder is synthetic and authored for this project (CC0-1.0). None of it is OpenStreetMap data.

All tests use the Murcia city-centre point **lat 37.98407, lon −1.12863** (`MURCIA_CENTER` in `tests/conftest.py`). It is the fixture origin and the reference for every test location.

- `synthetic_murcia.osm`: OSM XML 0.6. It is laid out on a local EPSG:25830 grid around that point. XML comments give each node's intended local position.
- `synthetic_aoi.geojson`: the area of interest, a 200 × 200 m square centred on that origin, with a 30 m margin.

What each element tests, with local coordinates in metres:

| Element | Local position | Expected result |
| --- | --- | --- |
| way 101 | (−50,−50)–(−20,−20) | `inside`; height 12 observed, 4 levels observed |
| way 102 | (80,0)–(120,30) | crosses the AOI edge and stays inside the 30 m margin, so it is kept whole as `margin`; 3 levels observed, no height (estimated later by `heights`). A 5 m margin no longer covers it, so the whole building is dropped |
| way 103 | (200,200)–(220,220) | dropped as `outside` |
| way 104 | (110,−60)–(125,−45) | `margin`; `height=tall` is reported as `unparseable_tag` |
| way 105 | y = 10, x −200…200 | `clipped` to x ±130; width inferred |
| way 106 | y = 60, x −80…80 | `inside`; width 2.5 observed |
| way 107 | bow-tie | `invalid_geometry` and rejected; kept after repair when repair is enabled |
| way 108 | same nodes as 101 | `duplicate_geometry` |
| way 109 | references node 999 | `missing_node_reference` |
| way 112 | closed ring with 2 distinct nodes | `degenerate_ring` |
| way 113 | (120,80)–(150,100) | crosses the margin edge; the whole building is dropped |
| second way 101 | — | `duplicate_element_id`; ignored |
| relation 201 | outer (30,−40)–(70,0), inner hole | multipolygon `inside`, area 1600 − 100 m² |
| relation 202 | route | `unsupported_relation` (info) |
| node 40 / node 41 | (−5,40) / (300,0) | POI `inside` / dropped |

Lon/lat values have 8 decimals, roughly 1 mm, so local positions match the table to within a few millimetres.
