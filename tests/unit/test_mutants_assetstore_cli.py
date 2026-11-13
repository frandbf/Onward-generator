"""``onward-generator asset-store scan | import`` (ADR-035): arguments, selection, output and exit codes.

The Unity export (``import_packages``) and the host profile (``resolve_host``) are replaced: no test launches Unity or
reads the real Asset Store cache under the home folder. Packages are small files in ``tmp_path``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from onward_generator.assetstore import __main__ as cli
from onward_generator.assetstore.importer import PackageResult
from onward_generator.assetstore.packages import Package, find_packages
from onward_generator.platform import PlatformError

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """No host profile and no Unity unless a test provides them, so even a mutated dispatch cannot reach either."""

    def no_host() -> None:
        raise PlatformError("no host profile in tests")

    def no_unity(*args: object, **kwargs: object) -> None:
        raise AssertionError("import_packages must be replaced in tests")

    monkeypatch.setattr(cli, "resolve_host", no_host)
    monkeypatch.setattr(cli, "import_packages", no_unity)


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """Three packages: alpha 0.5 MB, beta 1 MB, gamma 0.25 MB."""
    root = tmp_path / "cache"
    for publisher, name, size in (("Pub A", "Alpha Pack", MB // 2), ("Pub B", "Beta Pack", MB), ("Pub C", "Gamma Pack", MB // 4)):
        path = root / publisher / "3D Models" / f"{name}.unitypackage"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"\0" * size)
    return root


@pytest.fixture
def assets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The catalogue root, with gamma already imported."""
    root = tmp_path / "assets"
    (root / "asset_store_gamma_pack").mkdir(parents=True)
    (root / "asset_store_gamma_pack" / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "ASSETS", root)
    return root


@pytest.fixture
def editor(tmp_path: Path) -> Path:
    path = tmp_path / "Unity"
    path.write_bytes(b"")
    return path


def _host(monkeypatch: pytest.MonkeyPatch, *, unity_editor: Path, asset_store_cache: Path) -> None:
    host = SimpleNamespace(unity_editor=unity_editor, asset_store_cache=asset_store_cache)
    monkeypatch.setattr(cli, "resolve_host", lambda: host)


class FakeImport:
    """Stands in for ``import_packages``: records the call, reports each result and returns them."""

    def __init__(self, results: Callable[[Sequence[Package]], list[PackageResult]] | None = None) -> None:
        self.calls: list[tuple[list[Package], Path, Path, int, Any]] = []
        self.results = results or (lambda packages: [PackageResult(p, "imported", rows=1) for p in packages])

    def __call__(
        self, packages: Sequence[Package], unity: Path, assets: Path, *, max_triangles: int, report: Callable[[PackageResult], None]
    ) -> list[PackageResult]:
        self.calls.append((list(packages), unity, assets, max_triangles, report))
        out = self.results(packages)
        for result in out:
            report(result)
        return out


def _fake(monkeypatch: pytest.MonkeyPatch, results: Callable[[Sequence[Package]], list[PackageResult]] | None = None) -> FakeImport:
    fake = FakeImport(results)
    monkeypatch.setattr(cli, "import_packages", fake)
    return fake


def _ids(packages: Sequence[Package]) -> list[str]:
    return [p.id for p in packages]


def _line(package_id: str, size: float, state: str, owner: str, large: str = "") -> str:
    return f"{package_id:48s} {size:8.1f} MB  {state:12s}  {owner}{large}"


# ---- scan -----------------------------------------------------------------------------------------------------------


def test_scan_lists_size_state_and_owner(cache: Path, assets: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["scan", "--cache", str(cache), "--max-mb", "0.5"]) == 0
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        _line("alpha_pack", 0.5, "not imported", "Pub A / Alpha Pack"),
        _line("beta_pack", 1.0, "not imported", "Pub B / Beta Pack", "  (over --max-mb)"),
        _line("gamma_pack", 0.2, "imported", "Pub C / Gamma Pack"),
        "3 packages",
    ]
    assert out.err == ""


def test_scan_default_limit_is_the_importer_default(
    cache: Path, assets: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "DEFAULT_MAX_MB", 0.75)
    assert cli.main(["scan", "--cache", str(cache)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith("Pub A / Alpha Pack")
    assert lines[1].endswith("Pub B / Beta Pack  (over --max-mb)")


def test_scan_uses_the_host_cache_without_cache_flag(
    cache: Path, assets: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _host(monkeypatch, unity_editor=tmp_path / "none", asset_store_cache=cache)
    assert cli.main(["scan"]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "3 packages"


def test_scan_empty_cache_explains_how_to_download(tmp_path: Path, assets: Path, capsys: pytest.CaptureFixture[str]) -> None:
    empty = tmp_path / "empty"
    assert cli.main(["scan", "--cache", str(empty)]) == 0
    out = capsys.readouterr()
    assert out.out == (
        f"asset-store: no .unitypackage under {empty}. Download packs in Unity: Window > Package Manager > My Assets > Download.\n"
    )
    assert out.err == ""


# ---- import: host and editor ----------------------------------------------------------------------------------------


def test_import_without_host_profile_fails_on_stderr(
    cache: Path, assets: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_host() -> None:
        raise PlatformError("no host profile here")

    monkeypatch.setattr(cli, "resolve_host", no_host)
    fake = _fake(monkeypatch)
    assert cli.main(["import", "--cache", str(cache)]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == "asset-store: no host profile here\n"
    assert fake.calls == []


def test_import_without_editor_fails_on_stderr(
    cache: Path, assets: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "no" / "Unity"
    _host(monkeypatch, unity_editor=missing, asset_store_cache=cache)
    fake = _fake(monkeypatch)
    assert cli.main(["import"]) == 2
    out = capsys.readouterr()
    assert out.out == ""
    assert out.err == f"asset-store: the Unity editor is not at {missing} (pass --unity)\n"
    assert fake.calls == []


def test_import_unity_flag_overrides_the_host_editor(
    cache: Path, assets: Path, editor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host(monkeypatch, unity_editor=tmp_path / "no" / "Unity", asset_store_cache=cache)
    fake = _fake(monkeypatch)
    assert cli.main(["import", "--unity", str(editor), "--only", "alpha_pack"]) == 0
    assert fake.calls[0][1] == editor


# ---- import: selection ----------------------------------------------------------------------------------------------


def test_import_default_skips_large_and_imported(
    cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    fake = _fake(monkeypatch)
    assert cli.main(["import", "--max-mb", "0.5"]) == 0
    assert len(fake.calls) == 1
    packages, unity, root, max_triangles, report = fake.calls[0]
    assert _ids(packages) == ["alpha_pack"]
    assert unity == editor
    assert root == assets
    assert max_triangles == 8000
    assert report is cli._print
    assert capsys.readouterr().out.splitlines() == [
        "skip beta_pack: 1 MB is over --max-mb 0.5 (pass --only beta_pack to import it anyway)",
        "alpha_pack: imported, 1 rows",
        f"1 rows in 1 local catalogues under {assets}/asset_store_*",
    ]


def test_import_refresh_keeps_imported_packages(cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    fake = _fake(monkeypatch)
    assert cli.main(["import", "--refresh", "--max-mb", "0.5"]) == 0
    assert _ids(fake.calls[0][0]) == ["alpha_pack", "gamma_pack"]


def test_import_only_ignores_size_and_state(
    cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    fake = _fake(monkeypatch)
    assert cli.main(["import", "--only", "gamma_pack", "beta_pack", "--max-mb", "0.5", "--max-triangles", "123"]) == 0
    packages, _, _, max_triangles, _ = fake.calls[0]
    assert _ids(packages) == ["beta_pack", "gamma_pack"]
    assert max_triangles == 123
    assert type(max_triangles) is int
    assert not capsys.readouterr().out.startswith("skip")


def test_import_only_unknown_ids_exits_with_the_missing_ids(
    cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    fake = _fake(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        cli.main(["import", "--only", "zeta", "alpha_pack", "nope"])
    assert exc.value.code == "asset-store: no downloaded package with id nope, zeta (see: asset-store scan)"
    assert fake.calls == []


def test_import_nothing_left_to_import(
    cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    fake = _fake(monkeypatch)
    assert cli.main(["import", "--max-mb", "0.1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "asset-store: nothing to import"
    assert len(out) == 4
    assert fake.calls == []


# ---- import: summary and exit code ----------------------------------------------------------------------------------


def _packages(cache: Path) -> dict[str, Package]:
    return {p.id: p for p in find_packages(cache)}


def test_import_partial_failure_reports_and_succeeds(
    cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    by_id = _packages(cache)
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    _fake(
        monkeypatch,
        lambda _: [
            PackageResult(by_id["alpha_pack"], "imported", rows=3, skipped=Counter({"too_many_triangles": 2, "code": 1})),
            PackageResult(by_id["beta_pack"], "failed", error="Unity exited 1"),
        ],
    )
    assert cli.main(["import", "--only", "alpha_pack", "beta_pack"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "alpha_pack: imported, 3 rows  skipped: code 1, too_many_triangles 2",
        "beta_pack: failed, 0 rows  Unity exited 1",
        f"3 rows in 1 local catalogues under {assets}/asset_store_*; failed: beta_pack",
    ]


def test_import_all_failed_exits_one(
    cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    by_id = _packages(cache)
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    _fake(monkeypatch, lambda _: [PackageResult(by_id["alpha_pack"], "failed"), PackageResult(by_id["beta_pack"], "failed")])
    assert cli.main(["import", "--only", "alpha_pack", "beta_pack"]) == 1
    assert capsys.readouterr().out.splitlines()[-1] == (
        f"0 rows in 0 local catalogues under {assets}/asset_store_*; failed: alpha_pack, beta_pack"
    )


def test_import_empty_without_failure_exits_zero(
    cache: Path, assets: Path, editor: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    by_id = _packages(cache)
    _host(monkeypatch, unity_editor=editor, asset_store_cache=cache)
    _fake(monkeypatch, lambda _: [PackageResult(by_id["alpha_pack"], "empty")])
    assert cli.main(["import", "--only", "alpha_pack"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "alpha_pack: empty, 0 rows",
        f"0 rows in 0 local catalogues under {assets}/asset_store_*",
    ]


def test_print_flushes_each_line(cache: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(cli, "print", lambda *a, **k: calls.append((a, k)), raising=False)
    cli._print(PackageResult(_packages(cache)["alpha_pack"], "imported", rows=2))
    assert calls == [(("alpha_pack: imported, 2 rows",), {"flush": True})]
    assert calls[0][1]["flush"] is True


# ---- arguments and help ---------------------------------------------------------------------------------------------


def test_a_command_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main([])
    assert exc.value.code == 2
    assert "usage: onward-generator asset-store" in capsys.readouterr().err


def _help(argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> str:
    monkeypatch.setenv("COLUMNS", "400")
    with pytest.raises(SystemExit) as exc:
        cli.main([*argv, "--help"])
    assert exc.value.code == 0
    return capsys.readouterr().out


def test_top_help_names_the_program_and_its_summary(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = _help([], monkeypatch, capsys)
    assert text.startswith("usage: onward-generator asset-store ")
    assert "\n``onward-generator asset-store scan | import | textures`` (ADR-035).\n" in text


@pytest.mark.parametrize("command", ["scan", "import"])
def test_both_commands_document_cache_and_size(command: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = _help([command], monkeypatch, capsys)
    assert text.startswith(f"usage: onward-generator asset-store {command} ")
    assert "the Unity Asset Store cache (default: the host profile's asset_store_cache)" in text
    assert "skip packages larger than this unless named by --only" in text
    assert "XX" not in text


def test_import_help_documents_its_options(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    text = _help(["import"], monkeypatch, capsys)
    for pattern in (
        r"--only ONLY \[ONLY \.\.\.\]\s+package ids from scan\n",
        r"--refresh\s+import again packages that already have a local catalogue\n",
        r"--max-triangles MAX_TRIANGLES\s+skip models with more LOD0 triangles\n",
        r"--unity UNITY\s+the Unity editor executable \(default: the host profile's\)\n",
    ):
        assert re.search(pattern, text), pattern
    assert "XX" not in text
