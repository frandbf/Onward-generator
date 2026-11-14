"""Library lamp posts on detail's street lamps (overgrow/lamps.py, 14 November 2026)."""

from __future__ import annotations

import io
from dataclasses import replace

import numpy as np
import pytest
from PIL import Image

from onward_generator.generation.gltf import Node, Primitive
from onward_generator.generation.mesh import Mesh, box, merge
from onward_generator.overgrow import lamps as lamps_module
from onward_generator.overgrow.catalog import ASSETS_BY_ID
from onward_generator.overgrow.lamps import (
    ARM_MIN_OFFSET_M,
    LAMP_HEIGHT_M,
    LAMP_MAX_TRIANGLES,
    LAMP_MIN_TRIANGLES,
    LAMP_SOURCE_HEIGHT_M,
    LAMP_ROLES,
    lamp_candidates,
    lamp_model,
    post_frame,
    swap_lamps,
)
from onward_generator.overgrow.library import Prop


def _png(px: int = 64) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (px, px), (120, 90, 60)).save(out, format="PNG")
    return out.getvalue()


def _lamp_mesh(height: float = 10.0, x: float = 2.0, y: float = 3.0, arm: tuple[float, float] = (0.0, 1.5)) -> Mesh:
    """A 0.2 m post standing on z = 1 at (x, y), ``height`` tall, with an arm at its top towards ``arm``."""
    post = box(x - 0.1, x + 0.1, y - 0.1, y + 0.1, 1.0, 1.0 + height)
    ax, ay = arm
    top = 1.0 + height
    head = box(min(x, x + ax) - 0.05, max(x, x + ax) + 0.05, min(y, y + ay) - 0.05, max(y, y + ay) + 0.05, top - 0.3, top)
    return merge([post, head])


def _prop(asset_id: str = "lamp_a", role: str = "lamppost", mesh: Mesh | None = None, albedo: bytes | None = None) -> Prop:
    mesh = mesh if mesh is not None else merge([_lamp_mesh()] * 5)  # 120 triangles: LAMP_MIN_TRIANGLES
    spec = replace(ASSETS_BY_ID["concrete_road_barrier"], id=asset_id, role=role)
    corners = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
    uvs = corners[np.arange(len(mesh.vertices)) % 4]
    return Prop(spec, mesh, uvs, _png() if albedo is None else albedo)


def _lamp_node(name: str, x: float) -> Node:
    matrix = np.eye(4)
    matrix[0, 3] = x
    return Node(name, (Primitive(box(-0.1, 0.1, -0.1, 0.1, 0.0, 4.5), "urban_metal"),),
                {"object_id": name, "type": "urban_element", "source_ids": ["osm-node-1"]}, matrix, "urban:street_lamp")


def test_the_lamp_settings() -> None:
    assert LAMP_ROLES == frozenset({"lamppost", "streetlight", "lamp_post", "lamp"})
    assert LAMP_HEIGHT_M == (4.0, 7.0)
    assert LAMP_MAX_TRIANGLES == 1600
    assert LAMP_MIN_TRIANGLES == 120
    assert LAMP_SOURCE_HEIGHT_M == (3.5, 10.0)
    assert ARM_MIN_OFFSET_M == 0.1


@pytest.mark.parametrize(("height", "scale"), [(10.0, 0.7), (5.0, 1.0), (3.0, 4.0 / 3.0), (7.0, 1.0), (4.0, 1.0)])
def test_the_post_stands_on_the_origin_and_the_arm_points_along_x(height: float, scale: float) -> None:
    mesh, used = post_frame(_lamp_mesh(height=height, arm=(0.0, 1.5)))
    assert used == pytest.approx(scale)
    vertices = mesh.vertices
    assert vertices[:, 2].min() == pytest.approx(0.0)
    assert vertices[:, 2].max() == pytest.approx(height * scale)
    foot = vertices[vertices[:, 2] < 0.5]
    assert (foot[:, :2].min(axis=0) + foot[:, :2].max(axis=0)) / 2 == pytest.approx([0.0, 0.0], abs=1e-9)
    # The arm reached +y from the post; it now reaches +x, as detail's arm does.
    assert vertices[:, 0].max() == pytest.approx((1.5 + 0.05) * scale)
    assert vertices[:, 1].max() == pytest.approx(0.1 * scale)
    assert vertices[:, 1].min() == pytest.approx(-0.1 * scale)
    assert np.array_equal(mesh.triangles, _lamp_mesh().triangles)


@pytest.mark.parametrize("arm", [(-1.5, 0.0), (0.0, -1.5), (1.0, 1.0)])
def test_any_arm_direction_ends_along_x(arm: tuple[float, float]) -> None:
    mesh, _ = post_frame(_lamp_mesh(height=5.0, arm=arm))
    upper = mesh.vertices[mesh.vertices[:, 2] > 0.75 * 5.0]
    offset = upper[:, :2].mean(axis=0)
    assert offset[0] > ARM_MIN_OFFSET_M
    assert offset[1] == pytest.approx(0.0, abs=1e-9)


def test_a_post_without_an_arm_is_not_turned() -> None:
    plain = merge([box(1.9, 2.1, 2.9, 3.1, 0.0, 5.0), box(1.9, 2.1, 2.9, 3.19, 4.7, 5.0)])  # 0.09 m off: under the minimum
    mesh, _ = post_frame(plain)
    assert mesh.vertices[:, 1].max() == pytest.approx(0.19)
    assert mesh.vertices[:, 0].max() == pytest.approx(0.1)
    assert mesh.vertices[:, 0].min() == pytest.approx(-0.1)


def test_the_candidates_are_textured_lamp_posts_small_enough_to_repeat() -> None:
    big = merge([_lamp_mesh(x=float(i)) for i in range(LAMP_MAX_TRIANGLES // 24 + 1)])
    fits = merge([_lamp_mesh(x=float(i)) for i in range(LAMP_MAX_TRIANGLES // 24)])
    assert big.triangle_count > LAMP_MAX_TRIANGLES >= fits.triangle_count
    props = [
        _prop("z_lamp", "streetlight"), _prop("a_lamp", "lamppost"), _prop("bench", "bench"),
        _prop("flat_lamp", "lamppost", albedo=b""), _prop("huge_lamp", "lamp", mesh=big), _prop("max_lamp", "lamp_post", mesh=fits),
        _prop("kenney_urban_detail_light_Traffic", "lamp"), _prop("blocky_lamp", "lamp", mesh=merge([_lamp_mesh()] * 4)),
        _prop("head", "lamp", mesh=merge([_lamp_mesh(height=3.49)] * 5)), _prop("low_ok", "lamp", mesh=merge([_lamp_mesh(height=3.5)] * 5)),
        _prop("mast", "lamp", mesh=merge([_lamp_mesh(height=10.01)] * 5)), _prop("tall_ok", "lamp", mesh=merge([_lamp_mesh(height=10.0)] * 5)),
    ]
    assert [prop.spec.id for prop in lamp_candidates(props)] == ["a_lamp", "low_ok", "max_lamp", "tall_ok", "z_lamp"]


def test_the_seed_picks_one_model_and_an_empty_library_none() -> None:
    props = [_prop(f"lamp_{i}") for i in range(5)]
    assert lamp_model([], 1) is None
    assert lamp_model([_prop("bench", "bench")], 1) is None
    chosen = {lamp_model(props, seed).spec.id for seed in range(40)}  # type: ignore[union-attr]
    assert len(chosen) > 1
    assert lamp_model(props, 7).spec.id == lamp_model(list(reversed(props)), 7).spec.id  # type: ignore[union-attr]


def test_the_last_candidate_is_picked_when_the_seed_unit_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lamps_module, "stable_unit", lambda *parts: 1.0)
    assert lamp_model([_prop("lamp_a"), _prop("lamp_b")], 3).spec.id == "lamp_b"  # type: ignore[union-attr]
    monkeypatch.setattr(lamps_module, "stable_unit", lambda *parts: 0.0)
    assert lamp_model([_prop("lamp_a"), _prop("lamp_b")], 3).spec.id == "lamp_a"  # type: ignore[union-attr]


def test_every_street_lamp_is_drawn_with_the_library_post_and_keeps_its_node() -> None:
    other = Node("urban/bench/9", (Primitive(box(0, 1, 0, 1, 0, 1), "urban_wood"),), {"type": "urban_element"})
    named = Node("urban/street_lamp/9", (Primitive(box(0, 1, 0, 1, 0, 1), "x"),), {"type": "overgrowth"})  # not an urban element
    nodes = [_lamp_node("urban/street_lamp/1", 0.0), other, _lamp_node("urban/street_lamp/2", 5.0), named]
    prop = _prop("lamp_a")
    result = swap_lamps(nodes, [prop], 1, 100_000)
    assert [node.name for node in result.nodes] == [node.name for node in nodes]
    assert result.nodes[1] is other and result.nodes[3] is named
    for before, after in ((nodes[0], result.nodes[0]), (nodes[2], result.nodes[2])):
        assert np.array_equal(after.matrix, before.matrix)
        assert after.extras == {**before.extras, "asset_id": "lamp_a", "representation": "library_lamp"}
        assert after.mesh_key == "lamp:lamp_a"
        assert [primitive.material for primitive in after.primitives] == ["prop_lamp_a"]
        assert after.primitives[0].mesh.vertices[:, 2].max() == pytest.approx(7.0)
    assert result.images == {"prop_lamp_a": prop.albedo}
    assert result.added_triangles == 2 * prop.triangles - 2 * 12
    assert result.report == {"asset_id": "lamp_a", "count": 2, "scale": 0.7, "height_m": 7.0, "triangles": prop.triangles,
                             "added_triangles": 2 * prop.triangles - 24}


def test_lamps_that_do_not_fit_the_headroom_are_left_as_they_were() -> None:
    nodes = [_lamp_node("urban/street_lamp/1", 0.0), _lamp_node("urban/street_lamp/2", 5.0)]
    prop = _prop("lamp_a")
    added = 2 * prop.triangles - 24
    fits = swap_lamps(nodes, [prop], 1, added)
    assert fits.added_triangles == added and fits.report["count"] == 2  # type: ignore[index]
    over = swap_lamps(nodes, [prop], 1, added - 1)
    assert over.nodes == nodes
    assert over.images == {}
    assert over.added_triangles == 0
    assert over.report == {"asset_id": "lamp_a", "count": 0, "scale": 0.7, "height_m": 7.0, "triangles": prop.triangles,
                           "added_triangles": 0, "skipped": "over_headroom"}


def test_no_lamp_or_no_model_changes_nothing() -> None:
    nodes = [_lamp_node("urban/street_lamp/1", 0.0)]
    for result in (swap_lamps(nodes, [], 1, 100_000), swap_lamps([], [_prop()], 1, 100_000)):
        assert result.images == {} and result.added_triangles == 0 and result.report is None
    assert swap_lamps(nodes, [], 1, 100_000).nodes == nodes
