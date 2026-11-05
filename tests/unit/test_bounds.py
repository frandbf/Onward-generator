"""Playable envelope: site ring and refusal cases. Origin is MURCIA_CENTER."""

from __future__ import annotations

from pathlib import Path

import pytest
from shapely import box

from onward_generator.bounds.envelope import (
    BACKDROP_ID,
    BACKDROP_MATERIAL,
    HAZE_HEX,
    SKY_HEX,
    BoundsError,
    backdrop_node,
    backdrop_png,
    wall_footprint,
    wall_nodes,
    with_backdrop,
)
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


# ---- Horizon backdrop (ADR-014) ------------------------------------------------------------


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    return tuple(int(hex_colour[k : k + 2], 16) for k in (1, 3, 5))  # type: ignore[return-value]


def test_backdrop_is_one_inward_quad_per_hull_edge_outside_the_site() -> None:
    import numpy as np
    from shapely import Point

    assert MURCIA_CENTER == (-1.12863, 37.98407)
    site = box(-20.0, -20.0, 20.0, 20.0)
    node = backdrop_node(site, offset_m=2.0, height_m=30.0)
    assert node.name == BACKDROP_ID
    assert node.extras == {"object_id": BACKDROP_ID, "type": "backdrop", "attribute_origin": "generated", "source_ids": []}
    (prim,) = node.primitives
    assert prim.material == BACKDROP_MATERIAL
    mesh = prim.mesh
    # A square site: four quads, 3 m out (2 m offset plus the 1 m simplification allowance).
    assert mesh.triangle_count == 8 and len(mesh.vertices) == 16
    # Each quad is two triangles over its own four corners.
    assert mesh.triangles.tolist() == [t for k in range(0, 16, 4) for t in ([k, k + 1, k + 2], [k, k + 2, k + 3])]
    assert {round(abs(float(v)), 6) for v in mesh.vertices[:, :2].reshape(-1)} == {23.0}
    assert all(site.distance(Point(x, y)) >= 2.0 for x, y in mesh.vertices[:, :2])
    assert sorted(set(mesh.vertices[:, 2].tolist())) == [-1.0, 30.0]
    # Every face looks at the centre of the site.
    centres = mesh.vertices[mesh.triangles].mean(axis=1)
    inward = np.einsum("ij,ij->i", mesh.face_normals(), -centres)
    assert (inward > 0).all()
    # V runs from the centre of the bottom gradient row to the centre of the top row; U is constant.
    assert set(prim.uvs[:, 0].tolist()) == {0.5}
    bottom = mesh.vertices[:, 2] < 0
    assert np.allclose(prim.uvs[bottom, 1], 0.5 / 64) and np.allclose(prim.uvs[~bottom, 1], 1 - 0.5 / 64)


def test_backdrop_follows_the_convex_hull_and_refuses_bad_sizes() -> None:
    l_site = box(0.0, 0.0, 40.0, 10.0).union(box(0.0, 0.0, 10.0, 40.0))
    node = backdrop_node(l_site, offset_m=5.0, height_m=12.0)
    # The hull of an L is a pentagon: five quads.
    assert node.primitives[0].mesh.triangle_count == 10
    for offset, height, match in (
        (0.0, 30.0, r"^backdrop offset_m must be in \(0, 200\] metres$"),
        (201.0, 30.0, r"^backdrop offset_m must be in \(0, 200\] metres$"),
        (2.0, 1.9, "^backdrop height_m must be between 2 and 200 metres$"),
        (2.0, 201.0, "^backdrop height_m must be between 2 and 200 metres$"),
    ):
        with pytest.raises(BoundsError, match=match):
            backdrop_node(l_site, offset_m=offset, height_m=height)
    assert backdrop_node(l_site, offset_m=200.0, height_m=200.0).primitives[0].mesh.vertices[:, 2].max() == 200.0
    assert backdrop_node(l_site, offset_m=0.1, height_m=2.0).primitives[0].mesh.vertices[:, 2].max() == 2.0


def test_backdrop_gradient_runs_from_sky_at_the_top_to_haze_at_the_bottom() -> None:
    from io import BytesIO

    import numpy as np
    from PIL import Image

    image = np.asarray(Image.open(BytesIO(backdrop_png())))
    assert image.shape == (64, 4, 3)
    assert tuple(image[0, 0]) == _rgb(SKY_HEX) and tuple(image[-1, 0]) == _rgb(HAZE_HEX)
    sky, haze = np.array(_rgb(SKY_HEX), dtype=float), np.array(_rgb(HAZE_HEX), dtype=float)
    expected = np.rint(sky + (haze - sky) * (np.arange(64) / 63)[:, None]).astype(np.uint8)
    assert (image == expected[:, None, :]).all()
    assert backdrop_png() == backdrop_png()
    # The adapter draws the ring unlit, so these colours reach the screen as they are, then the fog.
    assert (_rgb(HAZE_HEX), _rgb(SKY_HEX)) == ((0xC8, 0xCD, 0xD2), (0xD6, 0xE0, 0xEC))


def test_backdrop_keeps_every_image_uv_and_extra_of_the_scene() -> None:
    from io import BytesIO

    import numpy as np
    from PIL import Image

    from onward_generator.generation.config import Material
    from onward_generator.generation.gltf import Node, Primitive, build_glb, images_by_material, nodes_from_glb, read_glb
    from onward_generator.generation.mesh import Mesh

    buf = BytesIO()
    Image.new("RGB", (4, 4), (120, 90, 60)).save(buf, format="PNG")
    ground = Mesh(np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [0.0, 4.0, 0.0]]), np.array([[0, 1, 2]]))
    uvs = np.array([[0.25, 0.0], [1.5, 0.0], [0.0, 2.0]], dtype=np.float32)
    scene = build_glb(
        [Node("surface/ground", (Primitive(ground, "ground", uvs),), {"light_anchors": {"rooms": []}})],
        {"ground": Material((0.5, 0.5, 0.5, 1.0), 1.0)},
        extras={"manifest": "scene_detail.json"},
        repeat_images={"ground": buf.getvalue()},
        preserve_uvs=True,
    )
    written = with_backdrop(scene, backdrop_node(box(-5.0, -5.0, 5.0, 5.0), offset_m=2.0, height_m=10.0))
    nodes, materials = nodes_from_glb(written)
    assert [n.name for n in nodes] == ["surface/ground", BACKDROP_ID]
    assert nodes[0].extras == {"light_anchors": {"rooms": []}}
    assert np.array_equal(nodes[0].primitives[0].uvs, uvs)
    assert materials[BACKDROP_MATERIAL] == Material((1.0, 1.0, 1.0, 1.0), 1.0)
    images = images_by_material(written)
    assert images["ground"] == buf.getvalue() and images[BACKDROP_MATERIAL] == backdrop_png()
    assert read_glb(written)[0]["extras"] == {"manifest": "scene_detail.json"}
    assert nodes[1].primitives[0].uvs is not None


def test_bounds_cli_keeps_the_backdrop_off_with_its_documented_defaults() -> None:
    from onward_generator.bounds.__main__ import build_parser
    from onward_generator.bounds.envelope import DEFAULT_BACKDROP_HEIGHT_M, DEFAULT_BACKDROP_OFFSET_M

    args = build_parser().parse_args(["--base", "b", "--out", "o"])
    assert args.backdrop is False
    assert (args.backdrop_offset_m, args.backdrop_height_m) == (DEFAULT_BACKDROP_OFFSET_M, DEFAULT_BACKDROP_HEIGHT_M) == (2.0, 30.0)
    given = build_parser().parse_args(["--base", "b", "--out", "o", "--backdrop",
                                       "--backdrop-offset-m", "4.5", "--backdrop-height-m", "12"])
    assert (given.backdrop, given.backdrop_offset_m, given.backdrop_height_m) == (True, 4.5, 12.0)


@pytest.mark.parametrize("flag", ["--backdrop-offset-m", "--backdrop-height-m"])
def test_bounds_cli_refuses_a_backdrop_size_that_is_not_a_number(tmp_path: Path, flag: str) -> None:
    assert main(["bounds", "--base", str(tmp_path), "--out", str(tmp_path / "out"), "--backdrop", flag, "tall"]) == 2
    assert not (tmp_path / "out").exists()

