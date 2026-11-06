from __future__ import annotations

from pathlib import Path

import pytest

from onward_generator.platform import PLATFORM_ENV, PlatformError, resolve_host

HOME = {"HOME": "/home/tester"}


def test_flag_beats_env() -> None:
    host = resolve_host("linux", env=HOME | {PLATFORM_ENV: "macos"}, system="Darwin")
    assert (host.name, host.source) == ("linux", "flag")


def test_env_beats_autodetect() -> None:
    host = resolve_host(None, env=HOME | {PLATFORM_ENV: "linux"}, system="Darwin")
    assert (host.name, host.source) == ("linux", "env")


def test_auto_flag_falls_through() -> None:
    assert resolve_host("auto", env=HOME | {PLATFORM_ENV: "linux"}, system="Darwin").source == "env"
    assert resolve_host("auto", env=HOME | {PLATFORM_ENV: "auto"}, system="Darwin").source == "auto"


def test_autodetect_darwin_and_linux() -> None:
    assert resolve_host(env=HOME, system="Darwin").name == "macos"
    host = resolve_host(env=HOME, system="Linux")
    assert (host.name, host.source) == ("linux", "auto")


def test_unsupported_system_raises() -> None:
    with pytest.raises(PlatformError, match="Windows"):
        resolve_host(env=HOME, system="Windows")


@pytest.mark.parametrize("flag, env", [("windows", {}), ("", {}), (None, {PLATFORM_ENV: "windows"}), (None, {PLATFORM_ENV: "MacOS"})])
def test_invalid_value_raises(flag: str | None, env: dict[str, str]) -> None:
    with pytest.raises(PlatformError, match="unknown platform"):
        resolve_host(flag, env=HOME | env, system="Linux")


@pytest.mark.parametrize("name", ["macos", "linux"])
def test_unity_env_override_wins(name: str) -> None:
    host = resolve_host(name, env=HOME | {"UNITY": "/x/Unity", "ADB": "adb", "LOG_ROOT": "/logs"}, system="Linux")
    assert host.unity_editor == Path("/x/Unity")
    assert host.adb == Path("adb")
    assert host.log_root == Path("/logs")


def test_macos_paths_unchanged() -> None:
    """The values the scripts and doctor used before the platform option existed."""
    host = resolve_host("macos", env={"HOME": "/Users/tester"}, system="Linux")
    editor = "/Applications/Unity/Hub/Editor/2021.3.23f1"
    assert host.unity_editor == Path(f"{editor}/Unity.app/Contents/MacOS/Unity")
    assert host.adb == Path(f"{editor}/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb")
    assert host.log_root == Path("/Users/tester/Library/Logs")
    assert host.asset_store_cache == Path("/Users/tester/Library/Unity/Asset Store-5.x")


def test_linux_paths_under_home() -> None:
    host = resolve_host("linux", env=HOME, system="Darwin")
    for value in (host.unity_editor, host.adb, host.log_root, host.asset_store_cache):
        assert str(value).startswith("/home/tester/")
        assert "/Applications" not in str(value) and "Library/" not in str(value)
    assert host.log_root == Path("/home/tester/.local/state/onward-generator")
    assert host.asset_store_cache == Path("/home/tester/.local/share/unity3d/Asset Store-5.x")
    assert resolve_host("linux", env=HOME | {"XDG_STATE_HOME": "/state"}).log_root == Path("/state/onward-generator")


def test_script_env_keys() -> None:
    host = resolve_host("linux", env=HOME)
    env = host.script_env()
    assert set(env) == {PLATFORM_ENV, "UNITY", "ADB", "LOG_ROOT"}
    assert env[PLATFORM_ENV] == "linux"
    assert env["UNITY"] == str(host.unity_editor)
    assert set(host.to_dict()) == {"name", "source", "unity_editor", "adb", "log_root", "asset_store_cache"}


def test_asset_store_cache_override() -> None:
    assert resolve_host("linux", env=HOME | {"ASSET_STORE_CACHE": "/cache"}).asset_store_cache == Path("/cache")
