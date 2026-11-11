"""The Unity Asset Store packages a user has downloaded, and a safe unpacker for them (ADR-035).

Unity keeps every package a user downloads with the Package Manager as a ``.unitypackage`` in its cache:
``<cache>/<publisher>/<category>/<name>.unitypackage`` (``HostProfile.asset_store_cache``). A package is a gzip tar:
one folder per asset GUID with ``pathname`` (the path under ``Assets/``), ``asset`` (the file) and ``asset.meta``.
Nothing is downloaded here and nothing leaves the user's machine: each user sees the packages of their own account.
"""

from __future__ import annotations

import posixpath
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

LOCAL_PREFIX = "asset_store_"
"""Catalogue folders under ``assets/`` written from Asset Store packages. Gitignored: they never reach the repository."""
LICENCE_NAME = "Unity Asset Store EULA"
MAX_MEMBER_BYTES = 512 * 1024 * 1024
CODE_SUFFIXES = frozenset({".cs", ".js", ".boo", ".dll", ".so", ".dylib", ".bundle", ".jslib", ".asmdef", ".asmref", ".rsp"})


class PackageError(ValueError):
    """A package that cannot be read or unpacked safely."""


@dataclass(frozen=True, slots=True)
class Package:
    path: Path
    publisher: str
    category: str
    name: str

    @property
    def id(self) -> str:
        """A stable folder-safe id from the package name: ``Low Poly Storage Pack`` becomes ``low_poly_storage_pack``."""
        return slug(self.name)

    @property
    def catalogue(self) -> str:
        return f"{LOCAL_PREFIX}{self.id}"

    @property
    def size_mb(self) -> float:
        return self.path.stat().st_size / (1024 * 1024)


def slug(text: str) -> str:
    value = "_".join(re.findall(r"[a-z0-9]+", text.lower()))[:48]
    value = value.rstrip("_")  # pragma: no mutate: the text is lower case, so an X in the stripped set changes nothing
    return value or "package"


def find_packages(cache: Path) -> list[Package]:
    """Every ``.unitypackage`` under the cache, sorted by id. A missing cache is no package."""
    if not cache.is_dir():
        return []
    found = []
    for path in cache.rglob("*.unitypackage"):
        parts = path.relative_to(cache).parts
        publisher = parts[0] if len(parts) >= 3 else ""
        category = parts[1] if len(parts) >= 3 else ""
        found.append(Package(path, publisher, category, path.stem))
    found.sort(key=lambda p: (p.id, str(p.path)))
    return found


def unpack(package: Path, project: Path) -> int:
    """Write the package's files and ``.meta`` files under ``project/Assets``; return the number of files.

    A ``pathname`` outside ``Assets/`` (absolute, ``..``, or another root) is skipped, as is a member above
    ``MAX_MEMBER_BYTES``: the archive is untrusted input.
    """
    try:
        archive = tarfile.open(package, "r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise PackageError(f"{package.name}: not a readable .unitypackage ({exc})") from exc
    written = 0
    try:
        members = {m.name.removeprefix("./"): m for m in archive.getmembers() if m.isfile()}
        guids = sorted({name.split("/")[0] for name in members if name.endswith("/pathname")})
        for guid in guids:
            target = _target(archive, members, guid, project)
            if target is None:
                continue
            asset, meta = members.get(f"{guid}/asset"), members.get(f"{guid}/asset.meta")
            if asset is not None and asset.size <= MAX_MEMBER_BYTES:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(_read(archive, asset))
                written += 1
            elif asset is None:
                target.mkdir(parents=True, exist_ok=True)
            if meta is not None and meta.size <= MAX_MEMBER_BYTES:
                target.parent.mkdir(parents=True, exist_ok=True)
                Path(f"{target}.meta").write_bytes(_read(archive, meta))
    finally:
        archive.close()
    return written


def _read(archive: tarfile.TarFile, member: tarfile.TarInfo) -> bytes:
    handle = archive.extractfile(member)
    assert handle is not None  # members are regular files, so extractfile always returns a handle
    return handle.read()


def _target(archive: tarfile.TarFile, members: dict[str, tarfile.TarInfo], guid: str, project: Path) -> Path | None:
    raw = _read(archive, members[f"{guid}/pathname"]).decode("utf-8", "replace").splitlines()
    path = raw[0].strip() if raw else ""  # pragma: no mutate: any fallback that does not start with Assets/ is skipped alike
    normal = posixpath.normpath(path)
    if not normal.startswith("Assets/") or normal.startswith("Assets/Editor"):
        return None
    if ".." in normal.split("/"):  # pragma: no mutate: after normpath a .. can only lead the path, which already fails the Assets/ test; kept as a guard
        return None
    if posixpath.splitext(normal)[1].lower() in CODE_SUFFIXES:
        return None  # Unity would compile and run a package's code in the export project: meshes and textures only
    return project / normal
