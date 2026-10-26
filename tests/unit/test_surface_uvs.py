"""Repeating surface UVs stay on the wall plane and scale with metres."""

from __future__ import annotations

import json

import numpy as np
import pytest

from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb, read_glb, surface_uvs
from onward_generator.generation.mesh import Mesh, merge, quad
from onward_generator.materials.openings import window_png
from onward_generator.materials.plan import load_texture_plan


def test_tall_wall_repeats_instead_of_folding_into_one_tile() -> None:
    mesh = Mesh(
        np.array([[0.0, 0.0, 0.0], [8.0, 0.0, 0.0], [8.0, 0.0, 30.0], [0.0, 0.0, 30.0]], dtype=np.float64),
        np.array([[0, 1, 2], [0, 2, 3]]),
    )
    uvs = surface_uvs(mesh, 2.0)
    assert float(uvs[:, 0].max() - uvs[:, 0].min()) == pytest.approx(4.0, abs=0.05)
    assert float(uvs[:, 1].max() - uvs[:, 1].min()) == pytest.approx(15.0, abs=0.05)


def test_diagonal_wall_keeps_height_on_v() -> None:
    mesh = Mesh(
        np.array([[0.0, 0.0, 0.0], [10.0, 10.0, 0.0], [10.0, 10.0, 8.0], [0.0, 0.0, 8.0]], dtype=np.float64),
        np.array([[0, 1, 2], [0, 2, 3]]),
    )
    uvs = surface_uvs(mesh, 2.0)
    assert float(uvs[:, 1].max() - uvs[:, 1].min()) == pytest.approx(4.0, abs=0.05)
    assert float(uvs[:, 0].max() - uvs[:, 0].min()) == pytest.approx(np.hypot(10.0, 10.0) / 2.0, abs=0.05)


def test_each_opening_fits_unit_square() -> None:
    mesh = merge([
        quad((0.0, 0.0, 0.0), (1.2, 0.0, 0.0), (1.2, 0.0, 1.8), (0.0, 0.0, 1.8)),
        quad((20.0, 0.0, 3.0), (21.0, 0.0, 3.0), (21.0, 0.0, 4.6), (20.0, 0.0, 4.6)),
    ])
    uvs = surface_uvs(mesh, 2.0, fit_unit=True)
    assert uvs.min() >= -1e-5
    assert uvs.max() <= 1.0 + 1e-5
    assert float(uvs[:4].max() - uvs[:4].min()) == pytest.approx(1.0, abs=1e-4)
    assert float(uvs[4:].max() - uvs[4:].min()) == pytest.approx(1.0, abs=1e-4)


def test_repeat_images_are_separate_textures() -> None:
    wall = Mesh(
        np.array([[0.0, 0.0, 0.0], [4.0, 0.0, 0.0], [4.0, 0.0, 6.0], [0.0, 0.0, 6.0]], dtype=np.float64),
        np.array([[0, 1, 2], [0, 2, 3]]),
    )
    png = window_png(32)
    data = build_glb(
        [Node("wall", (Primitive(wall, "plaster_ochre"), Primitive(wall, "glass_dark")))],
        {
            "plaster_ochre": Material((1.0, 1.0, 1.0, 1.0), 0.9),
            "glass_dark": Material((1.0, 1.0, 1.0, 1.0), 0.2),
        },
        repeat_images={"plaster_ochre": png, "glass_dark": png},
        repeat_tile_m={"plaster_ochre": 2.0, "glass_dark": 1.0},
        unit_uv_materials={"glass_dark"},
    )
    doc, _ = read_glb(data)
    assert len(doc["images"]) == 2
    assert doc["samplers"][0]["wrapS"] == 10497
    by_name = {m["name"]: m for m in doc["materials"]}
    assert by_name["plaster_ochre"]["pbrMetallicRoughness"]["baseColorTexture"]["index"] != (
        by_name["glass_dark"]["pbrMetallicRoughness"]["baseColorTexture"]["index"]
    )


def test_library_plan_colours_the_plaster_slots(tmp_path: Path) -> None:
    from onward_generator.materials.library_plan import select_texture_plan

    path = tmp_path / "texture_plan.json"
    path.write_text(json.dumps(select_texture_plan(
        name="Las Condes", map_id="lascondes", latitude=-33.40, longitude=-70.56, brief=None, seed=2,
    )), encoding="utf-8")
    plan = load_texture_plan(path)
    assert plan.by_id["ochre_facades"].catalog_ids == ("plaster_ochre_wall",)
    assert plan.by_id["plain_facades"].catalog_ids == ("plaster_white_wall",)
    assert plan.by_id["plain_facades"].albedo_mode == "color_albedo"
    assert plan.by_id["historic_facades"].library_id
    assert plan.by_id["brick"].catalog_ids == ("brick_red_wall",)
    assert "asphalt_road" not in plan.by_id["streets_plazas"].catalog_ids
