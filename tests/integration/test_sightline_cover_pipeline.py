"""``perf cover`` on the synthetic street block around MURCIA_CENTER (plan C7, ADR-032 C7 amendment).

The stage copies the playable scene, stands catalogue cover on the long open sightlines and leaves its
inputs as they were. A refusal exits 1 and leaves no directory.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from shapely import union_all

from onward_generator.cli import main
from onward_generator.detail.scene import load_base
from onward_generator.dress.geometry import geometry_fingerprint
from onward_generator.export.package import build_package
from onward_generator.gameplay.cover import joined
from onward_generator.gameplay.cover_stage import COVER_REPORT, CoverError, apply_cover, build_site, catalogue_licences
from onward_generator.gameplay.report import REPORT_FILE
from onward_generator.generation.config import Material, load_generation_config
from onward_generator.generation.gltf import Node, Primitive, build_glb, nodes_from_glb, read_glb
from onward_generator.generation.mesh import box
from onward_generator.overgrow.dressing import load_spawns
from onward_generator.overgrow.fit import check_scene
from onward_generator.overgrow.growth import grass_png
from onward_generator.overgrow.library import read_library
from tests.conftest import FIXED_NOW, MURCIA_CENTER

REPO = Path(__file__).resolve().parents[2]
EXPORT_PROJECT = REPO / "config" / "murcia-belluga.export.json"
GEOGRAPHIC = {"building", "surface", "curb", "landmark_part"}


def _prop_glb(path: Path, name: str, length: float, width: float, height: float) -> None:
    mesh = box(-length / 2, length / 2, -width / 2, width / 2, 0.0, height)
    path.write_bytes(build_glb(
        [Node(name, (Primitive(mesh, name, np.zeros((mesh.vertex_count, 2), dtype=np.float32)),), {"type": "prop"})],
        {name: Material((1, 1, 1, 1), 0.9)}, repeat_images={name: grass_png()}, preserve_uvs=True,
    ))


def _library(root: Path, rows: list[tuple[str, str, str, tuple[float, float, float]]]) -> Path:
    root.mkdir(parents=True)
    assets = []
    for asset_id, role, category, size in rows:
        _prop_glb(root / f"{asset_id}.glb", asset_id, *size)
        assets.append({
            "id": asset_id, "file": f"{asset_id}.glb", "category": category, "role": role, "surfaces": ["sidewalk"],
            "anchor": "ground", "lane": "forbid", "clearance_m": 0.0, "use": "test", "license": "CC0-1.0",
        })
    (root / "manifest.json").write_text(json.dumps({"assets": assets}), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def stage(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """The playable directory, its layout and a two-prop catalogue around MURCIA_CENTER."""
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("cover")
    detail = root / "detail"
    # A playable core, as on the bench maps: the wall stands on it and cover stays inside it (ADR-017).
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview",
                 "--core-radius-m", "80"]) == 0
    playable = root / "scene_playable"
    assert main(["bounds", "--base", str(detail), "--out", str(playable)]) == 0
    layout = root / "layout" / "layout.json"
    common = ["--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(playable)]
    assert main(["perf", "layout", *common, "--playable", "--gameplay-report", "--out", str(layout)]) == 0
    measurement = root / "measurement" / "layout.json"
    assert main(["perf", "layout", *common, "--out", str(measurement)]) == 0
    library = _library(root / "library", [
        ("synth_barrier", "barrier", "barriers", (1.6, 0.5, 1.3)), ("synth_barrel", "barrel", "waste", (0.6, 0.6, 0.9)),
    ])
    return {"scene": scene, "heights": heights, "base": base, "detail": detail, "playable": playable, "layout": layout,
            "measurement": measurement, "library": library, "root": root}


def _args(stage: dict[str, Path], out: Path, **override: Path) -> list[str]:
    paths = {**stage, **override}
    return ["perf", "cover", "--scene", str(paths["scene"]), "--heights", str(paths["heights"]), "--base", str(paths["base"]),
            "--detail", str(paths["playable"]), "--layout", str(paths["layout"]), "--library", str(paths["library"]), "--out", str(out)]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree(directory: Path) -> dict[str, str]:
    return {path.name: _sha(path) for path in sorted(directory.iterdir()) if path.is_file()}


def test_cover_stands_catalogue_props_with_provenance_and_leaves_its_inputs(
    stage: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    inputs = _tree(stage["playable"])
    layout_sha = _sha(stage["layout"])
    out = tmp_path / "scene_covered"
    assert main(_args(stage, out)) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("cover: placed ") and lines[0].endswith("(estimated from the 2D navmesh)")
    assert lines[1].startswith("cover: sight ") and lines[1].endswith("(estimated, report only)")
    assert lines[2] == f"wrote {out.resolve()}"
    assert _tree(stage["playable"]) == inputs and _sha(stage["layout"]) == layout_sha
    assert sorted(path.name for path in out.iterdir()) == sorted([
        "bounds_report.json", COVER_REPORT, "detail_report.json", REPORT_FILE, "scene_collision.glb", "scene_detail.json", "scene_visual.glb",
    ])
    assert not [path for path in out.parent.iterdir() if path.name.startswith(".")]

    report = json.loads((out / COVER_REPORT).read_text(encoding="utf-8"))
    assert (out / COVER_REPORT).read_text(encoding="utf-8") == json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    assert (report["schema"], report["schema_version"], report["provenance"], report["blocking"]) == (
        "onward_generator.cover_report", "0.1.0", "estimated", False)
    assert sorted(report) == sorted([
        "schema", "schema_version", "provenance", "blocking", "note", "inputs", "parameters", "caps", "keep_outs", "targets",
        "candidates", "refused_props", "placed", "rejects", "nothing_placed", "added", "before", "after",
    ])
    assert report["note"].startswith("Estimated. Cover props stand where the 2D gameplay report finds")
    assert report["inputs"] == {
        "detail_manifest_sha256": inputs["scene_detail.json"], "visual_sha256": inputs["scene_visual.glb"],
        "collision_sha256": inputs["scene_collision.glb"], "layout_sha256": layout_sha,
    }
    assert report["parameters"] == {"max_sightline_m": 60.0, "target_spacing_m": 12.0, "max_prop_length_m": 3.0, "max_added_tris": 80_000}
    assert report["caps"] == {"props": 24, "triangles": 12_000, "draws": 24, "new_assets": 2, "overgrow_triangles": 0}
    layout_doc = json.loads(stage["layout"].read_text(encoding="utf-8"))
    keep_outs = report["keep_outs"]
    assert keep_outs["spawn_points"] == len(load_spawns(stage["layout"])) > 0
    assert keep_outs["ammo"] == len(layout_doc["ammo_m"]) and keep_outs["hunt_routes"] == len(layout_doc["routes"])
    assert set(keep_outs) == {"spawn_points", "ammo", "climbs", "doors", "hunt_routes", "urban_elements", "solids"}
    assert report["candidates"] == ["synth_barrier", "synth_barrel"]
    # The headline before is the layout's own gameplay report: the same islands, inside the core.
    layout_report = json.loads((stage["layout"].parent / REPORT_FILE).read_text(encoding="utf-8"))
    assert report["before"]["headline"] == layout_report["headline"]
    placed = report["placed"]
    assert 1 <= len(placed) <= 24 and report["nothing_placed"] is None
    assert all(row["triangles"] == 12 and row["primitives"] == 1 for row in placed)
    assert any(row["rays_cut"] > 0 for row in placed)
    assert report["added"] == {"props": len(placed), "triangles": 12 * len(placed), "primitives": len(placed), "new_assets": sorted(
        {row["asset_id"] for row in placed})}
    manifest = json.loads((out / "scene_detail.json").read_text(encoding="utf-8"))
    records = [o for o in manifest["objects"] if o.get("op") == "sightline_cover"]
    assert len(records) == len(placed)
    for record in records:
        assert (record["type"], record["origin"], record["licence"], record["solid"]) == ("overgrowth", "generated", "CC0-1.0", True)
        assert record["asset_id"] in {"synth_barrier", "synth_barrel"}
        assert set(record["reason"]) == {"sample_m", "ray_to_m", "ray_length_m"} and record["reason"]["ray_length_m"] > 60.0
    visual, _ = nodes_from_glb((out / "scene_visual.glb").read_bytes())
    collision, _ = nodes_from_glb((out / "scene_collision.glb").read_bytes())
    props = [n.name for n in visual if "/cover/" in n.name]
    boxes = [n.name for n in collision if "/cover/" in n.name]
    assert len(props) == len(boxes) == len(records)
    assert all(name.startswith("overgrow/prop/") for name in props) and all(name.startswith("overgrow/collider/prop/") for name in boxes)
    assert not any("/cover/" in n.name and n.name.startswith("overgrow/collider/") for n in visual)
    assert not any(n.name.startswith("overgrow/prop/") for n in collision)
    assert any(n.name == "envelope/perimeter_wall#collider" for n in collision)
    assert {p.material for n in collision if "/cover/" in n.name for p in n.primitives} == {"collider"}
    collision_doc, _ = read_glb((out / "scene_collision.glb").read_bytes())
    grey = [m["pbrMetallicRoughness"]["baseColorFactor"] for m in collision_doc["materials"] if m["name"] == "collider"]
    assert grey == [pytest.approx([0.45, 0.45, 0.45, 1.0])]
    # The prop keeps the UVs it has in the catalogue: none are generated for its albedo.
    library = {prop.spec.id: prop for prop in read_library(stage["library"])[0]}
    for node in visual:
        if "/cover/" in node.name:
            expected = library[node.extras["asset_id"]].primitives()
            assert [np.asarray(p.uvs).tolist() for p in node.primitives] == [np.asarray(p.uvs).tolist() for p in expected], node.name
    detail_report = json.loads((out / "detail_report.json").read_text(encoding="utf-8"))
    assert detail_report["cover"]["op"] == "sightline_cover" and detail_report["cover"]["count"] == len(placed)
    assert detail_report["cover"]["geometry"]["identical"] is True and detail_report["cover"]["added_triangles"] == 12 * len(placed)
    old_doc, _ = read_glb((stage["playable"] / "scene_visual.glb").read_bytes())
    new_doc, _ = read_glb((out / "scene_visual.glb").read_bytes())
    assert new_doc.get("extras") == old_doc.get("extras")
    textured = {m["name"] for m in new_doc["materials"] if "baseColorTexture" in m["pbrMetallicRoughness"]}
    assert {f"prop_{row['asset_id']}" for row in placed} <= textured
    before_nodes, _ = nodes_from_glb((stage["playable"] / "scene_visual.glb").read_bytes())
    for old, new in zip(before_nodes, visual, strict=False):
        assert old.name == new.name
        for a, b in zip(old.primitives, new.primitives, strict=True):
            assert (a.uvs is None and b.uvs is None) or np.array_equal(a.uvs, b.uvs), old.name
    checked, problems = check_scene(out, stage["scene"])
    assert problems == [] and checked >= len(records)
    assert manifest["envelope"]["fit"] == {"checked": checked, "visual_sha256": _sha(out / "scene_visual.glb")}
    before = json.loads((stage["playable"] / "scene_detail.json").read_text(encoding="utf-8"))
    assert geometry_fingerprint([o for o in before["objects"] if o.get("type") in GEOGRAPHIC]) == geometry_fingerprint(
        [o for o in manifest["objects"] if o.get("type") in GEOGRAPHIC])
    assert manifest["attribution"] == before["attribution"]

    old, new = report["before"]["headline"], report["after"]["headline"]
    assert new["longest_open_sight_m"] <= old["longest_open_sight_m"]
    assert new["distinct_routes"] >= old["distinct_routes"]
    assert new["cover_per_100m_min_stretch"] >= old["cover_per_100m_min_stretch"]
    assert report["after"]["uncovered_stretches"] < report["before"]["uncovered_stretches"]
    gameplay = json.loads((out / REPORT_FILE).read_text(encoding="utf-8"))
    assert gameplay["headline"] == new and gameplay["layout_sha256"] == layout_sha

    again = tmp_path / "again"
    assert main(_args(stage, again)) == 0
    assert _tree(again) == _tree(out)


def test_the_cover_props_keep_the_ground_joined(stage: dict[str, Path], tmp_path: Path) -> None:
    base, _ = load_base(stage["base"], stage["scene"], stage["heights"], load_generation_config(None))
    result = apply_cover(base, stage["scene"], stage["playable"], stage["layout"], tmp_path / "covered", library=stage["library"])
    assert result.plan.placements
    layout = json.loads(stage["layout"].read_text(encoding="utf-8"))
    site = build_site(base, json.loads((stage["playable"] / "scene_detail.json").read_text(encoding="utf-8")), layout,
                      stage["layout"], stage["scene"], stage["playable"] / "scene_collision.glb")
    room = site.ground.difference(union_all(site.solids)) if site.solids else site.ground
    assert joined(room, [item.footprint for item in result.plan.placements])
    spawns = [tuple(p) for p in (layout["uplink_m"],)] + [(p["x"], p["y"]) for p in layout["ai_m"]]
    for item in result.plan.placements:
        centre = (item.spot.x, item.spot.y)
        assert all(np.hypot(centre[0] - x, centre[1] - y) >= 5.0 for x, y in spawns)
    assert catalogue_licences(stage["library"]) == {"synth_barrel": "CC0-1.0", "synth_barrier": "CC0-1.0"}
    assert MURCIA_CENTER == (-1.12863, 37.98407)


def test_with_nothing_eligible_the_scene_is_copied_and_the_reason_printed(
    stage: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    empty = _library(tmp_path / "empty", [("synth_plant", "plant", "vegetation", (1.0, 1.0, 1.0))])
    out = tmp_path / "copy"
    assert main(_args(stage, out, library=empty)) == 0
    assert capsys.readouterr().out.splitlines()[0] == "cover: nothing placed (no solid catalogue prop meets the cover rule)"
    for name in ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json"):
        assert _sha(out / name) == _sha(stage["playable"] / name), name
    report = json.loads((out / COVER_REPORT).read_text(encoding="utf-8"))
    assert report["placed"] == [] and report["refused_props"] == {"synth_plant": "not_a_cover_role"}
    assert report["before"] == report["after"]


def test_refusals_exit_1_and_leave_no_directory(stage: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(stage["playable"], broken)
    (broken / "scene_collision.glb").unlink()
    old = tmp_path / "old.json"
    layout = json.loads(stage["layout"].read_text(encoding="utf-8"))
    old.write_text(json.dumps({**layout, "schema_version": "0.2.0"}), encoding="utf-8")
    covered = tmp_path / "covered"
    assert main(_args(stage, covered)) == 0
    capsys.readouterr()
    cases = [
        ({"layout": stage["measurement"]}, "cover reads a playable onward_generator.gameplay_layout 0.3.0 layout (perf layout --playable)"),
        ({"layout": tmp_path / "missing.json"}, "missing"),
        ({"layout": old}, "not 'onward_generator.gameplay_layout' '0.2.0'"),
        ({"playable": broken}, "missing scene_collision.glb"),
        ({"playable": stage["detail"]}, "has no envelope/perimeter_wall"),
        ({"playable": covered}, "already holds sightline_cover props"),
    ]
    for override, message in cases:
        out = tmp_path / "out"
        assert main(_args(stage, out, **override)) == 1, message
        err = capsys.readouterr().err
        assert err.startswith("perf: ") and message in err, (message, err)
        assert not out.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["broken", "covered", "old.json"]
    assert main([*_args(stage, tmp_path / "out"), "--generation-config", str(tmp_path / "missing.json")]) == 1
    assert "missing.json" in capsys.readouterr().err and not (tmp_path / "out").exists()
    assert main(_args(stage, covered)) == 1
    assert "refusing to overwrite existing output" in capsys.readouterr().err
    assert main([*_args(stage, covered), "--overwrite"]) == 0
    assert sorted(path.name for path in tmp_path.iterdir()) == ["broken", "covered", "old.json"]


def test_a_scene_fit_refuses_is_not_written(stage: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.gameplay import cover_stage

    problems = [f"overgrow/prop/x/cover/{n} floating_0.10m" for n in range(11)]
    seen: list[dict[str, object]] = []

    def check(work: Path, _scene: Path) -> tuple[int, list[str]]:
        # The check reads the playable manifest's urban elements and signs beside the new visual.
        seen.append(json.loads((work / "scene_detail.json").read_text(encoding="utf-8")))
        return 11, problems

    monkeypatch.setattr(cover_stage, "check_scene", check)
    base, _ = load_base(stage["base"], stage["scene"], stage["heights"], load_generation_config(None))
    with pytest.raises(CoverError) as caught:
        apply_cover(base, stage["scene"], stage["playable"], stage["layout"], tmp_path / "out", library=stage["library"])
    assert str(caught.value) == "the covered scene fails overgrow fit: " + "; ".join(problems[:10])
    assert seen == [json.loads((stage["playable"] / "scene_detail.json").read_text(encoding="utf-8"))]
    assert list(tmp_path.iterdir()) == []


def test_export_reads_the_covered_scene(stage: dict[str, Path], tmp_path: Path) -> None:
    covered = tmp_path / "scene_covered"
    assert main(_args(stage, covered)) == 0
    package = tmp_path / "pkg"
    build_package(EXPORT_PROJECT, covered, stage["layout"], package, now=FIXED_NOW)
    collision, _ = nodes_from_glb((package / "scene_collision.glb").read_bytes())
    assert any("/cover/" in n.name for n in collision) and any(n.name == "envelope/perimeter_wall#collider" for n in collision)
    doc, _ = read_glb((package / "scene_visual.glb").read_bytes())
    cover_materials = {
        doc["materials"][prim["material"]]["name"]
        for node in doc["nodes"] if "/cover/" in node.get("name", "") for prim in doc["meshes"][node["mesh"]]["primitives"]
    }
    assert cover_materials and cover_materials <= {"prop_synth_barrier", "prop_synth_barrel"}
    for material in doc["materials"]:
        assert set(material) <= {"name", "pbrMetallicRoughness", "doubleSided"}
        assert set(material["pbrMetallicRoughness"]) <= {"baseColorFactor", "metallicFactor", "roughnessFactor", "baseColorTexture"}


def test_the_triangle_cap_and_a_nested_output_reach_the_plan(stage: dict[str, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from onward_generator.gameplay import cover_stage

    base, _ = load_base(stage["base"], stage["scene"], stage["heights"], load_generation_config(None))
    out = tmp_path / "a" / "b" / "covered"
    monkeypatch.setattr(cover_stage, "triangle_cap", lambda overgrown: 11)
    result = apply_cover(base, stage["scene"], stage["playable"], stage["layout"], out, library=stage["library"])
    assert result.plan.placements == () and result.plan.caps == cover_stage.Caps(24, 11, 24, 2)
    report = json.loads((out / COVER_REPORT).read_text(encoding="utf-8"))
    assert report["caps"]["triangles"] == 11 and report["nothing_placed"] == "no valid spot"
    assert set(report["rejects"]) >= {"cap_triangles", "no_valid_spot"}
    with pytest.raises(CoverError, match="refusing to overwrite existing output"):
        apply_cover(base, stage["scene"], stage["playable"], stage["layout"], out, library=stage["library"])
