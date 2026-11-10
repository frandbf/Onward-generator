"""``perf layout --playable --gameplay-report`` on the synthetic street block around MURCIA_CENTER (ADR-032).

The report is a separate file: layout.json and the export package are the same with the option on or off.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from onward_generator.cli import main
from onward_generator.export.package import MANIFEST_FILE, build_package
from onward_generator.gameplay.report import LAYOUT_SCHEMA, REPORT_FILE, REPORT_SCHEMA, REPORT_VERSION
from tests.conftest import FIXED_NOW

REPO = Path(__file__).resolve().parents[2]
EXPORT_PROJECT = REPO / "config" / "murcia-belluga.export.json"


@pytest.fixture(scope="module")
def playable(detail_inputs: tuple[Path, Path, Path], tmp_path_factory: pytest.TempPathFactory) -> tuple[list[str], Path]:
    """The ``perf layout`` arguments without ``--out``, and the playable directory (detail, then bounds)."""
    scene, heights, base = detail_inputs
    root = tmp_path_factory.mktemp("gameplay")
    detail = root / "detail"
    assert main(["detail", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--out", str(detail), "--no-preview"]) == 0
    walled = root / "scene_playable"
    assert main(["bounds", "--base", str(detail), "--out", str(walled)]) == 0
    args = ["perf", "layout", "--scene", str(scene), "--heights", str(heights), "--base", str(base), "--detail", str(walled), "--playable"]
    return args, walled


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_report_leaves_layout_and_package_unchanged(playable: tuple[list[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args, walled = playable
    off, on, again = tmp_path / "off", tmp_path / "on", tmp_path / "again"
    # With the option off an old report in the folder is neither in the way nor touched.
    off.mkdir()
    (off / REPORT_FILE).write_text("old", encoding="utf-8")
    assert main([*args, "--out", str(off / "layout.json")]) == 0
    assert (off / REPORT_FILE).read_text(encoding="utf-8") == "old"
    capsys.readouterr()
    assert main([*args, "--gameplay-report", "--out", str(on / "layout.json")]) == 0
    out = capsys.readouterr().out
    assert f"wrote {on / 'layout.json'}" in out and f"wrote {on / REPORT_FILE}" in out
    assert re.search(r"^gameplay: routes \d+  sight [\d.]+ m  balance [\d.]+ %  status \w+  \(estimated, report only\)$", out, re.M)
    assert out.index("gameplay: ") < out.index(f"wrote {on / REPORT_FILE}")
    assert main([*args, "--gameplay-report", "--out", str(again / "layout.json")]) == 0

    assert _sha(off / "layout.json") == _sha(on / "layout.json") == _sha(again / "layout.json")
    assert (on / REPORT_FILE).read_bytes() == (again / REPORT_FILE).read_bytes()
    report = json.loads((on / REPORT_FILE).read_text(encoding="utf-8"))
    assert (report["schema"], report["schema_version"]) == (REPORT_SCHEMA, REPORT_VERSION)
    assert report["provenance"] == "estimated"
    assert report["layout_sha256"] == _sha(on / "layout.json")
    assert json.loads((on / "layout.json").read_text(encoding="utf-8"))["schema"] == LAYOUT_SCHEMA
    assert report["status"] in ("ok", "no_cover", "no_path", "spawn_off_navmesh", "uplink_off_navmesh")
    assert set(report["headline"]) == {"distinct_routes", "max_overlap_pct", "longest_open_sight_m", "cover_per_100m_min_stretch", "contact_diff_pct"}
    assert report["proposed_checks"]["blocking"] is False
    if report["status"] in ("ok", "no_cover"):
        assert report["headline"]["distinct_routes"] >= 1
        assert len(report["pairs"]) == 2

    packages = []
    for name, layout in (("pkg_off", off / "layout.json"), ("pkg_on", on / "layout.json")):
        package = tmp_path / name
        build_package(EXPORT_PROJECT, walled, layout, package, now=FIXED_NOW)
        packages.append(package)
    listing = [sorted(p.name for p in package.iterdir()) for package in packages]
    assert listing[0] == listing[1]
    assert REPORT_FILE not in listing[1]
    manifests = [json.loads((package / MANIFEST_FILE).read_text(encoding="utf-8")) for package in packages]
    # The manifest also records where each file came from, which differs by folder only.
    assert [{k: v for k, v in f.items() if k != "source"} for f in manifests[0]["files"]] == [
        {k: v for k, v in f.items() if k != "source"} for f in manifests[1]["files"]
    ]
    for file in listing[0]:
        if file != MANIFEST_FILE:
            assert _sha(packages[0] / file) == _sha(packages[1] / file), file


def test_the_report_needs_the_playable_layout(playable: tuple[list[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args, _ = playable
    plain = [arg for arg in args if arg != "--playable"]
    assert main([*plain, "--gameplay-report", "--out", str(tmp_path / "layout.json")]) == 1
    assert capsys.readouterr().err == "perf: --gameplay-report needs --playable\n"
    assert not (tmp_path / "layout.json").exists() and not (tmp_path / REPORT_FILE).exists()


def test_a_rerun_without_overwrite_touches_neither_file(playable: tuple[list[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args, _ = playable
    layout = tmp_path / "layout.json"
    assert main([*args, "--gameplay-report", "--out", str(layout)]) == 0
    before = (_sha(layout), _sha(tmp_path / REPORT_FILE))
    capsys.readouterr()
    assert main([*args, "--gameplay-report", "--out", str(layout)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert (_sha(layout), _sha(tmp_path / REPORT_FILE)) == before
    # Only the report in the way is enough to refuse: the layout is not written either.
    layout.unlink()
    assert main([*args, "--gameplay-report", "--out", str(layout)]) == 1
    assert not layout.exists()
    assert main([*args, "--gameplay-report", "--overwrite", "--out", str(layout)]) == 0
    assert (_sha(layout), _sha(tmp_path / REPORT_FILE)) == before


def test_a_corrupt_collision_glb_writes_neither_file(playable: tuple[list[str], Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    args, walled = playable
    broken = tmp_path / "scene_playable"
    broken.mkdir()
    for path in walled.iterdir():
        if path.is_file():
            (broken / path.name).write_bytes(path.read_bytes())
    (broken / "scene_collision.glb").write_bytes(b"glTF broken on purpose, long enough to parse a header")
    detail_at = args.index("--detail") + 1
    broken_args = [*args[:detail_at], str(broken), *args[detail_at + 1:]]
    out = tmp_path / "out" / "layout.json"
    assert main([*broken_args, "--gameplay-report", "--out", str(out)]) == 1
    assert "not a readable collision GLB" in capsys.readouterr().err
    assert not out.exists() and not (out.parent / REPORT_FILE).exists()
