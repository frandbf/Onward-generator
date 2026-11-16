"""Generic shop words by the language of the map's centre (detail/shop_names.py, 15 November 2026)."""

from __future__ import annotations

import copy
import json

import pytest

from onward_generator.detail.shop_names import SHOP_NAMES_PATH, ShopNamesError, language_at, load_shop_names, parse_shop_names, shop_words

DOC = json.loads(SHOP_NAMES_PATH.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("lat", "lon", "language"), [
    (37.98407, -1.12863, "es"),  # Murcia
    (40.4168, -3.7038, "es"),  # Madrid
    (43.3183, -1.9812, "es"),  # San Sebastián, beside the French box
    (41.3874, 2.1686, "es"),  # Barcelona
    (38.8779, -6.9706, "es"),  # Badajoz, beside Portugal
    (28.4636, -16.2518, "es"),  # Santa Cruz de Tenerife
    (19.4326, -99.1332, "es"),  # Mexico City
    (-34.6037, -58.3816, "es"),  # Buenos Aires
    (48.84045, 2.34981, "fr"),  # Paris, Rue Mouffetard
    (43.2965, 5.3698, "fr"),  # Marseille
    (43.4832, -1.5586, "fr"),  # Biarritz
    (38.7223, -9.1393, "pt"),  # Lisbon
    (41.1579, -8.6291, "pt"),  # Porto
    (-23.5505, -46.6333, "pt"),  # São Paulo
    (2.047458, 45.326639, "en"),  # Mogadishu: Somali and Arabic signs are not drawn; English
    (33.36522, 43.79589, "en"),  # Fallujah
    (51.5072, -0.1276, "en"),  # London
    (35.6762, 139.6503, "en"),  # Tokyo
])
def test_the_map_centre_names_the_language(lat: float, lon: float, language: str) -> None:
    assert language_at(load_shop_names(), lat, lon)[0] == language


def test_the_words_of_each_language() -> None:
    table = load_shop_names()
    assert table.default_language == "en" and set(table.languages) == {"es", "en", "fr", "pt"}
    assert table.languages["es"][0] == "Comercio" and table.languages["es"][1]["shop=bakery"] == "Panadería"
    assert table.languages["en"][1]["shop=bakery"] == "Bakery" and table.languages["fr"][1]["shop=bakery"] == "Boulangerie"
    assert table.languages["pt"][1]["amenity=pharmacy"] == "Farmácia"
    assert len({frozenset(names) for _, names in table.languages.values()}) == 1  # one word per key in every language


def test_shop_words_follow_the_centre_or_the_map_s_own() -> None:
    murcia, mogadishu = shop_words([-1.12863, 37.98407]), shop_words([45.326639, 2.047458])
    assert (murcia.language, murcia.origin, murcia.default_name) == ("es", "region", "Comercio")
    assert (mogadishu.language, mogadishu.origin, mogadishu.generic_names["amenity=pharmacy"]) == ("en", "default", "Pharmacy")
    assert (shop_words(None).language, shop_words(None).origin) == ("en", "default")
    own = shop_words([45.326639, 2.047458], "Dukaan", {"shop": "Dukaan"})
    assert (own.language, own.origin, own.default_name, dict(own.generic_names)) == ("config", "config", "Dukaan", {"shop": "Dukaan"})
    assert shop_words([45.3, 2.0], "Dukaan", None).language == "en"  # both words or none


def test_a_box_holds_its_edges() -> None:
    doc = copy.deepcopy(DOC)
    doc["regions"] = [{"language": "fr", "boxes": [[10.0, 20.0, 30.0, 40.0]]}]
    table = parse_shop_names(doc)
    assert [language_at(table, la, lo)[0] for la, lo in ((10.0, 30.0), (20.0, 40.0), (9.999, 35.0), (15.0, 40.001))] == ["fr", "fr", "en", "en"]
    assert language_at(table, 15.0, 35.0) == ("fr", "region") and language_at(table, 0.0, 0.0) == ("en", "default")


def _broken(change) -> dict:  # type: ignore[no-untyped-def]
    doc = copy.deepcopy(DOC)
    change(doc)
    return doc


@pytest.mark.parametrize(("change", "message"), [
    (lambda d: d.update(schema="other"), "not a onward_generator.shop_names document"),
    (lambda d: d.update(languages={}), "languages must name at least one language"),
    (lambda d: d.update(default_language="de"), "default_language 'de' has no words"),
    (lambda d: d["languages"]["fr"].update(default_name=" "), "languages.fr needs a default_name"),
    (lambda d: d["languages"]["fr"]["generic_names"].update(shop=""), "languages.fr needs a default_name"),
    (lambda d: d["languages"]["fr"]["generic_names"].pop("shop"), "languages.fr must name the same keys as en: ['shop']"),
    (lambda d: d["regions"][0].update(language="de"), "regions[0] language 'de' has no words"),
    (lambda d: d["regions"][0].update(boxes=[]), "regions[0] needs boxes"),
    (lambda d: d["regions"][0].update(boxes=[[1.0, 2.0, 3.0]]), "regions[0]: a box is [lat0, lat1, lon0, lon1]"),
    (lambda d: d["regions"][0].update(boxes=[[2.0, 1.0, 3.0, 4.0]]), "regions[0]: a box runs from its south-west corner to its north-east corner"),
    (lambda d: d["regions"][0].update(boxes=[[1.0, 1.0, 3.0, 4.0]]), "regions[0]: a box runs from its south-west corner"),
    (lambda d: d["regions"][0].update(boxes=[[1.0, 2.0, 3.0, 3.0]]), "regions[0]: a box runs from its south-west corner"),
    (lambda d: d["regions"][0].update(boxes=[[1.0, 2.0, True, 4.0]]), "regions[0]: a box is [lat0, lat1, lon0, lon1]"),
    (lambda d: d["languages"].update(fr="mot"), "languages.fr needs a default_name"),
    (lambda d: d["regions"].insert(0, "fr"), "regions[0] language None has no words"),
])
def test_a_bad_table_is_refused(change, message: str) -> None:  # type: ignore[no-untyped-def]
    # The message starts with where the table came from: "shop_names" unless the loader names its file.
    with pytest.raises(ShopNamesError, match="^shop_names: " + message.replace("[", r"\[").replace("]", r"\]")):
        parse_shop_names(_broken(change))


def test_the_parsed_table_keeps_every_language_s_words() -> None:
    table = parse_shop_names(DOC)
    assert set(table.languages) == set(DOC["languages"])
    for code, body in DOC["languages"].items():
        assert table.languages[code][0] == body["default_name"]
        assert dict(table.languages[code][1]) == body["generic_names"]
