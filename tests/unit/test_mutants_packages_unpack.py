"""Asset Store package discovery and the safe unpacker: slugs, cache layout, member names, sizes and folders."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from onward_generator.assetstore import packages
from onward_generator.assetstore.packages import PackageError, find_packages, slug, unpack


def _archive(path: Path, members: dict[str, bytes], *, gzip: bool = True) -> Path:
    with tarfile.open(path, "w:gz" if gzip else "w") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return path


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# ---- slug -------------------------------------------------------------------------------------------------------------


def test_slug_trims_separators_at_both_ends_and_after_truncation() -> None:
    assert slug("  Low Poly!  ") == "low_poly"
    assert slug("_Pack_") == "pack"
    assert slug("a" * 47 + " b") == "a" * 47


# ---- find_packages ----------------------------------------------------------------------------------------------------


def test_a_package_outside_publisher_and_category_folders_has_neither(tmp_path: Path) -> None:
    (tmp_path / "Loose Pack.unitypackage").write_bytes(b"x")
    (tmp_path / "Pub").mkdir()
    (tmp_path / "Pub" / "Half.unitypackage").write_bytes(b"x")
    found = find_packages(tmp_path)
    assert [(p.path, p.publisher, p.category, p.name) for p in found] == [
        (tmp_path / "Pub" / "Half.unitypackage", "", "", "Half"),
        (tmp_path / "Loose Pack.unitypackage", "", "", "Loose Pack"),
    ]


def test_packages_with_the_same_id_are_ordered_by_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = [tmp_path / "B" / "c" / "Same.unitypackage", tmp_path / "A" / "c" / "same.unitypackage"]
    for path in paths:
        path.parent.mkdir(parents=True)
        path.write_bytes(b"x")
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: iter(paths))
    assert [p.path for p in find_packages(tmp_path)] == [paths[1], paths[0]]


# ---- unpack -----------------------------------------------------------------------------------------------------------


def test_an_unreadable_or_uncompressed_package_is_refused_by_name(tmp_path: Path) -> None:
    (tmp_path / "junk.unitypackage").write_bytes(b"not a tar")
    with pytest.raises(PackageError, match=r"^junk\.unitypackage: not a readable \.unitypackage \("):
        unpack(tmp_path / "junk.unitypackage", tmp_path / "project")
    plain = _archive(tmp_path / "plain.unitypackage", {"g/pathname": b"Assets/a.fbx", "g/asset": b"x"}, gzip=False)
    with pytest.raises(PackageError, match=r"^plain\.unitypackage: not a readable"):
        unpack(plain, tmp_path / "project")


def test_members_with_a_dot_slash_prefix_are_read(tmp_path: Path) -> None:
    pkg = _archive(tmp_path / "p.unitypackage", {"./g1/pathname": b"Assets/A/a.fbx", "./g1/asset": b"fbx", "./g1/asset.meta": b"m"})
    project = tmp_path / "project"
    assert unpack(pkg, project) == 1
    assert (project / "Assets/A/a.fbx").read_bytes() == b"fbx"
    assert (project / "Assets/A/a.fbx.meta").read_bytes() == b"m"


def test_every_file_in_a_shared_folder_is_written_and_counted(tmp_path: Path) -> None:
    pkg = _archive(
        tmp_path / "p.unitypackage",
        {
            "g1/pathname": b"Assets/Pack/Models/a.fbx",
            "g1/asset": b"a",
            "g1/asset.meta": b"ma",
            "g2/pathname": b"Assets/Pack/Models/b.fbx",
            "g2/asset": b"b",
            "g2/asset.meta": b"mb",
            "g3/pathname": b"Assets/Pack/Models/c.fbx",
            "g3/asset": b"c",
        },
    )
    project = tmp_path / "project"
    assert unpack(pkg, project) == 3
    assert _files(project) == [
        "Assets/Pack/Models/a.fbx",
        "Assets/Pack/Models/a.fbx.meta",
        "Assets/Pack/Models/b.fbx",
        "Assets/Pack/Models/b.fbx.meta",
        "Assets/Pack/Models/c.fbx",
    ]


def test_a_nested_folder_entry_is_created_even_when_it_already_exists(tmp_path: Path) -> None:
    pkg = _archive(
        tmp_path / "p.unitypackage",
        {
            "a1/pathname": b"Assets/Pack/Deep/Tex/t.png",
            "a1/asset": b"png",
            "b1/pathname": b"Assets/Pack/Deep/Tex",
            "b1/asset.meta": b"folder",
            "c1/pathname": b"Assets/Other/Nested/Dir",
        },
    )
    project = tmp_path / "project"
    assert unpack(pkg, project) == 1
    assert (project / "Assets/Pack/Deep/Tex").is_dir() and (project / "Assets/Other/Nested/Dir").is_dir()
    assert (project / "Assets/Pack/Deep/Tex.meta").read_bytes() == b"folder"


def test_members_up_to_the_size_limit_are_written_and_larger_ones_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(packages, "MAX_MEMBER_BYTES", 4)
    pkg = _archive(
        tmp_path / "p.unitypackage",
        {
            "g1/pathname": b"Assets/A/fit.fbx",
            "g1/asset": b"1234",
            "g1/asset.meta": b"meta",
            "g2/pathname": b"Assets/B/Deep/big.fbx",
            "g2/asset": b"12345",
            "g2/asset.meta": b"m",
            "g3/pathname": b"Assets/C/meta_big.fbx",
            "g3/asset": b"x",
            "g3/asset.meta": b"12345",
        },
    )
    project = tmp_path / "project"
    assert unpack(pkg, project) == 2
    assert _files(project) == ["Assets/A/fit.fbx", "Assets/A/fit.fbx.meta", "Assets/B/Deep/big.fbx.meta", "Assets/C/meta_big.fbx"]


def test_pathnames_are_decoded_leniently_and_an_empty_one_is_skipped(tmp_path: Path) -> None:
    pkg = _archive(
        tmp_path / "p.unitypackage",
        {
            "g1/pathname": b"Assets/caf\xff.fbx\nsecond line",
            "g1/asset": b"x",
            "g2/pathname": b"",
            "g2/asset": b"y",
        },
    )
    project = tmp_path / "project"
    assert unpack(pkg, project) == 1
    assert _files(project) == ["Assets/caf�.fbx"]


def test_dots_and_spaces_inside_a_name_are_not_a_parent_reference(tmp_path: Path) -> None:
    pkg = _archive(
        tmp_path / "p.unitypackage",
        {
            "g1/pathname": b"Assets/a .. b.fbx",
            "g1/asset": b"x",
            "g2/pathname": b"Assets/XX..XX/c.fbx",
            "g2/asset": b"y",
        },
    )
    project = tmp_path / "project"
    assert unpack(pkg, project) == 2
    assert _files(project) == ["Assets/XX..XX/c.fbx", "Assets/a .. b.fbx"]
