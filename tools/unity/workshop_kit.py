#!/usr/bin/env python3
"""Workshop kit: one folder per map with everything its Onward Workshop publication needs (19 November 2026).

    python3 tools/unity/workshop_kit.py maps/<id>/data/processed/export/pkg_NN [...]

For each package built in Unity with the final bake, this writes ``<out>/<map id>/``:

- ``OnwardProject/``: a reduced copy of the Unity project (``Assets`` with only this map's scene folder under
  ``Assets/OnwardAdapter``, ``Packages``, ``ProjectSettings``; no ``Library``). Its ``SharedLevelSettings.asset`` carries
  the Workshop title, description, display version 1.0 and the scene for PC and Quest, which the SDK's Custom Map
  Dashboard reads.
- ``workshop/``: ``cover_512x256.png``, ``publish.json`` (every Dashboard field) and ``PUBLICAR.md`` (the same fields,
  the data credits and the Unity Asset Store packages to check).
- ``LEEME.txt``: what to install on Windows and how to publish.

and ``<out>/<map id>.zip`` with that folder. A newer kit replaces the map's older one, whatever its folder name, so
``<out>`` holds one kit per map: the latest. The title and description are the map's ``config/export.json``
``map.title`` and ``map.description``, not the package's description, which carries every credit and runs past the
Workshop's 500 characters. Runs on the host with the standard library only: it reads the Unity project.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import struct
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

PROJECT = Path.home() / "Unity" / "OnwardPhase0Project"
OUT = Path.home() / "Unity" / "WorkshopExports"
DESCRIPTION_MAX = 500
"""The Workshop's description limit (Custom Content Guide)."""
DESCRIPTION_IDEAL = 300
"""Past this a description is accepted with a warning: the guide asks for about 250 characters."""
TITLE_MAX = 24
THUMB_SIZE = (512, 256)
DISPLAY_VERSION = "1.0"
ENVIRONMENTS = ["Desert", "Woodland", "Urban", "Snow", "Wilderness", "Industrial", "Jungle", "Mountain"]
"""``LevelDescriptor.EMapEnvironment`` of the Onward SDK v1.15, by value."""
SIZES = ["Small", "Medium", "Large"]
TIMES = ["Day", "Night", "Twilight"]
ASSET_STORE = re.compile(rb"asset_store_[a-z0-9_]+")
COPIED = ("Assets", "Packages", "ProjectSettings")


class KitError(ValueError):
    """A package that cannot be published as it is."""


@dataclass(frozen=True)
class Kit:
    """What one map's kit says, read from its package and its map."""

    map_id: str
    pkg: str
    package_id: str
    scene: str
    title: str
    description: str
    environment: str
    size: str
    time_of_day: str
    game_modes: list[str]
    credits: list[str]
    asset_store: list[str]
    triangles: int
    lightmaps: int
    texels_per_m: float
    warnings: list[str]


def png_size(data: bytes) -> tuple[int, int]:
    """Width and height of a PNG from its header."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise KitError("the thumbnail is not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def mode_names(ids: list[int], modes: dict[str, int]) -> list[str]:
    """``[4, 10, 1]`` → ``["Free Roam", "Hunt", "Uplink"]`` from the layout's name → id table."""
    names = {number: name.replace("_", " ").title() for name, number in modes.items()}
    return [names.get(number, f"mode {number}") for number in ids]


def credit_lines(attribution: list[dict[str, str]]) -> list[str]:
    """Each distinct data credit once, as ``text (licence)``."""
    lines: list[str] = []
    for item in attribution:
        line = f"{item['text']} ({item['license']})"
        if line not in lines:
            lines.append(line)
    return lines


def yaml_string(value: str) -> str:
    """A YAML double-quoted scalar: a JSON string is one, and Unity reads its ``\\u`` escapes."""
    return json.dumps(value)


def set_shared_settings(text: str, title: str, description: str) -> str:
    """``SharedLevelSettings.asset`` with the Workshop's title, description and display version, and the Quest scene
    as the PC scene too (the Dashboard asks for both)."""
    quest = re.search(r"^  Scene_Quest: (.*)$", text, re.MULTILINE)
    if quest is None or "guid" not in quest.group(1):
        raise KitError("SharedLevelSettings.asset has no Quest scene")
    replacements = {
        "Title": yaml_string(title),
        "Description": yaml_string(description),
        "DisplayVersion": DISPLAY_VERSION,
        "Scene_PC": quest.group(1),
    }
    for key, value in replacements.items():
        text, count = re.subn(rf"^  {key}:.*$", lambda _m, k=key, v=value: f"  {k}: {v}", text, count=1,
                              flags=re.MULTILINE)
        if count != 1:
            raise KitError(f"SharedLevelSettings.asset has no {key}")
    return text


def read_kit(package: Path, project: Path) -> Kit:
    """Check the package and gather what its kit says."""
    manifest_path = package / "onward_export.json"
    report_path = package / "build" / "scene_report.json"
    info3_path = package / "build" / "launcher" / "unity-export.local.info3"
    for path in (manifest_path, report_path, info3_path, package / "thumbnail.png"):
        if not path.is_file():
            raise KitError(f"{path} is missing: build the package in Unity first")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    item = json.loads(info3_path.read_text(encoding="utf-8-sig"))["WorkshopItem"]
    layout = json.loads((package / "layout.json").read_text(encoding="utf-8"))
    if report["manifest_sha256"] != hashlib.sha256(manifest_path.read_bytes()).hexdigest():
        raise KitError("the Unity build is not of this package: build it again")
    profile = report["lightmap_bake"]["profile"]
    if profile != "final":
        raise KitError(f"the light bake is {profile!r}: build it with ONWARD_BAKE=final")
    scene = report["scene"]
    if not (project / scene).is_file():
        raise KitError(f"{project / scene} is missing: build the package in Unity again")
    if png_size((package / "thumbnail.png").read_bytes()) != THUMB_SIZE:
        raise KitError(f"the thumbnail is not {THUMB_SIZE[0]}x{THUMB_SIZE[1]}")

    map_id = manifest["map"]["id"]
    card = json.loads((package.parents[3] / "config" / "export.json").read_text(encoding="utf-8"))["map"]
    title, description = card["title"].strip(), card["description"].strip()
    warnings: list[str] = []
    if not 1 <= len(title) <= TITLE_MAX:
        raise KitError(f"the title has {len(title)} characters: 1 to {TITLE_MAX}")
    if len(title) > 18:
        warnings.append(f"the title has {len(title)} characters; the guide suggests 6 to 18")
    if not 1 <= len(description) <= DESCRIPTION_MAX:
        raise KitError(f"the description has {len(description)} characters: 1 to {DESCRIPTION_MAX}")
    if len(description) > DESCRIPTION_IDEAL:
        warnings.append(f"the description has {len(description)} characters; the guide suggests about 250")
    if "OpenStreetMap" not in description:
        warnings.append("the description does not credit OpenStreetMap")
    found = ASSET_STORE.findall((package / "scene_visual.glb").read_bytes() + manifest_path.read_bytes())
    stores = sorted({name.decode() for name in found})
    return Kit(
        map_id=map_id, pkg=package.name, package_id=manifest["package_id"], scene=scene, title=title,
        description=description, environment=ENVIRONMENTS[item["EnvironmentType"]], size=SIZES[item["MapSize"]],
        time_of_day=TIMES[item["TimeOfDay"]], game_modes=mode_names(item["GameModes"], layout["modes"]),
        credits=credit_lines(manifest["attribution"]), asset_store=stores,
        triangles=int(report["triangles"]), lightmaps=int(report["lightmaps"]),
        texels_per_m=float(report["options"]["lightmap_texels_per_m"]), warnings=warnings,
    )


def publish_json(kit: Kit) -> dict[str, object]:
    """The Dashboard's fields and where they come from."""
    return {
        "title": kit.title, "description": kit.description, "map_environment": kit.environment,
        "map_size": kit.size, "time_of_day": kit.time_of_day, "thumbnail": "cover_512x256.png",
        "display_version": DISPLAY_VERSION, "game_modes": kit.game_modes, "scene": kit.scene,
        "package": {"id": kit.package_id, "map": kit.map_id, "pkg": kit.pkg, "bake": "final",
                    "lightmap_texels_per_m": kit.texels_per_m, "lightmaps": kit.lightmaps,
                    "unity_triangles": kit.triangles},
    }


def publicar_md(kit: Kit) -> str:
    """The publication sheet, in Spanish like the user's guides."""
    folder = kit.scene.rsplit("/", 1)[0]
    rows = [
        ("Title", f"`{kit.title}` ({len(kit.title)} caracteres; ideal 6–18, máx. {TITLE_MAX})"),
        ("Description", f"ver abajo ({len(kit.description)} caracteres; ideal ~250, máx. {DESCRIPTION_MAX})"),
        ("Map Environment", kit.environment), ("Map Size", kit.size), ("Time of Day", kit.time_of_day),
        ("Thumbnail", "ya asignada en la escena (= `cover_512x256.png`, 512×256)"),
        ("Display Version", f"`{DISPLAY_VERSION}`"), ("Scene PC / Scene Quest", "la misma escena para los dos"),
        ("Game modes", ", ".join(kit.game_modes) + " (salen de la escena)"),
        ("Published Item Id", "vacío: lo rellena el Dashboard al publicar. No lo borres: sirve para actualizar"),
    ]
    lines = [f"# {kit.title} — ficha de publicación (Onward Custom Map Dashboard)", "",
             f"Escena: `{kit.scene}`", f"Ajustes: `{folder}/SharedLevelSettings.asset` (ya rellenados en esta copia; "
             "revísalos en el Dashboard antes de pulsar Publish).", "", "| Campo | Valor |", "|---|---|"]
    lines += [f"| {field} | {value} |" for field, value in rows]
    lines += ["", "## Description", "", kit.description, "", "## Créditos de datos del paquete", ""]
    lines += [f"- {line}" for line in kit.credits]
    lines += ["", "## Paquetes de la Unity Asset Store usados", ""]
    if kit.asset_store:
        lines += ["Confirma que la licencia de cada uno permite usarlo en un mapa publicado:", ""]
        lines += [f"- `{name}`" for name in kit.asset_store]
    else:
        lines.append("Ninguno.")
    lines += ["", "## Datos técnicos", "",
              f"- Paquete `{kit.pkg}` (`{kit.package_id}`), horneado final, {kit.texels_per_m:g} téxeles/m, "
              f"{kit.lightmaps} mapas de luz.",
              f"- {kit.triangles:,} triángulos en Unity (guía Quest: ~250 000).".replace(",", " ")]
    if kit.warnings:
        lines += ["", "## Avisos", ""] + [f"- {w}" for w in kit.warnings]
    return "\n".join(lines) + "\n"


def leeme(kit: Kit, today: date) -> str:
    """How to publish, for someone at a Windows PC."""
    short = re.sub(r"[^A-Za-z0-9]", "", kit.title) or kit.map_id
    return f"""{kit.title.upper()} · paquete {kit.pkg} ({kit.package_id}) · {today:%d-%m-%Y}
Kit para el Workshop de Onward: horneado final a {kit.texels_per_m:g} texeles/m. Pruebalo en el Quest antes de publicar.

CONTENIDO
  OnwardProject/   Proyecto de Unity reducido: SDK de Onward v1.15 + solo la escena de este mapa.
                   (Sin Library/: Unity la regenera al abrirlo; la primera apertura tarda unos minutos.)
  workshop/        PUBLICAR.md (todos los campos del Dashboard, creditos y paquetes de la Asset Store),
                   publish.json y la caratula cover_512x256.png.

QUE INSTALAR EN WINDOWS
  1. App de Meta Quest Link / Oculus para PC, con la cuenta que tiene Onward.
  2. Unity Hub: https://unity.com/download
  3. Unity 2021.3.23f1 con "Android Build Support" (+ OpenJDK y Android SDK & NDK Tools).

COMO PUBLICAR
  1. Copia OnwardProject a una ruta corta, p. ej. C:\\Onward\\{short} (Windows limita las rutas largas).
  2. Unity Hub > Add project from disk > elige esa carpeta > abrela con 2021.3.23f1.
  3. En el Explorador de Windows, doble clic en
     OnwardProject\\Assets\\OnwardCustomContent\\RegistryKey\\OnwardUGCRegConfig.reg  y acepta.
  4. Reinicia el PC.
  5. Abre la app de Meta Quest Link (debe quedar abierta) y despues el proyecto en Unity.
  6. Abre la escena {kit.scene}
  7. Menu "Onward Custom Map Dashboard": acepta los terminos y revisa titulo, descripcion, miniatura y version
     (ya vienen rellenados: comparalos con workshop/PUBLICAR.md).
  8. Pulsa Publish. La aprobacion tarda hasta 2 semanas. Para actualizarlo, publica desde este mismo proyecto.

NOTAS
  - El SDK lleva un parche del generador en ContentBuilder.cs (copia el mapa de navegacion de Hunt para que
    los bots funcionen en Quest). No reimportes el .unitypackage del SDK o se pierde.
  - Guia oficial: https://onwardthegame.com/doc/CustomContentGuide.pdf
"""


def _only_scene(scene_folder: str):
    """``copytree`` ignore: under ``Assets/OnwardAdapter`` keep only this scene's folder and its ``.meta``."""
    def ignore(directory: str, names: list[str]) -> set[str]:
        skipped = {n for n in names if n == ".DS_Store"}
        if Path(directory).name == "OnwardAdapter" and Path(directory).parent.name == "Assets":
            skipped |= {n for n in names if n not in (scene_folder, f"{scene_folder}.meta")}
        return skipped
    return ignore


def owner(kit_dir: Path) -> str | None:
    """The map id of an existing kit folder, from its ``workshop/publish.json``."""
    try:
        return str(json.loads((kit_dir / "workshop" / "publish.json").read_text(encoding="utf-8"))["package"]["map"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_kit(package: Path, project: Path, out: Path, today: date) -> tuple[Kit, Path]:
    """Write the map's kit and its zip, then remove the map's older kits."""
    kit = read_kit(package, project)
    out.mkdir(parents=True, exist_ok=True)
    staging = out / f".staging-{kit.map_id}"
    shutil.rmtree(staging, ignore_errors=True)
    scene_folder = kit.scene.split("/")[2]
    for name in COPIED:
        shutil.copytree(project / name, staging / "OnwardProject" / name, symlinks=True,
                        ignore=_only_scene(scene_folder) if name == "Assets" else None)
    shared = staging / "OnwardProject" / kit.scene.rsplit("/", 1)[0] / "SharedLevelSettings.asset"
    shared.write_text(set_shared_settings(shared.read_text(encoding="utf-8"), kit.title, kit.description),
                      encoding="utf-8")
    workshop = staging / "workshop"
    workshop.mkdir()
    shutil.copyfile(package / "thumbnail.png", workshop / "cover_512x256.png")
    (workshop / "publish.json").write_text(json.dumps(publish_json(kit), ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    (workshop / "PUBLICAR.md").write_text(publicar_md(kit), encoding="utf-8")
    (staging / "LEEME.txt").write_text(leeme(kit, today), encoding="utf-8")

    for old in out.iterdir():
        if old.is_dir() and old != staging and owner(old) == kit.map_id:
            shutil.rmtree(old)
            Path(f"{old}.zip").unlink(missing_ok=True)
    final = out / kit.map_id
    staging.rename(final)
    archive = Path(f"{final}.zip")
    archive.unlink(missing_ok=True)
    shutil.make_archive(str(final), "zip", root_dir=out, base_dir=kit.map_id)
    return kit, final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write the Onward Workshop kit of each package, one folder per map.")
    parser.add_argument("packages", nargs="+", type=Path, metavar="PACKAGE",
                        help="an export package built in Unity with the final bake (maps/<id>/data/processed/export/pkg_NN)")
    parser.add_argument("--project", type=Path, default=PROJECT, help=f"the Unity project (default {PROJECT})")
    parser.add_argument("--out", type=Path, default=OUT, help=f"where the kits go (default {OUT})")
    args = parser.parse_args(argv)
    failed = 0
    for package in args.packages:
        try:
            kit, folder = write_kit(package.resolve(), args.project, args.out, date.today())
        except (KitError, OSError, KeyError, ValueError) as error:
            print(f"{package}: {error}", file=sys.stderr)
            failed += 1
            continue
        print(f"{kit.map_id}  {kit.pkg}  → {folder}  ({folder.name}.zip)")
        for warning in kit.warnings:
            print(f"  ! {warning}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
