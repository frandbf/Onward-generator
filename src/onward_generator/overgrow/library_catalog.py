"""Selectable prop library for the abandoned-city enrichment step.

Installed meshes live in the normalized library manifest. The Sketchfab review
lists models that were checked and not saved. A selection file names the
installed ids ``overgrow place`` may use.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

REVIEW_FILE = Path(__file__).resolve().parents[3] / "assets" / "normalized_review.json"
SELECTION_SCHEMA = "onward_generator.prop_selection"
SELECTION_VERSION = "0.1.0"


class LibraryError(ValueError):
    """The selection names something the library does not contain."""


def library_directories(library: str | Path) -> list[Path]:
    """One catalogue folder, or every child of a parent that has its own ``manifest.json``.

    A folder that contains ``manifest.json`` is that one catalogue. A parent such as
    ``assets`` has no manifest of its own; each direct child that has one is included,
    in directory-name order.
    """
    root = Path(library).expanduser()
    if (root / "manifest.json").is_file():
        return [root]
    if not root.is_dir():
        return []
    return [
        child for child in sorted(root.iterdir(), key=lambda path: path.name)
        if child.is_dir() and (child / "manifest.json").is_file()
    ]


def _manifest_rows(directory: Path) -> list[dict]:
    document = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    rows = []
    for row in document.get("assets") or []:
        if isinstance(row, dict) and row.get("id") and row.get("file"):
            rows.append(row)
    return rows


def installed_rows(library: str | Path) -> list[dict]:
    rows: list[dict] = []
    seen: dict[str, Path] = {}
    for directory in library_directories(library):
        for row in _manifest_rows(directory):
            ident = str(row["id"])
            previous = seen.get(ident)
            if previous is not None and previous != directory:
                raise LibraryError(f"{ident}: declared in {previous.name} and {directory.name}")
            seen[ident] = directory
            rows.append(row)
    return rows


def review_rows(path: str | Path = REVIEW_FILE) -> list[dict]:
    review = Path(path)
    if not review.is_file():
        return []
    document = json.loads(review.read_text(encoding="utf-8"))
    return [row for row in (document.get("assets") or []) if isinstance(row, dict)]


def read_selection(path: str | Path) -> list[str]:
    document = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    raw = document.get("ids") if isinstance(document, dict) else None
    if not isinstance(raw, list) or not raw or not all(isinstance(item, str) and item for item in raw):
        raise LibraryError(f"{path}: selection ids must be a non-empty list of strings")
    seen: set[str] = set()
    chosen: list[str] = []
    for item in raw:
        if item not in seen:
            seen.add(item)
            chosen.append(item)
    return chosen


def write_selection(path: str | Path, ids: Sequence[str]) -> None:
    target = Path(path).expanduser()
    if target.exists():
        raise FileExistsError(f"{target} already exists; choose another path or pass --overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": SELECTION_SCHEMA,
        "schema_version": SELECTION_VERSION,
        "ids": list(ids),
    }
    target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def choose_ids(library: str | Path, requested: Sequence[str], *, all_installed: bool) -> list[str]:
    """Ids that exist as normalized GLBs. Unknown and unusable reviews are refused."""
    installed = [str(row["id"]) for row in installed_rows(library)]
    known = set(installed)
    reviewed: dict[str, dict] = {}
    for row in review_rows():
        for key in (row.get("name"), row.get("uid")):
            if key:
                reviewed[str(key)] = row
    picked: list[str] = []
    seen: set[str] = set()
    wanted = list(installed) if all_installed else []
    wanted.extend(requested)
    if not wanted:
        raise LibraryError("name at least one --id, or pass --installed")
    for item in wanted:
        if item in seen:
            continue
        seen.add(item)
        if item in known:
            picked.append(item)
            continue
        review = reviewed.get(item)
        if review is not None and review.get("usable") is True:
            raise LibraryError(f"{item}: licence allows it, but the file was not downloaded")
        if review is not None:
            raise LibraryError(f"{item}: {review.get('reason') or 'not usable'}")
        raise LibraryError(f"{item}: not in the installed library")
    return picked
