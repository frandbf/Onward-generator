"""Tileable texture catalogues from Asset Store texture packages (ADR-035 amendment of 13 November 2026).

A texture package is a set of PBR maps per material (albedo, normal, height, AO...), not models, so it needs no Unity
export: the albedo and the normal map of each set named in ``config/dress/asset_store_textures.json`` are read
straight from the ``.unitypackage``, scaled to at most ``MAX_PX``, and written with a manifest to the gitignored
``assets/asset_store_<package>/``. A DirectX normal map has its green channel flipped, so every normal map is OpenGL,
as glTF and the wide library's are. The manifest has the imported-library shape (``textures``), so the texture plan
draws these rows with the ``wide_library`` switch while local catalogues are on (``assetstore.local``), and each
row carries its family, its calm contrast and its normal map. The images stay on this machine (Unity Asset Store EULA).
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import re
import tarfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from onward_generator.assetstore.packages import LICENCE_NAME, MAX_MEMBER_BYTES, Package
from onward_generator.io import atomic_write_bytes, atomic_write_json

TABLE_PATH = Path(__file__).resolve().parents[3] / "config" / "dress" / "asset_store_textures.json"
LIBRARY_SCHEMA = "onward_generator.imported_library"
MAX_PX = 1024
IMAGE_SUFFIXES = (".png", ".tga", ".tif", ".tiff", ".jpg", ".jpeg")
ALBEDO = re.compile(r"(?:_diffuse|_base_?colou?r|_albedo|_color)$", re.IGNORECASE)
NORMAL = re.compile(r"(?:_normal_directx|_normal_opengl|_normal|_n)$", re.IGNORECASE)


def read_table(path: Path = TABLE_PATH) -> dict[str, dict[str, dict[str, str]]]:
    """``{package id: {set stem: {"family", "note"}}}``."""
    return dict(json.loads(path.read_text(encoding="utf-8"))["packages"])


def texture_sets(archive: tarfile.TarFile) -> dict[str, dict[str, tuple[tarfile.TarInfo, bool]]]:
    """``{set stem: {"albedo": (member, False), "normal": (member, directx)}}`` of the images in ``archive``. A set is
    the image's file name without its map suffix (``wall05_Diffuse.tga`` -> ``wall05``)."""
    members = {m.name.removeprefix("./"): m for m in archive.getmembers() if m.isfile()}
    sets: dict[str, dict[str, tuple[tarfile.TarInfo, bool]]] = {}
    for name in sorted(members):
        if not name.endswith("/pathname"):
            continue
        handle = archive.extractfile(members[name])
        path = handle.read().decode("utf-8", "replace").splitlines()[0].strip() if handle is not None else ""  # pragma: no mutate: a regular member always has a handle
        asset = members.get(name.removesuffix("pathname") + "asset")
        stem, ext = posixpath.splitext(posixpath.basename(path))
        if asset is None or ext.lower() not in IMAGE_SUFFIXES or asset.size > MAX_MEMBER_BYTES:
            continue
        if (match := NORMAL.search(stem)) is not None:
            sets.setdefault(stem[:match.start()], {})["normal"] = (asset, "directx" in match.group(0).lower())
        elif (match := ALBEDO.search(stem)) is not None:
            sets.setdefault(stem[:match.start()], {})["albedo"] = (asset, False)
    return sets


def _image(archive: tarfile.TarFile, member: tarfile.TarInfo) -> Image.Image:
    handle = archive.extractfile(member)
    if handle is None:  # pragma: no mutate: a regular member always has a handle
        raise ValueError(f"{member.name}: no data")
    with Image.open(io.BytesIO(handle.read())) as picture:
        image = picture.convert("RGB")
    image.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
    return image


def _jpeg(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=92)
    return out.getvalue()


def opengl_normal(image: Image.Image, directx: bool) -> Image.Image:
    """``image`` as an OpenGL normal map: a DirectX map has its green channel (Y) flipped."""
    if not directx:
        return image
    pixels = np.asarray(image).copy()
    pixels[..., 1] = 255 - pixels[..., 1]
    return Image.fromarray(pixels)


def texture_rows(package: Package, assets: Path, chosen: Mapping[str, Mapping[str, str]]) -> list[dict[str, Any]]:
    """Write the albedo and normal map of each set ``chosen`` names under ``assets/<catalogue>/`` and return their
    library rows (none for a set the package lacks or whose albedo cannot be read)."""
    target = assets / package.catalogue
    rows: list[dict[str, Any]] = []
    with tarfile.open(package.path) as archive:
        sets = texture_sets(archive)
        for stem, spec in sorted(chosen.items()):
            maps = sets.get(stem, {})
            if "albedo" not in maps:
                continue
            try:
                albedo = _image(archive, maps["albedo"][0])
                normal = opengl_normal(_image(archive, maps["normal"][0]), maps["normal"][1]) if "normal" in maps else None
            except (OSError, ValueError):
                continue
            target.mkdir(parents=True, exist_ok=True)
            file = re.sub(r"[^A-Za-z0-9_]+", "_", stem)
            data = _jpeg(albedo)
            atomic_write_bytes(target / f"{file}_albedo.jpg", data)
            row: dict[str, Any] = {
                "id": f"as_{package.id[:20]}_{file}"[:80], "name": stem, "family": spec["family"], "provider": "asset_store",
                "license": "LicenseRef-Unity-Asset-Store-EULA", "license_url": "https://unity.com/legal/as-terms",
                "credit": f"{stem} from {package.name} by {package.publisher}, Unity Asset Store EULA; local copy, never committed.",
                "source_url": "", "surface": "ground" if spec["family"] in {"paving", "concrete", "asphalt"} else "wall",
                "bound": False, "path": (target / f"{file}_albedo.jpg").relative_to(assets.parent).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "note": f"Albedo {albedo.width} px. {spec['note']}",
            }
            if normal is not None:
                normal_data = _jpeg(normal)
                atomic_write_bytes(target / f"{file}_nor_gl.jpg", normal_data)
                row["normal"] = {"map": "nor_gl", "path": (target / f"{file}_nor_gl.jpg").relative_to(assets.parent).as_posix(),
                                 "sha256": hashlib.sha256(normal_data).hexdigest(), "bytes": len(normal_data)}
            rows.append(row)
    if rows:
        atomic_write_json(target / "manifest.json", {
            "schema": LIBRARY_SCHEMA, "schema_version": "0.1.0",
            "source": f"{LICENCE_NAME} (Extension Asset), local only: {package.publisher} / {package.name}. Converted on this "
                      "machine; never committed or redistributed outside a built map.",
            "package": {"publisher": package.publisher, "category": package.category, "name": package.name}, "textures": rows,
        })
        (target / "License.txt").write_text(
            f"{package.name} by {package.publisher}, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
            "Local copy for this user's maps only. Do not commit, share or publish these files.\n", encoding="utf-8")  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on
    return rows


def local_texture_rows(assets: Path) -> list[dict[str, Any]]:
    """Every texture row of the local catalogues under ``assets`` (``asset_store_*`` with an imported-library manifest)."""
    found: list[dict[str, Any]] = []
    for manifest in sorted(assets.glob("asset_store_*/manifest.json")):
        try:
            doc = json.loads(manifest.read_text(encoding="utf-8"))  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("schema") == LIBRARY_SCHEMA:
            found.extend(row for row in doc.get("textures") or [] if isinstance(row, dict) and row.get("id"))
    return found


__all__ = ["LIBRARY_SCHEMA", "MAX_PX", "TABLE_PATH", "local_texture_rows", "opengl_normal", "read_table", "texture_rows", "texture_sets"]
