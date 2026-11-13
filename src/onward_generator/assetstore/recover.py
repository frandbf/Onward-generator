"""Albedos a model's own materials lost, read back from the prefab that dresses it (ADR-035 amendment).

A package may dress a raw model file in a prefab variant that swaps its embedded materials for the package's ``.mat``
files. When that variant is refused (Persiang Carpets: its fringe material is transparent), the exporter still writes
the raw model, whose embedded materials carry a grey colour and no texture: the 46 Persian carpets became one grey.
This reads the variant's material overrides (``m_Materials.Array.data[N]`` on the model's GUID), the albedo of each
``.mat`` (the exporter's property names, in order) and its image straight from the ``.unitypackage``, and gives the
model's untextured materials those images. A transparent ``.mat`` (URP ``_Surface: 1``) is left as it was.
Nothing here runs package code: a prefab and a material are YAML, a texture is an image.
"""

from __future__ import annotations

import io
import re
import tarfile
from functools import lru_cache
from pathlib import Path

from PIL import Image

from onward_generator.assetstore.packages import MAX_MEMBER_BYTES

# The exporter's albedo property names (tools/unity/asset_store_export AlbedoNames), in its order of preference.
ALBEDO_NAMES = ("_BaseMap", "_BaseColorMap", "_MainTex", "_Albedo", "_AlbedoMap", "_Diffuse", "_MainTexture", "_BaseTexture",
                "_ColorMap", "_Texture", "_BaseColorTexture")
MAX_PX = 512
_OVERRIDE = re.compile(r"guid: (?P<model>[0-9a-f]{32}),\s*type: 3\}\s*propertyPath: m_Materials\.Array\.data\[(?P<index>\d+)\]"
                       r"\s*value:[^\n]*\n\s*objectReference: \{fileID: 2100000, guid: (?P<material>[0-9a-f]{32})")
_TEXTURE = re.compile(r"- (?P<name>_\w+):\s*m_Texture: \{fileID: \d+, guid: (?P<guid>[0-9a-f]{32})")


class _Package:
    """The members of a ``.unitypackage`` by GUID, read on demand."""

    def __init__(self, archive: tarfile.TarFile) -> None:
        self.archive = archive
        self.members = {m.name.removeprefix("./"): m for m in archive.getmembers() if m.isfile()}
        self.guid_of = {self.text(f"{guid}/pathname").splitlines()[0].strip(): guid
                        for guid in {name.split("/")[0] for name in self.members if name.endswith("/pathname")}
                        if self.text(f"{guid}/pathname").strip()}

    def data(self, name: str) -> bytes:
        member = self.members.get(name)
        if member is None or member.size > MAX_MEMBER_BYTES:
            return b""
        handle = self.archive.extractfile(member)
        return handle.read() if handle is not None else b""

    def text(self, name: str) -> str:
        return self.data(name).decode("utf-8", "replace")


def material_overrides(package: _Package, model_path: str) -> dict[int, str]:
    """``{submesh index: .mat GUID}`` a prefab of the package sets on the model at ``model_path``, or ``{}``."""
    model = package.guid_of.get(model_path)
    if model is None:
        return {}
    for path, guid in sorted(package.guid_of.items()):
        if not path.endswith(".prefab"):
            continue
        found = {int(m["index"]): m["material"] for m in _OVERRIDE.finditer(package.text(f"{guid}/asset")) if m["model"] == model}
        if found:
            return found
    return {}


def albedo_png(package: _Package, material: str) -> bytes | None:
    """The albedo image of the ``.mat`` with GUID ``material`` as an RGB PNG of at most ``MAX_PX``, or ``None``."""
    text = package.text(f"{material}/asset")
    if not text or re.search(r"- _Surface: 1\b", text):
        return None
    textures = {m["name"]: m["guid"] for m in _TEXTURE.finditer(text)}
    for name in ALBEDO_NAMES:
        guid = textures.get(name)
        if guid is None:
            continue
        try:
            with Image.open(io.BytesIO(package.data(f"{guid}/asset"))) as picture:
                image = picture.convert("RGB")
        except (OSError, ValueError):
            continue
        image.thumbnail((MAX_PX, MAX_PX), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
    return None


@lru_cache(maxsize=2)
def _open(path: str) -> _Package | None:
    """The package at ``path``, kept open: a catalogue reads it once per model (49 carpets of a 200 MB package)."""
    try:
        return _Package(tarfile.open(path, "r:gz"))
    except (OSError, tarfile.TarError):
        return None


def recover_albedos(package_path: Path, model_path: str, mtl_text: str, folder: Path, stem: str) -> tuple[str, int]:
    """``mtl_text`` with a ``map_Kd`` for each untextured material the dressing prefab gives an albedo, written to
    ``folder``; and how many were recovered. An unreadable package changes nothing."""
    blocks = re.split(r"(?m)^(?=newmtl )", mtl_text)
    if not any(block.startswith("newmtl ") and "map_Kd" not in block for block in blocks):  # pragma: no mutate: only spares opening the package; a textured material gets no map below either way
        return mtl_text, 0
    package = _open(str(package_path))
    if package is None:
        return mtl_text, 0
    overrides = material_overrides(package, model_path)
    recovered = 0
    index = -1
    out = []
    for block in blocks:
        if block.startswith("newmtl "):
            index += 1
            png = albedo_png(package, overrides[index]) if "map_Kd" not in block and index in overrides else None
            if png is not None:
                file = f"{stem}_recovered_{index}.png"
                (folder / file).write_bytes(png)
                block = (block if block.endswith("\n") else block + "\n") + f"map_Kd {file}\n"
                recovered += 1
        out.append(block)
    return "".join(out), recovered


__all__ = ["ALBEDO_NAMES", "MAX_PX", "albedo_png", "material_overrides", "recover_albedos"]
