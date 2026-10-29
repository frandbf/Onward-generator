"""Host platform: the paths the Unity steps use on macOS or Linux.

Precedence: ``--platform`` (``auto`` counts as not given), then ``ONWARD_PLATFORM``,
then ``platform.system()``. After the host is chosen, ``UNITY``, ``ADB``,
``LOG_ROOT`` and ``ASSET_STORE_CACHE`` still override single paths. Any other
platform name is an error. ``tools/unity/lib.sh`` applies the same rules to the
shell scripts. Decision: ``docs/decisions/ADR-011-host-platform.md``.
"""

from __future__ import annotations

import os
import platform as stdlib_platform
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

HostName = Literal["macos", "linux"]
Source = Literal["flag", "env", "auto"]
PLATFORM_ENV = "ONWARD_PLATFORM"
PLATFORM_CHOICES = ("auto", *get_args(HostName))
UNITY_VERSION = "2021.3.23f1"
_SYSTEMS: dict[str, HostName] = {"Darwin": "macos", "Linux": "linux"}


class PlatformError(ValueError):
    """The requested or detected host is not macOS or Linux."""


@dataclass(frozen=True, slots=True)
class HostProfile:
    name: HostName
    source: Source
    unity_editor: Path
    """The editor executable, not the ``.app`` bundle."""
    adb: Path
    log_root: Path
    asset_store_cache: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "source": self.source,
            "unity_editor": str(self.unity_editor),
            "adb": str(self.adb),
            "log_root": str(self.log_root),
            "asset_store_cache": str(self.asset_store_cache),
        }

    def script_env(self) -> dict[str, str]:
        """Variables ``tools/unity/lib.sh`` reads, so a script sees the same host."""
        return {
            PLATFORM_ENV: self.name,
            "UNITY": str(self.unity_editor),
            "ADB": str(self.adb),
            "LOG_ROOT": str(self.log_root),
        }


def _host_name(value: str, origin: str) -> HostName:
    if value not in _SYSTEMS.values():
        raise PlatformError(f"unknown platform {value!r} from {origin}; use one of {', '.join(PLATFORM_CHOICES)}")
    return value  # type: ignore[return-value]


def _choose(requested: str | None, env: Mapping[str, str], system: str | None) -> tuple[HostName, Source]:
    if requested is not None and requested != "auto":
        return _host_name(requested, "--platform"), "flag"
    value = env.get(PLATFORM_ENV, "")
    if value and value != "auto":
        return _host_name(value, PLATFORM_ENV), "env"
    detected = stdlib_platform.system() if system is None else system
    if detected not in _SYSTEMS:
        raise PlatformError(f"unsupported host system {detected!r}; set --platform macos or --platform linux")
    return _SYSTEMS[detected], "auto"


def resolve_host(requested: str | None = None, env: Mapping[str, str] | None = None, system: str | None = None) -> HostProfile:
    """The host profile. ``env`` defaults to ``os.environ`` and ``system`` to ``platform.system()``."""
    environ = os.environ if env is None else env
    name, source = _choose(requested, environ, system)
    home = Path(environ.get("HOME") or Path.home())
    if name == "macos":
        editor = Path("/Applications/Unity/Hub/Editor") / UNITY_VERSION
        unity = editor / "Unity.app/Contents/MacOS/Unity"
        adb = editor / "PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb"
        log_root = home / "Library/Logs"
        cache = home / "Library/Unity/Asset Store-5.x"
    else:
        editor = home / "Unity/Hub/Editor" / UNITY_VERSION / "Editor"
        unity = editor / "Unity"
        adb = editor / "Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb"
        log_root = Path(environ.get("XDG_STATE_HOME") or home / ".local/state") / "onward-generator"
        cache = home / ".local/share/unity3d/Asset Store-5.x"
    return HostProfile(
        name=name,
        source=source,
        unity_editor=Path(environ.get("UNITY") or unity),
        adb=Path(environ.get("ADB") or adb),
        log_root=Path(environ.get("LOG_ROOT") or log_root),
        asset_store_cache=Path(environ.get("ASSET_STORE_CACHE") or cache),
    )
