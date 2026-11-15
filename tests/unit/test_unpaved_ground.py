"""Earth on an unpaved street and ground decals from the map's own streets and light (15 November 2026)."""

from __future__ import annotations

import numpy as np
import pytest
from shapely.geometry import box

from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import cap, merge
from onward_generator.materials.apply import UNPAVED_MATERIAL, rematerialize_unpaved_carriageway, unpaved_region
from onward_generator.materials.library_plan import SLOT_BINDINGS, SLOT_FAMILY, deterministic_choices
from onward_generator.signs.damage import UNPAVED_MAJORITY, WAR_GROUND, ground_kinds
from onward_generator.signs.place import unpaved_share


def _road() -> Node:
    """A 20 x 8 m carriageway capped as generate does: the west half (dirt) and the east half apart."""
    mesh = merge([cap(box(0.0, 0.0, 10.0, 8.0), -0.15), cap(box(10.0, 0.0, 20.0, 8.0), -0.15)])
    uvs = mesh.vertices[:, :2] / 10.0
    return Node("surface/carriageway", (Primitive(mesh, "carriageway", uvs),), {"type": "surface"})


def _record(polygons: list) -> dict:
    return {"id": "surface/carriageway", "type": "surface", "unpaved": {"area_m2": 80.0, "polygons": polygons}}


def test_the_unpaved_region_comes_from_the_carriageway_record() -> None:
    square = [[[0.0, 0.0], [10.0, 0.0], [10.0, 8.0], [0.0, 8.0], [0.0, 0.0]]]
    region = unpaved_region([{"id": "surface/sidewalk"}, _record([square])])
    assert region is not None and region.area == pytest.approx(80.0)
    holed = [[[0.0, 0.0], [10.0, 0.0], [10.0, 8.0], [0.0, 8.0], [0.0, 0.0]], [[2.0, 2.0], [4.0, 2.0], [4.0, 4.0], [2.0, 4.0], [2.0, 2.0]]]
    assert unpaved_region([_record([holed])]).area == pytest.approx(76.0)
    assert unpaved_region([{"id": "surface/carriageway"}]) is None and unpaved_region([]) is None
    assert unpaved_region([_record([])]) is None


def test_the_unpaved_triangles_take_the_earth_material_in_the_same_node() -> None:
    other = Node("surface/sidewalk", (Primitive(cap(box(0.0, 8.0, 20.0, 10.0), 0.0), "sidewalk"),), {})
    nodes, changed = rematerialize_unpaved_carriageway([_road(), other], box(0.0, 0.0, 10.0, 8.0))
    assert changed == ["surface/carriageway"] and nodes[1] is other
    paved, dirt = nodes[0].primitives
    assert (nodes[0].name, paved.material, dirt.material) == ("surface/carriageway", "carriageway", UNPAVED_MATERIAL)
    assert dirt.mesh.vertices[:, 0].max() == pytest.approx(10.0) and paved.mesh.vertices[:, 0].min() == pytest.approx(10.0)
    assert dirt.mesh.triangle_count + paved.mesh.triangle_count == _road().primitives[0].mesh.triangle_count
    assert dirt.uvs is not None and np.allclose(dirt.uvs, dirt.mesh.vertices[:, :2] / 10.0)  # each vertex keeps its UV
    # No triangle inside the region: the node is left as it was.
    untouched, none = rematerialize_unpaved_carriageway([_road()], box(50.0, 50.0, 60.0, 60.0))
    assert none == [] and untouched[0].primitives[0].material == "carriageway"
    # A road without UVs keeps none.
    bare = Node("surface/carriageway", (Primitive(_road().primitives[0].mesh, "carriageway"),), {})
    assert all(p.uvs is None for p in rematerialize_unpaved_carriageway([bare], box(0.0, 0.0, 10.0, 8.0))[0][0].primitives)


def test_the_plan_has_an_earth_slot_for_unpaved_streets(monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.materials import library_plan

    assert SLOT_BINDINGS["unpaved_carriageway"] == ("dirt_road",) and SLOT_FAMILY["unpaved_carriageway"] == "earth"
    rows = [{"id": f"earth_{i}", "family": "earth"} for i in range(3)]
    monkeypatch.setattr(library_plan, "texture_family", lambda row: row.get("family"))
    monkeypatch.setattr(library_plan, "is_calm", lambda row, limit=None, *, extended=False: True)
    assert deterministic_choices(rows, 7)["unpaved_carriageway"].startswith("earth_")


def _street(highway: str, surface: str | None, length: float) -> dict:
    tags = {"highway": highway, **({"surface": surface} if surface else {})}
    return {"tags": tags, "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0], [length, 0.0]]}}


def test_the_unpaved_share_is_by_vehicular_length() -> None:
    streets = [_street("residential", "dirt", 60.0), _street("primary", "asphalt", 40.0), _street("footway", "dirt", 500.0),
               _street("service", None, 0.0), {"tags": {"building": "yes"}, "geometry": {"type": "Point", "coordinates": [0, 0]}}]
    assert unpaved_share(streets) == pytest.approx(0.6)
    assert unpaved_share([_street("track", " Gravel ", 10.0)]) == 1.0
    assert unpaved_share([]) == 0.0 and unpaved_share([_street("footway", "dirt", 10.0)]) == 0.0


def test_the_ground_decals_follow_the_streets_and_the_light() -> None:
    every = {kind for kind, *_ in WAR_GROUND.values()}
    assert UNPAVED_MAJORITY == 0.5
    assert ground_kinds(0.0, None) == every - {"sand"}  # a paved map: no sand drifts
    assert ground_kinds(0.0, "dusty") == every  # dust in the light: sand drifts
    assert ground_kinds(0.5, "golden_hour") == every - {"sand"}  # half dirt is not a dirt map
    assert ground_kinds(0.51, None) == every - {"pothole"}  # a dirt map: sand, no asphalt patch
    assert ground_kinds(1.0, "dusty") == every - {"pothole"}
