"""Particle texture catalogues from Asset Store particle packages (ADR-035 amendment of 18 November 2026).

A particle package (Unity's *Particle Pack Starter Assets*) ships prefabs whose materials use Unity's built-in particle
shaders, which a custom Onward map cannot carry: the adapter draws its effects with the SDK's ``PP_Particle_*``
shaders. So only the textures are taken. ``config/dress/asset_store_particles.json`` names, per package, the image of
each role (``flame``, ``smoke``, ``dust_mote``...) by its exact path in the package, the flipbook grid the pack's own
prefab plays, the largest side of the copy and whether its alpha is kept or made from its brightness (an image drawn on
black for additive blending). Each image is read straight from the ``.unitypackage`` (no editor, no
code from the package), kept with its alpha, and written as a PNG with a manifest to the gitignored
``assets/asset_store_<package>/``. The images stay on this machine (Unity Asset Store EULA).
"""

from __future__ import annotations

import hashlib
import io
import json
import posixpath
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from PIL import Image

from onward_generator.assetstore.local import LICENCE_SPDX, local_manifests
from onward_generator.assetstore.packages import LICENCE_NAME, MAX_MEMBER_BYTES, Package
from onward_generator.io import atomic_write_bytes, atomic_write_json

REPO_ROOT = Path(__file__).resolve().parents[3]
TABLE_PATH = REPO_ROOT / "config" / "dress" / "asset_store_particles.json"
SCHEMA_PATH = REPO_ROOT / "config" / "dress" / "asset_store_particles.schema.json"
PARTICLES_SCHEMA = "onward_generator.particle_textures"
ROLES = ("flame", "large_flame", "smoke", "dust_mote", "sand_puff", "spark")
ALPHA_MODES = ("keep", "luminance")


class ParticleTableError(ValueError):
    """``config/dress/asset_store_particles.json`` does not match its schema."""


@dataclass(frozen=True, slots=True)
class ParticleSpec:
    path: str
    tiles: tuple[int, int]
    max_px: int
    alpha: str
    prefab: str
    note: str


def parse_table(doc: Any, schema: Mapping[str, Any], where: str) -> dict[str, dict[str, ParticleSpec]]:
    """``{package id: {role: ParticleSpec}}`` of a table document, or ``ParticleTableError`` naming the first problem."""
    first = best_match(Draft202012Validator(schema).iter_errors(doc))
    if first is not None:
        path = "/".join(str(part) for part in first.absolute_path)
        raise ParticleTableError(f"{where}: /{path}: {first.message}")
    return {package: {role: ParticleSpec(spec["path"], (spec["tiles"][0], spec["tiles"][1]), spec["max_px"],
                                         spec["alpha"], spec["prefab"], spec["note"])
                      for role, spec in roles.items()}
            for package, roles in doc["packages"].items()}


def read_table(path: Path = TABLE_PATH, schema_path: Path = SCHEMA_PATH) -> dict[str, dict[str, ParticleSpec]]:
    schema = json.loads(schema_path.read_text(encoding="utf-8"))  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on
    return parse_table(json.loads(path.read_text(encoding="utf-8")), schema, str(path))  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on


def _members_by_path(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    """``{path in the package: its asset member}`` for every asset no larger than ``MAX_MEMBER_BYTES``."""
    members = {m.name: m for m in archive.getmembers() if m.isfile()}
    found: dict[str, tarfile.TarInfo] = {}
    for name, member in sorted(members.items()):
        if not name.endswith("/pathname"):
            continue
        handle = archive.extractfile(member)
        lines = handle.read().decode("utf-8", "replace").splitlines() if handle is not None else []  # pragma: no mutate: a regular member always has a handle
        asset = members.get(name.removesuffix("pathname") + "asset")
        if lines and asset is not None and asset.size <= MAX_MEMBER_BYTES:
            found[lines[0].strip()] = asset
    return found


def luminance_alpha(image: Image.Image) -> Image.Image:
    """An image drawn on black for additive blending, made fit for alpha blending: its alpha is its brightest channel
    and its colour is divided by that alpha, so a pixel drawn over a background looks as it did added to black."""
    rgb = np.asarray(image.convert("RGB"))
    alpha = rgb.max(axis=2)
    colour = rgb * 255.0 / np.maximum(alpha, 1)[..., None]  # a black pixel stays black: 0 / 1
    pixels = np.dstack([np.round(colour), alpha]).astype(np.uint8)  # colour <= 255: no channel exceeds the maximum
    return Image.fromarray(pixels)


def _png(archive: tarfile.TarFile, member: tarfile.TarInfo, max_px: int, alpha: str) -> tuple[bytes, int]:
    handle = archive.extractfile(member)
    if handle is None:  # pragma: no mutate: a regular member always has a handle
        raise ValueError(f"{member.name}: no data")  # pragma: no mutate: a regular member always has a handle
    with Image.open(io.BytesIO(handle.read())) as picture:
        image = luminance_alpha(picture) if alpha == "luminance" else picture.convert("RGBA")
    image.thumbnail((max_px, max_px))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue(), max(image.size)


def particle_rows(package: Package, assets: Path, chosen: Mapping[str, ParticleSpec]) -> list[dict[str, Any]]:
    """Write the image of each role ``chosen`` names under ``assets/<catalogue>/`` and return the manifest rows (none
    for a role whose image the package lacks or cannot be read)."""
    target = assets / package.catalogue
    rows: list[dict[str, Any]] = []
    with tarfile.open(package.path) as archive:
        members = _members_by_path(archive)
        for role, spec in sorted(chosen.items()):
            member = members.get(spec.path)
            if member is None:
                continue
            try:
                data, px = _png(archive, member, spec.max_px, spec.alpha)
            except (OSError, ValueError):
                continue
            target.mkdir(parents=True, exist_ok=True)
            file = f"particle_{role}.png"  # a role is one of ROLES: lower case and underscores
            atomic_write_bytes(target / file, data)
            rows.append({
                "role": role, "path": (target / file).relative_to(assets.parent).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "px": px,
                "tiles": list(spec.tiles), "frames": spec.tiles[0] * spec.tiles[1], "alpha": spec.alpha, "prefab": spec.prefab,
                "license": LICENCE_SPDX, "license_url": "https://unity.com/legal/as-terms",
                "credit": f"{posixpath.basename(spec.path)} from {package.name} by {package.publisher}, Unity Asset Store EULA; "
                          "local copy, never committed.",
                "note": spec.note,
            })
    if rows:
        atomic_write_json(target / "manifest.json", {
            "schema": PARTICLES_SCHEMA, "schema_version": "0.1.0",
            "source": f"{LICENCE_NAME} (Extension Asset), local only: {package.publisher} / {package.name}. Converted on this "
                      "machine; never committed or redistributed outside a built map.",
            "package": {"publisher": package.publisher, "category": package.category, "name": package.name},
            "particles": rows,
        })
        (target / "License.txt").write_text(
            f"{package.name} by {package.publisher}, from the Unity Asset Store, under the Standard Unity Asset Store EULA.\n"
            "Local copy for this user's maps only. Do not commit, share or publish these files.\n", encoding="utf-8")  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on
    return rows


def local_particles(assets: Path, env: Mapping[str, str] | None = None) -> dict[str, dict[str, Any]]:
    """``{role: row}`` of the particle textures in the local catalogues under ``assets``, the first catalogue in name
    order winning a role. A row whose file is missing or does not match its SHA-256 is left out. Empty while
    ``ONWARD_LOCAL_CATALOGUES=0``, so a run gives the same package on every machine."""
    found: dict[str, dict[str, Any]] = {}
    for _catalogue, document in local_manifests(assets, env):
        if document.get("schema") != PARTICLES_SCHEMA:
            continue
        for row in document.get("particles") or []:
            if (not isinstance(row, dict) or row.get("role") not in ROLES or row["role"] in found
                    or not isinstance(row.get("path"), str)):
                continue
            file = (assets.parent / row["path"]).resolve()
            if file.is_relative_to(assets.resolve()) and file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest() == row.get("sha256"):
                found[row["role"]] = {**row, "file": file}
    return found


__all__ = ["ALPHA_MODES", "PARTICLES_SCHEMA", "ROLES", "luminance_alpha", "TABLE_PATH", "ParticleSpec", "ParticleTableError", "local_particles",
           "parse_table", "particle_rows", "read_table"]
