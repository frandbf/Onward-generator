"""Plan D6 end to end: geo → heights → phase 03 → ``detail --lod2-landmarks`` on the synthetic block at MURCIA_CENTER.

314 is the church of the street block (a landmark, with the observed 40 m bell tower 406), 316 a chapel the LoD2
index does not cover, 317 a plain house next to the church. The synthetic block ZIP holds the church volume (a nave
whose east wall leans 0.4 m past the OSM footprint, a 35 m tower), the house's volume (a neighbour group that must
not be matched to the church) and a kiosk that stands on no footprint. The frame is EPSG:25830 like Madrid's.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest
from shapely.geometry import shape

from onward_generator.detail import detail_scene, load_base, load_detail_config
from onward_generator.detail.__main__ import load_lod2
from onward_generator.detail.__main__ import main as detail_main
from onward_generator.generation import load_generation_config
from onward_generator.generation.gltf import build_glb
from onward_generator.generation.mesh import closed_manifold_defects, merge, signed_volume
from tests.conftest import _detail_inputs
from tests.detail_fixture import BUILDINGS, POIS
from tests.lod2_fixture import ATTRIBUTION, box_group, frame_origin, obj_text, obj_zip, write_index

GEN = load_generation_config()
CFG = load_detail_config()
FIXTURE_BUILDINGS = {
    **BUILDINGS,
    316: ([(-60, 44), (-46, 44), (-46, 56), (-60, 56)], {"building": "chapel"}),
    317: ([(52, 6), (62, 6), (62, 20), (52, 20)], {"building": "residential", "building:levels": "3"}),
}


@pytest.fixture(scope="module")
def block(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path, Path]:
    root = tmp_path_factory.mktemp("lod2")
    scene, heights, base = _detail_inputs(root, FIXTURE_BUILDINGS, POIS)
    origin = frame_origin(scene)
    groups = [
        ("church_nave", box_group(30, 6, 50.4, 22, 14.0)),
        ("church_tower", box_group(36, 22, 44, 30, 35.0)),
        ("house", box_group(52, 6, 62, 20, 10.0)),
        ("kiosk", box_group(70, 40, 74, 44, 3.0)),
    ]
    index = write_index(root / "raw" / "madrid_lod2", {"M-000214": obj_zip(obj_text(groups, origin))})
    return scene, heights, base, index


def _detail(block: tuple[Path, Path, Path, Path], on: bool) -> Any:
    scene, heights, base, index = block
    generated, ref = load_base(base, scene, heights, GEN)
    return detail_scene(generated, ref, heights, scene, CFG, lod2_landmarks=load_lod2(str(index), str(scene)) if on else None)


def _object(detailed: Any, object_id: str) -> Any:
    return next(o for o in detailed.objects if o.id == object_id)


def _glb(detailed: Any) -> tuple[bytes, bytes]:
    materials = dict(GEN.materials) | dict(CFG.materials) | {"collider": GEN.materials[next(iter(GEN.materials))]}
    return build_glb(detailed.visual_nodes(), materials), build_glb(detailed.collision_nodes(), materials)


def test_the_church_is_drawn_with_its_lod2_volume_inside_its_footprint(block: tuple[Path, Path, Path, Path]) -> None:
    off, on = _detail(block, False), _detail(block, True)
    assert not on.critical
    church = _object(on, "building/osm-way-314")
    footprint = shape(next(f for f in json.loads(block[0].read_bytes())["features"] if f["id"] == "osm-way-314")["geometry"])
    lod2 = church.record["lod2"]
    assert lod2["objects"] == ["M-000214:church_nave", "M-000214:church_tower"]
    assert lod2["cod_manzan"] == ["M-000214"] and lod2["license"] == "CC-BY-4.0" and lod2["geometry"]["origin"] == "observed"
    assert lod2["clip"]["outside_area_m2"] == 0.0 and lod2["ground_offset"]["height_m"] == 35.0
    assert church.record["facade"]["representation"] == "observed_lod2"
    assert church.record["facade"]["representative_of_real_volume"] is True
    assert church.record["facade"]["representative_of_real_facade"] is False and church.record["facade"]["needs_custom_model"] is False
    assert church.uv_frame is None and church.glass_colliders == []
    # Within the cap, only the landmark materials, nothing outside the footprint.
    visual = merge([p.mesh for p in church.visual])
    assert 0 < visual.triangle_count <= 2000
    assert {p.material for p in church.visual} == {CFG.landmarks.wall_material, CFG.landmarks.roof_material}
    grown = footprint.buffer(1e-6)
    assert all(grown.covers(shape({"type": "Point", "coordinates": [float(x), float(y)]})) for x, y in visual.vertices[:, :2])
    assert visual.vertices[:, 2].min() == 0.0 and visual.vertices[:, 2].max() == pytest.approx(35.0)
    # Closed colliders, one per group, separate from the visual; no tower prism any more.
    assert len(church.colliders) == 2
    assert all(closed_manifold_defects(m) == 0 and signed_volume(m) > 0 for m in church.colliders)
    assert church.record["collider"]["source"] == "lod2_group_prisms" and church.record["collider"]["parts"] == 2
    assert [o.record["source_ids"][0] for o in off.objects if o.type == "landmark_part"] == ["osm-way-314"]  # the 40 m bell tower
    assert not [o for o in on.objects if o.type == "landmark_part" and o.record["source_ids"][0] == "osm-way-314"]
    # The footprint did not move: the record keeps its id, source and height.
    off_church = _object(off, "building/osm-way-314").record
    assert church.record["source_ids"] == off_church["source_ids"] and church.record["height"] == off_church["height"]


def test_the_rest_of_the_map_is_unchanged_and_the_attribution_names_madrid(block: tuple[Path, Path, Path, Path]) -> None:
    off, on = _detail(block, False), _detail(block, True)
    # The chapel has no LoD2 group: it keeps its placeholder and gets the info issue. The house and the kiosk are not landmarks.
    chapel_issues = [(i.code, i.severity) for i in on.issues if i.object_id == "osm-way-316" and i.code.startswith("landmark_lod2")]
    assert chapel_issues == [("landmark_lod2_no_match", "info")]
    for bid in ("osm-way-316", "osm-way-317", "osm-way-311"):
        a, b = _object(off, f"building/{bid}"), _object(on, f"building/{bid}")
        assert a.record == b.record
        assert [(p.material, p.mesh.vertices.tobytes()) for p in a.visual] == [(p.material, p.mesh.vertices.tobytes()) for p in b.visual]
    assert "lod2" not in _object(on, "building/osm-way-317").record
    landmark_ids = {o.id for o in off.objects} - {o.id for o in on.objects}
    assert all(i.startswith("landmark_tower/osm-way-314/") for i in landmark_ids) and landmark_ids
    assert {o.id for o in on.objects} - {o.id for o in off.objects} == set()
    assert on.attribution == [*off.attribution, {"text": ATTRIBUTION, "license": "CC-BY-4.0",
                                                 "license_url": "https://creativecommons.org/licenses/by/4.0/"}]
    assert on.stats["lod2_landmarks"]["matched"] == 1
    assert on.stats["lod2_landmarks"]["issues"] == {"landmark_lod2_no_match": 1}
    church = _object(on, "building/osm-way-314")
    assert on.stats["lod2_landmarks"]["visual_triangles"] == sum(p.mesh.triangle_count for p in church.visual)
    assert on.stats["lod2_landmarks"]["collision_triangles"] == sum(m.triangle_count for m in church.colliders)
    assert on.inputs["lod2_landmarks"]["path"] == str(block[3]) and on.inputs["lod2_landmarks"]["source"] == "es.madrid_lod2"
    # The kiosk stands on no footprint and the house is no landmark: neither group is drawn.
    drawn = [g for o in on.objects if "lod2" in o.record for g in o.record["lod2"]["objects"]]
    assert drawn == ["M-000214:church_nave", "M-000214:church_tower"]
    assert sum(p.mesh.triangle_count for o in on.objects for p in o.visual) - sum(p.mesh.triangle_count for o in off.objects for p in o.visual) == (
        on.stats["visual_triangles"] - off.stats["visual_triangles"])


def test_two_runs_with_the_option_on_are_identical(block: tuple[Path, Path, Path, Path]) -> None:
    a, b = _detail(block, True), _detail(block, True)
    assert _glb(a) == _glb(b)
    assert json.dumps(a.manifest(), sort_keys=True) == json.dumps(b.manifest(), sort_keys=True)


def _cli(block: tuple[Path, Path, Path, Path], out: Path, *extra: str) -> int:
    scene, heights, base, _ = block
    return detail_main(["--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(out), "--no-preview", *extra])


def test_the_cli_without_the_option_writes_the_same_bytes_and_with_it_adds_the_record(
        block: tuple[Path, Path, Path, Path], tmp_path: Path) -> None:
    assert _cli(block, tmp_path / "plain") == 0
    assert _cli(block, tmp_path / "again") == 0
    names = ("scene_visual.glb", "scene_collision.glb", "scene_detail.json", "detail_report.json")
    assert all((tmp_path / "plain" / n).read_bytes() == (tmp_path / "again" / n).read_bytes() for n in names)
    report = json.loads((tmp_path / "plain" / "detail_report.json").read_bytes())
    assert "lod2_landmarks" not in report["counts"] and "lod2_landmarks" not in report["inputs"]
    assert "Ayuntamiento" not in (tmp_path / "plain" / "scene_detail.json").read_text(encoding="utf-8")
    assert _cli(block, tmp_path / "on", "--lod2-landmarks", str(block[3])) == 0
    assert _cli(block, tmp_path / "on2", "--lod2-landmarks", str(block[3])) == 0
    assert all((tmp_path / "on" / n).read_bytes() == (tmp_path / "on2" / n).read_bytes() for n in names[:2])
    report = json.loads((tmp_path / "on" / "detail_report.json").read_bytes())
    assert report["counts"]["lod2_landmarks"]["matched"] == 1
    manifest = json.loads((tmp_path / "on" / "scene_detail.json").read_bytes())
    assert any(a["text"] == ATTRIBUTION for a in manifest["attribution"])


def test_the_cli_refuses_another_crs_and_a_broken_index_without_writing(
        block: tuple[Path, Path, Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scene, heights, base, index = block
    document = json.loads(scene.read_bytes())
    document["frame"]["crs"] = "EPSG:32630"
    other = tmp_path / "geo_scene.json"
    other.write_text(json.dumps(document), encoding="utf-8")
    code = detail_main(["--scene", str(other), "--heights", str(heights), "--base", str(base), "--out", str(tmp_path / "crs"),
                        "--no-preview", "--lod2-landmarks", str(index)])
    assert code == 1 and not (tmp_path / "crs").exists()
    assert "the LoD2 volumes are in EPSG:25830, but" in capsys.readouterr().err
    empty_zip = io.BytesIO()
    zipfile.ZipFile(empty_zip, "w").close()
    empty = write_index(tmp_path / "empty", {"M-000001": empty_zip.getvalue()})
    out = tmp_path / "kept"
    out.mkdir()
    (out / "scene_detail.json").write_text("old", encoding="utf-8")
    assert _cli(block, out, "--lod2-landmarks", str(empty), "--overwrite") == 1
    assert "block M-000001: the ZIP holds 0 .obj files" in capsys.readouterr().err
    assert (out / "scene_detail.json").read_text(encoding="utf-8") == "old"
