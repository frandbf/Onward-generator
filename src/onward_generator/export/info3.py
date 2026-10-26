"""Rewrite the local listing fields of a Launcher ``.info3`` after it is written.

The Quest list shows whatever ``MapName`` is in this sidecar. On the local
slot, Hunt appears when that name is ``Unity Export``, so this pass always
writes that name. The package title stays in the description and in
``config/export.json``. ``ID`` and ``BundleHash`` stay as the build wrote
them. ``EnvironmentType`` is updated when the layout names one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from onward_generator.config.loader import loads_strict


class Info3Error(ValueError):
    """The .info3 file is missing, malformed, or would lose its bundle identity."""


ENVIRONMENT_CODE = {"Desert": 0, "Woodland": 1, "Urban": 2, "Snow": 3}
# The Quest local slot lists Hunt when the card name is this string.
LOCAL_CARD_NAME = "Unity Export"
# Onward 2.0.4 solo menu listed Hunt only when the local card advertised this
# short set. A longer list (Assault, Escort, Evac, Retrieval) left Free Roam alone.
# 4 Free Roam, 10 Hunt, 1 Uplink. Same order as the card that loaded Hunt.
SOLO_GAME_MODES = [4, 10, 1]


def apply_local_listing(info3_path: str | Path, *, title: str, description: str, environment: str | None = None) -> dict[str, Any]:
    path = Path(info3_path).expanduser()
    if not path.is_file():
        raise Info3Error(f"info3 not found: {path}")
    if not title.strip() or len(title) > 80:
        raise Info3Error("title must be 1–80 characters")
    if not description.strip() or len(description) > 600:
        raise Info3Error("description must be 1–600 characters")
    try:
        doc = loads_strict(path.read_text(encoding="utf-8"))
    except (ValueError, json.JSONDecodeError) as exc:
        raise Info3Error(f"{path}: not strict JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise Info3Error(f"{path}: root must be a JSON object")
    item = doc.get("WorkshopItem")
    if not isinstance(item, dict):
        raise Info3Error(f"{path}: missing WorkshopItem object")
    bundle = item.get("BundleHash")
    identity = item.get("ID")
    if not isinstance(bundle, str) or not bundle:
        raise Info3Error(f"{path}: WorkshopItem.BundleHash is missing; refusing to rewrite")
    if identity != "unity-export.local":
        raise Info3Error(f"{path}: ID is {identity!r}; only the local test slot unity-export.local may be relabelled")
    item["MapName"] = LOCAL_CARD_NAME
    item["Description"] = description.strip()
    item["GameModes"] = list(SOLO_GAME_MODES)
    if environment is not None:
        if environment not in ENVIRONMENT_CODE:
            raise Info3Error(f"environment {environment!r} is not a LevelDescriptor.EMapEnvironment name")
        item["EnvironmentType"] = ENVIRONMENT_CODE[environment]
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return doc
