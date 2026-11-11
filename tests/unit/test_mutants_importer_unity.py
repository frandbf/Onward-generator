"""The Unity side of the Asset Store import (ADR-035): the export project, the batch command lines and their failures.

No test launches Unity: every runner is a fake that records its command line and writes what the editor would, and
``subprocess.run`` itself refuses to run unless a test replaces it. Packages and projects live in ``tmp_path``.
"""

from __future__ import annotations

import io
import json
import subprocess
import tarfile
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from onward_generator.assetstore import importer
from onward_generator.assetstore.importer import EXPORTER, ImportError_, PackageResult, ensure_project, export_package, import_packages
from onward_generator.assetstore.packages import Package, PackageError

BOX = "".join(f"v {x} {y} {z}\n" for x in (0, 1) for y in (0, 1) for z in (0, 0.5)) + "usemtl m\nf 1 2 4 3\nf 5 6 8 7\n"


@pytest.fixture(autouse=True)
def _no_process(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("a test must never start a process")

    monkeypatch.setattr(subprocess, "run", refuse)


def _unitypackage(path: Path, files: dict[str, bytes]) -> Package:
    """A .unitypackage holding ``files`` (path under the project to bytes), each with a ``.meta``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for k, (pathname, data) in enumerate(files.items()):
            for name, payload in ((f"g{k}/pathname", pathname.encode()), (f"g{k}/asset", data), (f"g{k}/asset.meta", b"guid: x")):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))
    return Package(path, "Pub", "3D/Props/Exterior", path.stem)


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


class FakeUnity:
    """Records each command line; creates the project, or exports one bench per package, as the editor would."""

    def __init__(self, *, create: bool = True, create_code: int = 0, export_code: int = 0, report: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.seen: list[set[str]] = []
        self.create, self.create_code, self.export_code, self.report = create, create_code, export_code, report

    def __call__(self, argv: Sequence[str]) -> int:
        argv = list(argv)
        self.calls.append(argv)
        if "-createProject" in argv:
            if self.create:
                (Path(argv[argv.index("-createProject") + 1]) / "Assets").mkdir(parents=True)
            return self.create_code
        project = Path(argv[argv.index("-projectPath") + 1])
        target = Path(argv[argv.index("-onwardOut") + 1]) / argv[argv.index("-onwardId") + 1]
        self.seen.append(_tree(project / "Assets") | ({"<stale target>"} if target.exists() else set()))
        target.mkdir(parents=True)
        (target / "Bench_A.obj").write_text(BOX, encoding="utf-8")
        (target / "Bench_A.mtl").write_text("newmtl m\nKd 0.5 0.2 0.2\n", encoding="utf-8")
        if self.report:
            items = [{"file": "Bench_A.obj", "status": "exported", "prefab": "Assets/Bench_A.prefab"}]
            (target / "report.json").write_text(json.dumps({"items": items}), encoding="utf-8")
        return self.export_code


def _create_argv(unity: Path, project: Path) -> list[str]:
    return [
        str(unity),
        "-batchmode",
        "-nographics",
        "-quit",
        "-createProject",
        str(project),
        "-logFile",
        str(project.parent / "create.log"),
    ]


def _export_argv(unity: Path, project: Path, export_root: Path, package_id: str, triangles: int) -> list[str]:
    return [
        str(unity),
        "-batchmode",
        "-nographics",
        "-projectPath",
        str(project),
        "-executeMethod",
        "AssetStoreExport.Run",
        "-onwardId",
        package_id,
        "-onwardOut",
        str(export_root),
        "-onwardMaxTriangles",
        str(triangles),
        "-quit",
        "-logFile",
        str(export_root / f"{package_id}.log"),
    ]


def _script(project: Path) -> Path:
    return project / "Assets" / "Editor" / EXPORTER.name


# ---- ensure_project ---------------------------------------------------------------------------------------------------


def test_a_new_project_is_created_in_batch_mode_and_given_the_exporter(tmp_path: Path) -> None:
    unity, project = tmp_path / "Unity" / "unity", tmp_path / "cache" / "asset_store" / "project"
    fake = FakeUnity()
    ensure_project(unity, project, fake)
    assert fake.calls == [_create_argv(unity, project)]
    assert _script(project).read_bytes() == EXPORTER.read_bytes() and _tree(project) == {
        "Assets",
        "Assets/Editor",
        f"Assets/Editor/{EXPORTER.name}",
    }


def test_a_new_project_may_sit_in_a_folder_that_exists(tmp_path: Path) -> None:
    project = tmp_path / "project"
    fake = FakeUnity()
    ensure_project(tmp_path / "unity", project, fake)
    assert fake.calls == [_create_argv(tmp_path / "unity", project)] and _script(project).is_file()


def test_an_existing_project_is_kept_and_its_exporter_refreshed(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _script(project).parent.mkdir(parents=True)
    _script(project).write_text("// old exporter", encoding="utf-8")
    (project / "Assets" / "Model.fbx").write_bytes(b"fbx")
    fake = FakeUnity()
    ensure_project(tmp_path / "unity", project, fake)
    assert fake.calls == []
    assert _script(project).read_bytes() == EXPORTER.read_bytes() and (project / "Assets" / "Model.fbx").read_bytes() == b"fbx"


def test_the_default_runner_runs_unity_quietly_without_raising_on_its_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unity, project = tmp_path / "unity", tmp_path / "project"
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        seen.append((args, kwargs))
        (project / "Assets").mkdir(parents=True)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", run)
    ensure_project(unity, project)
    assert seen == [((_create_argv(unity, project),), {"check": False, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL})]
    assert _script(project).is_file()


def test_the_default_runner_returns_the_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: SimpleNamespace(returncode=len(argv) + 4))
    assert importer._run(("unity", "-quit")) == 6


@pytest.mark.parametrize(("create", "code"), [(True, 3), (False, 0), (False, 1)])
def test_a_project_unity_could_not_create_is_an_import_error(tmp_path: Path, create: bool, code: int) -> None:
    project = tmp_path / "cache" / "project"
    with pytest.raises(ImportError_) as caught:
        ensure_project(tmp_path / "unity", project, FakeUnity(create=create, create_code=code))
    assert str(caught.value) == f"Unity could not create the export project (exit {code}); see {tmp_path / 'cache' / 'create.log'}"
    assert not _script(project).exists()


# ---- export_package ---------------------------------------------------------------------------------------------------


def _project(tmp_path: Path) -> Path:
    """An export project with its exporter and the leftovers of an earlier package: a folder, a file and their metas."""
    project = tmp_path / "project"
    _script(project).parent.mkdir(parents=True)
    _script(project).write_text("// exporter", encoding="utf-8")
    (project / "Assets" / "Editor.meta").write_text("guid: e", encoding="utf-8")
    (project / "Assets" / "Old").mkdir()
    (project / "Assets" / "Old" / "Old.prefab").write_text("old", encoding="utf-8")
    (project / "Assets" / "Old.meta").write_text("guid: o", encoding="utf-8")
    (project / "Assets" / "loose.txt").write_text("old", encoding="utf-8")
    return project


EDITOR = {"Editor", f"Editor/{EXPORTER.name}", "Editor.meta"}


def test_a_package_is_exported_alone_and_the_project_is_left_with_the_exporter_only(tmp_path: Path) -> None:
    package = _unitypackage(tmp_path / "cache" / "Bench Pack.unitypackage", {"Assets/Models/Bench.fbx": b"fbx"})
    project, export_root, unity = _project(tmp_path), tmp_path / "cache" / "deep" / "export", tmp_path / "unity"
    fake = FakeUnity()
    target = export_package(package, unity, project, export_root, 1234, fake)
    assert target == export_root / "bench_pack" and (target / "report.json").is_file()
    assert fake.calls == [_export_argv(unity, project, export_root, "bench_pack", 1234)]
    assert fake.seen == [EDITOR | {"Models", "Models/Bench.fbx", "Models/Bench.fbx.meta"}]
    assert _tree(project / "Assets") == EDITOR and _script(project).read_text(encoding="utf-8") == "// exporter"


def test_an_earlier_export_of_the_package_is_removed_before_unity_runs(tmp_path: Path) -> None:
    package = _unitypackage(tmp_path / "cache" / "Bench Pack.unitypackage", {"Assets/Bench.fbx": b"fbx"})
    export_root = tmp_path / "export"
    (export_root / "bench_pack").mkdir(parents=True)
    (export_root / "bench_pack" / "Old.obj").write_text("old", encoding="utf-8")
    fake = FakeUnity()
    target = export_package(package, tmp_path / "unity", _project(tmp_path), export_root, 8000, fake)
    assert "<stale target>" not in fake.seen[0] and not (target / "Old.obj").exists()


@pytest.mark.parametrize(("code", "report"), [(1, True), (0, False), (2, False)])
def test_a_failed_export_is_an_import_error_and_still_clears_the_project(tmp_path: Path, code: int, report: bool) -> None:
    package = _unitypackage(tmp_path / "cache" / "Bench Pack.unitypackage", {"Assets/Bench.fbx": b"fbx"})
    project, export_root = _project(tmp_path), tmp_path / "export"
    with pytest.raises(ImportError_) as caught:
        export_package(package, tmp_path / "unity", project, export_root, 8000, FakeUnity(export_code=code, report=report))
    assert str(caught.value) == f"Bench Pack: the Unity export failed (exit {code}); see {export_root / 'bench_pack.log'}"
    assert _tree(project / "Assets") == EDITOR


def test_an_unreadable_package_is_a_package_error_and_unity_never_runs(tmp_path: Path) -> None:
    path = tmp_path / "Broken.unitypackage"
    path.write_bytes(b"not a gzip tar")
    project, fake = _project(tmp_path), FakeUnity()
    with pytest.raises(PackageError):
        export_package(Package(path, "Pub", "Cat", "Broken"), tmp_path / "unity", project, tmp_path / "export", 8000, fake)
    assert fake.calls == [] and _tree(project / "Assets") == EDITOR


# ---- import_packages --------------------------------------------------------------------------------------------------


def test_import_packages_creates_the_project_once_and_reports_each_package(tmp_path: Path) -> None:
    good = _unitypackage(tmp_path / "cache" / "Bench Pack.unitypackage", {"Assets/Bench.fbx": b"fbx"})
    broken_path = tmp_path / "cache" / "Broken Pack.unitypackage"
    broken_path.write_bytes(b"not a gzip tar")
    broken = Package(broken_path, "Pub", "Cat", "Broken Pack")
    unity, assets, project, export_root = (
        tmp_path / "unity",
        tmp_path / "assets",
        tmp_path / "work" / "project",
        tmp_path / "work" / "export",
    )
    fake, reported = FakeUnity(), []
    results = import_packages(
        [good, broken], unity, assets, project=project, export_root=export_root, max_triangles=1234, run=fake, report=reported.append
    )
    assert fake.calls == [_create_argv(unity, project), _export_argv(unity, project, export_root, "bench_pack", 1234)]
    assert len(results) == 2 and len(reported) == 2 and all(r is s for r, s in zip(results, reported, strict=True))
    ok, failed = results
    assert (ok.package, ok.status, ok.rows, ok.error) == (good, "ok", 1, "")
    manifest = json.loads((assets / "asset_store_bench_pack" / "manifest.json").read_text(encoding="utf-8"))
    assert [row["id"] for row in manifest["assets"]] == ["as_bench_pack_Bench_A"]
    assert failed.package is broken and failed.status == "failed" and failed.rows == 0
    assert failed.error.startswith("Broken Pack.unitypackage: not a readable .unitypackage (")
    assert _script(project).read_bytes() == EXPORTER.read_bytes()


def test_a_failed_unity_export_is_a_failed_result_and_the_report_is_optional(tmp_path: Path) -> None:
    package = _unitypackage(tmp_path / "cache" / "Bench Pack.unitypackage", {"Assets/Bench.fbx": b"fbx"})
    export_root = tmp_path / "export"
    results = import_packages(
        [package],
        tmp_path / "unity",
        tmp_path / "assets",
        project=tmp_path / "project",
        export_root=export_root,
        run=FakeUnity(export_code=5),
    )
    assert results == [
        PackageResult(package, "failed", error=f"Bench Pack: the Unity export failed (exit 5); see {export_root / 'bench_pack.log'}")
    ]
    assert not (tmp_path / "assets").exists()


def test_import_packages_uses_the_default_triangle_budget(tmp_path: Path) -> None:
    package = _unitypackage(tmp_path / "cache" / "Bench Pack.unitypackage", {"Assets/Bench.fbx": b"fbx"})
    fake = FakeUnity()
    import_packages(
        [package], tmp_path / "unity", tmp_path / "assets", project=tmp_path / "project", export_root=tmp_path / "export", run=fake
    )
    assert fake.calls[-1][fake.calls[-1].index("-onwardMaxTriangles") + 1] == str(importer.DEFAULT_MAX_TRIANGLES) == "8000"


def test_a_project_that_cannot_be_created_stops_the_import(tmp_path: Path) -> None:
    package = _unitypackage(tmp_path / "cache" / "Bench Pack.unitypackage", {"Assets/Bench.fbx": b"fbx"})
    fake = FakeUnity(create=False)
    with pytest.raises(ImportError_):
        import_packages(
            [package], tmp_path / "unity", tmp_path / "assets", project=tmp_path / "project", export_root=tmp_path / "export", run=fake
        )
    assert len(fake.calls) == 1
