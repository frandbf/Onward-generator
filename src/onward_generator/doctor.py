"""Environment diagnostics (FR-001). Read-only: nothing is installed or changed."""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Literal

from onward_generator.config.validation import REPO_ROOT, load_schema_validator, schema_path

Status = Literal["ok", "warn", "fail"]

MIN_PYTHON = (3, 12)
PINNED: dict[str, str] = {"shapely": "2.2.0", "pyproj": "3.8.0", "jsonschema": "4.26.0", "pillow": "11.3.0"}
MIN_FREE_GB = 10.0
# Centre of the first built map (Belluga). It checks the projection; it does not name the tool.
MURCIA_CENTER = (-1.12863, 37.98407)
MURCIA_CENTER_EPSG25830 = (664343.977, 4205699.652)
UNITY_EDITOR = Path("/Applications/Unity/Hub/Editor/2021.3.23f1/Unity.app")
UNITY_ADB = Path("/Applications/Unity/Hub/Editor/2021.3.23f1/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb")
ONWARD_SDK = Path("~/Unity/OnwardSDK/OnwardCustomContent_SDK_v1.15.unitypackage").expanduser()


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    status: Status
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


def _python() -> Check:
    version = ".".join(map(str, sys.version_info[:3]))
    ok = sys.version_info[:2] >= MIN_PYTHON
    return Check("python", "ok" if ok else "fail", f"{version} (requires ≥ {'.'.join(map(str, MIN_PYTHON))})")


def _packages() -> list[Check]:
    checks = []
    for name, pinned in PINNED.items():
        try:
            installed = metadata.version(name)
        except metadata.PackageNotFoundError:
            checks.append(Check(f"package:{name}", "fail", f"not installed (pinned {pinned})"))
            continue
        status: Status = "ok" if installed == pinned else "warn"
        checks.append(Check(f"package:{name}", status, f"{installed} (pinned {pinned})"))
    return checks


def _geo_libraries() -> list[Check]:
    try:
        import shapely
        from pyproj import network, proj_version_str

        from onward_generator.geo.projection import DEFAULT_CRS, LocalFrame
    except ImportError as exc:
        return [Check("geo:libraries", "fail", str(exc))]

    checks = [Check("geo:libraries", "ok", f"GEOS {shapely.geos_version_string}, PROJ {proj_version_str}")]
    try:
        frame = LocalFrame.create(*MURCIA_CENTER)
        dx = frame.origin_easting - MURCIA_CENTER_EPSG25830[0]
        dy = frame.origin_northing - MURCIA_CENTER_EPSG25830[1]
        ok = abs(dx) < 0.01 and abs(dy) < 0.01
        offline = "network disabled" if not network.is_network_enabled() else "network ENABLED"
        checks.append(
            Check(
                "geo:projection",
                "ok" if ok else "fail",
                f"{DEFAULT_CRS} resolves offline ({offline}); Belluga centre → "
                f"({frame.origin_easting:.3f}, {frame.origin_northing:.3f})",
            )
        )
    except Exception as exc:  # noqa: BLE001 - any failure here is a diagnostic
        checks.append(Check("geo:projection", "fail", str(exc)))
    return checks


def _schema() -> Check:
    path = schema_path()
    if not path.is_file():
        return Check("config:schema", "fail", f"missing {path}")
    try:
        load_schema_validator()
    except Exception as exc:  # noqa: BLE001
        return Check("config:schema", "fail", f"{path}: {exc}")
    return Check("config:schema", "ok", f"{path} (JSON Schema 2020-12)")


def _directories(root: Path) -> list[Check]:
    checks = []
    for rel in ("output",):
        path = root / rel
        if not path.is_dir():
            checks.append(Check(f"dir:{rel}", "warn", "missing"))
        elif not os.access(path, os.W_OK):
            checks.append(Check(f"dir:{rel}", "fail", "not writable"))
        else:
            checks.append(Check(f"dir:{rel}", "ok", "writable"))
    return checks


def _disk(root: Path) -> Check:
    free_gb = shutil.disk_usage(root).free / 1024**3
    status: Status = "ok" if free_gb >= MIN_FREE_GB else "warn"
    return Check("disk", status, f"{free_gb:.1f} GiB free on the project volume (warn below {MIN_FREE_GB:.0f})")


def _toolchain() -> list[Check]:
    """Phase 0 build chain. Missing tools only matter for export, so they warn."""
    items = [
        ("onward:unity_editor", UNITY_EDITOR, "Unity 2021.3.23f1"),
        ("onward:adb", UNITY_ADB, "adb from Unity Android SDK"),
        ("onward:sdk_package", ONWARD_SDK, "Onward Custom Content SDK v1.15"),
    ]
    return [Check(name, "ok" if path.exists() else "warn", f"{label}: {path}" + ("" if path.exists() else " (not found)")) for name, path, label in items]


def run_checks(root: Path = REPO_ROOT) -> list[Check]:
    steps: list[Callable[[], Check | list[Check]]] = [
        _python,
        _packages,
        _geo_libraries,
        _schema,
        lambda: _directories(root),
        lambda: _disk(root),
        _toolchain,
    ]
    checks: list[Check] = []
    for step in steps:
        result = step()
        checks.extend(result if isinstance(result, list) else [result])
    return checks
