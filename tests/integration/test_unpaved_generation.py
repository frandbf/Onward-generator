"""Generate records the unpaved carriageway and caps it apart (15 November 2026)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from shapely import contains_xy
from shapely.geometry import MultiPolygon, Polygon

from onward_generator.generation.config import load_generation_config
from onward_generator.generation.scene import generate_scene


def _carriageway(scene: Path, heights: Path) -> tuple[dict, np.ndarray]:
    generated = generate_scene(scene, heights, load_generation_config())
    road = next(o for o in generated.objects if o.id == "surface/carriageway")
    # Only the carriageway records the dirt: a sidewalk or plaza on an unpaved street is not the road.
    assert all("unpaved" not in o.record for o in generated.objects if o.id != "surface/carriageway")
    return road.record, road.visual[0].mesh.vertices[road.visual[0].mesh.triangles]


def test_an_unpaved_street_is_recorded_and_capped_apart(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, _ = detail_inputs
    paved_record, paved_tris = _carriageway(scene, heights)
    assert "unpaved" not in paved_record  # a paved map's record is as before
    document = json.loads(scene.read_text(encoding="utf-8"))
    streets = [f for f in document["features"] if (f.get("tags") or {}).get("highway") in {"residential", "primary", "secondary", "tertiary", "unclassified", "service"}]
    assert len(streets) == 1  # the fixture's one street becomes dirt; a copy across it stays paved
    import copy

    paved = copy.deepcopy(streets[0])
    paved["id"] = str(paved["id"]) + "-cross"
    paved["geometry"] = {"type": "LineString", "coordinates": [[0.0, -60.0], [7.0, 60.0]]}  # askew: the dirt edge is not on the mm grid
    document["features"].append(paved)
    streets[0]["tags"]["surface"] = "dirt"
    dirt_scene = tmp_path / "geo_scene.json"
    dirt_scene.write_text(json.dumps(document), encoding="utf-8")
    from onward_generator.heights import run_heights, write_height_outputs

    dirt_heights, _ = write_height_outputs(run_heights(dirt_scene), tmp_path / "heights")
    record, tris = _carriageway(dirt_scene, dirt_heights)
    unpaved = record["unpaved"]
    assert unpaved["attribute_origin"] == "observed" and unpaved["area_m2"] > 0
    # Millimetres, as every other record of the scene.
    assert unpaved["area_m2"] == round(unpaved["area_m2"], 3)
    assert all(v == round(v, 3) for polygon in unpaved["polygons"] for ring in polygon for point in ring for v in point)
    assert any(point[0] != round(point[0], 2) for polygon in unpaved["polygons"] for ring in polygon for point in ring)
    assert any(point[1] != round(point[1], 2) for polygon in unpaved["polygons"] for ring in polygon for point in ring)
    region = MultiPolygon([Polygon(p[0], p[1:]) for p in unpaved["polygons"]])
    assert abs(region.area - unpaved["area_m2"]) < 0.01
    # No triangle straddles the two: its corners all lie in the region (or on its edge) when its centre does.
    centres = tris.mean(axis=1)
    inside = contains_xy(region, centres[:, 0], centres[:, 1])
    assert inside.any() and not inside.all()
    grown = region.buffer(0.01)
    assert all(contains_xy(grown, t[:, 0], t[:, 1]).all() for t in tris[inside])
    # The paved and unpaved caps cover the same carriageway as the single cap did.
    def area(t: np.ndarray) -> float:
        u, v = t[:, 1, :2] - t[:, 0, :2], t[:, 2, :2] - t[:, 0, :2]
        return float(np.abs(u[:, 0] * v[:, 1] - u[:, 1] * v[:, 0]).sum() / 2)

    assert abs(area(tris[inside]) - region.area) < 0.05  # the earth triangles are the region
    assert area(paved_tris) > 0
