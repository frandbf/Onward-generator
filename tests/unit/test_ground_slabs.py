"""Closed ground colliders, and the same slabs cut to a playable core (ADR-017).

Local metres around MURCIA_CENTER: a 128 m square site with a 6 m street along y = 0.
"""

from __future__ import annotations

import pytest
from shapely import LineString, box

from onward_generator.generation.config import load_generation_config
from onward_generator.generation.scene import ground_slabs
from onward_generator.generation.surfaces import LinearWay, SurfaceClass, build_partition

CONFIG = load_generation_config()
STREET = LinearWay("street", "vehicular", LineString([(-70, 0), (70, 0)]), 6.0, "config_default")


def _partition(*ways: LinearWay):  # type: ignore[no-untyped-def]
    return build_partition(box(-64, -64, 64, 64), [], list(ways), [], CONFIG)


def test_slabs_cover_the_ground_the_street_and_the_crossings() -> None:
    partition = _partition(STREET)
    objects, issues = ground_slabs(partition, CONFIG)
    assert issues == []
    assert [o.id for o in objects] == ["collider/walkable", "collider/carriageway", "collider/walkable_crossing"]
    walkable, lane, _crossing = objects
    street = partition.surfaces[SurfaceClass.CARRIAGEWAY].geometry
    thickness, depth = CONFIG.ground_thickness_m, CONFIG.carriageway_depth_m
    assert walkable.record == {
        "id": "collider/walkable", "type": "ground_collider", "top_z_m": 0.0, "bottom_z_m": -thickness,
        "area_m2": round(partition.site.difference(street).area, 3),
        "generation": {"method": "closed_slab"},
        "collider": {"triangles": walkable.collider.triangle_count, "closed": True},
        "checks": {"manifold_defects": 0},
    }
    assert walkable.type == "ground_collider" and walkable.visual == []
    assert lane.record["top_z_m"] == -depth
    assert walkable.collider.vertices[:, 2].min() == -thickness
    assert walkable.collider.vertices[:, 2].max() == 0.0


def test_without_a_street_there_is_only_the_ground_slab() -> None:
    objects, _ = ground_slabs(_partition(), CONFIG)
    assert [o.id for o in objects] == ["collider/walkable"]
    assert objects[0].record["area_m2"] == 128.0 * 128.0


def test_a_clip_keeps_the_part_of_each_slab_inside_it() -> None:
    partition = _partition(STREET)
    objects, issues = ground_slabs(partition, CONFIG, clip=box(-20, -20, 20, 20))
    assert issues == []
    for o in objects:
        assert o.record["clipped_to_core"] is True
        assert o.collider.vertices[:, 0].max() <= 20 + 1e-9 and o.collider.vertices[:, 1].min() >= -20 - 1e-9
    assert objects[0].record["area_m2"] == pytest.approx(40 * 40 - 40 * 6)
    # The area is kept to the square millimetre.
    odd = ground_slabs(partition, CONFIG, clip=box(-20, -20, 20.0001234, 20))[0][0]
    assert odd.record["area_m2"] == 1360.004
    assert "clipped_to_core" not in ground_slabs(partition, CONFIG)[0][0].record
    # A clip on the street alone leaves no ground slab and keeps the street.
    on_street = ground_slabs(partition, CONFIG, clip=box(-50, -1, -40, 1))[0]
    assert [o.id for o in on_street] == ["collider/carriageway"]


def test_a_slab_that_does_not_close_is_critical(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 10 m site 1 m thick: 100 m³, and the 1 % tolerance is exactly 1 m³.
    partition = build_partition(box(-5, -5, 5, 5), [], [], [], CONFIG)
    assert CONFIG.ground_thickness_m == 1.0
    expected = 100.0

    def check(volume: float, defects: int = 0) -> list[str]:
        monkeypatch.setattr("onward_generator.generation.scene.signed_volume", lambda _mesh: volume)
        monkeypatch.setattr("onward_generator.generation.scene.closed_manifold_defects", lambda _mesh: defects)
        objects, issues = ground_slabs(partition, CONFIG)
        assert objects[0].record["collider"]["closed"] is (defects == 0)
        assert objects[0].record["checks"] == {"manifold_defects": defects}
        return [f"{i.code} {i.severity} {i.object_id}" for i in issues]

    assert check(expected) == []
    assert check(expected * 1.005) == []
    assert check(101.0) == []
    assert check(expected * 1.02) == ["collider_not_closed critical collider/walkable"]
    assert check(expected, defects=1) == ["collider_not_closed critical collider/walkable"]
    assert ground_slabs(partition, CONFIG)[1][0].message == "1 defects; volume 100.0 vs 100.0 m³"
