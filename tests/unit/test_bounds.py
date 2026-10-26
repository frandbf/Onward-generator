"""Playable envelope: site ring and refusal cases. Origin is MURCIA_CENTER."""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely import box

from onward_generator.bounds.envelope import BoundsError, wall_footprint, wall_nodes
from onward_generator.cli import main
from tests.conftest import MURCIA_CENTER


def test_wall_ring_is_closed_and_uses_metres() -> None:
    lon, lat = MURCIA_CENTER
    assert (lon, lat) == MURCIA_CENTER
    site = box(-20.0, -20.0, 20.0, 20.0)
    ring = wall_footprint(site, 0.4)
    assert ring.area == pytest.approx(40.0 * 40.0 - 39.2 * 39.2, rel=1e-3)
    visual, collision = wall_nodes(site, height_m=12.0, thickness_m=0.4)
    assert visual.name == "envelope/perimeter_wall"
    assert visual.primitives[0].mesh.triangle_count > 0
    assert collision.name.endswith("#collider")
    zs = visual.primitives[0].mesh.vertices[:, 2]
    assert zs.min() == pytest.approx(0.0) and zs.max() == pytest.approx(12.0)


def test_wall_rejects_degree_sized_or_invalid_thickness() -> None:
    with pytest.raises(BoundsError):
        wall_footprint(box(0, 0, 0.2, 0.2), 0.4)
    with pytest.raises(BoundsError):
        wall_nodes(box(-10, -10, 10, 10), height_m=1.0, thickness_m=0.4)
    with pytest.raises(BoundsError):
        wall_footprint(box(-10, -10, 10, 10), 0.0)


def test_wall_appends_to_an_atlas_scene() -> None:
    from io import BytesIO

    import numpy as np
    from PIL import Image

    from onward_generator.generation.config import Material
    from onward_generator.generation.gltf import Node, Primitive, build_glb, glb_append, nodes_from_glb
    from onward_generator.generation.mesh import Mesh

    buf = BytesIO()
    Image.new("RGB", (4, 4), (200, 200, 200)).save(buf, format="PNG")
    ground = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]]),
        np.array([[0, 1, 2]]),
    )
    uvs = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    scene = build_glb(
        [Node("surface/ground", (Primitive(ground, "ground", uvs),))],
        {"ground": Material((0.5, 0.5, 0.5, 1.0), 1.0)},
        atlas_png=buf.getvalue(),
    )
    wall, _collision = wall_nodes(box(-5.0, -5.0, 5.0, 5.0), height_m=12.0, thickness_m=0.4)
    written = glb_append(scene, (wall,), {"envelope_wall": Material((0.32, 0.34, 0.36, 1.0), 0.95)})
    names = [node.name for node in nodes_from_glb(written)[0]]
    assert "surface/ground" in names
    assert "envelope/perimeter_wall" in names


def test_bounds_refuses_a_missing_base(tmp_path: Path) -> None:
    assert main(["bounds", "--base", str(tmp_path / "nope"), "--out", str(tmp_path / "out")]) == 1
    assert not (tmp_path / "out" / "scene_visual.glb").exists()
