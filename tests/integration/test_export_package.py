"""Phase 07.0: Onward export package on the synthetic street block around MURCIA_CENTER.

The Unity half of the adapter needs Unity and the SDK; it is exercised by
tools/unity/onward_adapter/run_export.sh, not here. These tests cover the package
contract the C# side relies on.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from onward_generator.cli import main
from onward_generator.config.loader import read_json_document
from onward_generator.config.models import GeneratorConfig
from onward_generator.config.validation import REPO_ROOT
from onward_generator.export.lightmap import (
    DEFAULT_CLASS_SCALES,
    LIGHTMAP_DENSITY_STEP,
    estimate,
    lightmap_areas,
    material_scales,
    page_budget_density,
    pages_at_least,
    surface_class,
)
from onward_generator.export.normal_map import DEFAULT_STRENGTH, albedo_png, decode_rgb, normal_map
from onward_generator.export.package import (
    EXPORT_CONFIG_DEFAULT,
    MANIFEST_FILE,
    SDK_SPAWN_POINTS,
    ExportError,
    build_package,
    interior_lights,
    sha256_file,
    verify_package,
)
from onward_generator.generation.gltf import pack_glb, read_glb
from tests.conftest import FIXED_NOW, MURCIA_CENTER

REPO = Path(__file__).resolve().parents[2]
EXPORT_PROJECT = REPO / "config" / "murcia-belluga.export.json"
EXAMPLE_PROJECT = REPO / "config" / "murcia-belluga.example.json"
ADAPTER_CS = REPO / "tools" / "unity" / "onward_adapter" / "Editor" / "OnwardMapAdapter.cs"


@pytest.fixture(scope="module")
def inputs(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("export")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--out", str(detail), "--no-preview"]) == 0
    layout = root / "layout.json"
    assert main(["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base),
                 "--detail", str(detail), "--out", str(layout)]) == 0
    return detail, layout


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def package(inputs: tuple[Path, Path], out: Path, **kw: Any):  # type: ignore[no-untyped-def]
    detail, layout = inputs
    return build_package(kw.pop("project", EXPORT_PROJECT), kw.pop("detail", detail), kw.pop("layout", layout), out,
                         now=FIXED_NOW, **kw)


def test_export_project_is_centred_on_murcia() -> None:
    doc, path = read_json_document(EXPORT_PROJECT)
    assert GeneratorConfig.from_document(doc, path).area.center == MURCIA_CENTER


def test_package_is_complete_and_verifiable(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    result = package(inputs, tmp_path / "pkg")
    out, m = result.directory, result.manifest
    assert sorted(p.name for p in out.iterdir()) == sorted(
        ["onward_export.json", "adapter_options.json", "quest_acceptance.md", "scene_visual.glb", "scene_collision.glb",
         "layout.json", "thumbnail.png"])
    assert verify_package(out) == []
    assert load(out / MANIFEST_FILE) == m
    assert m["adapter"] == {"name": "onward_unity", "version": "0.1.0", "device_verified": False}
    assert m["target"] == {"unity": "2021.3.23f1", "sdk": "1.15", "platform": "quest"}
    assert re.fullmatch(r"murcia_belluga_[0-9a-f]{10}", m["package_id"])
    assert load(out / "adapter_options.json")["variant"] == m["package_id"]
    detail, layout = inputs
    by_role = {f["role"]: f for f in m["files"]}
    assert by_role["visual"]["sha256"] == sha256_file(detail / "scene_visual.glb")
    assert by_role["layout"]["sha256"] == sha256_file(layout)
    attribution = load(detail / "scene_detail.json")["attribution"]
    assert m["attribution"] == attribution and attribution
    for a in attribution:
        assert f"{a['text']} ({a['license']})" in m["map"]["description"]
    assert any("export_adapter_unverified" in w for w in m["warnings"])
    checklist = (out / "quest_acceptance.md").read_text(encoding="utf-8")
    assert "Uplink" in checklist and "Free Roam" in checklist and m["package_id"] in checklist
    assert "docs/12-export.md" in checklist and "este `quest_acceptance.md` esté completo" in checklist


def test_the_package_estimates_its_unity_triangles(inputs: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Plan Q2: the GLB's triangles plus the adapter's 12 300 and its climb models, against the 250 000 limit."""
    from onward_generator.export.triangle_budget import climb_triangles, visual_triangles

    detail, layout = inputs
    m = package(inputs, tmp_path / "pkg").manifest
    glb = visual_triangles((detail / "scene_visual.glb").read_bytes())
    climbs = climb_triangles(load(layout))
    assert m["triangle_budget"] == {
        "estimated_unity_triangles": glb + 12_300 + climbs, "visual_triangles": glb, "adapter_triangles": 12_300,
        "climb_triangles": climbs, "limit": 250_000, "within": True, "origin": "estimated",
    }
    assert not [w for w in m["warnings"] if "[unity_triangle_budget]" in w]
    monkeypatch.setattr("onward_generator.export.triangle_budget.UNITY_TRIANGLE_LIMIT", glb)
    over = package(inputs, tmp_path / "over").manifest
    assert over["triangle_budget"]["within"] is False and over["triangle_budget"]["limit"] == glb
    assert [w for w in over["warnings"] if "[unity_triangle_budget]" in w] == [
        f"triangle_budget: an estimated {glb + 12_300 + climbs} Unity triangles, over the {glb} limit [unity_triangle_budget]"]


FAKE_UNITY = """#!/usr/bin/env bash
# Writes the scene report the real BuildScene would, and the adapter's OK marker.
while [[ $# -gt 0 ]]; do
  case "$1" in -logFile) log="$2"; shift ;; -exportReport) report="$2"; shift ;; esac
  shift
done
echo '[OnwardAdapter] OK' > "$log"
printf '{"triangles": %s}' "$FAKE_TRIANGLES" > "$report"
"""


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
@pytest.mark.parametrize(("triangles", "line"), [
    (250_000, "[export] triangles: 250000 in Unity of 250000"),
    (250_001, "[export] WARNING triangles: 250001 in Unity, over the 250000 limit; push will refuse this package [unity_triangle_budget]"),
])
def test_the_scene_step_reports_unity_s_triangles(inputs: tuple[Path, Path], tmp_path: Path, triangles: int, line: str) -> None:
    """Plan Q2: after the bake, run_export.sh prints Unity's own count against the limit. No Unity: a fake writes the report."""
    import os
    import subprocess
    import sys

    out = package(inputs, tmp_path / "pkg").directory
    unity = tmp_path / "unity"
    unity.write_text(FAKE_UNITY, encoding="utf-8")
    unity.chmod(0o755)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "ONWARD_PLATFORM": "linux", "UNITY": str(unity),
           "PYTHON": sys.executable, "LOG_DIR": str(tmp_path / "logs"), "FAKE_TRIANGLES": str(triangles)}
    script = REPO_ROOT / "tools" / "unity" / "onward_adapter" / "run_export.sh"
    result = subprocess.run(["bash", str(script), str(out), "scene"], env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert line in (result.stdout + result.stderr).splitlines()


def test_package_id_depends_only_on_content(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    a = package(inputs, tmp_path / "a").manifest["package_id"]
    b = package(inputs, tmp_path / "b").manifest["package_id"]
    assert a == b
    cfg = load(EXPORT_CONFIG_DEFAULT)
    cfg["build"]["combine_cell_m"] = 50.0
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert package(inputs, tmp_path / "c", export_config=tmp_path / "cfg.json").manifest["package_id"] != a


def test_existing_output_is_never_overwritten(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    out = tmp_path / "pkg"
    package(inputs, out)
    before = sha256_file(out / MANIFEST_FILE)
    with pytest.raises(FileExistsError):
        package(inputs, out)
    assert sha256_file(out / MANIFEST_FILE) == before


def test_export_disabled_project_is_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    with pytest.raises(ExportError, match="/export must be"):
        package(inputs, tmp_path / "pkg", project=EXAMPLE_PROJECT)
    assert not (tmp_path / "pkg").exists()


def test_failed_phase04_checks_are_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail = tmp_path / "detail"
    shutil.copytree(inputs[0], detail)
    report = load(detail / "detail_report.json")
    report["phase_04_checks"]["automatic_checks_passed"] = False
    (detail / "detail_report.json").write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ExportError, match="automatic checks did not pass"):
        package(inputs, tmp_path / "pkg", detail=detail)
    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize(
    ("envelope", "refused"),
    [(None, True), ({"id": "envelope/perimeter_wall", "fit": None}, True),
     ({"id": "envelope/perimeter_wall", "fit": {"checked": 3, "visual_sha256": "a" * 64}}, False)],
)
def test_an_overgrown_scene_needs_the_fit_bounds_recorded(
    inputs: tuple[Path, Path], tmp_path: Path, envelope: dict | None, refused: bool,
) -> None:
    detail = tmp_path / "detail"
    shutil.copytree(inputs[0], detail)
    manifest = load(detail / "scene_detail.json")
    manifest["objects"].append({"id": "overgrow/prop/barrel/0", "type": "overgrowth", "kind": "waste", "position_m": [0, 0, 0]})
    if envelope is not None:
        manifest["envelope"] = envelope
    (detail / "scene_detail.json").write_text(json.dumps(manifest), encoding="utf-8")
    if refused:
        with pytest.raises(ExportError) as refusal:
            package(inputs, tmp_path / "pkg", detail=detail)
        assert "detail: the scene has overgrowth but no envelope.fit; run overgrow fit, then bounds" in refusal.value.problems
        assert not (tmp_path / "pkg").exists()
    else:
        package(inputs, tmp_path / "pkg", detail=detail)
        assert (tmp_path / "pkg").is_dir()


def test_missing_attribution_is_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    detail = tmp_path / "detail"
    shutil.copytree(inputs[0], detail)
    manifest = load(detail / "scene_detail.json")
    manifest["attribution"] = []
    (detail / "scene_detail.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExportError, match="attribution"):
        package(inputs, tmp_path / "pkg", detail=detail)


@pytest.mark.parametrize(("mutate", "match"), [
    (lambda d: d.update(schema="other"), "schema must be"),
    (lambda d: d["attackers"].update(points_m=d["attackers"]["points_m"][:SDK_SPAWN_POINTS - 1]), "attackers has 7 spawn points"),
    (lambda d: d.pop("uplink_m"), "uplink_m"),
    (lambda d: d.update(level_size_m=0), "level_size_m"),
    (lambda d: d["damage_volume"].pop("size_m"), "damage_volume"),
])
def test_invalid_layout_is_refused(inputs: tuple[Path, Path], tmp_path: Path, mutate: Any, match: str) -> None:
    doc = load(inputs[1])
    mutate(doc)
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ExportError, match=match):
        package(inputs, tmp_path / "pkg", layout=layout)
    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize(("mutate", "match"), [
    (lambda c: c["map"].update(id="murcia belluga"), "/map/id"),
    (lambda c: c["adapter"].update(version="9.9.9"), "/adapter/version"),
    (lambda c: c["target"].update(sdk="1.16"), "/target/sdk"),
    (lambda c: c["build"].update(lightmap_max_size=3000), "/build/lightmap_max_size"),
    (lambda c: c["build"].update(interior_lights_max=257), "/build/interior_lights_max"),
    (lambda c: c["build"].update(interior_light_intensity=0), "/build/interior_light_intensity"),
    (lambda c: c["build"].update(fog_color="grey"), "/build/fog_color"),
    (lambda c: c["build"].update(albedo_compression="astc_8x8"), "/build/albedo_compression"),
    (lambda c: c["build"].update(albedo_compression=None), "/build/albedo_compression"),
    (lambda c: c["build"].update(fog_end_m=0), "/build/fog_end_m"),
    (lambda c: c["build"].update(lightmap_class_scales={"roof": 0}), "/build/lightmap_class_scales/roof"),
    (lambda c: c["build"].update(lightmap_class_scales={"roof": 1.5}), "/build/lightmap_class_scales/roof"),
    (lambda c: c["build"].update(lightmap_class_scales={"props": 0.5}), "/build/lightmap_class_scales"),
    (lambda c: c["build"].update(scenic_out_of_lightmap="yes"), "/build/scenic_out_of_lightmap"),
    (lambda c: c["build"].update(scenic_out_of_lightmap=1), "/build/scenic_out_of_lightmap"),
    (lambda c: c["build"].update(scenic_out_of_lightmap=None), "/build/scenic_out_of_lightmap"),
    (lambda c: c["build"].update(scenic_out_of_lightmaps=True), "scenic_out_of_lightmaps"),
    (lambda c: c["build"].update(lightmap_pages_max=0), "/build/lightmap_pages_max"),
    (lambda c: c["build"].update(lightmap_pages_max=-1), "/build/lightmap_pages_max"),
    (lambda c: c["build"].update(lightmap_pages_max=2.5), "/build/lightmap_pages_max"),
    (lambda c: c["build"].update(lightmap_pages_max="8"), "/build/lightmap_pages_max"),
    (lambda c: c["build"].update(lightmap_pages_max=True), "/build/lightmap_pages_max"),
    (lambda c: c["build"].update(lightmap_pages_max=None), "/build/lightmap_pages_max"),
    (lambda c: c["build"].update(lightmap_pages_max=65), "/build/lightmap_pages_max"),
    (lambda c: c["build"].update(lightmap_page_max=8), "lightmap_page_max"),
    # Plan D5: one facade_trim_* material, a bounded strength that needs it.
    (lambda c: c["build"].update(lit_normal_material="roof_plain"), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material="party_facade_trim_mediterranean"), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material="scenic_trim_mediterranean"), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_"), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_Mediterranean"), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_" + "a" * 53), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material=""), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material=1), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material=None), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material=["facade_trim_a", "facade_trim_b"]), "/build/lit_normal_material"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_a", lit_normal_strength=0), "/build/lit_normal_strength"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_a", lit_normal_strength=-1), "/build/lit_normal_strength"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_a", lit_normal_strength=8.01), "/build/lit_normal_strength"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_a", lit_normal_strength="2"), "/build/lit_normal_strength"),
    (lambda c: c["build"].update(lit_normal_material="facade_trim_a", lit_normal_strength=None), "/build/lit_normal_strength"),
    (lambda c: c["build"].update(lit_normal_strength=2), "'lit_normal_material' is a dependency of 'lit_normal_strength'"),
    (lambda c: c["build"].update(lit_normals_material="facade_trim_a"), "lit_normals_material"),
])
def test_invalid_export_config_is_refused(inputs: tuple[Path, Path], tmp_path: Path, mutate: Any, match: str) -> None:
    cfg = load(EXPORT_CONFIG_DEFAULT)
    mutate(cfg)
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ExportError, match=match):
        package(inputs, tmp_path / "pkg", export_config=tmp_path / "cfg.json")


def test_scenic_out_of_lightmap_absent_is_byte_identical(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    """Plan C4: the default card has no key, so the options, scales and estimate are those of before."""
    cfg = load(EXPORT_CONFIG_DEFAULT)
    assert "scenic_out_of_lightmap" not in cfg["build"]
    result = package(inputs, tmp_path / "pkg")
    options = load(result.directory / "adapter_options.json")
    assert "scenic_out_of_lightmap" not in options
    build = {k: v for k, v in cfg["build"].items() if k != "lightmap_class_scales"}
    visual = (inputs[0] / "scene_visual.glb").read_bytes()
    names = [str(m.get("name")) for m in read_glb(visual)[0].get("materials") or []]
    scales = material_scales(names, {**DEFAULT_CLASS_SCALES, **cfg["build"].get("lightmap_class_scales", {})})
    assert options == {"variant": result.manifest["package_id"], **build, "lightmap_scales": scales}
    assert result.manifest["lightmap_estimate"] == estimate(
        visual, texels_per_m=float(build["lightmap_texels_per_m"]), max_size=int(build["lightmap_max_size"]),
        cell_m=float(build["combine_cell_m"]), scales=scales)


# Plan D5: the integration fixture card built on workshop/d4 HEAD 02ee02a, before the D5 change. The package id
# hashes the scene, so it was re-pinned on 10 November 2026 when the shop windows gained their #glass panes and more
# props became solid. The adapter options were re-pinned with it: their only change is "variant", the package id.
# Re-pinned again on 11 November 2026 (plans Q5, Q6): the window reveals took the wall's material and the street lamps
# their spacing, which changes the scene; compared with main, the options again differ only in "variant".
GOLDEN_PACKAGE_ID = "murcia_belluga_5f5747aaa9"
GOLDEN_OPTIONS_SHA256 = "62a8410d03134868e8b1e8307780108d9e937bf5b09a9ffd1970a67068a2ce3c"


def test_lit_normal_absent_is_byte_identical(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    """Plan D5: without the key, no normal map, no new role, and the options and package_id of before D5."""
    cfg = load(EXPORT_CONFIG_DEFAULT)
    assert not any(key.startswith("lit_normal") for key in cfg["build"])
    result = package(inputs, tmp_path / "pkg")
    assert not (result.directory / "lit_normal.png").exists()
    assert "lit_normal" not in result.manifest
    assert all(f["role"] != "lit_normal" for f in result.manifest["files"])
    assert not any(w.startswith("build.lit_normal_material") for w in result.warnings)
    options = (result.directory / "adapter_options.json").read_bytes()
    assert not any(key.startswith("lit_normal") for key in json.loads(options))
    assert result.manifest["package_id"] == GOLDEN_PACKAGE_ID
    assert hashlib.sha256(options).hexdigest() == GOLDEN_OPTIONS_SHA256


def _albedo(colour_seed: int) -> bytes:
    """A 24 x 16 deterministic albedo with relief in its luma."""
    y, x = np.mgrid[0:16, 0:24]
    rgb = np.stack([(x * 9 + y * colour_seed) % 256, (x * 3 + y * 11) % 256, (x * y) % 256], axis=-1).astype(np.uint8)
    out = BytesIO()
    Image.fromarray(rgb).save(out, format="PNG")
    return out.getvalue()


@pytest.fixture(scope="module")
def trim_inputs(inputs: tuple[Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    """The fixture scene with two of its materials renamed to facade_trim_test (an embedded PNG) and facade_trim_bare."""
    detail = tmp_path_factory.mktemp("trim") / "detail"
    shutil.copytree(inputs[0], detail)
    doc, blob = read_glb((detail / "scene_visual.glb").read_bytes())
    images = doc.setdefault("images", [])
    png = _albedo(7)
    data = bytearray(blob)
    while len(data) % 4:
        data.append(0)
    doc["bufferViews"].append({"buffer": 0, "byteOffset": len(data), "byteLength": len(png)})
    data.extend(png)
    images.append({"mimeType": "image/png", "bufferView": len(doc["bufferViews"]) - 1})
    textures = doc.setdefault("textures", [])
    textures.append({"source": len(images) - 1})
    plain = [m for m in doc["materials"] if "baseColorTexture" not in (m.get("pbrMetallicRoughness") or {})]
    plain[0]["name"] = "facade_trim_test"
    plain[0].setdefault("pbrMetallicRoughness", {})["baseColorTexture"] = {"index": len(textures) - 1, "texCoord": 0}
    plain[1]["name"] = "facade_trim_bare"
    doc["buffers"] = [{"byteLength": len(data)}]
    (detail / "scene_visual.glb").write_bytes(pack_glb(doc, bytes(data)))
    return detail, inputs[1]


def _lit_card(tmp_path: Path, name: str, **build: Any) -> Path:
    cfg = load(EXPORT_CONFIG_DEFAULT)
    cfg["build"].update(build)
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def test_lit_normal_material_writes_the_derived_normal_map(trim_inputs: tuple[Path, Path], tmp_path: Path) -> None:
    card = _lit_card(tmp_path, "on", lit_normal_material="facade_trim_test")
    off = package(trim_inputs, tmp_path / "off")
    on = package(trim_inputs, tmp_path / "on", export_config=card)
    again = package(trim_inputs, tmp_path / "again", export_config=card)
    out = on.directory
    assert verify_package(out) == []
    entry = next(f for f in on.manifest["files"] if f["role"] == "lit_normal")
    assert entry == {"role": "lit_normal", "path": "lit_normal.png", "sha256": sha256_file(out / "lit_normal.png"),
                     "bytes": (out / "lit_normal.png").stat().st_size, "source": "generated"}
    assert [f["role"] for f in on.manifest["files"]][-1] == "lit_normal"
    visual = (trim_inputs[0] / "scene_visual.glb").read_bytes()
    expected = normal_map(decode_rgb(albedo_png(visual, "facade_trim_test")), DEFAULT_STRENGTH)
    with Image.open(out / "lit_normal.png") as image:
        assert image.mode == "RGB" and (np.asarray(image) == expected).all()
    assert on.manifest["lit_normal"] == {"material": "facade_trim_test", "strength": 2.0, "path": "lit_normal.png",
                                         "width": 24, "height": 16, "astc6x6_bytes": 304, "source": "generated",
                                         "derived_from": "albedo"}
    options = load(out / "adapter_options.json")
    assert options["lit_normal_material"] == "facade_trim_test" and "lit_normal_strength" not in options
    assert on.manifest["package_id"] != off.manifest["package_id"]
    assert on.manifest["package_id"] == again.manifest["package_id"]
    assert (out / "lit_normal.png").read_bytes() == (again.directory / "lit_normal.png").read_bytes()
    assert "lit_normal" not in off.manifest and not (off.directory / "lit_normal.png").exists()


def test_lit_normal_strength_changes_the_map_and_the_package(trim_inputs: tuple[Path, Path], tmp_path: Path) -> None:
    default = package(trim_inputs, tmp_path / "d", export_config=_lit_card(tmp_path, "d", lit_normal_material="facade_trim_test"))
    explicit = package(trim_inputs, tmp_path / "e", export_config=_lit_card(
        tmp_path, "e", lit_normal_material="facade_trim_test", lit_normal_strength=2.0))
    strong = package(trim_inputs, tmp_path / "s", export_config=_lit_card(
        tmp_path, "s", lit_normal_material="facade_trim_test", lit_normal_strength=4))
    assert (strong.directory / "lit_normal.png").read_bytes() != (default.directory / "lit_normal.png").read_bytes()
    assert (explicit.directory / "lit_normal.png").read_bytes() == (default.directory / "lit_normal.png").read_bytes()
    assert strong.manifest["package_id"] != default.manifest["package_id"]
    assert strong.manifest["lit_normal"]["strength"] == 4.0
    assert load(strong.directory / "adapter_options.json")["lit_normal_strength"] == 4


@pytest.mark.parametrize(("material", "match"), [
    ("facade_trim_nope", r"/build/lit_normal_material: material 'facade_trim_nope' is not in the visual GLB "
                         r"\(facade trim materials present: facade_trim_bare, facade_trim_test\)"),
    ("facade_trim_bare", "/build/lit_normal_material: material 'facade_trim_bare' has no base colour texture"),
])
def test_lit_normal_material_without_an_albedo_is_refused(trim_inputs: tuple[Path, Path], tmp_path: Path,
                                                          material: str, match: str) -> None:
    with pytest.raises(ExportError, match=match):
        package(trim_inputs, tmp_path / "pkg", export_config=_lit_card(tmp_path, "c", lit_normal_material=material))
    assert not (tmp_path / "pkg").exists()


@pytest.mark.parametrize(("directional", "warned"), [(False, True), (True, False)])
def test_lit_normal_warns_without_directional_lightmaps(trim_inputs: tuple[Path, Path], tmp_path: Path,
                                                        directional: bool, warned: bool) -> None:
    card = _lit_card(tmp_path, "c", lit_normal_material="facade_trim_test", lightmap_directional=directional)
    result = package(trim_inputs, tmp_path / "pkg", export_config=card)
    found = [w for w in result.warnings if w.startswith("build.lit_normal_material facade_trim_test: ")]
    assert bool(found) is warned
    if warned:
        assert found == ["build.lit_normal_material facade_trim_test: build.lightmap_directional is not true, so the baked "
                         "diffuse ignores the normal map and the relief shows only in the specular term; set "
                         "build.lightmap_directional true for the headset measurement (ADR-033)"]
    assert result.manifest["warnings"] == list(result.warnings)


def test_lit_normal_never_overwrites_a_package(trim_inputs: tuple[Path, Path], tmp_path: Path) -> None:
    card = _lit_card(tmp_path, "c", lit_normal_material="facade_trim_test")
    out = package(trim_inputs, tmp_path / "pkg", export_config=card).directory
    before = sha256_file(out / "lit_normal.png")
    with pytest.raises(FileExistsError):
        package(trim_inputs, out, export_config=card)
    assert sha256_file(out / "lit_normal.png") == before


def test_cli_package_names_the_lit_normal_material(trim_inputs: tuple[Path, Path], tmp_path: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    detail, layout = trim_inputs
    args = ["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout)]
    card = _lit_card(tmp_path, "c", lit_normal_material="facade_trim_test", lit_normal_strength=1.5)
    assert main([*args, "--out", str(tmp_path / "on"), "--config", str(card)]) == 0
    assert ("  lit normal (plan D5 spike, ADR-033 proposed): facade_trim_test on PP_Opaque_Lit, lit_normal.png "
            "24 x 16 px at strength 1.5, about 304 bytes in ASTC 6x6\n") in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "off")]) == 0
    assert "lit normal" not in capsys.readouterr().out


ESTIMATE_KEYS = {"texels_per_m", "max_size", "renderers", "renderers_capped", "texels", "texels_by_class", "pages_at_least"}


def _budget_card(tmp_path: Path, name: str, **build: Any) -> Path:
    """The default card at 512 px pages, where the fixture needs 4 pages at 10 texels/m and 2 at the 4 t/m floor."""
    cfg = load(EXPORT_CONFIG_DEFAULT)
    cfg["build"].update({"lightmap_max_size": 512, **build})
    path = tmp_path / f"{name}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _fixture_areas(inputs: tuple[Path, Path]) -> tuple[Any, list[dict[str, Any]]]:
    visual = (inputs[0] / "scene_visual.glb").read_bytes()
    names = [str(m.get("name")) for m in read_glb(visual)[0].get("materials") or []]
    return lightmap_areas(visual, cell_m=100.0), material_scales(names, DEFAULT_CLASS_SCALES)


def test_lightmap_pages_max_absent_is_byte_identical(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    """Plan C5: a card without the key runs no budget code; options and estimate keep their keys and values."""
    assert "lightmap_pages_max" not in load(EXPORT_CONFIG_DEFAULT)["build"]
    off = package(inputs, tmp_path / "off", export_config=_budget_card(tmp_path, "off"))
    options = load(off.directory / "adapter_options.json")
    assert "lightmap_pages_max" not in options and options["lightmap_texels_per_m"] == 10.0
    lm = off.manifest["lightmap_estimate"]
    assert set(lm) == ESTIMATE_KEYS and lm["pages_at_least"] == 4
    again = package(inputs, tmp_path / "again", export_config=_budget_card(tmp_path, "again"))
    assert again.manifest["package_id"] == off.manifest["package_id"]
    assert (again.directory / "adapter_options.json").read_bytes() == (off.directory / "adapter_options.json").read_bytes()


def test_lightmap_pages_max_lowers_the_adapter_density(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    areas, scales = _fixture_areas(inputs)
    expected = page_budget_density(areas, requested=10.0, pages_max=3, max_size=512, scales=scales)
    assert 4.0 <= expected < 10.0 and pages_at_least(areas, expected + LIGHTMAP_DENSITY_STEP, max_size=512, scales=scales) > 3
    off = package(inputs, tmp_path / "off", export_config=_budget_card(tmp_path, "off"))
    on = package(inputs, tmp_path / "on", export_config=_budget_card(tmp_path, "on", lightmap_pages_max=3))
    options = load(on.directory / "adapter_options.json")
    assert options["lightmap_texels_per_m"] == expected and options["lightmap_pages_max"] == 3
    off_options = load(off.directory / "adapter_options.json")
    assert {k: v for k, v in options.items() if k not in ("variant", "lightmap_texels_per_m", "lightmap_pages_max")} == {
        k: v for k, v in off_options.items() if k not in ("variant", "lightmap_texels_per_m")}
    lm = on.manifest["lightmap_estimate"]
    assert set(lm) == ESTIMATE_KEYS | {"texels_per_m_requested", "lightmap_pages_max"}
    assert (lm["texels_per_m"], lm["texels_per_m_requested"], lm["lightmap_pages_max"]) == (expected, 10.0, 3)
    assert lm["pages_at_least"] <= 3 < off.manifest["lightmap_estimate"]["pages_at_least"]
    assert {k: v for k, v in lm.items() if k not in ("texels_per_m_requested", "lightmap_pages_max")} == estimate(
        (inputs[0] / "scene_visual.glb").read_bytes(), texels_per_m=expected, max_size=512, cell_m=100.0, scales=scales)
    assert on.manifest["tiers"] == off.manifest["tiers"] and on.manifest["view_draws"] == off.manifest["view_draws"]
    assert on.manifest["package_id"] != off.manifest["package_id"]


@pytest.mark.parametrize("pages_max", [4, 5, 64])
def test_lightmap_pages_max_within_budget_keeps_the_density(inputs: tuple[Path, Path], tmp_path: Path, pages_max: int) -> None:
    off = package(inputs, tmp_path / "off", export_config=_budget_card(tmp_path, "off"))
    on = package(inputs, tmp_path / "on", export_config=_budget_card(tmp_path, "on", lightmap_pages_max=pages_max))
    options, off_options = load(on.directory / "adapter_options.json"), load(off.directory / "adapter_options.json")
    assert options["lightmap_texels_per_m"] == 10.0
    assert {k: v for k, v in options.items() if k not in ("variant", "lightmap_pages_max")} == {
        k: v for k, v in off_options.items() if k != "variant"}
    lm = on.manifest["lightmap_estimate"]
    assert lm == {**off.manifest["lightmap_estimate"], "texels_per_m_requested": 10.0, "lightmap_pages_max": pages_max}
    # The card changed, so the package does too, although the density did not.
    assert on.manifest["package_id"] != off.manifest["package_id"]


def test_lightmap_pages_max_unreachable_is_refused(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    with pytest.raises(ExportError) as caught:
        package(inputs, tmp_path / "pkg", export_config=_budget_card(tmp_path, "one", lightmap_pages_max=1))
    assert caught.value.problems == [
        "export config: /build/lightmap_pages_max: build.lightmap_pages_max 1 cannot be met: the lightmap needs at "
        "least 2 pages at 4 texels/m, and the density never goes below the 4 texels/m floor (ADR-005)"
    ]
    assert not (tmp_path / "pkg").exists()


def test_cli_package_prints_the_budget(inputs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    detail, layout = inputs
    args = ["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout)]
    assert main([*args, "--out", str(tmp_path / "on"), "--config", str(_budget_card(tmp_path, "on", lightmap_pages_max=3))]) == 0
    lm = load(tmp_path / "on" / MANIFEST_FILE)["lightmap_estimate"]
    assert (f"  lightmap estimate: {lm['texels']:,} texels at {lm['texels_per_m']:g} texels/m, at least "
            f"{lm['pages_at_least']} pages of 512 px, lowered from 10 t/m by build.lightmap_pages_max 3\n") in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "in"), "--config", str(_budget_card(tmp_path, "in", lightmap_pages_max=4))]) == 0
    lm = load(tmp_path / "in" / MANIFEST_FILE)["lightmap_estimate"]
    assert (f"  lightmap estimate: {lm['texels']:,} texels at 10 texels/m, at least 4 pages of 512 px, "
            "within build.lightmap_pages_max 4\n") in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "off"), "--config", str(_budget_card(tmp_path, "off"))]) == 0
    assert "pages of 512 px\n" in capsys.readouterr().out
    assert main([*args, "--out", str(tmp_path / "no"), "--config", str(_budget_card(tmp_path, "no", lightmap_pages_max=1))]) == 1
    assert "/build/lightmap_pages_max" in capsys.readouterr().err


def test_albedo_compression_reaches_the_adapter_options(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    # ASTC 6x6 by default; "none" rolls the headset back to uncompressed RGBA32 without a code change.
    default = package(inputs, tmp_path / "pkg")
    assert load(default.directory / "adapter_options.json")["albedo_compression"] == "astc_6x6"
    cfg = load(EXPORT_CONFIG_DEFAULT)
    cfg["build"]["albedo_compression"] = "none"
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    rolled_back = package(inputs, tmp_path / "none", export_config=tmp_path / "cfg.json")
    assert load(rolled_back.directory / "adapter_options.json")["albedo_compression"] == "none"
    assert rolled_back.manifest["package_id"] != default.manifest["package_id"]


def test_lightmap_scales_are_resolved_per_visual_material(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    result = package(inputs, tmp_path / "pkg")
    options = load(result.directory / "adapter_options.json")
    assert "lightmap_class_scales" not in options
    scales = {row["material"]: row["scale"] for row in options["lightmap_scales"]}
    names = {m["name"] for m in read_glb((inputs[0] / "scene_visual.glb").read_bytes())[0]["materials"]}
    assert set(scales) <= names
    assert {surface_class(name) for name in scales} == {"roof", "ground", "interior", "party"}
    assert all(scales[name] == DEFAULT_CLASS_SCALES[surface_class(name)] for name in scales)
    lm = result.manifest["lightmap_estimate"]
    assert lm["texels_per_m"] == options["lightmap_texels_per_m"] and lm["max_size"] == options["lightmap_max_size"]
    # Draws per view across the layout's site (this map has no core), out to the layout's far clip.
    views = result.manifest["view_draws"]
    assert views["views"] > 0 and 0 <= views["median"] <= views["p90"] <= views["max"]
    assert (views["cell_m"], views["far_m"]) == (options["combine_cell_m"], 1000.0)
    cfg = load(EXPORT_CONFIG_DEFAULT)
    cfg["build"]["combine_cell_m"] = 50.0
    (tmp_path / "cell.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert package(inputs, tmp_path / "cell", export_config=tmp_path / "cell.json").manifest["view_draws"]["cell_m"] == 50.0
    assert lm["texels"] > 0 and abs(lm["texels"] - sum(lm["texels_by_class"].values())) <= len(lm["texels_by_class"])
    cfg = load(EXPORT_CONFIG_DEFAULT)
    cfg["build"]["lightmap_class_scales"] = {"roof": 1.0, "facade": 0.5}
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    other = package(inputs, tmp_path / "other", export_config=tmp_path / "cfg.json")
    rows = load(other.directory / "adapter_options.json")["lightmap_scales"]
    assert {surface_class(row["material"]): row["scale"] for row in rows} == {"ground": 0.5, "interior": 0.5, "party": 0.5, "facade": 0.5}
    assert other.manifest["lightmap_estimate"]["texels"] != lm["texels"]
    assert other.manifest["package_id"] != result.manifest["package_id"]


def test_more_interior_lights_than_the_adapter_bakes_warns(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    visual = (inputs[0] / "scene_visual.glb").read_bytes()
    lights = interior_lights(visual)
    rooms = [r for n in read_glb(visual)[0]["nodes"] for r in ((n.get("extras") or {}).get("light_anchors") or {}).get("rooms", [])]
    assert lights == len(rooms) >= 3  # two stair interiors and the bakery
    over = "interior lights; the adapter bakes the first"
    assert not [w for w in package(inputs, tmp_path / "pkg").manifest["warnings"] if over in w]
    cfg = load(EXPORT_CONFIG_DEFAULT)
    for cap, warned in ((lights, False), (lights - 1, True)):
        cfg["build"]["interior_lights_max"] = cap
        (tmp_path / f"cfg{cap}.json").write_text(json.dumps(cfg), encoding="utf-8")
        found = [w for w in package(inputs, tmp_path / f"pkg{cap}", export_config=tmp_path / f"cfg{cap}.json").manifest["warnings"]
                 if over in w]
        assert found == ([f"detail: the scene asks for {lights} interior lights; the adapter bakes the first {cap} "
                          "(build.interior_lights_max) in node order. Set interiors.lights_max in the detail configuration to match."]
                         if warned else [])
    del cfg["build"]["interior_lights_max"]
    (tmp_path / "nocap.json").write_text(json.dumps(cfg), encoding="utf-8")
    assert not [w for w in package(inputs, tmp_path / "nocap", export_config=tmp_path / "nocap.json").manifest["warnings"] if over in w]


def test_rooms_without_a_light_are_not_counted() -> None:
    from onward_generator.generation.config import Material
    from onward_generator.generation.gltf import Node, Primitive, build_glb
    from onward_generator.generation.mesh import quad

    face = (Primitive(quad((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)), "m"),)
    anchors = {"rooms": [{"room_id": "a/L0", "light_m": [0, 0, 2.7], "probes_m": []},
                         {"room_id": "a/L1", "light": "over_cap", "probes_m": [0, 0, 1]},
                         {"room_id": "a/L2", "light_m": [0, 0, 5.7], "probes_m": []}]}
    nodes = [Node("interior/a", face, {"light_anchors": anchors}), Node("building/a", face, {}),
             Node("interior/b", face, {"light_anchors": {"rooms": [{"room_id": "b/L0", "light_m": [1, 1, 2.7]}]}})]
    assert interior_lights(build_glb(nodes, {"m": Material((1.0, 1.0, 1.0, 1.0), 0.5)})) == 3


def test_verify_detects_tampering(inputs: tuple[Path, Path], tmp_path: Path) -> None:
    out = package(inputs, tmp_path / "pkg").directory
    with (out / "layout.json").open("ab") as f:
        f.write(b" ")
    assert verify_package(out) == ["layout.json: sha256 differs from the manifest"]
    manifest = load(out / MANIFEST_FILE)
    manifest["files"][0]["path"] = "../escape.glb"
    (out / MANIFEST_FILE).write_text(json.dumps(manifest), encoding="utf-8")
    assert "../escape.glb: package paths must be plain file names" in verify_package(out)


def test_cli_package_and_verify(inputs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    detail, layout = inputs
    args = ["export", "package", "--project", str(EXPORT_PROJECT), "--detail", str(detail), "--layout", str(layout),
            "--out", str(tmp_path / "pkg")]
    assert main(args) == 0
    lm = load(tmp_path / "pkg" / MANIFEST_FILE)["lightmap_estimate"]
    assert (f"  lightmap estimate: {lm['texels']:,} texels at 10 texels/m, "
            f"at least {lm['pages_at_least']} pages of 2048 px\n") in capsys.readouterr().out
    assert main(args) == 1
    assert main(["export", "verify", str(tmp_path / "pkg")]) == 0
    assert main(["export", "package", "--project", str(EXAMPLE_PROJECT), "--detail", str(detail), "--layout", str(layout),
                 "--out", str(tmp_path / "other")]) == 1
    assert not (tmp_path / "other").exists()
    assert main(["export", "bogus"]) == 2


def test_playable_export_refuses_a_scene_without_the_perimeter_wall(tmp_path: Path) -> None:
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"schema": "onward_generator.gameplay_layout"}), encoding="utf-8")
    detail = tmp_path / "detail"
    detail.mkdir()
    with pytest.raises(ExportError, match="perimeter_wall"):
        build_package(EXPORT_PROJECT, detail, layout, tmp_path / "pkg")


def test_default_listing_title_is_apocalipsis_murciana() -> None:
    cfg = load(EXPORT_CONFIG_DEFAULT)
    assert cfg["map"]["title"] == "Apocalipsis Murciana"
    thumb = REPO / "tools" / "unity" / "onward_adapter" / "Editor" / "Thumbnails" / "apocalipsis_murciana.png"
    assert thumb.is_file() and thumb.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_info3_listing_is_rewritten_without_touching_the_bundle_id(tmp_path: Path) -> None:
    from onward_generator.export.info3 import Info3Error, apply_local_listing

    path = tmp_path / "unity-export.local.info3"
    path.write_text(json.dumps({
        "WorkshopItem": {
            "ID": "unity-export.local", "MapName": "Unity Export", "AvgRating": 0.0,
            "GameModes": [1, 4], "ThumbnailUrl": "", "BundleHash": "ABC123",
            "Description": "Your map that has been exported from the UGC Tool",
        },
        "IsSanitized": False,
    }), encoding="utf-8")
    doc = apply_local_listing(path, title="Apocalipsis Murciana",
                              description="Generated test. Data: © OpenStreetMap contributors (ODbL-1.0).")
    assert doc["WorkshopItem"]["MapName"] == "Unity Export"
    assert doc["WorkshopItem"]["ID"] == "unity-export.local"
    assert doc["WorkshopItem"]["BundleHash"] == "ABC123"
    assert doc["WorkshopItem"]["GameModes"] == [4, 10, 1]
    assert "OpenStreetMap" in doc["WorkshopItem"]["Description"]
    other = tmp_path / "workshop.info3"
    other.write_text(json.dumps({
        "WorkshopItem": {"ID": "workshop-123", "MapName": "Other", "BundleHash": "ABC123", "Description": "x"},
    }), encoding="utf-8")
    with pytest.raises(Info3Error, match="only the local test slot"):
        apply_local_listing(other, title="X", description="Y")


def test_unity_adapter_knows_the_shop_glass_and_decal_material_names() -> None:
    # C# cannot be compiled here; at least the names it keys on are the ones the Python stages write.
    from onward_generator.detail.interiors import GLASS_VIEW, SHOP_GLASS
    from onward_generator.signs.shop import DECAL_MATERIAL

    source = ADAPTER_CS.read_text(encoding="utf-8")
    assert f'ViewGlassMaterial = "{GLASS_VIEW}"' in source
    assert f'ShopGlassMaterial = "{SHOP_GLASS}"' in source
    assert f'ShopDecalPrefix = "{DECAL_MATERIAL.format("")}"' in source
    assert 'SdkGlassNames = { "GlassVfx", "Glass" }' in source


def test_unity_adapter_walls_the_lift_cabin_on_three_sides() -> None:
    """From a distance the game draws its cabin without the outer walls: the adapter's shell hides that, door side open."""
    source = ADAPTER_CS.read_text(encoding="utf-8")
    shell = source[source.index("private static Elevator PlaceCabin"):source.index("AddClimbLook(name + \"_Look\"")]
    assert "Box(new Vector3(0f, 1.6f, -1.65f), new Vector3(3.3f, 3.2f, 0.1f))" in shell  # back
    assert shell.count("new Vector3(0.1f, 3.2f, 3.4f)") == 2  # sides
    assert "Box(new Vector3(0f, 3.25f, 0f), new Vector3(3.4f, 0.1f, 3.5f))" in shell  # roof
    assert "1.65f)" not in shell.replace("-1.65f)", "")  # nothing on the door side


def test_unity_adapter_puts_the_sprite_glass_in_the_transparent_queue() -> None:
    """The sprite graph reports queue 2000 in batchmode. A pane left there was drawn among the opaque meshes, the room
    behind it over it, and on the headset a window read as a hole. The decals go one step later still."""
    source = ADAPTER_CS.read_text(encoding="utf-8")
    assert "view.renderQueue = (int)RenderQueue.Transparent;" in source
    assert "view.renderQueue = Math.Max((int)RenderQueue.Transparent, sdkGlass != null ? sdkGlass.renderQueue : 0) + 1;" in source


def test_unity_adapter_matches_the_package_contract() -> None:
    source = ADAPTER_CS.read_text(encoding="utf-8")
    cfg = load(EXPORT_CONFIG_DEFAULT)
    assert f'AdapterName = "{cfg["adapter"]["name"]}"' in source
    assert f'AdapterVersion = "{cfg["adapter"]["version"]}"' in source
    assert 'PackageSchema = "onward_generator.onward_export_package"' in source
    assert 'PackageSchemaVersion = "0.1.0"' in source
    for option in cfg["build"]:
        assert re.search(rf"public \w+ {option} =", source), option
    assert "apocalipsis_murciana.png" in source
    assert "f.role == \"thumbnail\"" in source
    assert "LoadListingThumbnail" in source
    assert "ContributesGi" in source
    # Every combined mesh is unwrapped with a margin of 4 lightmap texels at its own density (a core cell's
    # included, plan E10), not Unity's default fraction.
    assert "Unwrapping.GenerateSecondaryUVSet(combined, LightmapUnwrap(combined, opt.lightmap_texels_per_m * core * ScaleInLightmap(opt, visual.Doc.materials, mat)));" in source
    assert "internal const float LightmapMarginTexels = 4f;" in source
    assert "Mathf.Clamp(LightmapMarginTexels / side, param.packMargin, LightmapMarginMax)" in source
    assert "tree_" in source
    # Overgrow props (material prop_<id>) are lit by probes, not lightmaps: small ones baked black.
    assert 'StartsWith("m_prop_")' in source
    # Grass tufts and vines too: lightmapped, the small cards baked black (Barcelona Sarria).
    assert 'n.Contains("abandoned_grass") || n.Contains("abandoned_vine")' in source
    # The unlit see-through glass and the sign rims take no lightmap texels either.
    assert '|| IsSeeThroughOrRim(n) ||' in source
    assert 'lowerName == "m_" + ViewGlassMaterial || lowerName == "m_" + ShopGlassMaterial' in source
    assert 'lowerName.EndsWith("_sign_edge")' in source
    # They stay in the bake to cast its shadows; only the horizon backdrop leaves GI.
    assert "renderer.receiveGI = ReceiveGI.LightProbes;" in source
    # A combined probe-lit renderer would sample one probe for a whole 100 m cell: it takes the ambient light.
    # Furniture is combined per building and storey instead, inside its rooms, and blends their probes (ADR-031).
    assert "renderer.lightProbeUsage = furnitureMeshes.Contains(name) ? LightProbeUsage.BlendProbes : LightProbeUsage.Off;" in source
    assert 'nodeName.StartsWith("furnish/", StringComparison.Ordinal) && nodeName.Split(\'/\').Length == 3' in source
    assert "cx = FurnitureCellBase - index;" in source and "private const float FurnitureStoreyM = 2.5f;" in source
    assert "if (mats.Any(m => !CastsBakedShadow(m))) flags &= ~StaticEditorFlags.ContributeGI;" in source
    # ADR-013: the adapter reads the light anchors from the visual node extras, all baked.
    assert "public GLightAnchors light_anchors;" in source
    # A room detail left without a light past interiors.lights_max still gives its probes.
    assert source.index("probes.Add(Local(room.probes_m[i]") < source.index("if (room.light_m == null || room.light_m.Length != 3) continue;")
    assert "LightmapBakeType.Baked" in source and "LightProbeGroup" in source
    # An outdoor column needs open sky to its ground and 1 m of room: one on a shop floor or in a gap between
    # footprints baked black and darkened the street probes beside it (Barcelona Sarria). Triggers do not count.
    assert "if (!OpenColumn(hit.position))" in source
    assert "private const float OutdoorProbeClearM = 1f;" in source and "private const float OutdoorProbeClearFromM = 1.2f;" in source
    assert source.count("QueryTriggerInteraction.Ignore") >= 2
    # Each climbed roof gets probes 1 and 3 m over it, before the group is built.
    assert "private static readonly float[] ClimbRoofProbeHeightsM = { 1f, 3f };" in source
    assert source.index("AddClimbProbes(pkg.Layout, probes);") < source.index('AddLightProbes(probes, IsSchema(pkg.Layout.schema, "gameplay_layout"));')
    # ADR-014: linear fog from the options; the backdrop stays out of the lightmap.
    assert "FogMode.Linear" in source and 'n.Contains("horizon_backdrop")' in source
    # The ring alone is drawn with the SDK's unlit graph: lit, it took only the ambient light (Barcelona Sarria).
    assert 'UnlitShaderPath = SdkRoot + "/Internal/Shaders/PP_Opaque_Unlit.shadergraph";' in source
    assert 'BackdropMaterial = "horizon_backdrop";' in source
    assert "string shaderPath = safe == BackdropMaterial ? UnlitShaderPath : LitShaderPath;" in source
    assert source.count("UnlitShaderPath") == 2
    # Lightmap scale per material from adapter_options.json, and the baked texel count in the log and report.
    assert "public LightmapScale[] lightmap_scales" in source
    assert "public string material; public float scale" in source
    assert '\\"lightmap_texels\\":{LightmapTexels()}' in source
    # Albedo: ASTC 6x6 through a TextureImporter by default, RGBA32 assets with "none", 4096 px atlas not downscaled.
    assert 'public string albedo_compression = "astc_6x6";' in source
    # Plan C4: the scenic walls leave the lightmap only with the option, at every ContributesGi call site.
    assert "public bool scenic_out_of_lightmap = false;" in source
    assert 'm.name.ToLowerInvariant().StartsWith("m_scenic_", StringComparison.Ordinal)' in source
    assert "ContributesGi(material) && !(opt != null && opt.scenic_out_of_lightmap && IsScenic(material))" in source
    assert 'ContributesGi(materials[i], opt) ? "gi" : "nogi"' in source
    assert "renderer.scaleInLightmap = ContributesGi(renderer.sharedMaterial, opt) ?" in source
    assert "if (!ContributesGi(renderer.sharedMaterial, opt))" in source
    assert source.count("!ContributesGi(m, opt)") == 2
    # The one-argument rule is called only from the overload.
    assert re.findall(r"ContributesGi\((?:materials\[i\]|renderer\.sharedMaterial|m)\)", source) == []
    assert source.count("ContributesGi(material)") == 1
    # The scenic walls keep ContributeGI: they still cast the baked sun's shadows on the core.
    casts = source[source.index("private static bool CastsBakedShadow"):source.index("private static StaticEditorFlags LightmapFlags")]
    assert "scenic" not in casts
    assert 'AlbedoNone = "none"' in source and 'AlbedoAstc6x6 = "astc_6x6"' in source
    assert "TextureImporterFormat.ASTC_6x6" in source and "AlbedoMinMaxTextureSize = 4096" in source


def test_unity_adapter_promotes_only_the_lit_normal_material() -> None:
    """Plan D5 spike (ADR-033, proposed): the SDK lit graph, the normal map import and the tangents, all behind the option."""
    source = ADAPTER_CS.read_text(encoding="utf-8")
    assert 'LitNormalShaderPath = SdkRoot + "/Internal/Shaders/PP_Opaque_Lit.shadergraph";' in source
    assert 'LitShaderPath = SdkRoot + "/Internal/Shaders/PP_Opaque_Simple_Lit.shadergraph";' in source
    assert 'public string lit_normal_material = "";' in source
    # The everyday shader line is unchanged and the lit graph is used in the promoted branch only.
    assert "string shaderPath = safe == BackdropMaterial ? UnlitShaderPath : LitShaderPath;" in source
    assert source.count("LitNormalShaderPath") == 5
    material = source[source.index("private static Material CreateLitNormalMaterial"):source.index("private static Material FindSdkGlass")]
    assert "AssetDatabase.LoadAssetAtPath<Shader>(LitNormalShaderPath)" in material
    for prop in ("_BaseColor", "_BaseMap", "_BumpMap", "_Smoothness"):
        assert f'"{prop}"' in material
    assert 'material.SetTexture("_BumpMap", normal);' in material and 'material.SetTexture("_BaseMap", atlas);' in material
    assert 'if (specular) material.SetColor("_SpecColor"' in material
    assert "private const float LitNormalSmoothness = 0.1f;" in source
    assert "if (!string.IsNullOrEmpty(litNormal) && safe == litNormal)\n                return CreateLitNormalMaterial(" in source
    # The normal map: NormalMap, linear, ASTC 6x6 on Android, read only from the package's lit_normal file.
    loader = source[source.index("private static Texture2D LoadNormalPng"):source.index("private static void AddTangents")]
    assert "importer.textureType = TextureImporterType.NormalMap;" in loader and "importer.sRGBTexture = false;" in loader
    assert "TextureImporterFormat.ASTC_6x6" in loader and "importer.mipmapEnabled = true;" in loader
    assert source.count("pkg.Path(LitNormalRole)") == 1
    scene = source[source.index("if (!string.IsNullOrEmpty(opt.lit_normal_material))"):]
    assert scene.index("pkg.Path(LitNormalRole)") < scene.index("Material[] materials =")
    assert 'private const string LitNormalRole = "lit_normal";' in source
    package_check = source[source.index("private static Package LoadPackage"):source.index("// ---- glTF subset")]
    assert "m.files.Count(f => f.role == LitNormalRole)" in package_check
    assert "has no lit_normal_material" in package_check and "needs exactly one" in package_check
    # Tangents only on the promoted material's meshes, after the lightmap unwrap.
    combined = source[source.index("private static int BuildCombinedVisual"):source.index("// ---- Entry points")]
    assert combined.index("Unwrapping.GenerateSecondaryUVSet(combined") < combined.index("AddTangents(combined);")
    assert "if (litNormal >= 0 && mat == litNormal)" in combined
    assert "if (litNormal >= 0 && visualMaterials[i].Contains(litNormal))\n                            AddTangents(mesh);" in source
    assert source.count("RecalculateTangents") == 1 and source.count("AddTangents(") == 3
    assert "opt.combine_cell_m, flags, opt, litNormal);" in source


def test_default_fog_colour_is_the_backdrop_haze() -> None:
    """The ring is drawn unlit, so its bottom row on screen is the fog colour and it fades into the fog at any distance."""
    from onward_generator.bounds.envelope import HAZE_HEX

    build = load(EXPORT_CONFIG_DEFAULT)["build"]
    assert build["fog_enabled"] is True and build["fog_end_m"] > build["fog_start_m"]
    assert build["fog_color"] == HAZE_HEX == "#C8CDD2"
