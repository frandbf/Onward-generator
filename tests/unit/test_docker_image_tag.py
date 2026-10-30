"""tools/docker/image.sh names one image per checkout, so parallel worktrees do not share one."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "docker" / "image.sh"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _checkout(root: Path) -> Path:
    """A repository with tools/docker/image.sh committed, the layout the script expects."""
    repo = root / "main"
    (repo / "tools" / "docker").mkdir(parents=True)
    shutil.copy2(SCRIPT, repo / "tools" / "docker" / "image.sh")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-qm", "init")
    return repo


def _tag(repo: Path, **env: str) -> str:
    environ = {key: value for key, value in os.environ.items() if key != "ONWARD_IMAGE"} | env
    done = subprocess.run([str(repo / "tools" / "docker" / "image.sh")], capture_output=True, text=True, check=True, env=environ)
    return done.stdout


def test_a_plain_checkout_uses_the_shared_tag(tmp_path: Path) -> None:
    assert _tag(_checkout(tmp_path)) == "onward-generator:dev\n"


def test_a_linked_worktree_gets_its_own_tag(tmp_path: Path) -> None:
    repo = _checkout(tmp_path)
    worktree = tmp_path / "Agent work+1"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    assert _tag(worktree) == "onward-generator:dev-Agent-work-1\n"
    assert _tag(repo) == "onward-generator:dev\n"


def test_onward_image_wins(tmp_path: Path) -> None:
    assert _tag(_checkout(tmp_path), ONWARD_IMAGE="registry/x:7") == "registry/x:7\n"


def test_outside_git_uses_the_shared_tag(tmp_path: Path) -> None:
    folder = tmp_path / "plain" / "tools" / "docker"
    folder.mkdir(parents=True)
    shutil.copy2(SCRIPT, folder / "image.sh")
    assert _tag(tmp_path / "plain", GIT_CEILING_DIRECTORIES=str(tmp_path)) == "onward-generator:dev\n"
