"""Generic shop words by the language of the map's centre (15 November 2026).

A shop OSM gives no ``name`` gets a generic word for its ``shop``/``amenity``/``office``/``craft`` value. Those words
were Spanish on every map, so a bakery in Mogadishu read "Panadería". ``config/shop_names.json`` holds the words of a
few languages and coarse boxes on the map centre that choose one, first match wins, else ``default_language``. A map's
own detail config may name its words (``shops.default_name``, ``shops.generic_names``); then they win.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from types import MappingProxyType

REPO_ROOT = Path(__file__).resolve().parents[3]
SHOP_NAMES_PATH = REPO_ROOT / "config" / "shop_names.json"
SCHEMA = "onward_generator.shop_names"


class ShopNamesError(ValueError):
    """``config/shop_names.json`` is not a valid shop names table."""


@dataclass(frozen=True, slots=True)
class ShopWords:
    """The words a map's shops are named with: the language they come from and how it was chosen."""

    language: str
    origin: str  # "region", "default" or "config"
    default_name: str
    generic_names: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ShopNames:
    default_language: str
    regions: tuple[tuple[str, tuple[tuple[float, float, float, float], ...]], ...]
    languages: Mapping[str, tuple[str, Mapping[str, str]]]


def _box(raw: object, where: str) -> tuple[float, float, float, float]:
    if not isinstance(raw, list) or len(raw) != 4 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw):
        raise ShopNamesError(f"{where}: a box is [lat0, lat1, lon0, lon1]")
    lat0, lat1, lon0, lon1 = (float(v) for v in raw)
    if not (lat0 < lat1 and lon0 < lon1):
        raise ShopNamesError(f"{where}: a box runs from its south-west corner to its north-east corner")
    return lat0, lat1, lon0, lon1


def parse_shop_names(doc: object, where: str = "shop_names") -> ShopNames:
    """The checked table: every region's language and the default have words, every language names one word per key of
    the default language's table."""
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ShopNamesError(f"{where}: not a {SCHEMA} document")
    raw_languages = doc.get("languages")
    if not isinstance(raw_languages, dict) or not raw_languages:
        raise ShopNamesError(f"{where}: languages must name at least one language")
    languages: dict[str, tuple[str, Mapping[str, str]]] = {}
    for code, body in raw_languages.items():
        names = body.get("generic_names") if isinstance(body, dict) else None
        default = body.get("default_name") if isinstance(body, dict) else None
        if not isinstance(default, str) or not default.strip() or not isinstance(names, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in names.items()):
            raise ShopNamesError(f"{where}: languages.{code} needs a default_name and generic_names of words")
        languages[code] = (default, MappingProxyType(dict(sorted(names.items()))))
    default_language = doc.get("default_language")
    if default_language not in languages:
        raise ShopNamesError(f"{where}: default_language {default_language!r} has no words")
    keys = set(languages[str(default_language)][1])
    for code, (_, names) in languages.items():
        if set(names) != keys:
            raise ShopNamesError(f"{where}: languages.{code} must name the same keys as {default_language}: {sorted(keys ^ set(names))}")
    regions = []
    for index, region in enumerate(doc.get("regions") or []):
        language = region.get("language") if isinstance(region, dict) else None
        if language not in languages:
            raise ShopNamesError(f"{where}: regions[{index}] language {language!r} has no words")
        boxes = region.get("boxes")
        if not isinstance(boxes, list) or not boxes:
            raise ShopNamesError(f"{where}: regions[{index}] needs boxes")
        regions.append((str(language), tuple(_box(box, f"{where}: regions[{index}]") for box in boxes)))
    return ShopNames(str(default_language), tuple(regions), MappingProxyType(languages))


@cache
def load_shop_names(path: Path = SHOP_NAMES_PATH) -> ShopNames:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ShopNamesError(f"{path}: {exc}") from exc
    return parse_shop_names(doc, str(path))


def language_at(table: ShopNames, latitude: float, longitude: float) -> tuple[str, str]:
    """The language of a point and its origin: the first region whose box holds it (edges included), else the default."""
    for language, boxes in table.regions:
        if any(lat0 <= latitude <= lat1 and lon0 <= longitude <= lon1 for lat0, lat1, lon0, lon1 in boxes):
            return language, "region"
    return table.default_language, "default"


def shop_words(origin_wgs84: Sequence[float] | None, own_default: str | None = None, own_names: Mapping[str, str] | None = None,
               table: ShopNames | None = None) -> ShopWords:
    """The words for a map centred on ``origin_wgs84`` (``[longitude, latitude]``): the map's own words when its detail
    config names them, else the language of its centre, else the default language without a centre."""
    if own_default is not None and own_names is not None:
        return ShopWords("config", "config", own_default, own_names)
    names = table or load_shop_names()
    language, chosen = language_at(names, float(origin_wgs84[1]), float(origin_wgs84[0])) if origin_wgs84 else (names.default_language, "default")
    default, generic = names.languages[language]
    return ShopWords(language, chosen, default, generic)


__all__ = ["SHOP_NAMES_PATH", "ShopNames", "ShopNamesError", "ShopWords", "language_at", "load_shop_names", "parse_shop_names", "shop_words"]
