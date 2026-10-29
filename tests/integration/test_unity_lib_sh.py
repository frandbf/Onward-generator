"""tools/unity/lib.sh: host precedence and hash helpers. Runs bash only, never Unity."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from onward_generator.config.validation import REPO_ROOT
from onward_generator.platform import resolve_host

LIB = REPO_ROOT / "tools" / "unity" / "lib.sh"
SHOW = 'source "$LIB"; resolve_host "$@"; shift "$HOST_SHIFT"; printf "%s\\n" "$ONWARD_PLATFORM" "$UNITY" "$ADB" "$LOG_ROOT" "$#"'

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")


def _fake_uname(tmp_path: Path, system: str) -> Path:
    """A PATH directory whose ``uname`` prints ``system``, so auto-detection is testable on any host."""
    bin_dir = tmp_path / f"bin-{system}"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "uname"
    script.write_text(f"#!/bin/sh\necho {system}\n", encoding="utf-8")
    script.chmod(0o755)
    return bin_dir


def _bash(tmp_path: Path, script: str, *args: str, env: dict[str, str] | None = None, system: str = "Linux") -> subprocess.CompletedProcess[str]:
    base = {
        "PATH": f"{_fake_uname(tmp_path, system)}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": "/home/tester",
        "LIB": str(LIB),
    }
    return subprocess.run(["bash", "-c", script, "lib", *args], env=base | (env or {}), capture_output=True, text=True, check=False)


def test_resolve_host_flag_env_uname(tmp_path: Path) -> None:
    result = _bash(tmp_path, SHOW, "--platform", "macos", "PKG", "tool", env={"ONWARD_PLATFORM": "linux"})
    assert result.returncode == 0, result.stderr
    name, unity, adb, log_root, remaining = result.stdout.splitlines()
    expected = resolve_host("macos", env={"HOME": "/home/tester"})
    assert name == "macos"
    assert (unity, adb, log_root) == (str(expected.unity_editor), str(expected.adb), str(expected.log_root))
    assert remaining == "2"

    result = _bash(tmp_path, SHOW, "--platform=auto", "PKG", env={"ONWARD_PLATFORM": "linux"}, system="Darwin")
    assert result.stdout.splitlines()[0] == "linux" and result.stdout.splitlines()[-1] == "1"

    result = _bash(tmp_path, SHOW, "PKG", system="Darwin")
    assert result.stdout.splitlines()[0] == "macos"

    result = _bash(tmp_path, SHOW, "PKG", system="Linux")
    name, unity, adb, log_root, remaining = result.stdout.splitlines()
    expected = resolve_host("linux", env={"HOME": "/home/tester"})
    assert (name, unity, adb, log_root) == ("linux", str(expected.unity_editor), str(expected.adb), str(expected.log_root))


def test_explicit_paths_still_win(tmp_path: Path) -> None:
    result = _bash(tmp_path, SHOW, "--platform", "linux", env={"UNITY": "/opt/Unity", "LOG_ROOT": "/logs"})
    name, unity, _adb, log_root, _remaining = result.stdout.splitlines()
    assert (name, unity, log_root) == ("linux", "/opt/Unity", "/logs")


def test_hash_helpers_match_hashlib(tmp_path: Path) -> None:
    sample = tmp_path / "sample.bin"
    sample.write_bytes(b"onward map\n" * 7)
    script = 'source "$LIB"; resolve_host --platform linux; file_md5 "$1"; file_sha256 "$1"'
    result = _bash(tmp_path, script, str(sample))
    assert result.returncode == 0, result.stderr
    md5, sha_line = result.stdout.splitlines()
    assert md5 == hashlib.md5(sample.read_bytes()).hexdigest()
    assert sha_line == f"{hashlib.sha256(sample.read_bytes()).hexdigest()}  {sample}"


@pytest.mark.parametrize("args, env, system", [(("--platform", "windows"), {}, "Linux"), ((), {"ONWARD_PLATFORM": "bsd"}, "Linux"), ((), {}, "FreeBSD"), (("--platform",), {}, "Linux")])
def test_unknown_platform_exits_2(tmp_path: Path, args: tuple[str, ...], env: dict[str, str], system: str) -> None:
    result = _bash(tmp_path, SHOW, *args, env=env, system=system)
    assert result.returncode == 2
    assert result.stdout == ""
