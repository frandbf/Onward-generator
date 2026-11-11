"""Which local Asset Store catalogues the generator reads, and the furnishing rows they add (ADR-035).

The catalogues are ``assets/asset_store_<id>/`` folders written by ``asset-store import`` on this machine; git ignores
them. ``ONWARD_LOCAL_CATALOGUES=0`` hides them from the library and from furnishing, so a run (or the test suite)
gives the same output on every machine.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from onward_generator.assetstore.packages import LICENCE_NAME, LOCAL_PREFIX

ENV = "ONWARD_LOCAL_CATALOGUES"
LICENCE_SPDX = "LicenseRef-Unity-Asset-Store-EULA"
ROWS_PER_SLOT = 4
"""At most this many local rows join one set slot, lightest first: every distinct row counts against ``max_assets``."""


def enabled(env: Mapping[str, str] | None = None) -> bool:
    return (os.environ if env is None else env).get(ENV) != "0"


def is_local(name: str) -> bool:
    return name.startswith(LOCAL_PREFIX)


def local_manifests(assets: Path, env: Mapping[str, str] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """``(catalogue, manifest)`` of every readable local catalogue under ``assets`` that names the Asset Store EULA."""
    if not enabled(env) or not assets.is_dir():
        return []
    found = []
    for folder in sorted(assets.iterdir()):
        if not (folder.is_dir() and is_local(folder.name)):
            continue
        try:
            document = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))  # pragma: no mutate: UTF-8 is also the locale encoding of every host and image this runs on
        except (OSError, json.JSONDecodeError):
            continue
        source = document.get("source", "") if isinstance(document, dict) else ""  # pragma: no mutate: any default without the licence name is skipped alike
        if LICENCE_NAME in str(source):
            found.append((folder.name, document))
    return found


def furnish_rows(assets: Path, *, floors: bool, env: Mapping[str, str] | None = None) -> list[tuple[str, dict[str, Any]]]:
    """Interior rows with a ``furnish`` hint: for the stair floors (``floors``) or for the shop sets."""
    rows = []
    for catalogue, document in local_manifests(assets, env):
        for row in document.get("assets") or []:
            hint = row.get("furnish") if isinstance(row, dict) else None
            if not isinstance(hint, dict) or row.get("placement") != "interior" or not row.get("id"):
                continue
            if floors and not (hint.get("floors") and hint.get("slot") == "wall"):
                continue
            rows.append((catalogue, row))
    return rows
