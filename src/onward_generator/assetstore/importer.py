"""Turn downloaded Asset Store packages into local catalogues under ``assets/asset_store_<id>/`` (ADR-035).

For each package: unpack its meshes, materials and textures (no code) into a throwaway Unity project, run the editor
in batch mode with ``tools/unity/asset_store_export`` (one prefab, LOD0, becomes OBJ + MTL + albedo PNG in metres),
convert each OBJ to an opaque albedo-only GLB, and write a manifest the library reads. The catalogue folders are
gitignored: the files stay on the user's machine, as the Unity Asset Store EULA requires.
"""

from __future__ import annotations

import datetime
import io
import json
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from onward_generator.assetstore.classify import classify
from onward_generator.assetstore.objglb import ObjError, obj_to_glb
from onward_generator.assetstore.packages import LICENCE_NAME, Package, PackageError, unpack
from onward_generator.generation.gltf import read_glb, scene_primitives
from onward_generator.io import atomic_write_bytes, atomic_write_json
from onward_generator.overgrow.library_catalog import LibraryError
from onward_generator.overgrow.manifest import check_manifest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXPORTER = REPO_ROOT / "tools" / "unity" / "asset_store_export" / "Editor" / "AssetStoreExport.cs"
DEFAULT_PROJECT = REPO_ROOT / "cache" / "asset_store" / "project"
DEFAULT_EXPORT = REPO_ROOT / "cache" / "asset_store" / "export"
DEFAULT_MAX_TRIANGLES = 8000
DEFAULT_MAX_MB = 400.0
CENTIMETRE_SUSPECT_M = 0.05
FLAT_STD = 2.0
BLANK_MEAN = 225.0
SEMANTICS = {
    "origin": "estimated",
    "basis": "written by onward_generator.assetstore from each model name, package category and bounding box; no person reviewed a rendered view",
    "fields": ["description", "placement", "grouping", "condition", "tags"],
}
Runner = Callable[[Sequence[str]], int]


class ImportError_(RuntimeError):
    """A package that could not be exported or converted."""


@dataclass
class PackageResult:
    package: Package
    status: str
    rows: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    error: str = ""


def _run(argv: Sequence[str]) -> int:
    return subprocess.run(list(argv), check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode


def ensure_project(unity: Path, project: Path, run: Runner = _run) -> None:
    """Create the throwaway export project once and copy the exporter script into it."""
    if not (project / "Assets").is_dir():
        project.parent.mkdir(parents=True, exist_ok=True)
        code = run([str(unity), "-batchmode", "-nographics", "-quit", "-createProject", str(project), "-logFile", str(project.parent / "create.log")])
        if code != 0 or not (project / "Assets").is_dir():
            raise ImportError_(f"Unity could not create the export project (exit {code}); see {project.parent / 'create.log'}")
    editor = project / "Assets" / "Editor"
    editor.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(EXPORTER, editor / EXPORTER.name)


def _clear(project: Path) -> None:
    """Leave only ``Assets/Editor`` (the exporter) in the export project."""
    for child in (project / "Assets").iterdir():
        if child.name not in ("Editor", "Editor.meta"):
            shutil.rmtree(child) if child.is_dir() else child.unlink()


def export_package(package: Package, unity: Path, project: Path, export_root: Path, max_triangles: int, run: Runner = _run) -> Path:
    """Unpack ``package`` into the project, run the exporter, and return the folder with its OBJ files and report."""
    _clear(project)
    try:
        unpack(package.path, project)
        target = export_root / package.id
        if target.exists():
            shutil.rmtree(target)
        log = export_root / f"{package.id}.log"
        export_root.mkdir(parents=True, exist_ok=True)
        code = run([str(unity), "-batchmode", "-nographics", "-projectPath", str(project), "-executeMethod", "AssetStoreExport.Run",
                    "-onwardId", package.id, "-onwardOut", str(export_root), "-onwardMaxTriangles", str(max_triangles),
                    "-quit", "-logFile", str(log)])
        if code != 0 or not (target / "report.json").is_file():
            raise ImportError_(f"{package.name}: the Unity export failed (exit {code}); see {log}")
        return target
    finally:
        _clear(project)


def _size(data: bytes) -> tuple[int, float, float, float]:
    """Triangles, then length, depth and height as the library loads the GLB (Z up)."""
    primitives = [p for p in scene_primitives(data) if p.mesh.triangle_count]
    vertices = np.concatenate([p.mesh.vertices for p in primitives])
    spans = vertices.max(axis=0) - vertices.min(axis=0)
    length, depth, height = (round(float(s), 3) for s in spans[:3])
    return sum(p.mesh.triangle_count for p in primitives), length, depth, height


def blank_albedo(data: bytes) -> bool:
    """Whether every material of a GLB is one near-white colour: a model whose texture was lost reads as a blank shape."""
    doc, blob = read_glb(data)
    images = doc.get("images") or []
    if not images:
        return True
    for image in images:
        view = doc["bufferViews"][image["bufferView"]]
        start = int(view.get("byteOffset", 0))
        pixels = np.asarray(Image.open(io.BytesIO(blob[start:start + int(view["byteLength"])])).convert("RGB"), dtype=np.float32)
        flat = pixels.reshape(-1, 3)
        if float(flat.std(axis=0).max()) >= FLAT_STD or float(flat.mean()) < BLANK_MEAN:
            return False
    return True


def unit_factor(items: Sequence[dict[str, Any]]) -> float:
    """100 when every exported model of a package is under ``CENTIMETRE_SUSPECT_M`` on its longest side, else 1.

    Old packages (Unity 3.x FBX in centimetres with no unit conversion) come out a hundred times too small: a
    6 mm fire hydrant. One factor for the whole package keeps its models in proportion.
    """
    sizes = [max(item["size"]) for item in items if item.get("status") == "exported" and item.get("size")]
    return 100.0 if sizes and max(sizes) < CENTIMETRE_SUSPECT_M else 1.0


def build_catalogue(package: Package, exported: Path, assets: Path) -> PackageResult:
    """Convert the exported OBJ files and write ``assets/<catalogue>/`` with its manifest; skipped models are counted."""
    report = json.loads((exported / "report.json").read_text(encoding="utf-8"))
    result = PackageResult(package, "ok")
    target = assets / package.catalogue
    if target.exists():
        shutil.rmtree(target)
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    factor = unit_factor(report.get("items") or [])
    for item in report.get("items") or []:
        if item.get("status") != "exported":
            result.skipped[str(item.get("status", "unknown")).split(":")[0]] += 1
            continue
        stem = Path(str(item["file"])).stem
        asset_id = f"as_{package.id[:20]}_{stem}"[:80]
        if asset_id in seen:
            result.skipped["duplicate_name"] += 1
            continue
        try:
            data = obj_to_glb((exported / item["file"]).read_text(encoding="utf-8", errors="replace"),
                              (exported / f"{stem}.mtl").read_text(encoding="utf-8", errors="replace"), stem, exported, factor)
        except (OSError, ObjError, ValueError) as exc:
            result.skipped[f"convert: {type(exc).__name__}"] += 1
            continue
        if blank_albedo(data):
            result.skipped["untextured_white"] += 1
            continue
        triangles, length, depth, height = _size(data)
        decision = classify(stem, (length, depth, height), triangles, package.name, package.category)
        if decision.row is None:
            result.skipped[decision.reason] += 1
            continue
        seen.add(asset_id)
        turn = {"yaw_deg": 90} if decision.row["placement"] != "interior" and depth > 1.2 * length else {}
        target.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target / f"{stem}.glb", data)
        rows.append({"id": asset_id, "file": f"{stem}.glb", **decision.row, "triangles": triangles, "length_m": length,
                     "depth_m": depth, "height_m": height, **turn, "collider": "none", "license": "LicenseRef-Unity-Asset-Store-EULA",
                     "source_prefab": str(item.get("prefab", ""))})
    result.rows = len(rows)
    if not rows:
        result.status = "empty"
        return result
    document = {
        "schema": "onward_generator.abandoned_prefabs", "schema_version": "0.2.0", "unit": "m", "collider": "none",
        "shader": "PP_Opaque_Simple_Lit albedo only",
        "source": f"{LICENCE_NAME} (Extension Asset), local only: {package.publisher} / {package.name}. Converted on this machine; "
                  "never committed or redistributed outside a built map.",
        "semantics": {**SEMANTICS, "reviewed_on": datetime.date.today().isoformat()}, "package": {"publisher": package.publisher, "category": package.category, "name": package.name},
        "skipped": dict(sorted(result.skipped.items())), "unit_factor": factor, "assets": rows,
    }
    check_manifest(document, package.catalogue)
    atomic_write_json(target / "manifest.json", document)
    (target / "License.txt").write_text(
        f"{package.name} by {package.publisher}, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
        "Local copy for this user's maps only. Do not commit, share or publish these files.\n", encoding="utf-8")
    return result


def import_packages(packages: Sequence[Package], unity: Path, assets: Path, *, project: Path = DEFAULT_PROJECT,
                    export_root: Path = DEFAULT_EXPORT, max_triangles: int = DEFAULT_MAX_TRIANGLES, run: Runner = _run,
                    report: Callable[[PackageResult], None] | None = None) -> list[PackageResult]:
    ensure_project(unity, project, run)
    results = []
    for package in packages:
        try:
            exported = export_package(package, unity, project, export_root, max_triangles, run)
            result = build_catalogue(package, exported, assets)
        except (ImportError_, PackageError, LibraryError, OSError, json.JSONDecodeError) as exc:
            result = PackageResult(package, "failed", error=str(exc))
        results.append(result)
        if report is not None:
            report(result)
    return results
