"""The street profile (plan D2) through materials and overgrow on the synthetic Simple 3D Buildings block at MURCIA_CENTER.

A stub provider answers ``tests/fixtures/street_profile/applied.json``; no model runs. The profile's flags come
from ``profile_flags`` and are passed to the real stage CLIs. Off, the plan and the stages are as before.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from onward_generator.ai import street_profile as sp
from onward_generator.ai.providers import ProviderResult
from onward_generator.cli import main
from onward_generator.dress.geometry import geometry_fingerprint
from onward_generator.generation.config import Material
from onward_generator.generation.gltf import Node, Primitive, build_glb
from onward_generator.generation.mesh import box
from onward_generator.maps.workspace import create_map, store_sightline_cover, store_street_profile
from onward_generator.overgrow.growth import grass_png
from onward_generator.pipeline_run import plan_steps
from tests.conftest import MURCIA_CENTER_LAT, MURCIA_CENTER_LON, _detail_inputs

REPO = Path(__file__).resolve().parents[2]
APPLIED = (REPO / "tests" / "fixtures" / "street_profile" / "applied.json").read_text(encoding="utf-8")
GEOGRAPHIC = {"building", "surface", "curb", "landmark_part"}


class Stub:
    name = "stub"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system: str, instruction: str, schema: dict[str, Any]) -> ProviderResult:
        self.calls += 1
        if not self.replies:
            raise AssertionError("the provider was called again")
        return ProviderResult(self.replies.pop(0), {"provider": "stub"})


def _library(root: Path) -> Path:
    """Four ground props, one per condition the profile may pick, and a conditionless shrub."""
    root.mkdir(parents=True)
    rows = []
    for asset_id, role, category, condition in (
        ("synth_barrel_burned", "barrel", "waste", "burned"), ("synth_bin_intact", "bin", "waste", "intact"),
        ("synth_car_wrecked", "wreck", "vehicles", "wrecked"), ("synth_crate_worn", "crate", "waste", "worn"),
        ("synth_shrub", "shrub", "vegetation", None),
    ):
        mesh = box(-0.4, 0.4, -0.4, 0.4, 0.0, 0.9)
        (root / f"{asset_id}.glb").write_bytes(build_glb(
            [Node(asset_id, (Primitive(mesh, asset_id, np.zeros((mesh.vertex_count, 2), dtype=np.float32)),), {"type": "prop"})],
            {asset_id: Material((1, 1, 1, 1), 0.9)}, repeat_images={asset_id: grass_png()}, preserve_uvs=True,
        ))
        row: dict[str, Any] = {
            "id": asset_id, "file": f"{asset_id}.glb", "category": category, "role": role, "surfaces": ["sidewalk"],
            "anchor": "ground", "lane": "forbid", "clearance_m": 0.0, "use": "test", "license": "CC0-1.0",
        }
        if condition:
            row["condition"] = condition
        rows.append(row)
    (root / "manifest.json").write_text(json.dumps({"assets": rows}), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    from tests.detail_fixture import S3DB_BUILDINGS, S3DB_RELATIONS

    work = tmp_path_factory.mktemp("street_profile")
    (work / "inputs").mkdir()
    scene, heights, base = _detail_inputs(work / "inputs", S3DB_BUILDINGS, {}, S3DB_RELATIONS)
    detail = work / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    library = _library(work / "library")
    root = work / "map"
    create_map(root, map_id="murcia", name="Murcia", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    store_street_profile(root, True)
    store_sightline_cover(root, True)
    stub = Stub(APPLIED)
    first = sp.resolve_street_profile(root, provider=stub, library=library).record
    replayed = sp.resolve_street_profile(root, provider=Stub(), library=library).record
    flags = sp.profile_flags(root)
    plan = root / "config" / "texture_plan.json"
    out: dict[str, Any] = {"first": first, "replayed": replayed, "flags": flags, "stub_calls": stub.calls, "root": root,
                           "library": library, "detail": detail}

    def materials(name: str, *extra: str) -> None:
        out[name] = work / name
        assert main(["materials", "apply", "--base", str(detail), "--out", str(out[name]), "--plan", str(plan),
                     "--scene", str(scene), "--no-tile-cache", *extra]) == 0

    def overgrow(name: str, base_dir: Path, *extra: str) -> None:
        out[name] = work / name
        argv = ["overgrow", "place", "--base", str(base_dir), "--scene", str(scene), "--profile", "100", "--seed", "48152",
                "--library", str(library), *extra, "--out", str(out[name])]
        assert main(argv) == 0

    materials("materials_off")
    materials("materials_on", *flags["materials"])
    materials("materials_again", *flags["materials"])
    overgrow("overgrow_off", out["materials_off"])
    on = list(flags["overgrow"])
    overgrow("overgrow_on", out["materials_on"], *on)
    overgrow("overgrow_again", out["materials_again"], *on)
    return out


def _report(path: Path, name: str) -> dict[str, Any]:
    return json.loads((path / name).read_text(encoding="utf-8"))


def _buildings(path: Path) -> list[dict[str, Any]]:
    return [o for o in _report(path, "scene_detail.json")["objects"] if o.get("type") == "building"]


def test_on_with_stub_applies_ids(runs: dict[str, Any]) -> None:
    record = runs["first"]
    assert runs["stub_calls"] == 1
    assert record["origin"] == "ai_generated" and record["provenance"] == "ai_generated"
    assert record["ids"] == {"dominant_typology": "mediterranean", "palette": "extended_pools", "props_profile": "wrecked",
                             "abandonment": "years_30", "cover": "light"}
    assert {row["field"]: row["status"] for row in record["changes"]} == dict.fromkeys(sp.FIELDS, "applied")
    rec = str(sp.record_path(runs["root"]))
    selection = sp.selection_path(runs["root"])
    assert runs["flags"] == {
        "materials": ["--facade-style", "mediterranean", "--repetition", "--cell-m", "100.0", "--street-profile", rec],
        "overgrow": ["--selection", str(selection), "--profile", "30", "--street-profile", rec],
        "cover": ["--max-props", "8", "--street-profile", rec],
    }
    # The real catalogue: the wrecked group is burned, damaged and wrecked, plus the conditionless shrub.
    assert json.loads(selection.read_text())["ids"] == ["synth_barrel_burned", "synth_car_wrecked", "synth_shrub"]
    block = {"schema_version": "0.1.0", "raw_sha256": record["raw_sha256"], "ids": record["ids"], "provenance": "ai_generated"}
    materials = _report(runs["materials_on"], "materials_report.json")
    assert materials["street_profile"] == block and "street_profile" not in _report(runs["materials_off"], "materials_report.json")
    assert materials["facades"]["style"] == "mediterranean" and materials["facades"]["style_origin"] == "config"
    assert "repetition" in materials and "repetition" not in _report(runs["materials_off"], "materials_report.json")
    summary = _report(runs["overgrow_on"], "overgrow_report.json")
    assert summary["street_profile"] == block and summary["profile_years"] == 30
    assert summary["library_selection"] == ["synth_barrel_burned", "synth_car_wrecked", "synth_shrub"]
    off = _report(runs["overgrow_off"], "overgrow_report.json")
    assert "street_profile" not in off and off["profile_years"] == 100 and off["library_selection"] is None


def test_on_twice_identical(runs: dict[str, Any]) -> None:
    replayed = runs["replayed"]
    assert replayed["origin"] == "replay" and replayed["attempts"] == 0 and replayed["raw"] == runs["first"]["raw"]
    # Reports carry the time and their base directory, as every run does; everything else is byte for byte.
    for one, two in (("materials_on", "materials_again"), ("overgrow_on", "overgrow_again")):
        names = sorted(p.name for p in runs[one].iterdir())
        assert names == sorted(p.name for p in runs[two].iterdir())
        for name in names:
            a, b = (runs[one] / name).read_bytes(), (runs[two] / name).read_bytes()
            if name.endswith(".json"):
                assert _stable(a, runs[one]) == _stable(b, runs[two]), name
            else:
                assert a == b, name


def _stable(data: bytes, directory: Path) -> Any:
    def scrub(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: scrub(v) for k, v in node.items() if k not in ("created_at", "generated_at")}
        if isinstance(node, list):
            return [scrub(v) for v in node]
        if isinstance(node, str):
            return node.replace(str(directory.parent / "materials_again"), "M").replace(str(directory.parent / "materials_on"), "M")
        return node

    return scrub(json.loads(data))


def test_observed_attributes_win(runs: dict[str, Any]) -> None:
    off, on = _buildings(runs["materials_off"]), _buildings(runs["materials_on"])
    assert on == off
    tinted = next(o for o in on if o["id"] == "building/osm-way-801")["facade"]["materials"]
    assert (tinted["wall_origin"], tinted["wall_source"]) == ("observed", "building:colour")
    assert _buildings(runs["overgrow_on"]) == _buildings(runs["overgrow_off"])
    for obj in on:
        assert "street_profile" not in json.dumps(obj)


def test_osm_geometry_untouched(runs: dict[str, Any]) -> None:
    def fingerprint(path: Path) -> Any:
        objects = _report(path, "scene_detail.json")["objects"]
        return geometry_fingerprint([o for o in objects if o.get("type") in GEOGRAPHIC])

    reference = fingerprint(runs["detail"])
    for name in ("materials_off", "materials_on", "overgrow_off", "overgrow_on"):
        assert fingerprint(runs[name]) == reference, name
    assert len(_buildings(runs["overgrow_on"])) == len(_buildings(runs["detail"]))


def test_off_is_byte_identical(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    create_map(plain, map_id="plain", name="Plain", latitude=MURCIA_CENTER_LAT, longitude=MURCIA_CENTER_LON, radius_m=150)
    store_sightline_cover(plain, True)
    golden = plan_steps(plain)
    store_street_profile(plain, False)
    assert plan_steps(plain) == golden
    assert all("--street-profile" not in argv and "--max-props" not in argv for _name, argv in golden)
    assert not list((plain / "config").glob("street_profile*.json"))
    # The switch on with a neutral or failed profile still plans the same commands.
    store_street_profile(plain, True)
    record = sp.resolve_street_profile(plain, provider=Stub("not json", "{}"), library=None).record
    assert record["origin"] == "rules" and record["reason"]
    assert plan_steps(plain) == golden
