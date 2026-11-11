"""Furnishing by priority in ``detail`` (light anchors, nearest first) and cover props that join the shared prop atlas."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from onward_generator.detail import config_from_document, load_base
from onward_generator.detail.furnish import ShopFurnishing
from onward_generator.gameplay.cover_stage import apply_cover
from onward_generator.generation.config import Material, load_generation_config
from onward_generator.generation.gltf import Node, Primitive, build_glb, images_by_material, nodes_from_glb, read_glb
from onward_generator.generation.mesh import box
from onward_generator.overgrow.atlas import ATLAS_PREFIX, PROP_ATLAS_EXTRA, Cell
from onward_generator.overgrow.growth import grass_png
from tests.conftest import _detail_inputs
from tests.integration.test_shop_interiors import DOC, SHOP, _detail, _obj
from tests.integration.test_sightline_cover_pipeline import stage  # noqa: F401 - the module fixture this file reuses

# ---- detail: the furnished interior's light anchors ----------------------------------------------------------------------


def _shops_on(**interiors: object) -> dict[str, Any]:
    doc = copy.deepcopy(DOC)
    doc["interiors"]["shops"]["furnish"]["enabled"] = True
    doc["interiors"].update(interiors)
    return doc


def test_the_furnished_shop_carries_the_light_anchors_its_furnishing_wrote(
    detail_inputs: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = ShopFurnishing.furnish_interior

    def furnish(self: ShopFurnishing, building_id: str, plan: Any, built: Any, penetrations: Any, ic: Any) -> list[str]:
        messages = original(self, building_id, plan, built, penetrations, ic)
        built.light_anchors = {**built.light_anchors, "furnished": building_id}
        return messages

    monkeypatch.setattr(ShopFurnishing, "furnish_interior", furnish)
    on = _detail(detail_inputs, config_from_document(_shops_on()))
    assert _obj(on, f"interior/{SHOP}").light_anchors["furnished"] == SHOP
    assert all("furnished" not in o.light_anchors for o in on.objects if o.type == "interior" and o.id != f"interior/{SHOP}")


def test_with_no_light_left_every_room_of_every_interior_loses_its_light(detail_inputs: tuple[Path, Path, Path]) -> None:
    """Only the shop is furnished: its new anchors replace its own entry in the light cap, not a stair interior's."""
    dark = _detail(detail_inputs, config_from_document(_shops_on(lights_max=0)))
    interiors = [o for o in dark.objects if o.type == "interior"]
    assert len(interiors) >= 2 and interiors[0].id != f"interior/{SHOP}"
    for o in interiors:
        assert [room.get("light") for room in o.light_anchors["rooms"]] == ["over_cap"] * len(o.light_anchors["rooms"]), o.id
        assert not any("light_m" in room for room in o.light_anchors["rooms"]), o.id


# ---- detail: the furniture budget goes to the interior nearest the map centre --------------------------------------------


def test_the_floor_budget_goes_to_the_interior_nearest_the_map_centre(tmp_path: Path) -> None:
    # 802 is 9.90 m from the centre and 10.63 m from (1, 0) and (0, 1); 801 is 10.75 m from the centre and 10.07 m from both.
    near = ([(-17, -17), (-7, -17), (-7, -7), (-17, -7)], {"building": "apartments", "building:levels": "4"})
    far = ([(7.6, 7.6), (17.6, 7.6), (17.6, 17.6), (7.6, 17.6)], {"building": "apartments", "building:levels": "4"})
    inputs = _detail_inputs(tmp_path, {802: near, 801: far}, {})
    doc = copy.deepcopy(DOC)
    doc["interiors"]["furnish"]["enabled"] = True
    unlimited = _detail(inputs, config_from_document(doc))
    cost = {
        o.record["source_ids"][0]: sum(r["furnishing"]["visual_triangles"] for r in o.record["rooms"])
        for o in unlimited.objects
        if o.type == "interior"
    }
    assert set(cost) == {"osm-way-801", "osm-way-802"}
    doc["interiors"]["furnish"]["max_triangles"] = cost["osm-way-802"]
    tight = _detail(inputs, config_from_document(doc))
    rooms = {o.record["source_ids"][0]: o.record["rooms"] for o in tight.objects if o.type == "interior"}
    assert all("furnishing" in room for room in rooms["osm-way-802"])
    assert not any("furnishing" in room for room in rooms["osm-way-801"])
    assert {i.object_id for i in tight.issues if i.code == "floor_not_furnished"} == {"osm-way-801"}


# ---- cover: a prop already in the shared atlas -------------------------------------------------------------------------


def _rewrite_visual(directory: Path, *, atlas_cell: bool, keep_extras: bool) -> None:
    data = (directory / "scene_visual.glb").read_bytes()
    doc, _ = read_glb(data)
    nodes, materials = nodes_from_glb(data)
    images = images_by_material(data)
    extras: dict[str, Any] | None = dict(doc.get("extras") or {}) if keep_extras else None
    if atlas_cell:
        atlas = f"{ATLAS_PREFIX}0"
        mesh = box(-0.1, 0.1, -0.1, 0.1, 0.0, 0.1)
        nodes.append(Node("probe/atlas", (Primitive(mesh, atlas, np.zeros((mesh.vertex_count, 2), dtype=np.float32)),), {"type": "probe"}))
        materials[atlas] = Material((1, 1, 1, 1), 0.9)
        images[atlas] = grass_png()
        assert extras is not None
        extras[PROP_ATLAS_EXTRA] = {"prop_synth_barrier": Cell(atlas, 0.0, 0.0, 0.5, 0.5).to_list()}
    (directory / "scene_visual.glb").write_bytes(build_glb(nodes, materials, extras=extras, repeat_images=images, preserve_uvs=True))


@pytest.fixture(scope="module")
def cover_base(stage: dict[str, Path]):  # noqa: F811 - the imported fixture is the parameter
    base, _ = load_base(stage["base"], stage["scene"], stage["heights"], load_generation_config(None))
    return base


def test_a_cover_prop_in_the_shared_atlas_is_no_new_asset_and_joins_its_cell(
    stage: dict[str, Path],  # noqa: F811 - the imported fixture is the parameter
    cover_base: Any,
    tmp_path: Path,
) -> None:
    atlased = tmp_path / "atlased"
    shutil.copytree(stage["playable"], atlased)
    _rewrite_visual(atlased, atlas_cell=True, keep_extras=True)
    result = apply_cover(cover_base, stage["scene"], atlased, stage["layout"], tmp_path / "out", library=stage["library"])
    placed = {row["asset_id"] for row in result.report["placed"]}
    assert "synth_barrier" in placed and result.report["added"]["new_assets"] == sorted(placed - {"synth_barrier"})
    visual, _ = nodes_from_glb((tmp_path / "out" / "scene_visual.glb").read_bytes())
    barrier = [p.material for n in visual if "/cover/" in n.name and n.extras.get("asset_id") == "synth_barrier" for p in n.primitives]
    assert barrier and set(barrier) == {f"{ATLAS_PREFIX}0"}


def test_a_visual_without_extras_is_covered(stage: dict[str, Path], cover_base: Any, tmp_path: Path) -> None:  # noqa: F811 - the imported fixture is the parameter
    bare = tmp_path / "bare"
    shutil.copytree(stage["playable"], bare)
    _rewrite_visual(bare, atlas_cell=False, keep_extras=False)
    result = apply_cover(cover_base, stage["scene"], bare, stage["layout"], tmp_path / "out", library=stage["library"])
    assert result.report["placed"] and "synth_barrier" in result.report["added"]["new_assets"]
    assert read_glb((tmp_path / "out" / "scene_visual.glb").read_bytes())[0].get("extras") is None
