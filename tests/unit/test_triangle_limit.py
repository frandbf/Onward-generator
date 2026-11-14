"""map.json triangle_limit: the Unity triangle limit overgrow fills to and export package checks (ADR-005, 14 November 2026)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from onward_generator.export.triangle_budget import UNITY_TRIANGLE_LIMIT
from onward_generator.maps.workspace import TRIANGLE_LIMIT_MAX, TRIANGLE_LIMIT_MIN, MapError, create_map, triangle_limit
from onward_generator.next_step import export_invocation, overgrow_command
from onward_generator.overgrow.place import triangle_headroom

LON = -58.381647718021945
LAT = -34.60369254204782


def _map(tmp_path: Path, limit: object | None) -> Path:
    root = tmp_path / "m"
    create_map(root, map_id="m", name="M", latitude=LAT, longitude=LON, radius_m=150)
    if limit is not None:
        document = json.loads((root / "map.json").read_text(encoding="utf-8"))
        document["triangle_limit"] = limit
        (root / "map.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def test_the_bounds() -> None:
    assert (TRIANGLE_LIMIT_MIN, TRIANGLE_LIMIT_MAX) == (100_000, 300_000) and TRIANGLE_LIMIT_MAX == UNITY_TRIANGLE_LIMIT


@pytest.mark.parametrize("limit", [100_000, 250_000, 300_000])
def test_a_limit_is_read(tmp_path: Path, limit: int) -> None:
    assert triangle_limit(_map(tmp_path, limit)) == limit


def test_no_key_is_no_limit(tmp_path: Path) -> None:
    assert triangle_limit(_map(tmp_path, None)) is None


@pytest.mark.parametrize("limit", [99_999, 300_001, True, "250000", 250_000.0])
def test_a_bad_limit_is_refused(tmp_path: Path, limit: object) -> None:
    with pytest.raises(MapError, match=f'^map.json "triangle_limit" must be an integer from 100000 to 300000, got {limit!r}$'):
        triangle_limit(_map(tmp_path, limit))


def test_run_hands_the_limit_to_overgrow_and_export(tmp_path: Path) -> None:
    root = _map(tmp_path, 250_000)
    base = root / "data" / "processed" / "scene_signs"
    over = overgrow_command(base, root / "data" / "processed" / "geo" / "geo_scene.json").display()
    assert "--triangle-limit 250000" in over
    detail = root / "data" / "processed" / "scene_covered"
    export = export_invocation(root, detail, root / "layout.json", tmp_path / "pkg").display()
    assert export.endswith("--triangle-limit 250000") or " --triangle-limit 250000" in export
    plain = _map(tmp_path / "plain", None)
    assert "--triangle-limit" not in overgrow_command(plain / "data" / "processed" / "scene_signs").display()
    assert "--triangle-limit" not in export_invocation(plain, detail, plain / "layout.json", tmp_path / "pkg2").display()


def test_the_headroom_follows_the_limit() -> None:
    assert triangle_headroom(100_000, 20_000) == 300_000 - 12_300 - 12_000 - 100_000 - 20_000
    assert triangle_headroom(100_000, 20_000, 250_000) == 250_000 - 12_300 - 12_000 - 100_000 - 20_000
    assert triangle_headroom(240_000, 0, 250_000) == 0


@pytest.mark.parametrize(("argv", "expected"), [(["--triangle-limit", "250000"], 250_000), ([], UNITY_TRIANGLE_LIMIT)])
def test_export_package_hands_the_limit_to_the_package(monkeypatch: pytest.MonkeyPatch, argv: list[str], expected: int) -> None:
    import onward_generator.export.__main__ as cli
    from onward_generator.export.package import ExportError

    seen: dict[str, object] = {}

    def fake(*args: object, **kwargs: object) -> None:
        seen.update(kwargs)
        raise ExportError(["stop here"])

    monkeypatch.setattr(cli, "build_package", fake)
    assert cli.main(["package", "--project", "p.json", "--detail", "d", "--layout", "l.json", "--out", "o", *argv]) == 1
    assert seen["triangle_limit"] == expected
