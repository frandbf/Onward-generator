"""run_export.sh push listing modes and inspect against a fake adb. Runs bash only, never Unity or a headset."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from onward_generator.config.validation import REPO_ROOT
from onward_generator.export.info3 import DEVICE_CONTENT_DIR, LOCAL_ID

SCRIPT = REPO_ROOT / "tools" / "unity" / "onward_adapter" / "run_export.sh"
BUNDLE = b"content3 bundle"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("md5sum") is None,
                                reason="bash and md5sum are required")

# Device paths live under $FAKE_DEVICE; every adb call is logged.
FAKE_ADB = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_DEVICE.calls"
case "$1" in
  get-state) echo device ;;
  push) cp "$2" "$FAKE_DEVICE$3" ;;
  pull) cp "$FAKE_DEVICE$2" "$3" ;;
  shell) shift; cmd="$*"; bash -c "${cmd//\\/sdcard/$FAKE_DEVICE/sdcard}" ;;
  *) exit 9 ;;
esac
"""


def _png(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"


@pytest.fixture
def setup(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    package = tmp_path / "pkg"
    launcher = package / "build" / "launcher"
    launcher.mkdir(parents=True)
    (package / "onward_export.json").write_text(json.dumps({
        "package_id": "murcia_abc", "map": {"id": "murcia", "title": "Apocalipsis Murciana", "description": "D"},
    }), encoding="utf-8")
    (launcher / f"{LOCAL_ID}.content3").write_bytes(BUNDLE)
    (launcher / f"{LOCAL_ID}.info3").write_text(json.dumps({
        "WorkshopItem": {"ID": LOCAL_ID, "MapName": "Unity Export", "GameModes": [4, 10, 1], "ThumbnailUrl": "",
                         "BundleHash": hashlib.md5(BUNDLE).hexdigest(), "Description": "D"},
        "IsSanitized": False,
    }), encoding="utf-8")
    (launcher / "thumbnail.png").write_bytes(_png(512, 256))
    device = tmp_path / "device"
    folder = device / DEVICE_CONTENT_DIR.lstrip("/")
    folder.mkdir(parents=True)
    adb = tmp_path / "adb"
    adb.write_text(FAKE_ADB, encoding="utf-8")
    adb.chmod(0o755)
    env = {
        "PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "ONWARD_PLATFORM": "linux",
        "UNITY": shutil.which("true") or "/bin/true", "ADB": str(adb), "PYTHON": sys.executable,
        "LOG_DIR": str(tmp_path / "logs"), "FAKE_DEVICE": str(device), "CONFIRM_PUSH": "yes",
    }
    return package, folder, env


def _run(package: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), str(package), *args], env=env, capture_output=True, text=True, check=False)


def test_default_push_keeps_the_local_slot(setup: tuple[Path, Path, dict[str, str]]) -> None:
    package, folder, env = setup
    (folder / f"{LOCAL_ID}.info3").write_text("old", encoding="utf-8")
    result = _run(package, env, "push")
    assert result.returncode == 0, result.stdout + result.stderr
    launcher = package / "build" / "launcher"
    assert sorted(p.name for p in folder.iterdir()) == [f"{LOCAL_ID}.content3", f"{LOCAL_ID}.info3", f"{LOCAL_ID}.png"]
    assert (folder / f"{LOCAL_ID}.info3").read_bytes() == (launcher / f"{LOCAL_ID}.info3").read_bytes()
    assert not (package / "build" / "listings").exists()
    backup = next((package / "build" / "device_backup").iterdir())
    assert (backup / f"{LOCAL_ID}.info3").read_text(encoding="utf-8") == "old"
    assert "listing local, id unity-export.local" in result.stdout
    assert " listing local id unity-export.local md5 " in (package / "build" / "push.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("slot", ["named", "named-thumb"])
def test_named_slot_adds_a_pair_and_leaves_the_rest(setup: tuple[Path, Path, dict[str, str]], slot: str) -> None:
    package, folder, env = setup
    for name, text in ((f"{LOCAL_ID}.info3", "local"), ("onwardgen.murcia.info3", "previous"), ("12345.info3", "workshop")):
        (folder / name).write_text(text, encoding="utf-8")
    result = _run(package, env, "push", "--listing", slot)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (folder / f"{LOCAL_ID}.info3").read_text(encoding="utf-8") == "local"
    assert (folder / "12345.info3").read_text(encoding="utf-8") == "workshop"
    assert (folder / "onwardgen.murcia.content3").read_bytes() == BUNDLE
    assert (folder / "onwardgen.murcia.png").read_bytes() == _png(512, 256)
    item = json.loads((folder / "onwardgen.murcia.info3").read_text(encoding="utf-8"))["WorkshopItem"]
    assert (item["ID"], item["MapName"]) == ("onwardgen.murcia", "Apocalipsis Murciana")
    assert item["ThumbnailUrl"] == ("" if slot == "named" else f"file://{DEVICE_CONTENT_DIR}/onwardgen.murcia.png")
    assert (package / "build" / "listings" / slot / "onwardgen.murcia.info3").is_file()
    backup = next((package / "build" / "device_backup").iterdir())
    assert sorted(p.name for p in backup.iterdir()) == ["onwardgen.murcia.info3", f"{LOCAL_ID}.info3"]
    assert (backup / "onwardgen.murcia.info3").read_text(encoding="utf-8") == "previous"


def test_local_thumb_slot_rewrites_only_the_copy(setup: tuple[Path, Path, dict[str, str]]) -> None:
    package, folder, env = setup
    launcher_info = package / "build" / "launcher" / f"{LOCAL_ID}.info3"
    before = launcher_info.read_bytes()
    result = _run(package, env, "--listing=local-thumb", "push")
    assert result.returncode == 0, result.stdout + result.stderr
    assert launcher_info.read_bytes() == before
    item = json.loads((folder / f"{LOCAL_ID}.info3").read_text(encoding="utf-8"))["WorkshopItem"]
    assert (item["ID"], item["MapName"]) == (LOCAL_ID, "Unity Export")
    assert item["ThumbnailUrl"] == f"file://{DEVICE_CONTENT_DIR}/{LOCAL_ID}.png"


def test_environment_flag_picks_the_listing(setup: tuple[Path, Path, dict[str, str]]) -> None:
    package, folder, env = setup
    result = _run(package, env | {"ONWARD_QUEST_LISTING": "named"}, "push")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (folder / "onwardgen.murcia.info3").is_file()
    assert not (folder / f"{LOCAL_ID}.info3").exists()
    # --listing wins over the variable; an empty variable is the default.
    result = _run(package, env | {"ONWARD_QUEST_LISTING": "named"}, "push", "--listing", "local")
    assert "listing local, id unity-export.local" in result.stdout
    result = _run(package, env | {"ONWARD_QUEST_LISTING": ""}, "push")
    assert "listing local, id unity-export.local" in result.stdout


def test_push_refusals_touch_nothing(setup: tuple[Path, Path, dict[str, str]]) -> None:
    package, folder, env = setup
    result = _run(package, env, "push", "--listing", "workshop")
    assert result.returncode == 2
    assert "unknown Quest listing 'workshop'; use one of: local local-thumb named named-thumb" in result.stderr
    assert _run(package, env | {"ONWARD_QUEST_LISTING": "Named"}, "push").returncode == 2
    assert _run(package, env, "push", "--listing").returncode == 2
    assert _run(package, env | {"CONFIRM_PUSH": "no"}, "push", "--listing", "named").returncode == 2
    (package / "build" / "launcher" / "thumbnail.png").write_bytes(_png(512, 512))
    result = _run(package, env, "push", "--listing", "named")
    assert result.returncode == 1
    assert "listing for 'named' was refused" in result.stdout
    assert list(folder.iterdir()) == []
    assert not Path(env["FAKE_DEVICE"] + ".calls").exists()


def test_build_map_validates_the_listing_first(setup: tuple[Path, Path, dict[str, str]]) -> None:
    package, _folder, env = setup
    build_map = SCRIPT.with_name("build_map.sh")
    for args, extra in ((["--listing", "workshop", str(package)], {}), ([str(package)], {"ONWARD_QUEST_LISTING": "x"})):
        result = subprocess.run(["bash", str(build_map), *args], env=env | extra, capture_output=True, text=True,
                                check=False, stdin=subprocess.DEVNULL)
        assert result.returncode == 2
        assert "use one of: local local-thumb named named-thumb" in result.stderr
        assert "Package" not in result.stdout
    # A valid mode in any position: the launcher pair exists, so no Unity step, and no terminal skips the push.
    result = subprocess.run(["bash", str(build_map), str(package), "--listing=named-thumb"], env=env,
                            capture_output=True, text=True, check=False, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already has the Quest build" in result.stdout
    assert "Quest upload skipped" in result.stdout
    assert not Path(env["FAKE_DEVICE"] + ".calls").exists()


def test_inspect_copies_listings_only(setup: tuple[Path, Path, dict[str, str]]) -> None:
    package, folder, env = setup
    (folder / "12345.info3").write_text('{"WorkshopItem": {}}', encoding="utf-8")
    (folder / "12345.content3").write_bytes(b"bundle")
    (folder / "12345.png").write_bytes(b"png")
    result = _run(package, env | {"CONFIRM_PUSH": ""}, "inspect")
    assert result.returncode == 0, result.stdout + result.stderr
    out = next((package / "build" / "device_inspect").iterdir())
    assert sorted(p.name for p in out.iterdir()) == ["12345.info3", "files_tree.txt"]
    assert "12345.content3" in (out / "files_tree.txt").read_text(encoding="utf-8")
    assert sorted(p.name for p in folder.iterdir()) == ["12345.content3", "12345.info3", "12345.png"]


def test_shell_and_python_list_the_same_modes() -> None:
    from onward_generator.export.info3 import CANDIDATE_LISTINGS

    line = next(row for row in (REPO_ROOT / "tools" / "unity" / "lib.sh").read_text(encoding="utf-8").splitlines()
                if row.startswith("QUEST_LISTINGS="))
    assert line.split("=", 1)[1].strip('"').split() == ["local", *CANDIDATE_LISTINGS]
