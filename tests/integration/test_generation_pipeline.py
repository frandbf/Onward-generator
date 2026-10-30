"""IT-003/IT-004/IT-005/IT-007: synthetic block around MURCIA_CENTER → geo → heights → 3D scene."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import shapely

from onward_generator.cli import main
from onward_generator.generation import GenerationError, generate_scene, load_generation_config
from onward_generator.generation.config import DEFAULT_CONFIG_PATH, config_from_document
from onward_generator.generation.gltf import read_glb
from onward_generator.generation.markings import ZEBRA_LIFT_M
from onward_generator.generation.surfaces import SurfaceClass, crossing_sites
from onward_generator.geo.pipeline import GeoResult, write_outputs
from onward_generator.heights import run_heights, write_height_outputs

CFG = load_generation_config()


@pytest.fixture
def inputs(pipeline_result: GeoResult, tmp_path: Path) -> tuple[Path, Path]:
    scene, _ = write_outputs(pipeline_result, tmp_path / "geo")
    heights, _ = write_height_outputs(run_heights(scene), tmp_path / "heights")
    return scene, heights


def test_synthetic_block_generates_without_critical_errors(inputs: tuple[Path, Path]) -> None:
    generated = generate_scene(*inputs, CFG)
    report = generated.report()
    assert report["phase_03_exit"]["passed"], report["issues"]
    assert report["checks"]["manifold_defects"] == 0 and report["checks"]["surface_overlap_m2"] < 0.01
    ids = {o.id for o in generated.objects}
    assert {"building/osm-way-101", "building/osm-way-102", "building/osm-relation-201"} <= ids
    assert "building/osm-way-108" not in ids  # duplicate of 101
    assert "building/osm-way-113" not in ids  # leaves the map; omitted whole
    assert "duplicate_building_skipped" in report["issues_by_code"]
    assert {"surface/carriageway", "surface/sidewalk", "surface/pedestrian", "surface/ground", "surface/curb"} <= ids
    assert {"collider/walkable", "collider/carriageway", "collider/walkable_crossing"} <= ids
    # The crossings are cut out of the carriageway slab and sit at its level (ADR-015).
    slabs = {o.id: o.record for o in generated.objects if o.type == "ground_collider"}
    crossing, lane = slabs["collider/walkable_crossing"], slabs["collider/carriageway"]
    assert crossing["top_z_m"] == lane["top_z_m"] == -CFG.carriageway_depth_m
    assert crossing["area_m2"] == pytest.approx(generated.partition.crossings.area, abs=0.01) and crossing["area_m2"] > 0
    road_area = generated.partition.surfaces[SurfaceClass.CARRIAGEWAY].geometry.area
    assert crossing["area_m2"] + lane["area_m2"] == pytest.approx(road_area, abs=0.01)

    b101 = next(o for o in generated.objects if o.id == "building/osm-way-101")
    assert b101.record["height"]["height_m"] == 12.0 and b101.record["height"]["height_source"] == "explicit_height"
    assert b101.record["collider"]["closed"] and b101.record["collider"]["volume_m3"] == pytest.approx(900 * 12.0, rel=1e-3)
    assert b101.record["source"]["license"] == "CC0-1.0"
    road = next(o for o in generated.objects if o.id == "surface/carriageway")
    assert road.record["z_m"] == -CFG.carriageway_depth_m and road.record["source_ids"] == ["osm-way-105"]
    for o in generated.objects:
        if o.type == "building":
            fp = next(p for p in o.visual if p.material == "building_wall").mesh
            assert fp.vertices[:, 2].min() == 0.0 and fp.vertices[:, 2].max() == o.record["height"]["height_m"]


def test_a_map_without_streets_has_no_carriageway_or_crossing_slab(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    scene, _ = inputs
    doc = json.loads(scene.read_text(encoding="utf-8"))
    doc["features"] = [f for f in doc["features"] if f.get("kind") != "highway"]
    edited = tmp_path / "no_streets" / "geo_scene.json"
    edited.parent.mkdir()
    edited.write_text(json.dumps(doc), encoding="utf-8")
    heights, _ = write_height_outputs(run_heights(edited), tmp_path / "no_streets_heights")
    generated = generate_scene(edited, heights, CFG)
    assert generated.report()["phase_03_exit"]["passed"]
    slabs = {o.id for o in generated.objects if o.type == "ground_collider"}
    assert slabs == {"collider/walkable"}
    assert generated.partition.crossings.is_empty


def test_only_osm_crossings_get_zebra_paint(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    """A zebra node and an unmarked node on way 105: one strip painted, one observed bare, the rest generated."""
    scene, heights = inputs
    plain = generate_scene(scene, heights, CFG)
    assert "surface/zebra_crossing" not in {o.id for o in plain.objects}
    assert plain.stats["crossings"]["observed"] == 0 and plain.stats["crossings"]["strips"] > 2
    vehicular = [w for w in plain.ways if w.road_class == "vehicular"]
    carriageway = plain.partition.surfaces[SurfaceClass.CARRIAGEWAY].geometry
    zebra, bare = [s for s in crossing_sites(vehicular) if not s.strip().intersection(carriageway).is_empty][:2]

    doc = json.loads(scene.read_text(encoding="utf-8"))
    doc["features"] += [
        {"id": "osm-node-9001", "kind": "poi", "tags": {"highway": "crossing", "crossing": "zebra"},
         "geometry": {"type": "Point", "coordinates": list(zebra.centre)}},
        {"id": "osm-node-9002", "kind": "poi", "tags": {"highway": "crossing", "crossing": "unmarked"},
         "geometry": {"type": "Point", "coordinates": list(bare.centre)}},
    ]
    edited = tmp_path / "crossings" / "geo_scene.json"
    edited.parent.mkdir()
    edited.write_text(json.dumps(doc), encoding="utf-8")
    edited_heights, _ = write_height_outputs(run_heights(edited), tmp_path / "crossings_heights")
    generated = generate_scene(edited, edited_heights, CFG)
    assert generated.report()["phase_03_exit"]["passed"]

    paint = next(o for o in generated.objects if o.id == "surface/zebra_crossing")
    (primitive,) = paint.visual
    assert primitive.material == "road_marking" and paint.collider is None and paint.type == "surface"
    record = paint.record
    assert (record["id"], record["type"], record["surface_class"], record["materials"]) == (
        "surface/zebra_crossing", "surface", "road_marking", ["road_marking"])
    assert record["generation"] == {"method": "zebra_bars_on_osm_crossings", "attribute_origin": "observed",
                                    "bar_layout": "generated", "bar_m": 0.5, "gap_m": 0.5, "lift_m": ZEBRA_LIFT_M}
    first = record["crossings"][0]
    assert first["centre_m"] == [round(zebra.centre[0], 3), round(zebra.centre[1], 3)]
    assert first["bars"] == generated.stats["crossings"]["bars"] > 0
    counts = generated.stats["crossings"]
    assert counts["observed"] == counts["observed_painted"] + counts["observed_unpainted"] == 2
    assert counts["observed_painted"] == 1 and counts["generated_unpainted"] == plain.stats["crossings"]["strips"] - 2
    assert primitive.mesh.triangle_count == 2 * counts["bars"] == paint.record["visual"]["triangles"]
    z = -CFG.carriageway_depth_m + ZEBRA_LIFT_M
    assert (primitive.mesh.vertices[:, 2] == z).all() and paint.record["z_m"] == pytest.approx(z)
    assert paint.record["source_ids"] == ["osm-node-9001"]
    assert paint.record["generation"]["attribute_origin"] == "observed"
    origins = [(c["origin"], c["painted"], c["evidence"]) for c in paint.record["crossings"][:2]]
    assert origins == [("observed", True, ["osm-node-9001"]), ("observed", False, ["osm-node-9002"])]
    assert all(c["origin"] == "generated" and not c["painted"] for c in paint.record["crossings"][2:])
    assert paint.record["area_m2"] == pytest.approx(counts["bars"] * 0.5 * 4.0, abs=1e-3)
    assert paint.record["osm_crossings_unmatched"] == []
    assert "road_marking" in generated.stats["visual_triangles_by_material"]


def test_heights_from_another_scene_are_rejected(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    scene, heights = inputs
    doc = json.loads(heights.read_text(encoding="utf-8"))
    doc["scene"]["sha256"] = "0" * 64
    other = tmp_path / "other_heights.json"
    other.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GenerationError, match="different geo scene"):
        generate_scene(scene, other, CFG)


def test_missing_height_is_critical(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    scene, heights = inputs
    doc = json.loads(heights.read_text(encoding="utf-8"))
    doc["buildings"] = [b for b in doc["buildings"] if b["building_id"] != "osm-way-102"]
    edited = tmp_path / "edited_heights.json"
    edited.write_text(json.dumps(doc), encoding="utf-8")
    report = generate_scene(scene, edited, CFG).report()
    assert not report["phase_03_exit"]["passed"]
    assert [i["object_id"] for i in report["issues"] if i["severity"] == "critical"] == ["osm-way-102"]


def test_degrees_are_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    scene, heights = inputs
    doc = json.loads(scene.read_text(encoding="utf-8"))
    doc["frame"]["unit"] = "degree"
    bad = tmp_path / "geo_scene.json"
    bad.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(GenerationError):
        generate_scene(bad, heights, CFG)


def test_cli_writes_reproducible_outputs(inputs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene, heights = inputs
    args = ["generate", "--scene", str(scene), "--heights", str(heights)]
    assert main([*args, "--out", str(tmp_path / "a")]) == 0
    assert "phase 03 exit: PASSED" in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "b"), "--no-preview"]) == 0
    for name in ("scene_visual.glb", "scene_collision.glb", "scene3d.json", "generation_report.json"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    doc, _ = read_glb((tmp_path / "a" / "scene_visual.glb").read_bytes())
    assert doc["extras"]["attribution"][0]["license"] == "CC0-1.0"
    assert (tmp_path / "a" / "preview_southwest.png").read_bytes()[:4] == b"\x89PNG"
    assert main([*args, "--out", str(tmp_path / "a")]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Each real phase 03 build, as (scene, heights); the cache starts empty."""
    import onward_generator.generation.scene as scene_module

    builds: list[tuple[str, str]] = []
    real = scene_module._generate_scene

    def build(scene_path: str | Path, heights_path: str | Path, config: object) -> object:
        builds.append((str(scene_path), str(heights_path)))
        return real(scene_path, heights_path, config)  # type: ignore[arg-type]

    monkeypatch.setattr(scene_module, "_generate_scene", build)
    monkeypatch.setattr(scene_module, "_LAST", {})
    return builds


def test_the_same_inputs_build_phase_03_once_per_process(inputs: tuple[Path, Path], counted: list[tuple[str, str]]) -> None:
    first = generate_scene(*inputs, CFG)
    first.objects.clear()
    first.stats["changed"] = True
    second = generate_scene(*inputs, CFG)
    assert len(counted) == 1
    # A caller that edits its scene does not edit the next caller's.
    assert second.objects and "changed" not in second.stats
    assert second.config is CFG
    third = generate_scene(*inputs, CFG)
    assert json.dumps(second.manifest()) == json.dumps(third.manifest())
    assert json.dumps(second.report()) == json.dumps(third.report())
    assert len(counted) == 1
    # Geometries are shared, not copied: a copy would lose the set_precision grid.
    assert second.partition is not third.partition
    assert second.partition.site is third.partition.site
    for surface_class, surface in second.partition.surfaces.items():
        assert surface.geometry is third.partition.surfaces[surface_class].geometry
        assert surface is not third.partition.surfaces[surface_class]
    assert second.ways and all(a.geometry is b.geometry for a, b in zip(second.ways, third.ways, strict=True))
    assert max(shapely.get_precision(s.geometry) for s in second.partition.surfaces.values()) > 0


def test_changed_inputs_or_config_build_phase_03_again(inputs: tuple[Path, Path], tmp_path: Path, counted: list[tuple[str, str]]) -> None:
    scene, heights = inputs
    generate_scene(scene, heights, CFG)
    doc = json.loads(heights.read_text(encoding="utf-8"))
    doc["note"] = "same buildings, other bytes"
    heights.write_text(json.dumps(doc), encoding="utf-8")
    generate_scene(scene, heights, CFG)
    assert len(counted) == 2
    copied = tmp_path / "copy" / "heights.json"
    copied.parent.mkdir()
    copied.write_bytes(heights.read_bytes())
    # The manifest records the resolved path, so another path is another build.
    assert generate_scene(scene, copied, CFG).inputs["heights"]["path"] == str(copied.resolve())
    assert len(counted) == 3
    other = load_generation_config(DEFAULT_CONFIG_PATH)
    assert other.sha256 == CFG.sha256
    generate_scene(scene, copied, other)
    assert len(counted) == 3
    edited = tmp_path / "generation.json"
    edited.write_text(json.dumps(dict(CFG.document)) + "\n", encoding="utf-8")
    generate_scene(scene, copied, load_generation_config(edited))
    assert len(counted) == 4
    # Only the last build is kept.
    generate_scene(scene, heights, CFG)
    assert len(counted) == 5


def test_a_config_without_a_hash_or_a_missing_input_is_never_cached(
    inputs: tuple[Path, Path], tmp_path: Path, counted: list[tuple[str, str]],
) -> None:
    scene, heights = inputs
    unhashed = config_from_document(json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")))
    generate_scene(scene, heights, unhashed)
    generate_scene(scene, heights, unhashed)
    assert len(counted) == 2
    for missing in ((tmp_path / "none.json", heights), (scene, tmp_path / "none.json")):
        with pytest.raises(GenerationError, match="input not found"):
            generate_scene(*missing, CFG)
    assert len(counted) == 4
