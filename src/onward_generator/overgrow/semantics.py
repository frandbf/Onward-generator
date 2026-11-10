"""What every library asset is, where it belongs and how it stands (ADR-036): one ``semantic`` block per manifest row.

Every row of every catalogue under ``assets/`` gets the block, in the shared catalogues and in the local Asset Store
ones, outdoor or not:

- ``kind``: what it is, from one closed vocabulary (``KINDS``): ``shelf_tall``, ``counter``, ``table``, ``chair``,
  ``bed``, ``fridge``, ``rug``, ``decor_food``, ``vehicle``, ``barrier``, ``tree``, … or ``other``.
- ``settings``: where it belongs, from ``SETTINGS``: shop types (``bakery``, ``bar``, ``grocery``, ``pharmacy``,
  ``shop``, ``office``), home rooms (``kitchen``, ``living``, ``dining``, ``bedroom``, ``bathroom``) and outdoor
  places (``street``, ``yard``, ``park``, ``industrial``).
- ``mount``: how it stands, from ``MOUNTS``: ``wall`` (its back on a wall), ``center`` (free-standing in a room),
  ``corner``, ``counter_top``, ``table_top``, ``floor_flat`` (a rug), ``seat`` (round a table), ``facade``,
  ``ground`` (outdoors), or ``none`` (a kit piece the generator never places).

The block is estimated from the model's name, its catalogue, its role and placement and its size, as the other
semantic fields are (``semantics.origin: estimated``); a person may correct a row by hand, and ``describe`` keeps a
row whose block says ``origin: reviewed``. Nothing here is observed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

KINDS = (
    # Rooms
    "counter", "register", "shelf_tall", "shelf_low", "display_case", "fridge", "kitchen_unit", "appliance",
    "table", "coffee_table", "desk", "chair", "stool", "sofa", "armchair", "bed", "wardrobe", "cabinet_low",
    "tv", "bath", "toilet", "washer", "lamp_floor", "lamp_small", "plant_pot", "rug", "decor_food", "decor_tableware",
    "decor_misc", "storage", "trash_small", "mannequin",
    # Outdoors
    "vehicle", "barrier", "cone", "bollard", "hydrant", "bench", "lamp_post", "traffic_light", "sign", "bin",
    "dumpster", "barrel", "crate", "pallet", "planter", "tree", "plant", "rock", "debris", "path", "manhole",
    "aircon", "camera", "fence", "structure", "kit_piece", "other",
)
SETTINGS = ("bakery", "bar", "grocery", "pharmacy", "shop", "office", "kitchen", "living", "dining", "bedroom", "bathroom",
            "street", "yard", "park", "industrial")
MOUNTS = ("wall", "center", "corner", "counter_top", "table_top", "floor_flat", "seat", "facade", "ground", "none")
SHOPS = ("bakery", "bar", "grocery", "pharmacy", "shop")
HOME = ("kitchen", "living", "dining", "bedroom", "bathroom")

# (kind, words, mount, settings); the first rule whose words meet the name wins, so the specific come first.
_ROOM_RULES: tuple[tuple[str, frozenset[str], str, tuple[str, ...]], ...] = (
    ("register", frozenset({"register", "cashregister", "till", "cashier", "pos"}), "counter_top", SHOPS),
    ("counter", frozenset({"counter", "checkout", "reception", "kitchenbar", "barcounter", "servery"}), "wall", (*SHOPS, "kitchen")),
    ("display_case", frozenset({"showcase", "vitrine", "display", "displaycase", "glasscase"}), "wall", ("bakery", "shop", "pharmacy", "grocery")),
    ("cabinet_low", frozenset({"nightstand", "bedside", "bedsidetable", "bedtable", "nighttable"}), "wall", ("bedroom",)),
    ("fridge", frozenset({"fridge", "refrigerator", "freezer", "vending", "cooler", "kitchenfridge"}), "wall", ("bar", "grocery", "bakery", "kitchen")),
    ("washer", frozenset({"washer", "washing", "washingmachine", "dryer", "dishwasher"}), "wall", ("bathroom", "kitchen")),
    ("toilet", frozenset({"toilet", "wc", "urinal"}), "wall", ("bathroom",)),
    ("bath", frozenset({"bathtub", "bath", "shower", "washbasin", "basin"}), "wall", ("bathroom",)),
    ("kitchen_unit", frozenset({"kitchenisland", "island"}), "center", ("kitchen",)),
    ("kitchen_unit", frozenset({"oven", "stove", "cooker", "hob", "sink", "kitchencabinet", "kitchen"}), "wall", ("kitchen", "bakery", "bar")),
    ("appliance", frozenset({"microwave", "kettle", "toaster", "blender", "espresso", "coffeemachine", "mixer", "printer"}), "counter_top", ("kitchen", "bar", "bakery", "office")),
    ("tv", frozenset({"tv", "television", "tvstand", "cabinettelevision"}), "wall", ("living", "bedroom")),
    ("bed", frozenset({"bed", "bunk", "crib", "cot"}), "wall", ("bedroom",)),
    ("sofa", frozenset({"sofa", "couch", "loveseat", "lounge"}), "wall", ("living",)),
    ("armchair", frozenset({"armchair", "recliner", "ottoman", "pouf", "beanbag"}), "corner", ("living", "bedroom")),
    ("wardrobe", frozenset({"wardrobe", "closet", "locker", "lockers", "cupboard", "armoire"}), "wall", ("bedroom", "office", "shop")),
    ("desk", frozenset({"desk", "workstation"}), "wall", ("office", "bedroom")),
    ("coffee_table", frozenset({"coffeetable", "sidetable", "endtable"}), "center", ("living",)),
    ("stool", frozenset({"stool", "barstool"}), "seat", ("bar", "kitchen", "bakery")),
    ("chair", frozenset({"chair", "seat", "officechair"}), "seat", ("dining", "bar", "bakery", "office", "kitchen")),
    ("table", frozenset({"table", "dining", "diningtable"}), "center", ("dining", "bar", "bakery", "kitchen")),
    ("cabinet_low", frozenset({"drawer", "drawers", "dresser", "nightstand", "bedside", "sideboard", "commode", "chest", "cabinet", "buffet"}), "wall", ("bedroom", "living", "dining", "office")),
    ("shelf_tall", frozenset({"shelf", "shelves", "shelving", "bookcase", "bookshelf", "rack", "racks", "gondola", "etagere"}), "wall", ("shop", "grocery", "pharmacy", "bakery", "living", "office")),
    ("mannequin", frozenset({"mannequin", "dummy", "clothesrack", "hanger"}), "center", ("shop",)),
    ("lamp_floor", frozenset({"floorlamp", "standlamp", "lampstand"}), "corner", ("living", "bedroom")),
    ("lamp_small", frozenset({"lamp", "light", "lights", "desklamp", "tablelamp", "candle"}), "table_top", ("living", "bedroom", "office")),
    ("plant_pot", frozenset({"plant", "plants", "houseplant", "pottedplant", "ficus", "cactus", "flower", "flowers"}), "corner", (*HOME, *SHOPS, "office")),
    ("rug", frozenset({"rug", "carpet", "carpets", "mat", "doormat"}), "floor_flat", ("living", "bedroom", "dining", "shop")),
    ("trash_small", frozenset({"trash", "trashcan", "bin", "garbage", "wastebasket"}), "corner", (*HOME, *SHOPS, "office")),
    ("storage", frozenset({"crate", "box", "boxes", "giftbox", "barrel", "pallet", "bag", "sack", "basket", "suitcase", "container", "carton"}), "corner", ("grocery", "shop", "industrial")),
)
_FOOD = frozenset({"food", "bread", "loaf", "cake", "cupcake", "croissant", "baguette", "donut", "doughnut", "cookie", "muffin", "pie",
                   "apple", "banana", "orange", "fruit", "cheese", "egg", "meat", "fish", "pizza", "burger", "sandwich", "chocolate",
                   "candy", "cereal", "carrot", "tomato", "watermelon", "lemon", "pear", "grapes", "corn", "sushi", "taco", "steak",
                   "icecream", "cream", "soda", "can", "frappe", "popsicle", "pancake", "waffle", "bacon", "sausage"})
_TABLEWARE = frozenset({"cup", "mug", "plate", "plates", "glass", "bottle", "wine", "bowl", "fork", "knife", "spoon", "pan", "pot",
                        "teapot", "jar", "tray", "napkin", "kettle", "cutlery", "tea", "coffee", "carafe", "jug", "beer", "dish"})
_MISC = frozenset({"book", "books", "picture", "pictures", "painting", "frame", "clock", "mirror", "pillow", "cushion", "towel",
                   "toiletpaper", "paper", "laptop", "computer", "monitor", "keyboard", "phone", "radio", "speaker", "candle",
                   "trophy", "globe", "statue", "figurine", "basket", "rollingpin", "menu", "sign"})
_OUTDOOR_BY_ROLE = {
    "vehicle": "vehicle", "wreck": "vehicle", "barrier": "barrier", "cone": "cone", "bollard": "bollard", "hydrant": "hydrant",
    "bench": "bench", "lamp": "lamp_post", "pole": "lamp_post", "trafficlight": "traffic_light", "traffic": "traffic_light",
    "sign": "sign", "trash": "bin", "bin": "bin", "dumpster": "dumpster", "barrel": "barrel", "drum": "barrel", "crate": "crate",
    "box": "crate", "pallet": "pallet", "planter": "planter", "tree": "tree", "plant": "plant", "bush": "plant", "shrub": "plant",
    "flower": "plant", "rock": "rock", "debris": "debris", "path": "path", "road": "path", "manhole": "manhole", "aircon": "aircon",
    "camera": "camera", "fence": "fence", "gate": "fence", "wall": "fence", "awning": "structure", "fire_escape": "structure",
    "tank": "barrel", "container": "dumpster", "can": "bin", "cart": "crate", "beam": "structure", "scaffold": "structure",
    "building": "structure", "meter": "sign", "post": "bollard", "antenna": "structure", "dish": "structure", "table": "bench", "bed": "debris",
}
_PACK_SETTINGS = (
    ("bakery", "bakery"), ("coffee", "bar"), ("cafe", "bar"), ("restaurant", "bar"), ("fast_food", "bar"), ("bar", "bar"),
    ("grocery", "grocery"), ("market", "grocery"), ("pharmacy", "pharmacy"), ("office", "office"), ("kitchen", "kitchen"),
    ("bedroom", "bedroom"), ("bathroom", "bathroom"), ("dining", "dining"), ("living", "living"), ("shop", "shop"),
    ("storage", "industrial"), ("warehouse", "industrial"), ("garage", "industrial"),
)


def words_of(text: str) -> set[str]:
    """Lowercase words of a name, its camelCase split and joined pairs ('kitchenCabinet' gives kitchen, cabinet,
    kitchencabinet), and singulars of plurals."""
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", " ", text)
    tokens = [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w and not w.isdigit()]
    found = set(tokens)
    found.update(a + b for a, b in zip(tokens, tokens[1:]))
    for word in tokens:
        if word.endswith("es") and len(word) > 4:
            found.add(word[:-2])
        if word.endswith("s") and len(word) > 3:
            found.add(word[:-1])
    return found


def _stem(asset_id: str) -> str:
    """The model's own name: the catalogue prefixes (``as_<pack>_``, ``qt_<pack>_``, ``kenney_<pack>_``) dropped."""
    return re.sub(r"^(as_[a-z0-9]+(_[a-z0-9]+)*?_(?=[A-Z])|qt_[a-z]+_|kenney_[a-z]+_)", "", asset_id)


def _pack_settings(catalogue: str) -> tuple[str, ...]:
    lower = catalogue.lower()
    return tuple(dict.fromkeys(setting for word, setting in _PACK_SETTINGS if word in lower))


def _size(row: Mapping[str, Any]) -> tuple[float, float, float]:
    return float(row.get("length_m") or 0.0), float(row.get("depth_m") or 0.0), float(row.get("height_m") or 0.0)


# A model hung on a wall or a ceiling: the furnishing planner stands pieces on the floor, so it never places these.
_HUNG = frozenset({"upper", "uppercabinet", "wallshelf", "walllamp", "wallpicture", "ceiling", "pendant", "chandelier", "hanging",
                   "towelrack", "wallmounted", "sconce", "mirror", "picture", "painting", "pictureframe", "clock", "curtain",
                   "curtains", "wallcabinet", "cabinetwall", "lampwall", "wallclock", "extractor", "hood", "splashback"})
# Kinds that are furniture-sized: a model of one of them smaller than SMALL_M on every side is a part (a handle, a
# drawer front split off its cabinet by an exporter) or a toy, and is never placed.
_LARGE_KINDS = frozenset({"counter", "shelf_tall", "shelf_low", "display_case", "fridge", "kitchen_unit", "table", "desk",
                          "sofa", "bed", "wardrobe", "cabinet_low", "tv", "bath", "toilet", "washer", "armchair", "chair", "stool"})
SMALL_M = 0.45


_PART_WORDS = frozenset({"base", "handle", "handles", "top", "countertop", "counterbase", "door", "doors", "drawer", "knob",
                         "leg", "legs", "front", "panel", "lid", "glass", "frame", "shelfboard", "board", "plinth"})


def is_part(row: Mapping[str, Any], words: set[str]) -> bool:
    """A child of one FBX the exporter split (``source_prefab`` ``<file>#<child>``) named as a part of a whole: a
    cabinet's base, its counter top, a handle. Alone it reads as half a piece of furniture."""
    source = str(row.get("source_prefab") or "")
    if "#" not in source:
        return False
    child = words_of(source.rsplit("#", 1)[1])
    return bool(child & _PART_WORDS) or bool(words & {"base", "handle", "countertop", "counterbase"})


def _named_room(words: set[str]) -> list[str]:
    """The room a model's own name names (bathroomCabinet, kitchenFridge, kidsBed), which then wins over its kind's rooms."""
    named = [room for word, room in (("bathroom", "bathroom"), ("kitchen", "kitchen"), ("bedroom", "bedroom"), ("kids", "bedroom"),
                                     ("nursery", "bedroom"), ("office", "office"), ("dining", "dining"), ("living", "living"))
             if word in words]
    return list(dict.fromkeys(named))


def _room(words: set[str], row: Mapping[str, Any], catalogue: str) -> dict[str, Any]:
    length, depth, height = _size(row)
    pack = _pack_settings(catalogue)
    small = max(length, depth, height) <= SMALL_M
    if is_part(row, words):
        return {"kind": "other", "mount": "none", "settings": list(pack), "part": True}
    if words & _HUNG:
        kind = next((k for k, vocabulary, _m, _s in _ROOM_RULES if words & vocabulary), "decor_misc")
        return {"kind": kind, "mount": "none", "settings": list(pack), "hung": True}
    rules = _ROOM_RULES
    if words & {"lamp", "lampstand", "floorlamp", "desklamp", "tablelamp"}:
        # A table lamp is a lamp, not a table: lamp rules first.
        rules = tuple(r for r in _ROOM_RULES if r[0] in ("lamp_floor", "lamp_small")) + _ROOM_RULES
    for kind, vocabulary, mount, settings in rules:
        if words & vocabulary:
            if kind == "lamp_small" and (words & {"floor", "stand"}) and height >= 1.0:
                kind, mount, settings = "lamp_floor", "corner", ("living", "bedroom")
            if kind in _LARGE_KINDS and small and kind not in ("chair", "stool"):
                return {"kind": "other", "mount": "none", "settings": list(pack), "part": True}
            if kind == "shelf_tall" and height and height < 1.2:
                kind = "shelf_low"
            if kind == "table" and height and height < 0.55:
                kind, mount, settings = "coffee_table", "center", ("living",)
            if kind == "plant_pot" and height > 1.8:
                mount = "corner"
            return {"kind": kind, "mount": mount, "settings": _named_room(words) or list(dict.fromkeys((*settings, *pack)))}
    if words & _FOOD or catalogue == "kenney_food":
        return {"kind": "decor_food", "mount": "counter_top", "settings": ["bakery", "bar", "grocery", "kitchen", "dining"]}
    if words & _TABLEWARE:
        return {"kind": "decor_tableware", "mount": "table_top", "settings": ["bar", "kitchen", "dining", "bakery"]}
    if words & _MISC or small:
        return {"kind": "decor_misc", "mount": "table_top", "settings": ["living", "bedroom", "office", "shop"]}
    return {"kind": "other", "mount": "none", "settings": list(pack)}


def _outdoor(words: set[str], row: Mapping[str, Any]) -> dict[str, Any]:
    role = str(row.get("role") or "")
    placement = str(row.get("placement") or "")
    category = str(row.get("category") or "")
    kind = _OUTDOOR_BY_ROLE.get(role)
    if kind is None:
        kind = next((k for word, k in _OUTDOOR_BY_ROLE.items() if word in words), "other")
    if placement == "kit_only":
        kind, mount = "kit_piece", "none"
    elif row.get("anchor") == "facade" or placement == "wall_mounted":
        mount = "facade"
    else:
        mount = "ground"
    surfaces = set(row.get("surfaces") or ())
    settings = []
    if surfaces & {"carriageway", "curb", "sidewalk", "plaza"} or placement in ("street", "kerb"):
        settings.append("street")
    if surfaces & {"yard", "courtyard", "empty_lot"}:
        settings.append("yard")
    if surfaces & {"park"} or category == "vegetation":
        settings.append("park")
    if category in ("waste", "structures") and kind in ("barrel", "crate", "pallet", "dumpster", "structure"):
        settings.append("industrial")
    return {"kind": kind, "mount": mount, "settings": list(dict.fromkeys(settings)) or ["street"]}


def describe(row: Mapping[str, Any], catalogue: str) -> dict[str, Any]:
    """The ``semantic`` block of one manifest row of ``catalogue``; a reviewed block is returned as it is."""
    previous = row.get("semantic")
    if isinstance(previous, Mapping) and previous.get("origin") == "reviewed":
        return dict(previous)
    file_words = words_of(str(row.get("file") or "").rsplit("/", 1)[-1].rsplit(".", 1)[0])
    # A local Asset Store id carries its package's name (as_<package>_<model>): "kitchen" in every row of a kitchen
    # pack. Its file name is the model's own name; the shared catalogues' ids are that name with a short prefix.
    words = file_words if str(row.get("id") or "").startswith("as_") else words_of(_stem(str(row.get("id") or ""))) | file_words
    block = _room(words, row, catalogue) if row.get("placement") == "interior" else _outdoor(words, row)
    return {**block, "origin": "estimated", "basis": "name, catalogue, role, placement and size (overgrow/semantics.py)"}


def opaque(glb: bytes) -> bool:
    """Whether every material of a GLB is opaque: the export draws opaque only, so the libraries refuse the rest."""
    from onward_generator.generation.gltf import read_glb  # the GLB reader pulls numpy; keep describe() light

    doc, _blob = read_glb(glb)
    return all(str(m.get("alphaMode") or "OPAQUE") == "OPAQUE" for m in doc.get("materials") or [])


def annotate(document: dict[str, Any], catalogue: str, folder: Path | None = None) -> dict[str, Any]:
    """The manifest with a ``semantic`` block on every row (rows reviewed by a person keep theirs).

    With ``folder`` (the catalogue's directory) each row's GLB is opened: a model with a non-opaque material, which
    the libraries refuse, is never placed (``mount: none``, ``transparent: true``).
    """
    rows = []
    for row in document.get("assets") or []:
        if not (isinstance(row, dict) and row.get("id")):
            rows.append(row)
            continue
        block = describe(row, catalogue)
        file = folder / str(row.get("file") or "") if folder is not None else None
        if file is not None and file.is_file() and block.get("origin") != "reviewed" and not opaque(file.read_bytes()):
            block = {**block, "mount": "none", "transparent": True}
        rows.append({**row, "semantic": block})
    return {**document, "assets": rows}


def counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """How many rows of each kind: what ``library describe`` prints."""
    found: dict[str, int] = {}
    for row in rows:
        kind = str((row.get("semantic") or {}).get("kind", "none"))
        found[kind] = found.get(kind, 0) + 1
    return dict(sorted(found.items(), key=lambda item: (-item[1], item[0])))
