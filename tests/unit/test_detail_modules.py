"""Phase 04 building blocks: prototype dimensions, watertightness, frames, facade layout, config, glTF instancing."""

from __future__ import annotations

import copy
import json

import numpy as np
import pytest
from shapely import Polygon, box

from onward_generator.detail.config import DEFAULT_CONFIG_PATH, DetailConfigError, config_from_document, load_detail_config
from onward_generator.detail.facades import (
    WELD_M,
    face_with_openings,
    interior_storey,
    parapet_strip,
    refine_exposure,
    stable_choice,
    stable_unit,
    storeys_for,
)
from onward_generator.detail.modules import (
    balcony,
    frame_matrix,
    lathe,
    recess,
    recess_rim,
    stair,
    stair_geometry,
    urban_prototypes,
)
from onward_generator.generation.gltf import Node, Primitive, build_glb, gltf_matrix, read_glb
from onward_generator.generation.mesh import (
    box as mesh_box,
)
from onward_generator.generation.mesh import (
    cap,
    closed_manifold_defects,
    merge,
    open_edge_defects,
    signed_volume,
    transform,
)

CFG = load_detail_config()
DOC = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))


def _wall_with_openings(length: float, height: float, rects: list[tuple[float, float, float, float]]):
    """Wall face in the plane y = 0 facing −y (module frame), as phase 04 builds it."""
    to_world = np.eye(4)
    to_world[:3, 0], to_world[:3, 1], to_world[:3, 2] = [1, 0, 0], [0, 0, 1], [0, -1, 0]
    return transform(cap(face_with_openings(length, 0.0, height, rects), 0.0), to_world)


def test_box_is_closed_with_exact_volume_and_omits_faces() -> None:
    b = mesh_box(0, 2, 0, 3, 0, 4)
    assert closed_manifold_defects(b) == 0 and signed_volume(b) == pytest.approx(24.0)
    assert mesh_box(0, 1, 0, 1, 0, 1, omit=("+y",)).triangle_count == 10
    with pytest.raises(ValueError):
        mesh_box(0, 1, 0, 1, 0, 1, omit=("up",))


def test_frame_matrix_rejects_mirrored_frames() -> None:
    with pytest.raises(ValueError):
        frame_matrix((0, 0, 0), (1, 0, 0), (0, -1, 0))


def test_recess_dimensions_and_watertight_with_its_wall() -> None:
    w, h, d = 1.1, 1.5, 0.15
    proto = recess(w, h, d, "glass_dark")
    allv = np.vstack([m.vertices for _, m in proto.parts])
    assert allv.min(axis=0).tolist() == [0, 0, 0] and allv.max(axis=0) == pytest.approx([w, d, h])
    s0, v0 = 1.2, 0.9
    wall = _wall_with_openings(4.0, 3.0, [(s0, v0, s0 + w, v0 + h)])
    m = frame_matrix((s0, 0.0, v0), (1, 0, 0), (0, 1, 0))
    shell = merge([wall, *(transform(x, m) for _, x in proto.parts)])
    outline = [((0, 0, 0), (4, 0, 0)), ((4, 0, 0), (4, 0, 3)), ((4, 0, 3), (0, 0, 3)), ((0, 0, 3), (0, 0, 0))]
    assert open_edge_defects(shell, allowed=outline, quantum=WELD_M) == 0
    # Without the recess the hole edges are open.
    assert open_edge_defects(wall, allowed=outline, quantum=WELD_M) > 0


def test_through_door_is_open_only_at_its_back_rim_and_base() -> None:
    w, h, t = 1.2, 2.4, 0.3
    proto = recess(w, h, t, None)
    assert [mat for mat, _ in proto.parts] == ["frame_stone"]
    wall = _wall_with_openings(5.0, 3.0, [(1.0, 0.0, 1.0 + w, h)])
    m = frame_matrix((1.0, 0.0, 0.0), (1, 0, 0), (0, 1, 0))
    rim = [(tuple((m @ [*p, 1])[:3]), tuple((m @ [*q, 1])[:3])) for p, q in recess_rim(w, h, t, include_bottom=False)]
    outline = [((5, 0, 0), (5, 0, 3)), ((5, 0, 3), (0, 0, 3)), ((0, 0, 3), (0, 0, 0))]
    shell = merge([wall, transform(proto.parts[0][1], m)])
    assert open_edge_defects(shell, z_base=0.0, allowed=[*outline, *rim], quantum=WELD_M) == 0
    assert open_edge_defects(shell, z_base=0.0, allowed=outline, quantum=WELD_M) > 0


def test_face_with_openings_area_and_notches() -> None:
    face = face_with_openings(10.0, 0.0, 6.0, [(1, 0, 2.2, 2.4), (4, 1, 5, 2.5), (6, 3, 7, 4.5)])
    assert face.is_valid and len(face.interiors) == 2
    assert face.area == pytest.approx(60 - 1.2 * 2.4 - 1.5 - 1.5)


def test_balcony_dimensions_and_closed_collider() -> None:
    b = CFG.templates["generic_residential"].balcony
    assert b is not None
    proto = balcony(b.width_m, b.depth_m, b.slab_m, b.railing_m)
    allv = np.vstack([m.vertices for _, m in proto.parts])
    assert allv.min(axis=0) == pytest.approx([0, -b.depth_m, -b.slab_m])
    assert allv.max(axis=0) == pytest.approx([b.width_m, 0, b.railing_m])
    assert proto.triangle_count == 20
    assert proto.collider is not None and closed_manifold_defects(proto.collider) == 0
    assert signed_volume(proto.collider) == pytest.approx(b.width_m * b.depth_m * (b.slab_m + b.railing_m))
    slab = proto.parts[0][1]
    # The slab is only open where it meets the wall (y = 0).
    wall_plane = [((0, 0, -b.slab_m), (b.width_m, 0, -b.slab_m)), ((b.width_m, 0, -b.slab_m), (b.width_m, 0, 0)),
                  ((b.width_m, 0, 0), (0, 0, 0)), ((0, 0, 0), (0, 0, -b.slab_m))]
    assert open_edge_defects(slab, allowed=wall_plane, quantum=WELD_M) == 0


@pytest.mark.parametrize("rise", [2.6, 3.0, 3.75, 4.2])
def test_stair_dimensions_and_colliders(rise: float) -> None:
    st = CFG.interiors.stair
    g = stair_geometry(rise, st.width_m, st.max_riser_m, st.tread_m)
    assert g.riser_m <= st.max_riser_m + 1e-12 and g.risers * g.riser_m == pytest.approx(rise)
    assert g.run_m == pytest.approx((g.risers - 1) * st.tread_m)
    assert 2 * g.riser_m + g.tread_m == pytest.approx(0.63, abs=0.05)  # comfortable step rule (Blondel)
    proto = stair(g)
    solid = proto.parts[0][1]
    assert closed_manifold_defects(solid) == 0
    steps = sum(i * g.riser_m * g.tread_m for i in range(1, g.risers)) * g.width_m
    assert signed_volume(solid) == pytest.approx(steps)
    lo, hi = solid.vertices.min(axis=0), solid.vertices.max(axis=0)
    assert lo == pytest.approx([0, 0, 0]) and hi == pytest.approx([g.run_m, g.width_m, (g.risers - 1) * g.riser_m])
    ramp = proto.collider
    assert ramp is not None and closed_manifold_defects(ramp) == 0
    assert signed_volume(ramp) == pytest.approx(0.5 * g.run_m * rise * g.width_m)
    assert ramp.vertices[:, 2].max() == pytest.approx(rise)


def test_urban_prototypes_have_closed_colliders_on_the_ground() -> None:
    for kind, proto in urban_prototypes().items():
        assert proto.collider is not None, kind
        assert closed_manifold_defects(proto.collider) == 0 and signed_volume(proto.collider) > 0, kind
        allv = np.vstack([m.vertices for _, m in proto.parts])
        assert np.isfinite(allv).all() and allv[:, 2].min() == pytest.approx(0.0), kind
        assert 0 < proto.triangle_count <= 200, kind


def test_lathe_is_closed() -> None:
    mesh = lathe([(0.0, 0.0), (1.0, 0.5), (0.0, 1.0)], 8)
    assert closed_manifold_defects(mesh) == 0 and signed_volume(mesh) > 0


def _east(segments, x: float):
    return sorted((s for s in segments if abs(s.a[0] - x) < 1e-9 and abs(s.b[0] - x) < 1e-9), key=lambda s: s.a[1])


def test_party_walls_split_the_ring_and_refinement_keeps_area() -> None:
    a = box(0, 0, 10, 12)
    neighbour = box(10, 3, 20, 7)  # touches y ∈ [3, 7] of a's east wall
    refined, segments = refine_exposure(a, neighbour, CFG)
    assert refined.area == pytest.approx(a.area) and refined.is_valid
    assert sum(s.length for s in segments) == pytest.approx(a.length)
    east = _east(segments, 10.0)
    assert [s.exposed for s in east] == [True, False, True]
    assert [round(s.length, 2) for s in east] == pytest.approx([3.0, 4.0, 5.0], abs=0.06)
    assert len(refined.exterior.coords) == 5 + 2  # two collinear breakpoints inserted


def test_short_exposed_leftovers_stay_blank() -> None:
    a = box(0, 0, 10, 8)
    _, segments = refine_exposure(a, box(10, 2, 20, 6), CFG)
    east = _east(segments, 10.0)
    assert len(east) == 1 and not east[0].exposed  # 2 m leftovers < min_facade_length_m
    assert sum(s.exposed for s in segments) == 3


def test_neighbour_across_a_narrow_gap_is_a_party_wall_but_a_street_is_not() -> None:
    a = box(0, 0, 10, 10)
    assert not _east(refine_exposure(a, box(10.5, 0, 20, 10), CFG)[1], 10.0)[0].exposed
    assert _east(refine_exposure(a, box(14, 0, 20, 10), CFG)[1], 10.0)[0].exposed


def test_parapet_strip_is_exact_with_courtyards() -> None:
    p = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(8, 8), (12, 8), (12, 12), (8, 12)]])
    inner = p.buffer(-0.25, join_style="mitre")
    strip = parapet_strip(p, inner)
    assert strip is not None and strip.area == pytest.approx(p.area - inner.area)
    assert set(p.exterior.coords) <= {c for g in getattr(strip, "geoms", [strip]) for c in g.exterior.coords}


def test_interior_storey_raises_an_estimate_and_keeps_a_measurement() -> None:
    short = storeys_for(8.0, 3, False, CFG)
    assert short.height_m == pytest.approx(8.0 / 3)
    raised = interior_storey(8.0, short, "fallback")
    assert raised is not None
    height, storeys = raised
    assert height == pytest.approx(8.4) and storeys.count == 3
    assert storeys.height_m == pytest.approx(2.8) and storeys.origin == "interior_storey"
    assert interior_storey(8.4, storeys, "fallback") is None
    assert interior_storey(8.0, short, "explicit_height") is None
    assert interior_storey(8.0, short, "override") is None
    assert interior_storey(8.0, short, "authoritative_levels") is None
    tall = storeys_for(12.0, 4, False, CFG)
    assert interior_storey(12.0, tall, "building_type") is None


def test_storeys_follow_heights_levels_or_are_recomputed() -> None:
    assert storeys_for(15.0, 5, False, CFG).origin == "heights_levels"
    s = storeys_for(15.0, 1, False, CFG)
    assert s.origin == "recomputed" and s.count == 5 and s.height_m == pytest.approx(3.0)
    assert storeys_for(15.0, 1, True, CFG).count == 1


def test_stable_choices_are_deterministic_and_seeded() -> None:
    assert stable_unit(1, "a", 2) == stable_unit(1, "a", 2)
    assert stable_unit(1, "a", 2) != stable_unit(2, "a", 2)
    options = ("x", "y", "z")
    picks = {stable_choice(48152, options, f"osm-way-{i}", "wall") for i in range(50)}
    assert picks == set(options)


def _mutated(path: list[str | int], value: object) -> dict:
    doc = copy.deepcopy(DOC)
    target = doc
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return doc


@pytest.mark.parametrize(
    ("path", "value", "fragment"),
    [
        (["templates", "generic_residential", "wall_materials"], ["missing_plaster"], "not defined"),
        (["templates", "generic_residential", "bay_m"], 1.5, "exceeds bay_m"),
        (["templates", "generic_commercial", "applies_to"], ["unknown"], "already mapped"),
        (["landmark_template"], "nope", "not a template"),
        (["openings", "recess_depth_m"], 0.4, "thinner than"),
        (["interiors", "rule", "min_area_m2"], 500.0, "below max_area_m2"),
        (["templates", "generic_residential", "narrow_window", "height_m"], 2.0, "minimum storey"),
        (["seed"], -1, "minimum"),
        (["interiors", "stair", "max_riser_m"], 0.3, "maximum"),
    ],
)
def test_invalid_detail_config_is_rejected(path: list[str | int], value: object, fragment: str) -> None:
    with pytest.raises(DetailConfigError, match=fragment):
        config_from_document(_mutated(path, value))


def test_gltf_instancing_shares_meshes_and_converts_matrices() -> None:
    proto = urban_prototypes()["bollard"]
    materials = {"urban_metal": CFG.materials["urban_metal"]}
    m1 = frame_matrix((5.0, 2.0, 0.0), (0.0, 1.0, 0.0), (-1.0, 0.0, 0.0))
    m2 = frame_matrix((-3.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    nodes = [Node(f"b{i}", tuple(Primitive(m, mat) for mat, m in proto.parts), {}, mx, proto.key) for i, mx in enumerate((m1, m2))]
    doc, _ = read_glb(build_glb(nodes, materials))
    assert len(doc["meshes"]) == 1 and [n["mesh"] for n in doc["nodes"]] == [0, 0]
    g = np.array(doc["nodes"][0]["matrix"]).reshape(4, 4).T
    p_local = np.array([0.1, 0.0, 0.9, 1.0])
    to_gltf = np.array([[1, 0, 0, 0], [0, 0, 1, 0], [0, -1, 0, 0], [0, 0, 0, 1.0]])
    assert g @ (to_gltf @ p_local) == pytest.approx(to_gltf @ (m1 @ p_local))
    assert gltf_matrix(np.eye(4)) == np.eye(4).reshape(-1).tolist()
