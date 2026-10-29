from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from onward_generator import __version__
from onward_generator.cli import main
from onward_generator.doctor import MURCIA_CENTER, MURCIA_CENTER_EPSG25830, run_checks
from onward_generator.config.validation import REPO_ROOT
from tests.conftest import AOI_FIXTURE, MURCIA_CENTER as TEST_CENTER, ORIGIN, OSM_FIXTURE

EXAMPLE = REPO_ROOT / "config" / "murcia-belluga.example.json"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"onward-generator {__version__}"
    assert main(["version", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"name": "onward-generator", "version": __version__}


def test_validate_config_valid(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-config", str(EXAMPLE)]) == 0
    assert "valid" in capsys.readouterr().out


def test_validate_config_invalid_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"project": {}}', encoding="utf-8")
    assert main(["validate-config", str(path), "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["valid"] is False
    assert {"severity": "error", "code": "schema_required", "path": "/project", "message": "'name' is a required property"} in report["diagnostics"]


def test_usage_errors_return_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert main(["unknown"]) == 2
    assert main(["--help"]) == 0
    capsys.readouterr()


def test_doctor_checks_core_environment(capsys: pytest.CaptureFixture[str]) -> None:
    checks = {c.name: c for c in run_checks()}
    for name in ("python", "package:shapely", "package:pyproj", "package:jsonschema", "geo:libraries", "geo:projection", "config:schema"):
        assert checks[name].status == "ok", checks[name]
    assert MURCIA_CENTER == TEST_CENTER
    assert f"{MURCIA_CENTER_EPSG25830[0]:.3f}" in checks["geo:projection"].detail
    assert main(["doctor", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and len(report["checks"]) == len(checks)


def test_doctor_reports_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator import doctor

    monkeypatch.setattr(doctor, "MIN_PYTHON", (99, 0))
    assert main(["doctor"]) == 1
    assert "doctor: FAILED" in capsys.readouterr().out


def test_geo_subcommand_delegates(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "geo"
    args = ["geo", "--source", str(OSM_FIXTURE), "--aoi", str(AOI_FIXTURE), "--out", str(out), "--origin", *map(str, ORIGIN)]
    assert main(args) == 0
    assert (out / "geo_scene.json").is_file()
    capsys.readouterr()


def test_global_platform_flag_before_subcommand(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ONWARD_PLATFORM", "macos")
    main(["--platform", "linux", "doctor", "--json"])
    report = json.loads(capsys.readouterr().out)
    platform = {c["name"]: c for c in report["checks"]}["host:platform"]
    assert platform["status"] == "ok" and platform["detail"].startswith("linux")
    assert main(["--platform=macos", "version"]) == 0
    capsys.readouterr()


def test_global_platform_flag_reaches_module_subcommands(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ONWARD_PLATFORM", "auto")
    with pytest.raises(SystemExit) as exited:
        main(["--platform", "linux", "map", "--help"])
    assert exited.value.code == 0
    assert "unrecognized" not in capsys.readouterr().err
    assert os.environ["ONWARD_PLATFORM"] == "linux"


def test_platform_command_json(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from onward_generator.platform import resolve_host

    monkeypatch.setenv("ONWARD_PLATFORM", "linux")
    assert main(["platform", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == resolve_host().to_dict()
    assert report["name"] == "linux" and report["source"] == "env"
    assert main(["--platform", "macos", "platform"]) == 0
    assert "macos" in capsys.readouterr().out


def test_bad_platform_exits_invalid(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ONWARD_PLATFORM", "auto")
    assert main(["--platform", "windows", "doctor"]) == 2
    assert "unknown platform 'windows'" in capsys.readouterr().err
    assert main(["--platform"]) == 2
    assert main(["run", "--platform", "windows", "--name", "x", "--lat", "1", "--lon", "1"]) == 2
    monkeypatch.setenv("ONWARD_PLATFORM", "windows")
    assert main(["platform"]) == 2
    capsys.readouterr()


def test_doctor_fails_on_an_unknown_platform_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ONWARD_PLATFORM", "windows")
    checks = {c.name: c for c in run_checks()}
    assert checks["host:platform"].status == "fail"
    assert "onward:unity_editor" not in checks
