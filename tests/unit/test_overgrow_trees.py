"""Library cut-out trees on detail's street trees, and the cut-out list through the scene (ADR-040)."""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from onward_generator.export.cutouts import CUTOUT_KEY, CutoutError, carried_material_extras, cutout_options, scene_cutouts
from onward_generator.generation.gltf import Material, Node, Primitive, build_glb, pack_glb, read_glb
from onward_generator.generation.mesh import Mesh, box, merge
from onward_generator.overgrow import trees as trees_module
from onward_generator.overgrow.atlas import atlas_candidates
from onward_generator.overgrow.catalog import ASSETS_BY_ID
from onward_generator.overgrow.library import CUTOUT_ROLES, Prop, PropPart, level_of_detail, reject_reason
from onward_generator.overgrow.trees import (
    TREE_HEADROOM_SHARE,
    TREE_MAX_TRIANGLES,
    TREE_MIN_HEIGHT_M,
    TREE_SCALE,
    crown_reach,
    swap_trees,
    tree_candidates,
    trunk_frame,
)


def _png(px: int = 8) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", (px, px), (40, 120, 50, 255)).save(out, format="PNG")
    return out.getvalue()


def _tree_mesh(height: float = 5.0, crown: float = 2.0, x: float = 3.0, y: float = -1.0, copies: int = 1) -> Mesh:
    """A 0.3 m trunk on z = 0.5 at (x, y) and a ``crown``-radius square canopy: bark first, then leaves."""
    trunk = box(x - 0.15, x + 0.15, y - 0.15, y + 0.15, 0.5, 0.5 + height * 0.6)
    canopy = box(x - crown, x + crown, y - crown, y + crown, 0.5 + height * 0.5, 0.5 + height)
    return merge([trunk] * copies + [canopy] * copies)


def _tree(asset_id: str = "holly_a", height: float = 5.0, crown: float = 2.0, cutoff: float | None = 0.5,
          role: str = "tree", category: str = "vegetation", copies: int = 1) -> Prop:
    mesh = _tree_mesh(height, crown, copies=copies)
    spec = replace(ASSETS_BY_ID["shrub_03"], id=asset_id, role=role, category=category)
    uvs = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])[np.arange(len(mesh.vertices)) % 4]
    half = mesh.triangle_count // 2
    parts = (PropPart(half, _png()), PropPart(mesh.triangle_count, _png(4), cutoff))
    return Prop(spec, mesh, uvs, parts[0].albedo, parts=parts)


def _tree_node(name: str, x: float, y: float, crown: float = 1.9, narrowed: float = 1.0) -> Node:
    matrix = np.eye(4)
    matrix[0, 3], matrix[1, 3] = x, y
    matrix = matrix @ np.diag([narrowed, narrowed, 1.0, 1.0])
    canopy = box(-crown, crown, -crown / 2, crown / 2, 2.0, 4.0)
    return Node(name, (Primitive(box(-0.2, 0.2, -0.2, 0.2, 0.0, 2.5), "tree_trunk"), Primitive(canopy, "tree_canopy")),
                {"object_id": name, "type": "urban_element", "source_ids": ["osm-node-7"]}, matrix, "urban:tree")


def test_the_tree_settings() -> None:
    assert (TREE_MAX_TRIANGLES, TREE_MIN_HEIGHT_M, TREE_SCALE, TREE_HEADROOM_SHARE) == (2400, 3.0, (0.6, 1.6), 0.5)
    assert CUTOUT_ROLES == frozenset({"tree", "bush"})


def test_the_candidates_are_cut_out_trees_tall_and_small_enough() -> None:
    props = [_tree("b_holly"), _tree("a_holly"), _tree("opaque", cutoff=None), _tree("bush", role="bush"),
             _tree("low", height=2.9), _tree("exact", height=3.0), _tree("rock", category="barriers"),
             _tree("big", copies=TREE_MAX_TRIANGLES // 24 + 1), _tree("fits", copies=TREE_MAX_TRIANGLES // 24)]
    assert props[-2].triangles > TREE_MAX_TRIANGLES >= props[-1].triangles
    assert [prop.spec.id for prop in tree_candidates(props)] == ["a_holly", "b_holly", "exact", "fits"]


def test_the_trunk_stands_on_the_origin_and_the_reach_is_measured_from_it() -> None:
    mesh = trunk_frame(_tree_mesh(height=5.0, crown=2.0, x=3.0, y=-1.0))
    assert mesh.vertices[:, 2].min() == pytest.approx(0.0)
    foot = mesh.vertices[mesh.vertices[:, 2] < 0.3]
    assert (foot[:, :2].min(axis=0) + foot[:, :2].max(axis=0)) / 2 == pytest.approx([0.0, 0.0], abs=1e-9)
    assert crown_reach([mesh]) == pytest.approx(2.0 * np.sqrt(2))
    assert crown_reach([]) == 0.0
    assert crown_reach([Mesh(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)), mesh]) == pytest.approx(2.0 * np.sqrt(2))


def test_each_tree_takes_a_library_tree_scaled_to_its_crown_and_keeps_its_node() -> None:
    nodes = [_tree_node("urban/tree/far", 30.0, 0.0), _tree_node("urban/tree/near", 2.0, 1.0, narrowed=0.5),
             Node("urban/bench/1", (Primitive(box(0, 1, 0, 1, 0, 1), "urban_wood"),), {"type": "urban_element"})]
    prop = _tree("holly_a")
    result = swap_trees(nodes, [prop], 4, 1_000_000)
    assert result.nodes[2] is nodes[2]
    reach = crown_reach([trunk_frame(prop.mesh)])
    for before, after in zip(nodes[:2], result.nodes[:2], strict=True):
        scale = crown_reach([p.mesh for p in before.primitives]) / reach
        assert np.allclose(after.matrix, before.matrix @ np.diag([scale, scale, scale, 1.0]))
        assert after.extras == {**before.extras, "asset_id": "holly_a", "representation": "library_tree", "tree_scale": round(scale, 4)}
        assert after.mesh_key == "tree:holly_a"
        assert [p.material for p in after.primitives] == ["prop_holly_a", "prop_holly_a__1"]
        # Scaled by its matrix, the crown reaches as far from the trunk as the procedural one did.
        assert crown_reach([p.mesh for p in after.primitives]) * scale == pytest.approx(crown_reach([p.mesh for p in before.primitives]))
    assert result.images == prop.images()
    assert result.cutouts == {"prop_holly_a__1": 0.5}
    added = 2 * (prop.triangles - 24)
    assert result.added_triangles == added
    assert result.report == {"assets": {"holly_a": 2}, "count": 2, "trees": 2, "out_of_scale": 0, "over_budget": 0,
                             "added_triangles": added, "budget_triangles": 500_000}


def test_the_trees_nearest_the_centre_go_first_within_the_share_of_the_headroom() -> None:
    nodes = [_tree_node("urban/tree/far", 30.0, 0.0), _tree_node("urban/tree/near", 2.0, 1.0)]
    prop = _tree("holly_a", copies=3)
    one = prop.triangles - 24
    assert one == 48
    result = swap_trees(nodes, [prop], 4, int((one + 1) / TREE_HEADROOM_SHARE))
    assert result.nodes[0] is nodes[0] and result.nodes[1].extras["asset_id"] == "holly_a"
    assert result.report["count"] == 1 and result.report["over_budget"] == 1  # type: ignore[index]
    assert swap_trees(nodes, [prop], 4, int(one / TREE_HEADROOM_SHARE)).report["count"] == 1  # type: ignore[index]
    assert swap_trees(nodes, [prop], 4, int((one - 1) / TREE_HEADROOM_SHARE)).report["count"] == 0  # type: ignore[index]


@pytest.mark.parametrize(("crown", "swapped"), [(0.5, False), (1.9, True), (6.0, False)])
def test_a_tree_out_of_scale_keeps_its_procedural_crown(crown: float, swapped: bool) -> None:
    result = swap_trees([_tree_node("urban/tree/a", 0.0, 0.0, crown=crown)], [_tree("holly_a", crown=2.0)], 1, 1_000_000)
    assert ("asset_id" in result.nodes[0].extras) is swapped
    assert result.report["out_of_scale"] == (0 if swapped else 1)  # type: ignore[index]


@pytest.mark.parametrize(("scale", "count"), [(0.599, 0), (0.601, 1), (1.599, 1), (1.601, 0)])
def test_the_scale_bounds(scale: float, count: int) -> None:
    node = _tree_node("urban/tree/a", 0.0, 0.0)
    reach = crown_reach([p.mesh for p in node.primitives])
    prop = _tree("holly_a", crown=reach / scale / np.sqrt(2))
    assert swap_trees([node], [prop], 1, 1_000_000).report["count"] == count  # type: ignore[index]


def test_the_scale_bounds_are_inclusive(monkeypatch: pytest.MonkeyPatch) -> None:
    node = _tree_node("urban/tree/a", 0.0, 0.0)
    for scale in TREE_SCALE:
        monkeypatch.setattr(trees_module, "crown_reach", lambda meshes, s=scale: s if len(meshes) == 2 else 1.0)
        assert swap_trees([node], [_tree("holly_a")], 1, 1_000_000).report["count"] == 1, scale  # type: ignore[index]


def test_the_seed_and_the_node_name_pick_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = [_tree_node(f"urban/tree/{i}", float(i), 0.0) for i in range(12)]
    props = [_tree("holly_a"), _tree("holly_b", crown=1.8)]
    used = swap_trees(nodes, props, 9, 10_000_000).report["assets"]  # type: ignore[index]
    assert set(used) == {"holly_a", "holly_b"}
    monkeypatch.setattr(trees_module, "stable_unit", lambda *parts: 1.0)
    assert swap_trees(nodes, props, 9, 10_000_000).report["assets"] == {"holly_b": 12}  # type: ignore[index]


def test_no_tree_or_no_candidate_changes_nothing() -> None:
    nodes = [_tree_node("urban/tree/a", 0.0, 0.0), Node("overgrow/tree/1", (), {"type": "urban_element"})]
    for result in (swap_trees(nodes, [], 1, 10_000), swap_trees(nodes[1:], [_tree()], 1, 10_000)):
        assert result.report is None and result.images == {} and result.cutouts == {} and result.added_triangles == 0
    assert swap_trees(nodes, [_tree("opaque", cutoff=None)], 1, 10_000).nodes == nodes


def test_the_library_keeps_cut_out_leaves_on_trees_and_bushes_only() -> None:
    doc = {"materials": [{"name": "bark", "alphaMode": "OPAQUE"}, {"name": "leaves", "alphaMode": "MASK", "alphaCutoff": 0.4}]}
    mesh = _tree_mesh()
    for role, reason in (("tree", None), ("bush", None), ("plant", "alpha_mode_mask")):
        assert reject_reason(replace(ASSETS_BY_ID["shrub_03"], role=role), doc, mesh) == reason
    assert reject_reason(replace(ASSETS_BY_ID["concrete_road_barrier"], role="tree"), doc, mesh) == "alpha_mode_mask"
    blend = {"materials": [{"name": "leaves", "alphaMode": "BLEND"}]}
    assert reject_reason(replace(ASSETS_BY_ID["shrub_03"], role="tree"), blend, mesh) == "alpha_mode_blend"
    tree = _tree("holly_a", copies=40)
    assert tree.cutouts() == {"prop_holly_a__1": 0.5}
    assert level_of_detail(tree) is tree  # a cut-out is kept whole
    assert _tree("opaque", cutoff=None).cutouts() == {}


def test_the_library_reads_each_mask_threshold(tmp_path: Path) -> None:
    from onward_generator.overgrow.library import _cutoffs

    doc = {"asset": {"version": "2.0"}, "materials": [{"name": "bark"}, {"name": "leaves", "alphaMode": "MASK", "alphaCutoff": 0.3},
                                                       {"alphaMode": "MASK"}]}
    assert _cutoffs(pack_glb(doc, b"")) == {"leaves": 0.3, "mat_2": 0.5}


def test_cut_out_leaves_keep_their_own_image_out_of_the_atlas() -> None:
    node = Node("overgrow/prop/x/0", (Primitive(box(0, 1, 0, 1, 0, 1), "prop_leaves", np.full((8, 2), 0.5)),
                                      Primitive(box(0, 1, 0, 1, 0, 1), "prop_bark", np.full((8, 2), 0.5))))
    images = {"prop_leaves": _png(), "prop_bark": _png()}
    assert atlas_candidates([node], images) == ["prop_bark", "prop_leaves"]
    assert atlas_candidates([node], images, frozenset({"prop_leaves"})) == ["prop_bark"]


def _glb(extras: dict | None) -> bytes:
    node = Node("n", (Primitive(box(0, 1, 0, 1, 0, 1), "leaves"),))
    return build_glb([node], {"leaves": Material((1, 1, 1, 1), 0.9)}, **({"extras": extras} if extras is not None else {}))


def test_the_cut_out_list_is_read_and_carried_with_the_normal_maps() -> None:
    binding = {"library_id": "ph", "path": "a.jpg", "sha256": "f"}
    assert scene_cutouts(_glb(None)) == {}
    assert carried_material_extras(_glb(None)) == {}
    assert carried_material_extras(_glb(None), {}) == {}
    assert carried_material_extras(_glb(None), {"b": 0.5, "a": 0.25}) == {"materials": {CUTOUT_KEY: {"a": 0.25, "b": 0.5}}}
    assert list(carried_material_extras(_glb(None), {"b": 0.5, "a": 0.25})["materials"][CUTOUT_KEY]) == ["a", "b"]
    listed = _glb({"materials": {"cutouts": {"leaves": 0.4}, "normal_maps": {"leaves": binding}}})
    assert scene_cutouts(listed) == {"leaves": 0.4}
    assert carried_material_extras(listed) == {"materials": {"normal_maps": {"leaves": binding}, CUTOUT_KEY: {"leaves": 0.4}}}
    assert carried_material_extras(listed, {"leaves": 0.6, "x": 0.5}) == {
        "materials": {"normal_maps": {"leaves": binding}, CUTOUT_KEY: {"leaves": 0.6, "x": 0.5}}}
    assert carried_material_extras(_glb({"materials": {"normal_maps": {"leaves": binding}}})) == {"materials": {"normal_maps": {"leaves": binding}}}
    doc, _ = read_glb(_glb(carried_material_extras(listed)))
    assert doc["extras"]["materials"][CUTOUT_KEY] == {"leaves": 0.4}


def test_the_adapter_options_name_each_cut_out_material_once() -> None:
    assert cutout_options({"b": 0.5, "a": 0.25}, {"a", "b", "c"}) == [{"material": "a", "cutoff": 0.25}, {"material": "b", "cutoff": 0.5}]
    assert cutout_options({}, {"a"}) == []
    with pytest.raises(CutoutError, match="^cut-out material z is not a material of the visual GLB$"):
        cutout_options({"z": 0.5}, {"a"})
    for bad in (0.0, 1.0, -0.1, 1.2):
        with pytest.raises(CutoutError, match=f"^cut-out material a: the threshold must lie between 0 and 1, got {bad}$"):
            cutout_options({"a": bad}, {"a"})
