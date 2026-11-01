"""Rewrite the local listing fields of a Launcher ``.info3`` after it is written.

The Quest list shows whatever ``MapName`` is in this sidecar. On the local
slot, Hunt appears when that name is ``Unity Export``, so this pass always
writes that name. The package title stays in the description and in
``config/export.json``. ``ID`` and ``BundleHash`` stay as the build wrote
them. ``EnvironmentType`` is updated when the layout names one.

``write_candidate_listing`` writes the opt-in headset candidates of
ADR-007 (amendment 1). Each is a new ``.info3`` derived from the local one; the
Launcher output is never rewritten. None of them is verified on the headset.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import struct
from pathlib import Path
from typing import Any

from onward_generator.config.loader import loads_strict


class Info3Error(ValueError):
    """The .info3 file is missing, malformed, or would lose its bundle identity."""


ENVIRONMENT_CODE = {"Desert": 0, "Woodland": 1, "Urban": 2, "Snow": 3}
# The Quest local slot lists Hunt when the card name is this string.
LOCAL_CARD_NAME = "Unity Export"
# The id and file stem the SDK Launcher always writes.
LOCAL_ID = "unity-export.local"
# Onward 2.0.4 solo menu listed Hunt only when the local card advertised this
# short set. A longer list (Assault, Escort, Evac, Retrieval) left Free Roam alone.
# 4 Free Roam, 10 Hunt, 1 Uplink. Same order as the card that loaded Hunt.
SOLO_GAME_MODES = [4, 10, 1]
# Where the Custom Content Guide tells a creator to copy the Launcher pair on a Quest.
DEVICE_CONTENT_DIR = "/sdcard/Android/data/com.downpourinteractive.onward/files/CustomContent"
# Opt-in listing modes (ADR-007, amendment 1), chosen with ONWARD_QUEST_LISTING.
# "local", the default, is today's install and needs no rewrite.
# local-thumb: the local slot, MapName kept, ThumbnailUrl pointing at its png.
# named: a second pair with its own id and the map title as MapName.
# named-thumb: named plus ThumbnailUrl pointing at its png.
CANDIDATE_LISTINGS = ("local-thumb", "named", "named-thumb")
NAMED_ID_PREFIX = "onwardgen."
# The Quest listing card stores Description in at most this many characters.
LISTING_DESCRIPTION_MAX = 600
# Custom Content Guide: "Thumbnail: 512px wide by 256px tall. No exceptions."
THUMBNAIL_SIZE = (512, 256)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAP_ID = re.compile(r"[A-Za-z0-9_]{1,40}")


def _read_info3(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise Info3Error(f"info3 not found: {path}")
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
    if not isinstance(bundle, str) or not bundle:
        raise Info3Error(f"{path}: WorkshopItem.BundleHash is missing; refusing to rewrite")
    if item.get("ID") != LOCAL_ID:
        raise Info3Error(f"{path}: ID is {item.get('ID')!r}; only the local test slot {LOCAL_ID} may be relabelled")
    return doc


def _check_title(title: str) -> None:
    if not title.strip() or len(title) > 80:
        raise Info3Error("title must be 1–80 characters")


def _credit_lines(clause: str) -> list[str]:
    return [part.strip() for part in clause.rstrip(".").split("; ") if part.strip()]


def fit_listing_description(description: str, *, limit: int = LISTING_DESCRIPTION_MAX) -> str:
    """The listing text, at most ``limit`` characters.

    A package description is the map sentence plus `` Data: `` and one credit
    per source. Sources already named in the sentence are not repeated. Credits
    that do not fit are left off the card; the package ``attribution`` list
    keeps every one. A text with no credit clause is cut at ``limit``.
    """
    text = description.strip()
    if not text:
        raise Info3Error("description must be 1–600 characters")
    if len(text) <= limit:
        return text
    head, marker, tail = text.rpartition(" Data: ")
    if not marker:
        return text[:limit].rstrip()
    sentence, earlier, earlier_tail = head.rpartition(" Data: ")
    if earlier:
        stated = _credit_lines(earlier_tail)
    else:
        sentence, stated = head, []
    ordered: list[str] = []
    for credit in stated + _credit_lines(tail):
        if credit not in ordered:
            ordered.append(credit)

    def render(chosen: list[str]) -> str:
        if not chosen:
            return sentence.rstrip()
        return f"{sentence.rstrip()} Data: {'; '.join(chosen)}."

    chosen: list[str] = []
    for credit in ordered:
        if len(render(chosen + [credit])) <= limit:
            chosen.append(credit)
    result = render(chosen)
    if len(result) > limit:
        result = result[:limit].rstrip()
    if not result:
        raise Info3Error("description must be 1–600 characters")
    return result


def apply_local_listing(info3_path: str | Path, *, title: str, description: str, environment: str | None = None) -> dict[str, Any]:
    path = Path(info3_path).expanduser()
    _check_title(title)
    description = fit_listing_description(description)
    doc = _read_info3(path)
    item = doc["WorkshopItem"]
    item["MapName"] = LOCAL_CARD_NAME
    item["Description"] = description.strip()
    item["GameModes"] = list(SOLO_GAME_MODES)
    if environment is not None:
        if environment not in ENVIRONMENT_CODE:
            raise Info3Error(f"environment {environment!r} is not a LevelDescriptor.EMapEnvironment name")
        item["EnvironmentType"] = ENVIRONMENT_CODE[environment]
    path.write_text(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return doc


def listing_item_id(mode: str, map_id: str) -> str:
    """The ``ID`` and file stem a candidate listing uses on the headset."""
    if mode not in CANDIDATE_LISTINGS:
        raise Info3Error(f"listing mode {mode!r} is not one of {', '.join(CANDIDATE_LISTINGS)}")
    if mode == "local-thumb":
        return LOCAL_ID
    if not _MAP_ID.fullmatch(map_id):
        raise Info3Error(f"map id {map_id!r} must match [A-Za-z0-9_]{{1,40}}")
    return NAMED_ID_PREFIX + map_id


def device_thumbnail_url(item_id: str) -> str:
    """``file://`` URL of the png that ``push`` copies beside the pair."""
    return f"file://{DEVICE_CONTENT_DIR}/{item_id}.png"


def check_listing_thumbnail(path: str | Path) -> tuple[int, int]:
    """Refuse a listing image that is not a 512×256 PNG."""
    source = Path(path).expanduser()
    if not source.is_file():
        raise Info3Error(f"thumbnail not found: {source}")
    data = source.read_bytes()
    if len(data) < 24 or data[:8] != PNG_SIGNATURE or data[12:16] != b"IHDR":
        raise Info3Error(f"{source}: not a PNG")
    size = struct.unpack_from(">II", data, 16)
    if size != THUMBNAIL_SIZE:
        raise Info3Error(f"{source}: thumbnail is {size[0]}×{size[1]}; the listing needs 512×256")
    return size


def candidate_listing(doc: dict[str, Any], *, mode: str, title: str, map_id: str) -> dict[str, Any]:
    """A new info3 document for a candidate listing, from the local listing ``doc``."""
    _check_title(title)
    item_id = listing_item_id(mode, map_id)
    out = copy.deepcopy(doc)
    item = out["WorkshopItem"]
    item["ID"] = item_id
    item["ThumbnailUrl"] = device_thumbnail_url(item_id) if mode.endswith("-thumb") else ""
    if mode.startswith("named"):
        item["MapName"] = title.strip()
    return out


def write_candidate_listing(source_info3: str | Path, out_dir: str | Path, *, mode: str, title: str,
                            map_id: str, content3: str | Path) -> Path:
    """Write ``<id>.info3`` for listing ``mode`` into ``out_dir``. Never overwrites.

    ``source_info3`` is the Launcher's local listing. It is read, not changed.
    ``BundleHash`` must be the MD5 of ``content3``, the bundle that ``push``
    copies under the listing's id. A file already at the target is accepted only
    when it holds the same bytes.
    """
    doc = _read_info3(Path(source_info3).expanduser())
    bundle = Path(content3).expanduser()
    if not bundle.is_file():
        raise Info3Error(f"content3 not found: {bundle}")
    digest = hashlib.md5(bundle.read_bytes()).hexdigest()
    if doc["WorkshopItem"]["BundleHash"].lower() != digest:
        raise Info3Error(f"BundleHash {doc['WorkshopItem']['BundleHash']} is not the MD5 of {bundle} ({digest})")
    listing = candidate_listing(doc, mode=mode, title=title, map_id=map_id)
    data = json.dumps(
        listing,
        separators=(",", ":"),
        ensure_ascii=False,  # pragma: no mutate: None is falsy like False and writes the same JSON
    ).encode() + b"\n"
    target = Path(out_dir).expanduser() / f"{listing['WorkshopItem']['ID']}.info3"
    if target.exists():
        if target.read_bytes() == data:
            return target
        raise Info3Error(f"{target} exists with other content; candidate listings are never overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target
