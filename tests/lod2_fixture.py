"""Synthetic LoD2 block ZIPs for plan D6 tests (CC0-1.0; not Ayuntamiento de Madrid data).

The OBJ files imitate the FME output of the Madrid municipal 3D model: a ``# COORDINATE_SYSTEM`` header whose last
EPSG authority is the projected CRS, absolute EPSG:25830 coordinates with an orthometric height, CRLF line ends,
``usemtl`` groups and ``v//n`` face tokens. Boxes are built in local metres and moved by the frame origin.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from onward_generator.generation.mesh import Mesh, box

HEADER = (
    '# COORDINATE_SYSTEM:  OGC_DEF PROJCS["ETRS89 / UTM zone 30N",GEOGCS["ETRS89",DATUM["European_Terrestrial_Reference_System_1989",'
    'SPHEROID["GRS 1980",6378137,298.257222101,AUTHORITY["EPSG","7019"]],AUTHORITY["EPSG","6258"]],UNIT["degree",0.0174532925199433,'
    'AUTHORITY["EPSG","9122"]],AUTHORITY["EPSG","4258"]],PROJECTION["Transverse_Mercator"],UNIT["metre",1,AUTHORITY["EPSG","9001"]],'
    'AUTHORITY["EPSG","{epsg}"]]'
)
GROUND_Z = 650.0
"""The orthometric height of the synthetic street."""
ATTRIBUTION = "Origen de los datos: Ayuntamiento de Madrid (synthetic test fixture)"


def box_group(x0: float, y0: float, x1: float, y1: float, top: float, *, base: float = 0.0) -> Mesh:
    """A closed box in local metres, ``base`` and ``top`` above the synthetic street, without its bottom face like LoD2."""
    mesh = box(x0, x1, y0, y1, GROUND_Z + base, GROUND_Z + top, omit=("-z",))
    return mesh


def obj_text(groups: Sequence[tuple[str, Mesh]], origin: Sequence[float], *, epsg: int | None = 25830) -> str:
    """One OBJ: every group moved by ``origin`` (the frame's ``origin_projected``), its own vertices, then its faces."""
    lines = ["# Created with a synthetic fixture", "mtllib materials.mtl"]
    if epsg is not None:
        lines.append(HEADER.replace("{epsg}", str(epsg)))
    offset = 0
    for name, mesh in groups:
        lines.append(f"# Number of Elements in set       : {mesh.triangle_count}")
        lines.append(f"usemtl {name}")
        for x, y, z in mesh.vertices:
            lines.append(f"v {x + origin[0]:.6f} {y + origin[1]:.6f} {z:.6f}")
        lines.append("vn 0 0 1")
        lines.extend(f"f {a + offset + 1}//1 {b + offset + 1}//1 {c + offset + 1}//1 " for a, b, c in mesh.triangles)
        offset += mesh.vertex_count
    return "\r\n".join(lines) + "\r\n"


def obj_zip(text: str, *, name: str = "M-000001.obj", extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 10, 29, 23, 9, 0)), text)
        archive.writestr(zipfile.ZipInfo("materials.mtl", date_time=(2026, 10, 29, 23, 9, 0)), "newmtl a\nKd 0 0 0\n")
        for member, body in (extra or {}).items():
            archive.writestr(member, body)
    return buffer.getvalue()


def write_index(folder: Path, zips: dict[str, bytes], **changes: Any) -> Path:
    """``lod2_index.json`` and its ZIPs in ``folder``, as ``source madrid-lod2`` writes them."""
    folder.mkdir(parents=True, exist_ok=True)
    entries = []
    for code, body in sorted(zips.items()):
        (folder / f"{code}.zip").write_bytes(body)
        entries.append({"cod_manzan": code, "path": f"{code}.zip", "sha256": hashlib.sha256(body).hexdigest(),
                        "size_bytes": len(body), "url": f"https://geoportal.madrid.es/fsdescargas/x/{code}.zip"})
    document = {"schema": "onward_generator.lod2_landmarks", "schema_version": "1.0.0", "source": "es.madrid_lod2",
                "provider": "Synthetic LoD2 provider", "license": "CC-BY-4.0", "attribution": ATTRIBUTION, "data_date": "2026-07-23",
                "crs": "EPSG:25830", "scene": {"path": "geo_scene.json", "sha256": "0" * 64}, "candidates": [], "blocks": entries} | changes
    path = folder / "lod2_index.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def frame_origin(scene: Path) -> tuple[float, float]:
    east, north = json.loads(scene.read_bytes())["frame"]["origin_projected"]
    return float(east), float(north)


def triangles_of(mesh: Mesh) -> np.ndarray:
    return mesh.vertices[mesh.triangles]
