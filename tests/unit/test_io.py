"""Shared output helpers: atomic writes and the no-clobber output check."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from onward_generator import io as out_io
from onward_generator.io import atomic_write_bytes, atomic_write_json, prepare_outputs


def _leftovers(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir() if p.name.endswith(".tmp"))


def test_atomic_write_bytes_replaces_the_file_with_umask_permissions(tmp_path: Path) -> None:
    target = tmp_path / "scene.glb"
    target.write_bytes(b"old")
    previous = os.umask(0o027)
    try:
        atomic_write_bytes(target, b"new")
    finally:
        os.umask(previous)
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert _leftovers(tmp_path) == []


def test_atomic_write_bytes_failure_keeps_the_old_file_and_removes_the_temporary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "scene.glb"
    target.write_bytes(b"old")

    def fail(src: str, dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(out_io.os, "replace", fail)
    with pytest.raises(OSError, match="disk full"):
        atomic_write_bytes(target, b"new")
    assert target.read_bytes() == b"old"
    assert _leftovers(tmp_path) == []


def test_atomic_write_bytes_into_a_missing_directory_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        atomic_write_bytes(tmp_path / "missing" / "x.bin", b"x")


def test_atomic_write_json_is_indented_utf8_with_a_final_newline(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    atomic_write_json(target, {"name": "Plaza de Belluga", "city": "Murcia", "n": 1})
    text = target.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert '\n  "name": "Plaza de Belluga"' in text
    assert json.loads(text) == {"name": "Plaza de Belluga", "city": "Murcia", "n": 1}
    atomic_write_json(target, {"calle": "Trapería ñ"})
    assert "Trapería ñ" in target.read_text(encoding="utf-8")  # not \\u-escaped


def test_atomic_write_json_rejects_unserialisable_payloads(tmp_path: Path) -> None:
    target = tmp_path / "manifest.json"
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": object()})
    assert not target.exists()


def test_prepare_outputs_creates_the_directory_and_resolves_paths(tmp_path: Path) -> None:
    out = tmp_path / "a" / "b"
    targets = prepare_outputs(out, ("scene.glb", "manifest.json"), overwrite=False)
    assert out.is_dir()
    assert targets == (out.resolve() / "scene.glb", out.resolve() / "manifest.json")


def test_prepare_outputs_refuses_existing_files_unless_overwrite(tmp_path: Path) -> None:
    (tmp_path / "scene.glb").write_bytes(b"x")
    with pytest.raises(FileExistsError, match="scene.glb"):
        prepare_outputs(tmp_path, ("scene.glb", "manifest.json"), overwrite=False)
    assert prepare_outputs(tmp_path, ("scene.glb",), overwrite=True) == (tmp_path.resolve() / "scene.glb",)


def test_prepare_outputs_never_overwrites_an_input(tmp_path: Path) -> None:
    source = tmp_path / "scene.glb"
    source.write_bytes(b"x")
    with pytest.raises(ValueError, match="overwrite an input"):
        prepare_outputs(tmp_path, ("scene.glb",), overwrite=True, protected=(source,))
    relative = Path(os.path.relpath(source))
    with pytest.raises(ValueError, match="overwrite an input"):
        prepare_outputs(tmp_path, ("scene.glb",), overwrite=True, protected=(relative,))


def test_prepare_outputs_failed_check_creates_nothing(tmp_path: Path) -> None:
    source = tmp_path / "in.glb"
    source.write_bytes(b"x")
    out = tmp_path / "new"
    with pytest.raises(ValueError):
        prepare_outputs(out, ("in.glb",), overwrite=False, protected=(out / "in.glb",))
    assert not out.exists()
