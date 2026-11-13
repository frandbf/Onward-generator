"""Wall decal catalogues from Asset Store texture packages (ADR-038).

A decal package (Bricks Decals FREE: patches of brick showing through broken plaster) is a set of albedo images with a
transparent background, not models, so it needs no Unity export: the albedo PNGs are read straight from the
``.unitypackage`` (normal, height and smoothness maps are left out), scaled to at most ``MAX_PX`` on their long side,
and written with a manifest to ``assets/asset_store_<package>/`` like the model catalogues of ADR-035. The folder is
gitignored: the images stay on the user's machine, as the Unity Asset Store EULA requires.
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import tarfile
from pathlib import Path
from typing import Any

from PIL import Image

from onward_generator.assetstore.packages import LICENCE_NAME, MAX_MEMBER_BYTES, Package
from onward_generator.io import atomic_write_bytes, atomic_write_json

DECALS_SCHEMA = "onward_generator.wall_decals"
MAX_PX = 512
# Maps of a texture set that are not its albedo.
NOT_ALBEDO = re.compile(r"(normal|norm|height|heigth|smooth|rough|metal|ao|occlusion|mask|emiss)", re.IGNORECASE)
IMAGE_SUFFIXES = (".png", ".tga")
MIN_TRANSPARENT = 0.05
"""A decal shows the wall around it: an image with less than this share of transparent pixels is a texture, not a decal."""
WIDTH_M = 1.6
"""The width a decal is drawn at on the wall when its image is square; a long one is wider, a tall one narrower."""


def _albedo_members(archive: tarfile.TarFile) -> list[tuple[str, tarfile.TarInfo]]:
    members = {m.name.removeprefix("./"): m for m in archive.getmembers() if m.isfile()}
    found = []
    for name in sorted(members):
        if not name.endswith("/pathname"):
            continue
        handle = archive.extractfile(members[name])
        path = handle.read().decode("utf-8", "replace").splitlines()[0].strip() if handle is not None else ""
        asset = members.get(name.removesuffix("pathname") + "asset")
        stem = posixpath.splitext(posixpath.basename(path))[0]
        if (asset is not None and asset.size <= MAX_MEMBER_BYTES and path.lower().endswith(IMAGE_SUFFIXES)
                and "decal" in path.lower() and not NOT_ALBEDO.search(stem)):
            found.append((path, asset))
    return found


def decal_rows(package: Package, assets: Path) -> list[dict[str, Any]]:
    """Write the package's decals under ``assets/<catalogue>/`` and return their manifest rows (none for a package
    without decal images)."""
    target = assets / package.catalogue
    rows: list[dict[str, Any]] = []
    with tarfile.open(package.path, "r:gz") as archive:
        for path, member in _albedo_members(archive):
            stem = posixpath.splitext(posixpath.basename(path))[0]
            handle = archive.extractfile(member)
            if handle is None:
                continue
            try:
                with Image.open(io.BytesIO(handle.read())) as picture:
                    image = picture.convert("RGBA")
            except (OSError, ValueError):
                continue
            alpha = image.getchannel("A")
            transparent = sum(alpha.histogram()[:250]) / (image.width * image.height)
            if transparent < MIN_TRANSPARENT:
                continue
            image.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            image.save(out, format="PNG")
            file = f"{re.sub(r'[^A-Za-z0-9_]+', '_', stem)}.png"
            target.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(target / file, out.getvalue())
            aspect = image.width / image.height
            rows.append({
                "id": f"as_{package.id[:20]}_{file[:-4]}"[:80], "file": file, "kind": "brick_patch",
                "description": f"Wall decal ({stem.replace('_', ' ').lower()}) from {package.name}: brick showing through broken plaster.",
                "width_m": round(min(3.2, WIDTH_M * aspect ** 0.5), 3), "height_m": round(min(3.2, WIDTH_M / aspect ** 0.5), 3),
                "license": "LicenseRef-Unity-Asset-Store-EULA",
            })
    if rows:
        atomic_write_json(target / "manifest.json", {
            "schema": DECALS_SCHEMA, "schema_version": "0.1.0",
            "source": f"{LICENCE_NAME} (Extension Asset), local only: {package.publisher} / {package.name}. Converted on this "
                      "machine; never committed or redistributed outside a built map.",
            "package": {"publisher": package.publisher, "category": package.category, "name": package.name}, "assets": rows,
        })
        (target / "License.txt").write_text(
            f"{package.name} by {package.publisher}, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
            "Local copy for this user's maps only. Do not commit, share or publish these files.\n", encoding="utf-8")  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on
    return rows


def read_decal_catalogues(assets: Path) -> list[tuple[Path, dict[str, Any]]]:
    """Every decal row of the local catalogues under ``assets`` (``asset_store_*`` with a decals manifest), with its
    image path; sorted by id."""
    found: list[tuple[Path, dict[str, Any]]] = []
    for manifest in sorted(assets.glob("asset_store_*/manifest.json")):
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("schema") != DECALS_SCHEMA:
            continue
        for row in doc.get("assets") or []:
            image = manifest.parent / str(row.get("file") or "")
            if image.is_file():
                found.append((image, row))
    return sorted(found, key=lambda item: str(item[1]["id"]))


__all__ = ["DECALS_SCHEMA", "MAX_PX", "WIDTH_M", "decal_rows", "read_decal_catalogues"]
