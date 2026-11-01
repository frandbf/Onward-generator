"""tools/docker/inputs.sh hashes what the image is built from, so run.sh rebuilds a stale image."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "docker" / "inputs.sh"
INPUTS = ("Dockerfile", "requirements.lock", "requirements-mutate.lock", "requirements-lint.lock", "pyproject.toml")

pytestmark = pytest.mark.skipif(shutil.which("sha256sum") is None and shutil.which("shasum") is None, reason="needs sha256sum or shasum")


def _checkout(root: Path) -> Path:
    (root / "tools" / "docker").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "tools" / "docker" / "inputs.sh")
    for name in INPUTS:
        (root / name).write_text(f"{name}\n", encoding="utf-8")
    return root


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(repo / "tools" / "docker" / "inputs.sh")], capture_output=True, text=True, check=False)


def _hash(repo: Path) -> str:
    done = _run(repo)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_the_hash_is_one_stable_sha256(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    first = _hash(repo)
    assert len(first.strip()) == 64
    assert first.endswith("\n")
    assert _hash(repo) == first


@pytest.mark.parametrize("name", INPUTS)
def test_a_change_in_any_input_changes_the_hash(tmp_path: Path, name: str) -> None:
    repo = _checkout(tmp_path)
    before = _hash(repo)
    (repo / name).write_text("duckdb==1.5.6\n", encoding="utf-8")
    assert _hash(repo) != before


def test_other_files_do_not_change_the_hash(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    before = _hash(repo)
    (repo / "README.md").write_text("docs\n", encoding="utf-8")
    assert _hash(repo) == before


def test_a_missing_input_fails(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    (repo / "requirements.lock").unlink()
    assert _run(repo).returncode != 0
