"""``overgrow`` builds the shop furniture plan (ADR-031): nodes, shared meshes, colliders and the refusals.

The plan is written here by hand, in local metres around MURCIA_CENTER, with real catalogue rows.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import numpy as np
import pytest

from onward_generator.detail.config import FURNISH_CATALOGUES, REPO_ROOT
from onward_generator.generation.mesh import transform
from onward_generator.overgrow.furnish import FurnishBuildError, build_furniture, planned_pieces
from onward_generator.overgrow.library import read_interior_catalogue

ASSETS = REPO_ROOT / "assets"
PROPS, _ = read_interior_catalogue(ASSETS, FURNISH_CATALOGUES, ["Shelf_01", "wooden_display_shelves_01", "kenney_food_loaf"])


def _piece(n: int, asset_id: str, position: list[float], yaw: float, solid: bool, slot: str = "wall", building: str = "b1") -> dict:
    found = PROPS[asset_id]
    return {"id": f"furnish/{building}/{n}", "asset_id": asset_id, "catalogue": found.catalogue, "slot": slot, "position_m": position,
            "yaw_deg": yaw, "triangles": found.prop.triangles, "solid": solid, "glb_sha256": found.glb_sha256}


P0 = _piece(0, "Shelf_01", [1.0, 2.0, 0.0], 90.0, True)
P1 = _piece(1, "Shelf_01", [4.0, 2.0, 0.0], 0.0, True)
P2 = _piece(2, "wooden_display_shelves_01", [6.0, 5.0, 0.0], 180.0, False)
P3 = _piece(0, "kenney_food_loaf", [3.0, 3.0, 0.9], 270.0, False, slot="decor", building="")


def _manifest(*pieces: dict) -> dict:
    furnished = {"id": "b1/L0", "furnishing": {"pieces": list(pieces[:3])}}
    return {"objects": [
        "not an object",
        {"type": "building", "rooms": [{"furnishing": {"pieces": [P1]}}]},
        {"type": "interior", "source_ids": ["b1"], "rooms": ["not a room", {"id": "b1/L1"}, furnished]},
        {"type": "interior", "rooms": [{"furnishing": {"pieces": list(pieces[3:])}}]},
    ]}


PLAN = _manifest(P0, P1, P2, P3)


def test_the_planned_pieces_are_those_of_the_interiors_with_their_building() -> None:
    assert planned_pieces(PLAN) == [("b1", P0), ("b1", P1), ("b1", P2), ("", P3)]
    assert planned_pieces({"objects": [{"type": "interior", "source_ids": [], "rooms": [{"furnishing": {"pieces": [P3]}}]}]}) == [("", P3)]
    assert planned_pieces({}) == []


def test_each_piece_is_a_node_its_model_box_centred_on_the_planned_spot() -> None:
    built = build_furniture(PLAN, ASSETS)
    assert [n.name for n in built.nodes] == ["furnish/b1/0", "furnish/b1/1", "furnish/b1/2", "furnish//0"]
    first, second = built.nodes[0], built.nodes[1]
    assert first.primitives and second.primitives == ()  # one mesh per row, shared by its copies
    assert first.mesh_key == second.mesh_key == "prop:Shelf_01"
    for node, piece in zip(built.nodes, (P0, P1, P2, P3), strict=True):
        mesh = transform(PROPS[piece["asset_id"]].prop.mesh, node.matrix)
        low, high = mesh.vertices.min(axis=0), mesh.vertices.max(axis=0)
        assert (low[:2] + high[:2]) / 2 == pytest.approx(piece["position_m"][:2], abs=1e-6)
        assert low[2] == pytest.approx(piece["position_m"][2], abs=1e-6)
    size = PROPS["Shelf_01"].prop.size_m
    turned = transform(PROPS["Shelf_01"].prop.mesh, first.matrix).vertices
    assert np.ptp(turned[:, 0]) == pytest.approx(size[1], abs=1e-6)  # yaw 90: the long side runs north
    assert first.extras == {
        "type": "furnishing", "origin": "generated", "attribute_origin": "generated", "semantics_origin": "estimated",
        "asset_id": "Shelf_01", "catalogue": "polyhaven_shop", "licence": "CC0-1.0", "role": "shelf", "slot": "wall",
        "building_id": "b1", "solid": True,
    }
    lod = PROPS["wooden_display_shelves_01"].prop
    assert lod.source_triangles and built.nodes[2].extras["lod"] == {"source_triangles": lod.source_triangles, "triangles": lod.triangles}
    assert built.nodes[2].extras["solid"] is False and built.nodes[3].extras["slot"] == "decor"
    assert built.triangles == sum(PROPS[p["asset_id"]].prop.triangles for p in (P0, P1, P2, P3))


def test_only_solid_pieces_get_a_box_with_the_node_matrix() -> None:
    built = build_furniture(PLAN, ASSETS)
    assert [c.name for c in built.colliders] == ["furnish/collider/b1/0", "furnish/collider/b1/1"]
    assert built.solid_pieces == 2
    for collider, node in zip(built.colliders, built.nodes, strict=False):
        assert np.array_equal(collider.matrix, node.matrix)
        assert collider.extras == {"type": "collision", "origin": "generated", "asset_id": "Shelf_01", "role": "shelf", "furnishing": True}
        (primitive,) = collider.primitives
        assert primitive.material == "collider" and primitive.mesh.triangle_count == 12
        mesh = PROPS["Shelf_01"].prop.mesh
        assert primitive.mesh.vertices.min(axis=0) == pytest.approx(mesh.vertices.min(axis=0))
        assert primitive.mesh.vertices.max(axis=0) == pytest.approx(mesh.vertices.max(axis=0))
    assert built.report() == {"pieces": 4, "triangles": built.triangles, "colliders": 2, "materials": sorted(built.images)}


def test_no_plan_reads_nothing() -> None:
    assert build_furniture({"objects": []}, None).nodes == []


def test_the_plan_needs_a_library_of_allowed_catalogues() -> None:
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(PLAN, None)
    assert str(caught.value) == "scene_detail.json plans interior furniture (ADR-031, ADR-034); overgrow place needs --library"
    odd = _manifest({**P0, "catalogue": "kenney"}, {**P1, "catalogue": "normalized"})
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(odd, ASSETS)
    assert str(caught.value) == "interior furniture names catalogues that are not allowed: kenney, normalized"


def _library(tmp_path: Path, placement: str = "interior") -> Path:
    root = tmp_path / "library"
    shutil.copytree(ASSETS / "polyhaven_shop", root / "polyhaven_shop")
    manifest = json.loads((root / "polyhaven_shop" / "manifest.json").read_text(encoding="utf-8"))
    for row in manifest["assets"]:
        if row["id"] == "Shelf_01":
            row["placement"] = placement
    (root / "polyhaven_shop" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def test_a_row_missing_or_refused_stops_the_build_with_its_reason(tmp_path: Path) -> None:
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(_manifest(P0), tmp_path)
    assert str(caught.value) == f"interior furniture Shelf_01: not installed under {tmp_path} (missing)"
    library = _library(tmp_path, placement="kit_only")
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(_manifest(P0), library)
    assert str(caught.value) == f"interior furniture Shelf_01: not installed under {library} (not_interior)"


def test_a_glb_or_a_count_other_than_the_plan_stops_the_build() -> None:
    sha = PROPS["Shelf_01"].glb_sha256
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(_manifest({**P0, "glb_sha256": "0123456789abcdef" * 4}), ASSETS)
    assert str(caught.value) == (f"interior furniture Shelf_01: the GLB is not the one detail planned (sha256 {sha[:12]}, "
                                 "plan 0123456789ab); run detail again")
    with pytest.raises(FurnishBuildError) as caught:
        build_furniture(_manifest({**P0, "triangles": 7}), ASSETS)
    assert str(caught.value) == f"interior furniture Shelf_01: {PROPS['Shelf_01'].prop.triangles} triangles, detail priced 7"


def test_a_thick_rug_is_pressed_flat_on_the_floor_and_a_thin_one_is_left() -> None:
    from onward_generator.overgrow.furnish import RUG_THICK_M

    assert RUG_THICK_M == 0.01
    rug = _piece(0, "kenney_food_loaf", [3.0, 3.0, 0.25], 30.0, False, slot="rug", building="")
    loaf = _piece(1, "kenney_food_loaf", [5.0, 3.0, 0.25], 30.0, False, slot="decor", building="")
    built = build_furniture({"objects": [{"type": "interior", "rooms": [{"furnishing": {"pieces": [rug, loaf]}}]}]}, ASSETS)
    mesh = PROPS["kenney_food_loaf"].prop.mesh
    pressed, kept = (transform(mesh, node.matrix).vertices for node in built.nodes)
    model = mesh.vertices
    assert np.ptp(model[:, 2]) > RUG_THICK_M  # a loaf is thicker than a rug may be
    assert pressed[:, 2].min() == pytest.approx(0.25, abs=1e-9) and np.ptp(pressed[:, 2]) == pytest.approx(RUG_THICK_M, abs=1e-9)
    assert np.ptp(pressed[:, 0]) == pytest.approx(np.ptp(kept[:, 0]), abs=1e-9)  # its plan is the same
    assert (pressed[:, :2].min(axis=0) + pressed[:, :2].max(axis=0)) / 2 == pytest.approx([3.0, 3.0], abs=1e-6)
    assert np.ptp(kept[:, 2]) == pytest.approx(np.ptp(model[:, 2]), abs=1e-9) and kept[:, 2].min() == pytest.approx(0.25, abs=1e-9)
