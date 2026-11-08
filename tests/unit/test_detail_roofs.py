"""Pitched roofs from roof:* tags: shapes, rises, directions, fallbacks, and closure on the walls."""

from __future__ import annotations

import math

import numpy as np
import pytest
from shapely import Polygon, affinity, box

from onward_generator.detail.config import PitchedRoof
from onward_generator.detail.roofs import (
    APPROXIMATIONS,
    DEFAULT_MAX_RISE_M,
    MAX_DOME_RISE_M,
    MAX_ROOF_VERTICES,
    BuiltRoof,
    _gables,
    _snap,
    build_roof,
    pitched_spec,
    with_rise,
)
from onward_generator.generation.mesh import merge, open_edge_defects, upward_area, walls

EAVE = 10.0
L_SHAPE = Polygon([(0, 0), (12, 0), (12, 5), (6, 5), (6, 10), (0, 10)])
HEXAGON = Polygon([(4, 0), (8, 2), (8, 6), (4, 8), (0, 6), (0, 2)])
# A rectangle whose long wall carries the collinear cuts that refine_exposure inserts.
REFINED = Polygon([(0, 0), (3, 0), (8, 0), (10, 0), (10, 6), (0, 6)])


def _spec(**tags: str) -> PitchedRoof:
    spec = pitched_spec(tags, "roof_tile", 3.0)
    assert spec is not None
    return spec


def _built(polygon: Polygon, **tags: str) -> BuiltRoof:
    built = build_roof(polygon, EAVE, _spec(**tags))
    assert isinstance(built, BuiltRoof), built
    return built


def _closed(polygon: Polygon, built: BuiltRoof) -> bool:
    shell = merge([walls(polygon, 0.0, EAVE), built.roof, built.gables])
    return open_edge_defects(shell, z_base=0.0, quantum=1e-6) == 0 and upward_area(built.roof) == pytest.approx(polygon.area)


def test_no_spec_without_a_drawable_shape() -> None:
    for tags in ({}, {"roof:shape": "flat"}, {"roof:shape": "crosspitched"}, {"roof:height": "3"}):
        assert pitched_spec(tags, "roof_tile", 3.0) is None


def test_the_spec_reads_the_rise_direction_and_orientation() -> None:
    spec = _spec(**{"roof:shape": "Gabled", "roof:height": "2.5 m", "roof:levels": "2", "roof:angle": "40",
                    "roof:direction": "SE", "roof:orientation": "across"})
    assert spec == PitchedRoof("gabled", "gabled", "roof_tile", 2.5, "roof:height", 40.0, 135.0, True)
    assert _spec(**{"roof:shape": "hipped", "roof:levels": "2"}).rise_m == 6.0
    assert _spec(**{"roof:shape": "hipped", "roof:levels": "2"}).rise_rule == "roof:levels"
    assert _spec(**{"roof:shape": "hipped", "roof:levels": "0"}).rise_rule == "default_pitch"
    angled = _spec(**{"roof:shape": "hipped", "roof:angle": "45"})
    assert angled.rise_m is None and angled.rise_rule == "roof:angle" and angled.angle_deg == 45.0
    for bad in ("90", "120"):
        steep = _spec(**{"roof:shape": "hipped", "roof:angle": bad})
        assert steep.angle_deg is None and steep.rise_rule == "default_pitch"
    plain = _spec(**{"roof:shape": "skillion", "roof:orientation": "along"})
    assert plain.direction_deg is None and plain.across is False and plain.rise_m is None


def test_shapes_without_a_builder_are_drawn_as_the_nearest_one() -> None:
    for tag, shape in APPROXIMATIONS.items():
        spec = _spec(**{"roof:shape": tag})
        assert spec.shape == shape and spec.tag == tag
        assert _built(box(0, 0, 10, 8), **{"roof:shape": tag}).record["approximation"] == f"{tag} drawn as {shape}"
    assert "approximation" not in _built(box(0, 0, 10, 8), **{"roof:shape": "gabled"}).record


@pytest.mark.parametrize("polygon", [box(0, 0, 10, 6), REFINED, L_SHAPE, HEXAGON, box(0, 0, 8, 8),
                                     affinity.rotate(box(0, 0, 10, 6), 33, origin=(0, 0)),
                                     Polygon([(0, 0), (10, 0.2), (10, 6), (0.1, 6)])])
@pytest.mark.parametrize("tags", [{}, {"roof:direction": "45"}, {"roof:orientation": "across"}, {"roof:height": "2"}])
@pytest.mark.parametrize("shape", ["gabled", "hipped", "half-hipped", "skillion", "pyramidal", "dome"])
def test_every_roof_closes_on_its_walls_and_covers_the_footprint(polygon: Polygon, tags: dict[str, str], shape: str) -> None:
    built = _built(polygon, **{"roof:shape": shape, **tags})
    assert _closed(polygon, built)
    assert built.roof.vertices[:, 2].min() == pytest.approx(EAVE)
    assert built.roof.vertices[:, 2].max() == pytest.approx(EAVE + built.rise_m)
    if built.gables.triangle_count:
        assert built.gables.vertices[:, 2].min() == pytest.approx(EAVE)


def test_a_half_hipped_roof_is_a_gable_up_to_half_its_rise_and_a_hip_above() -> None:
    footprint = box(X0, Y0, X0 + 12, Y0 + 8)
    roof = _built(footprint, **{"roof:shape": "half-hipped", "roof:height": "2"})
    assert roof.shape == "half-hipped" and "approximation" not in roof.record and roof.rise_m == 2.0

    def z_of(x: float, y: float) -> float:
        side = 0.5 * min(y - Y0, Y0 + 8 - y)  # 2 m over a half width of 4 m
        end = 1.0 + 0.5 * min(x - X0, X0 + 12 - x)
        return min(side, end)

    assert _faces_follow(roof, z_of) and _closed(footprint, roof)
    # Each end wall carries a trapezoid: its top edge is 1 m up, a quarter of the width in from each corner.
    top = roof.gables.vertices[roof.gables.vertices[:, 2] > EAVE + 1e-9]
    assert sorted(map(tuple, np.round(top - [X0, Y0, EAVE], 9).tolist())) == [
        (0.0, 2.0, 1.0), (0.0, 6.0, 1.0), (12.0, 2.0, 1.0), (12.0, 6.0, 1.0)]
    assert roof.record["pitch_deg"] == pytest.approx(math.degrees(math.atan2(2, 4)), abs=0.05)
    # 16 × 8 m with a vertex half way along every wall: an end wall's middle meets the hip at 1 m. Between the
    # two long walls' middles runs a diagonal shorter than the 12 m ridge, so a flat triangulation that missed
    # a fold (the ridge, a hip) would leave a face off the field.
    mids = Polygon([(X0, Y0), (X0 + 8, Y0), (X0 + 16, Y0), (X0 + 16, Y0 + 4), (X0 + 16, Y0 + 8), (X0 + 8, Y0 + 8),
                    (X0, Y0 + 8), (X0, Y0 + 4)])
    split = _built(mids, **{"roof:shape": "half-hipped", "roof:height": "2"})

    def long_z(x: float, y: float) -> float:
        return min(0.5 * min(y - Y0, Y0 + 8 - y), 1.0 + 0.5 * min(x - X0, X0 + 16 - x))

    assert _faces_follow(split, long_z) and _closed(mids, split)
    assert {(X0, Y0 + 4, EAVE + 1.0), (X0 + 16, Y0 + 4, EAVE + 1.0)} <= set(map(tuple, split.gables.vertices.tolist()))
    # The same with a vertex every metre round the walls (48): every fold crosses short triangles.
    ring = [(X0 + x, Y0) for x in range(16)] + [(X0 + 16, Y0 + y) for y in range(8)]
    ring += [(X0 + 16 - x, Y0 + 8) for x in range(16)] + [(X0, Y0 + 8 - y) for y in range(8)]
    dense = Polygon(ring)
    assert _faces_follow(_built(dense, **{"roof:shape": "half-hipped", "roof:height": "2"}), long_z)
    # Notched on an end wall below a hip foot and on a long wall below a hip: the notch vertices sit off
    # the folds, so a cut that missed a hip would leave a face off the field.
    notched_end = Polygon([(X0, Y0), (X0 + 16, Y0), (X0 + 16, Y0 + 8), (X0 + 1.5, Y0 + 8), (X0 + 1.5, Y0 + 7.9),
                           (X0, Y0 + 7.9), (X0, Y0 + 3), (X0 + 0.1, Y0 + 3), (X0 + 0.1, Y0 + 1), (X0, Y0 + 1)])
    near = _built(notched_end, **{"roof:shape": "half-hipped", "roof:height": "2"})
    assert near.shape == "half-hipped" and _closed(notched_end, near) and _faces_follow(near, long_z)
    # The narrowest and the flattest it draws: 1 m across, a 0.1 m rise.
    assert _built(box(X0, Y0, X0 + 10, Y0 + 1), **{"roof:shape": "half-hipped"}).shape == "half-hipped"
    assert _built(footprint, **{"roof:shape": "half-hipped", "roof:height": "0.1"}).rise_m == pytest.approx(0.1)
    narrow = build_roof(box(X0, Y0, X0 + 10, Y0 + 0.99), EAVE, _spec(**{"roof:shape": "half-hipped"}))
    flat = build_roof(footprint, EAVE, with_rise(_spec(**{"roof:shape": "half-hipped"}), 0.099))
    assert (narrow, flat) == ("footprint_too_narrow", "rise_too_small")
    # On a footprint that does not fill its rectangle it is drawn as a gable.
    notched = _built(L_SHAPE, **{"roof:shape": "half-hipped"})
    assert notched.shape == "gabled" and notched.record["approximation"] == "half-hipped drawn as gabled: footprint not rectangular"


def _faces_follow(built: BuiltRoof, z_of: object) -> bool:
    """Every vertex and every triangle centroid of the roof lies on ``eave + z_of(x, y)``: each face is one plane."""
    v = built.roof.vertices
    points = [*v, *v[built.roof.triangles].mean(axis=1)]
    return all(abs(p[2] - EAVE - z_of(p[0], p[1])) < 1e-9 for p in points)  # type: ignore[operator]


# Away from the origin, so a sum of corners is not their difference.
X0, Y0 = 300.0, -200.0


def test_each_face_is_one_plane_of_the_tagged_shape() -> None:
    gabled = _built(box(X0, Y0, X0 + 10, Y0 + 6), **{"roof:shape": "gabled", "roof:height": "3"})
    assert _faces_follow(gabled, lambda x, y: 3.0 * (1 - abs(y - Y0 - 3) / 3))
    # Long and thin: a ridge cut shorter than the footprint's width would not cross it.
    long = _built(box(X0, Y0, X0 + 100, Y0 + 6), **{"roof:shape": "gabled", "roof:height": "3"})
    assert _faces_follow(long, lambda x, y: 3.0 * (1 - abs(y - Y0 - 3) / 3))
    skillion = _built(box(X0, Y0, X0 + 10, Y0 + 6), **{"roof:shape": "skillion", "roof:direction": "W", "roof:height": "2"})
    assert _faces_follow(skillion, lambda x, y: 2.0 * (x - X0) / 10)
    for w, h in ((10, 6), (2, 1.2), (10, 9), (8, 8)):
        hipped = _built(box(X0, Y0, X0 + w, Y0 + h), **{"roof:shape": "hipped", "roof:height": "1.5"})
        def dist(x: float, y: float, w: float = w, h: float = h) -> float:
            return 1.5 / (h / 2) * min(x - X0, X0 + w - x, y - Y0, Y0 + h - y)
        assert _faces_follow(hipped, dist), (w, h)
        assert _closed(box(X0, Y0, X0 + w, Y0 + h), hipped)
    # A vertex inside the rectangle's long side rises with its distance to it.
    notched = Polygon([(X0, Y0), (X0 + 4, Y0 + 0.3), (X0 + 10, Y0), (X0 + 10, Y0 + 6), (X0, Y0 + 6)])
    near = _built(notched, **{"roof:shape": "hipped", "roof:height": "1.5"})
    assert near.shape == "hipped" and _faces_follow(near, lambda x, y: 0.5 * min(x - X0, X0 + 10 - x, y - Y0, Y0 + 6 - y))
    assert _closed(notched, near)
    # Turned 30°, with notches in a short end and both long sides: distances are taken in the turned frame.
    turn = math.radians(30)

    def local(x: float, y: float) -> tuple[float, float]:
        dx, dy = x - X0, y - Y0
        return dx * math.cos(turn) + dy * math.sin(turn), -dx * math.sin(turn) + dy * math.cos(turn)

    shape = Polygon([(0, 0), (2, 0.2), (7, 0.1), (12, 0), (12, 6), (6, 5.85), (0, 6), (0.15, 3)])
    turned = affinity.translate(affinity.rotate(shape, 30, origin=(0, 0)), X0, Y0)
    roof = _built(turned, **{"roof:shape": "hipped", "roof:height": "1.5"})

    def turned_dist(x: float, y: float) -> float:
        u, v = local(x, y)
        return 0.5 * min(u, 12 - u, v, 6 - v)

    assert roof.shape == "hipped" and _faces_follow(roof, turned_dist) and _closed(turned, roof)
    # On a hexagon the ridge follows the direction; its vertices half way down sit half way up.
    hexagon = affinity.translate(HEXAGON, X0, Y0)
    north = _built(hexagon, **{"roof:shape": "gabled", "roof:direction": "N", "roof:height": "2"})
    assert _faces_follow(north, lambda x, y: 2.0 * (1 - abs(y - Y0 - 4) / 4))
    east = _built(hexagon, **{"roof:shape": "gabled", "roof:direction": "E", "roof:height": "2"})
    assert _faces_follow(east, lambda x, y: 2.0 * (1 - abs(x - X0 - 4) / 4))


def test_a_clockwise_footprint_is_turned_round() -> None:
    clockwise = Polygon([(0, 0), (0, 6), (10, 6), (10, 0)])
    built = _built(clockwise, **{"roof:shape": "gabled"})
    assert _closed(Polygon([(0, 0), (10, 0), (10, 6), (0, 6)]), built)
    assert upward_area(built.roof) == pytest.approx(60.0)


def test_a_gabled_ridge_runs_along_the_long_side_through_the_middle() -> None:
    built = _built(box(0, 0, 10, 6), **{"roof:shape": "gabled"})
    top = built.roof.vertices[built.roof.vertices[:, 2] > EAVE + 1e-6]
    assert np.allclose(top[:, 1], 3.0) and sorted(set(top[:, 0].round(6))) == [0.0, 10.0]
    assert built.rise_m == pytest.approx(math.tan(math.radians(30)) * 3)
    assert built.record == {"type": "gabled", "rise_m": round(built.rise_m, 3), "pitch_deg": 30.0, "rise_rule": "default_pitch",
                            "rise_origin": "estimated", "direction_origin": "estimated"}
    # The two gable ends are triangles on the short walls, in the wall material, facing out.
    assert built.gables.triangle_count == 2
    normals = built.gables.face_normals()
    centres = built.gables.vertices[built.gables.triangles].mean(axis=1)
    assert np.all(np.sign(normals[:, 0]) == np.sign(centres[:, 0] - 5.0)) and np.allclose(normals[:, 2], 0.0)
    across = _built(box(0, 0, 10, 6), **{"roof:shape": "gabled", "roof:orientation": "across"})
    ridge = across.roof.vertices[across.roof.vertices[:, 2] > EAVE + 1e-6]
    assert np.allclose(ridge[:, 0], 5.0) and across.rise_m == pytest.approx(math.tan(math.radians(30)) * 5)
    assert across.record["direction_origin"] == "observed"


def test_a_roof_direction_turns_the_ridge_across_the_slope() -> None:
    built = _built(box(0, 0, 10, 6), **{"roof:shape": "gabled", "roof:direction": "E"})
    ridge = built.roof.vertices[np.isclose(built.roof.vertices[:, 2], built.roof.vertices[:, 2].max())]
    assert np.allclose(ridge[:, 0], 5.0) and built.record["direction_origin"] == "observed"
    north = _built(box(0, 0, 10, 6), **{"roof:shape": "gabled", "roof:direction": "N"})
    assert np.allclose(north.roof.vertices[np.isclose(north.roof.vertices[:, 2], north.roof.vertices[:, 2].max())][:, 1], 3.0)


def test_a_skillion_falls_towards_its_direction() -> None:
    south = _built(box(0, 0, 10, 6), **{"roof:shape": "skillion", "roof:direction": "S", "roof:height": "2"})
    v = south.roof.vertices
    assert np.allclose(v[v[:, 1] == 6.0][:, 2], EAVE + 2.0) and np.allclose(v[v[:, 1] == 0.0][:, 2], EAVE)
    assert south.record["pitch_deg"] == round(math.degrees(math.atan2(2.0, 6.0)), 1)
    east = _built(box(0, 0, 10, 6), **{"roof:shape": "skillion", "roof:direction": "90", "roof:height": "2"})
    assert np.allclose(east.roof.vertices[east.roof.vertices[:, 0] == 0.0][:, 2], EAVE + 2.0)
    # Without a direction it falls across the long side: from one long wall to the other.
    plain = _built(box(0, 0, 10, 6), **{"roof:shape": "skillion", "roof:height": "2"})
    assert set(plain.roof.vertices[:, 2].round(6)) == {EAVE, EAVE + 2.0}
    assert all(len(set(plain.roof.vertices[plain.roof.vertices[:, 1] == y][:, 2].round(6))) == 1 for y in (0.0, 6.0))


def test_a_hipped_roof_has_a_ridge_half_a_width_short_of_each_end() -> None:
    built = _built(box(0, 0, 10, 6), **{"roof:shape": "hipped"})
    ridge = built.roof.vertices[np.isclose(built.roof.vertices[:, 2], built.roof.vertices[:, 2].max())]
    assert sorted(set(map(tuple, ridge[:, :2].round(6)))) == [(3.0, 3.0), (7.0, 3.0)]
    assert built.gables.triangle_count == 0 and built.roof.triangle_count == 6
    square = _built(box(0, 0, 8, 8), **{"roof:shape": "hipped"})
    apex = square.roof.vertices[np.isclose(square.roof.vertices[:, 2], square.roof.vertices[:, 2].max())]
    assert np.allclose(apex[:, :2], 4.0) and square.roof.triangle_count == 4
    # On a footprint far from a rectangle a hipped roof is a pyramid from the centroid, and says so.
    for polygon in (L_SHAPE, HEXAGON):
        fan = _built(polygon, **{"roof:shape": "hipped"})
        assert fan.shape == "pyramidal" and fan.record["approximation"] == "hipped drawn as pyramidal: footprint not rectangular"
        assert "direction_origin" not in fan.record
    near = _built(Polygon([(0, 0), (10, 0.2), (10, 6), (0.1, 6)]), **{"roof:shape": "hipped"})
    assert near.shape == "hipped" and near.gables.triangle_count > 0


def test_pyramid_and_dome_rise_to_one_apex_over_the_centroid() -> None:
    pyramid = _built(HEXAGON, **{"roof:shape": "pyramidal"})
    assert pyramid.roof.triangle_count == 6 and pyramid.gables.triangle_count == 0
    apex = pyramid.roof.vertices[np.argmax(pyramid.roof.vertices[:, 2])]
    assert apex[:2] == pytest.approx((4.0, 4.0)) and pyramid.rise_m == pytest.approx(math.tan(math.radians(30)) * HEXAGON.exterior.distance(HEXAGON.centroid))
    dome = _built(HEXAGON, **{"roof:shape": "dome"})
    assert dome.roof.triangle_count == 6 * (2 * 3 + 1) and dome.rise_m == pytest.approx(HEXAGON.exterior.distance(HEXAGON.centroid))
    heights = sorted(set(dome.roof.vertices[:, 2].round(6)))
    rings = [round(EAVE + dome.rise_m * math.sin(k * math.pi / 8), 6) for k in range(4)]
    assert heights == pytest.approx([*rings, EAVE + dome.rise_m])  # three rings on a quarter circle, then the apex
    assert _built(box(0, 0, 100, 100), **{"roof:shape": "dome"}).rise_m == MAX_DOME_RISE_M
    assert _built(box(0, 0, 100, 100), **{"roof:shape": "dome", "roof:height": "20"}).rise_m == 20.0


def test_the_default_rise_is_capped_and_a_tagged_one_is_not() -> None:
    assert _built(box(0, 0, 60, 40), **{"roof:shape": "gabled"}).rise_m == DEFAULT_MAX_RISE_M
    assert _built(box(0, 0, 60, 40), **{"roof:shape": "gabled", "roof:height": "9"}).rise_m == 9.0
    assert _built(box(0, 0, 60, 40), **{"roof:shape": "gabled", "roof:angle": "45"}).rise_m == pytest.approx(20.0)
    assert _built(box(0, 0, 60, 40), **{"roof:shape": "skillion", "roof:angle": "45"}).rise_m == pytest.approx(40.0)
    assert _built(box(0, 0, 60, 40), **{"roof:shape": "hipped", "roof:angle": "45"}).rise_m == pytest.approx(20.0)
    assert _built(box(0, 0, 60, 40), **{"roof:shape": "pyramidal", "roof:angle": "45"}).rise_m == pytest.approx(20.0)
    assert with_rise(_spec(**{"roof:shape": "gabled"}), 1.5).rise_m == 1.5
    assert _built(box(0, 0, 10, 6), **{"roof:shape": "gabled", "roof:levels": "1"}).record["rise_origin"] == "observed"


def test_what_cannot_be_drawn_comes_back_as_a_reason() -> None:
    courtyard = Polygon([(0, 0), (20, 0), (20, 20), (0, 20)], [[(5, 5), (15, 5), (15, 15), (5, 15)]])
    assert build_roof(courtyard, EAVE, _spec(**{"roof:shape": "gabled"})) == "footprint_has_courtyard"
    many = Polygon([(10 * math.cos(2 * math.pi * k / (MAX_ROOF_VERTICES + 1)), 10 * math.sin(2 * math.pi * k / (MAX_ROOF_VERTICES + 1)))
                    for k in range(MAX_ROOF_VERTICES + 1)])
    assert build_roof(many, EAVE, _spec(**{"roof:shape": "dome"})) == "too_many_vertices"
    enough = Polygon(list(many.exterior.coords)[:MAX_ROOF_VERTICES])
    assert isinstance(build_roof(enough, EAVE, _spec(**{"roof:shape": "dome"})), BuiltRoof)
    sliver = box(0, 0, 20, 0.9)
    for shape in ("gabled", "skillion", "hipped"):
        assert build_roof(sliver, EAVE, _spec(**{"roof:shape": shape})) == "footprint_too_narrow"
        # Exactly 1 m across the slope is wide enough.
        assert isinstance(build_roof(box(0, 0, 20, 1.0), EAVE, _spec(**{"roof:shape": shape, "roof:height": "1"})), BuiltRoof)
    assert isinstance(build_roof(box(0, 0, 20, 1.9), EAVE, _spec(**{"roof:shape": "skillion"})), BuiltRoof)
    assert isinstance(build_roof(box(0, 0, 1.1, 1.1), EAVE, _spec(**{"roof:shape": "hipped"})), BuiltRoof)
    assert isinstance(build_roof(box(0, 0, 1.1, 1.1), EAVE, _spec(**{"roof:shape": "pyramidal", "roof:height": "1"})), BuiltRoof)
    for shape in ("gabled", "skillion", "hipped", "pyramidal", "dome"):
        assert build_roof(box(0, 0, 10, 6), EAVE, _spec(**{"roof:shape": shape, "roof:height": "0.05"})) == "rise_too_small"
        assert isinstance(build_roof(box(0, 0, 10, 6), EAVE, _spec(**{"roof:shape": shape, "roof:height": "0.1"})), BuiltRoof)
    # A jog the centroid cannot see would fold a fan under the roof.
    jog = Polygon([(0.04, 0), (5, 0), (5, 3.6), (0, 3.6), (0, 3.4), (0.04, 3.4)])
    for shape in ("pyramidal", "dome"):
        assert build_roof(jog, EAVE, _spec(**{"roof:shape": shape})) == "centroid_does_not_see_every_eave"
    assert isinstance(build_roof(Polygon([(0, 0), (5, 0), (5, 0), (5, 4), (0, 4)]), EAVE, _spec(**{"roof:shape": "pyramidal"})), BuiltRoof)
    # A repeated first vertex is skipped and the jog after it is still seen.
    doubled = Polygon([(0.04, 0), (0.04, 0), (5, 0), (5, 3.6), (0, 3.6), (0, 3.4), (0.04, 3.4)])
    assert build_roof(doubled, EAVE, _spec(**{"roof:shape": "pyramidal"})) == "centroid_does_not_see_every_eave"
    # Two 2 cm steps on the centroid's own line: their faces would be edge-on.
    edge_on = Polygon([(0.02, 0), (4, 0), (4, 1), (3.98, 1), (3.98, 2), (0, 2), (0, 1), (0.02, 1)])
    assert build_roof(edge_on, EAVE, _spec(**{"roof:shape": "pyramidal"})) == "centroid_does_not_see_every_eave"


def test_a_cut_through_a_corner_snaps_onto_it() -> None:
    ring = np.array([(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)])
    piece = Polygon([(10, 4.5e-16), (10, 0), (0, 0), (3, 3), (7, 3)])
    snapped = _snap(piece, ring)
    assert snapped is not None and list(snapped.exterior.coords)[:-1] == [(10.0, 0.0), (0.0, 0.0), (3.0, 3.0), (7.0, 3.0)]
    assert _snap(Polygon([(10, 1e-8), (10, 0), (5, 1e-9)]), ring) is None
    assert _snap(Polygon([(10, 1e-6), (10, 0), (5, 0.5)]), ring) is not None
    assert _snap(Polygon([(0, 1e-8), (10, 0), (10, 6), (0, 0)]), ring) is not None
    # The last vertex snapped onto the first is dropped too.
    closing = _snap(Polygon([(0, 0), (10, 0), (10, 6), (1e-9, 0)]), ring)
    assert closing is not None and sorted(closing.exterior.coords[:-1]) == [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0)]
    assert _snap(Polygon([(0, 0), (5, 0), (10, 0)]), ring) is None  # three points on a line: no area


def test_gables_rise_only_where_the_roof_leaves_the_eaves() -> None:
    ring = np.array([(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)])
    tops = {(0.0, 0.0): EAVE, (4.0, 0.0): EAVE, (4.0, 2.0): EAVE + 1.0, (2.0, 2.0): EAVE + 2.0, (0.0, 2.0): EAVE + 1.0}
    gables = _gables(ring, EAVE, tops)
    # The east wall is a triangle, the north wall a pentagon through the ridge point, the west wall a triangle.
    assert gables.triangle_count == 1 + 3 + 1
    assert _gables(ring, EAVE, {k: EAVE for k in tops}).triangle_count == 0
    # A repeated vertex (an edge of no length) is skipped, and the edges after it still get their gables.
    doubled = np.array([(0.0, 0.0), (0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)])
    assert _gables(doubled, EAVE, tops).triangle_count == gables.triangle_count
    # A point 1e-5 m off a 1 mm edge is not on it.
    narrow = np.array([(0.0, 0.0), (0.001, 0.0), (0.001, 2.0), (0.0, 2.0)])
    near = {(0.0, 0.0): EAVE + 1, (0.001, 0.0): EAVE + 1, (0.0005, 1e-5): EAVE + 2, (0.001, 2.0): EAVE, (0.0, 2.0): EAVE}
    assert _gables(narrow, EAVE, near).triangle_count == 2 + 1 + 1
    normals = gables.face_normals()
    assert normals[0][0] > 0 and normals[-1][0] < 0 and all(n[1] > 0 for n in normals[1:4])


@pytest.mark.parametrize(("rule", "origin"), [("ndsm_spread", "estimated"), ("default_pitch", "estimated"), ("roof:height", "observed"),
                                               ("roof:levels", "observed"), ("roof:angle", "observed")])
def test_a_rise_from_no_tag_is_estimated(rule: str, origin: str) -> None:
    spec = PitchedRoof("gabled", "gabled", "roof_tile", 1.5, rule, None, None, False)
    built = build_roof(box(0, 0, 20, 10), EAVE, spec)
    assert isinstance(built, BuiltRoof)
    assert built.record["rise_rule"] == rule and built.record["rise_origin"] == origin and built.record["direction_origin"] == "estimated"


def test_an_ndsm_gable_runs_its_ridge_along_the_long_side_and_falls_back_on_a_courtyard() -> None:
    from onward_generator.detail.lidar_roofs import RoofSpread, spread_spec

    spec = spread_spec(RoofSpread("measured", 10.0, 11.5, 20, 24), "roof_tile")
    built = build_roof(box(0, 0, 20, 10), EAVE, spec)
    assert isinstance(built, BuiltRoof) and built.rise_m == 1.5
    ridge = built.roof.vertices[built.roof.vertices[:, 2] > EAVE + 1e-6]
    assert np.allclose(ridge[:, 1], 5.0) and sorted(set(ridge[:, 0].round(6))) == [0.0, 20.0]
    turned = build_roof(box(0, 0, 10, 20), EAVE, spec)
    assert isinstance(turned, BuiltRoof)
    assert np.allclose(turned.roof.vertices[turned.roof.vertices[:, 2] > EAVE + 1e-6][:, 0], 5.0)
    courtyard = box(0, 0, 20, 20).difference(box(5, 5, 15, 15))
    assert build_roof(courtyard, EAVE, spec) == "footprint_has_courtyard"
