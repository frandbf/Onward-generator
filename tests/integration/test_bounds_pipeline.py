"""Bounds overlay on the synthetic block around MURCIA_CENTER."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.dress.geometry import compare_fingerprints, geometry_fingerprint
from onward_generator.generation.gltf import nodes_from_glb, read_glb
from tests.conftest import MURCIA_CENTER


def test_bounds_adds_wall_without_moving_geography(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    lon, lat = MURCIA_CENTER
    assert (lon, lat) == MURCIA_CENTER
    scene, heights, base = detail_inputs
    assert scene.is_file() and heights.is_file()
    out = tmp_path / "playable"
    assert main(["bounds", "--base", str(base), "--out", str(out)]) == 0
    assert (out / "bounds_report.json").is_file()
    report = json.loads((out / "bounds_report.json").read_text())
    assert report["wall"]["id"] == "envelope/perimeter_wall"
    assert report["geometry"]["identical"] is True
    src = json.loads((base / "scene3d.json").read_text())
    dst = json.loads((out / "scene3d.json").read_text())
    keep = {"building", "surface", "curb", "landmark_part"}
    compare = compare_fingerprints(
        geometry_fingerprint([o for o in src["objects"] if o.get("type") in keep]),
        geometry_fingerprint([o for o in dst["objects"] if o.get("type") in keep]),
    )
    assert compare["identical"], compare
    assert any(o.get("id") == "envelope/perimeter_wall" for o in dst["objects"])
    names = {n.name for n in nodes_from_glb((out / "scene_visual.glb").read_bytes())[0]}
    assert "envelope/perimeter_wall" not in names
    collision = {n.name for n in nodes_from_glb((out / "scene_collision.glb").read_bytes())[0]}
    assert "envelope/perimeter_wall#collider" in collision
    doc, _ = read_glb((out / "scene_collision.glb").read_bytes())
    assert any(n.get("name") == "envelope/perimeter_wall#collider" for n in doc["nodes"])
    # original generate dir is untouched
    assert not any(o.get("id") == "envelope/perimeter_wall" for o in src["objects"])


def test_bounds_on_detail_keeps_phase04_report(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights),
                 "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    out = tmp_path / "detail_playable"
    assert main(["bounds", "--base", str(detail), "--out", str(out), "--height-m", "10"]) == 0
    report = json.loads((out / "detail_report.json").read_text())
    assert report["phase_04_checks"]["automatic_checks_passed"] is True
    assert report["bounds"]["wall"]["height_m"] == 10
    assert (detail / "scene_visual.glb").read_bytes() == (out / "scene_visual.glb").read_bytes()
    collision = {n.name for n in nodes_from_glb((out / "scene_collision.glb").read_bytes())[0]}
    assert "envelope/perimeter_wall#collider" in collision


def test_bounds_backdrop_is_visual_only_and_recorded(
    detail_inputs: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    from onward_generator.bounds.envelope import BACKDROP_ID, BACKDROP_MATERIAL

    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights),
                 "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    out = tmp_path / "playable"
    assert main(["bounds", "--base", str(detail), "--out", str(out), "--backdrop",
                 "--backdrop-offset-m", "4", "--backdrop-height-m", "25"]) == 0
    report = json.loads((out / "bounds_report.json").read_text())
    ring = report["backdrop"]
    printed = capsys.readouterr().out.splitlines()
    quads = ring["quads"]
    assert f"bounds: backdrop {BACKDROP_ID} {quads} quads, {2 * quads} triangles" in printed
    assert isinstance(quads, int) and quads >= 3
    assert ring == {
        "id": BACKDROP_ID, "type": "backdrop", "attribute_origin": "generated",
        "generation": {"method": "horizon_backdrop_ring"}, "offset_m": 4.0, "height_m": 25.0,
        "quads": quads, "material": BACKDROP_MATERIAL, "visual": {"triangles": 2 * quads},
        "collider": {"triangles": 0}, "lightmap": False, "visible": True,
    }
    assert report["geometry"]["identical"] is True
    manifest = json.loads((out / "scene_detail.json").read_text())
    assert manifest["envelope"]["backdrop"] == ring
    assert [o for o in manifest["objects"] if o.get("type") == "backdrop"] == [ring]
    before = nodes_from_glb((detail / "scene_visual.glb").read_bytes())[0]
    after = nodes_from_glb((out / "scene_visual.glb").read_bytes())[0]
    assert [n.name for n in after] == [n.name for n in before] + [BACKDROP_ID]
    # Every other node keeps its extras, light anchors included.
    assert [n.extras for n in after[:-1]] == [n.extras for n in before]
    (prim,) = after[-1].primitives
    assert prim.material == BACKDROP_MATERIAL and prim.mesh.triangle_count == ring["visual"]["triangles"]
    assert prim.mesh.vertices[:, 2].max() == 25.0
    collision = {n.name for n in nodes_from_glb((out / "scene_collision.glb").read_bytes())[0]}
    assert not any(name.startswith(BACKDROP_ID) for name in collision)

    plain = tmp_path / "plain"
    assert main(["bounds", "--base", str(detail), "--out", str(plain)]) == 0
    assert not any(line.startswith("bounds: backdrop") for line in capsys.readouterr().out.splitlines())
    assert json.loads((plain / "bounds_report.json").read_text())["backdrop"] is None
    assert json.loads((plain / "scene_detail.json").read_text())["envelope"]["backdrop"] is None
    # The library default is off too, so other callers keep the old visual GLB.
    from onward_generator.bounds.envelope import apply_bounds

    assert apply_bounds(detail, tmp_path / "library")["backdrop"] is None
    assert (tmp_path / "library" / "scene_visual.glb").read_bytes() == (detail / "scene_visual.glb").read_bytes()


def _overgrown(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> Path:
    """A detail scene that says it came out of overgrow place."""
    scene, heights, base = detail_inputs
    detail = tmp_path / "scene_overgrow"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights),
                 "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    (detail / "overgrow_report.json").write_text("{}", encoding="utf-8")
    return detail


def _fit(directory: Path, **change: object) -> None:
    from onward_generator.dress.geometry import file_sha256

    fit = {"schema": "onward_generator.overgrow_fit", "checked": 3, "rejected": [],
           "visual_sha256": file_sha256(directory / "scene_visual.glb"), **change}
    (directory / "fit.json").write_text(json.dumps(fit), encoding="utf-8")


def test_bounds_records_the_fit_of_an_overgrown_scene(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    detail = _overgrown(detail_inputs, tmp_path)
    _fit(detail)
    out = tmp_path / "scene_playable"
    assert main(["bounds", "--base", str(detail), "--out", str(out)]) == 0
    fit = json.loads((out / "scene_detail.json").read_text())["envelope"]["fit"]
    assert fit == json.loads((out / "bounds_report.json").read_text())["fit"]
    assert fit["checked"] == 3 and len(fit["visual_sha256"]) == 64


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (None, "overgrow output without fit.json; run overgrow fit first"),
        ({"rejected": ["overgrow/prop/x/0 off_wall"]}, "not a passing overgrow fit"),
        ({"schema": "onward_generator.something_else"}, "not a passing overgrow fit"),
        ({"visual_sha256": "0" * 64}, "checked another scene_visual.glb; run overgrow fit again"),
    ],
)
def test_bounds_refuses_an_overgrown_scene_without_its_fit(
    detail_inputs: tuple[Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str], change: dict | None, message: str,
) -> None:
    detail = _overgrown(detail_inputs, tmp_path)
    if change is not None:
        _fit(detail, **change)
    assert main(["bounds", "--base", str(detail), "--out", str(tmp_path / "scene_playable")]) != 0
    assert message in capsys.readouterr().err
    assert not (tmp_path / "scene_playable" / "scene_detail.json").exists()


def test_a_scene_without_overgrowth_needs_no_fit(detail_inputs: tuple[Path, Path, Path], tmp_path: Path) -> None:
    scene, heights, base = detail_inputs
    detail = tmp_path / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights),
                 "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    out = tmp_path / "playable"
    assert main(["bounds", "--base", str(detail), "--out", str(out)]) == 0
    assert json.loads((out / "scene_detail.json").read_text())["envelope"]["fit"] is None
