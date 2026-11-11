"""Catalogue rows for exported Asset Store models, from the model name, the package category and the measured size.

The semantics are ``estimated`` from words, as for the Kenney and Quaternius imports: no person reviewed a rendered view.
A model is skipped, with its reason, when it is a building or room kit piece (a kit building never replaces an OSM
footprint), when its name says nothing the generator places, or when its size is not a street object. Interior rows
that fit a furnishing slot carry a ``furnish`` hint (ADR-035) that ``detail`` may add to its default sets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

KIT_WORDS = frozenset({
    "wall", "walls", "floor", "floors", "roof", "ceiling", "window", "windows", "door", "doors", "stair", "stairs", "staircase",
    "column", "pillar", "tile", "tiles", "modular", "corner", "beam", "facade", "building", "house", "room", "plane", "ground",
    "terrain", "road", "street", "sidewalk", "pavement", "skybox", "camera", "particle", "fx", "decal", "collider", "trim",
    "frame_wall", "baseboard", "skirting", "railing", "balcony", "garage_door", "hangar", "kiosk", "shop", "store", "cabin", "tower",
})
VEHICLE_WORDS = frozenset({"car", "truck", "van", "bus", "sedan", "taxi", "police", "pickup", "jeep", "suv", "vehicle", "ambulance",
                           "wagon", "lorry", "hatchback", "coupe", "minivan", "cargo", "scout", "zil", "humvee"})
STREET_WORDS = frozenset({"cone", "barrier", "hydrant", "meter", "dumpster", "bollard", "streetlight", "lamppost", "roadblock",
                          "jersey", "manhole", "drain", "antenna", "satellite", "dish", "tire", "tyre", "trafficlight", "traffic",
                          "post", "hydrent", "mailbox", "phonebooth", "bench"})
STORAGE_WORDS = frozenset({"barrel", "drum", "crate", "box", "boxes", "pallet", "bin", "trash", "trashcan", "garbage", "can",
                           "bag", "sack", "container", "tank", "gas", "canister", "jerrycan", "bucket", "giftbox", "suitcase",
                           "luggage", "carton", "basket"})
WALL_WORDS = frozenset({"shelf", "shelves", "shelving", "bookcase", "bookshelf", "cabinet", "cupboard", "drawer", "drawers",
                        "dresser", "wardrobe", "closet", "locker", "lockers", "fridge", "refrigerator", "oven", "stove", "cooker",
                        "sink", "dishwasher", "washer", "washing", "rack", "display", "showcase", "tv", "television", "sideboard",
                        "vending", "freezer", "commode", "chest"})
COUNTER_WORDS = frozenset({"counter", "register", "cashier", "checkout", "desk", "reception", "bar_counter", "till"})
INTERIOR_WORDS = WALL_WORDS | COUNTER_WORDS | frozenset({
    "chair", "stool", "table", "sofa", "couch", "armchair", "bed", "bench_indoor", "lamp", "plant", "plants", "pot", "rug", "carpet",
    "mat", "picture", "pictures", "frame", "painting", "kitchen", "bread", "cake", "cupcake", "croissant", "baguette", "cup", "mug",
    "plate", "bowl", "bottle", "glass", "food", "fruit", "toilet", "bath", "bathtub", "shower", "monitor", "computer", "laptop",
    "microwave", "kettle", "toaster", "blender", "printer", "phone", "book", "books", "vase", "clock", "mirror", "pillow", "nightstand",
    "bedside", "ottoman", "pouf", "coffee", "espresso", "machine", "appliance", "fan", "radiator", "heater", "basket",
    "tray", "rolling", "teapot", "jar", "pan", "cutting", "board", "napkin", "menu",
})
FOOD_WORDS = frozenset({"bread", "cake", "cupcake", "croissant", "baguette", "donut", "pie", "bakery", "pastry", "cookie", "muffin"})
INTERIOR_WORDS = INTERIOR_WORDS | FOOD_WORDS
BAR_WORDS = frozenset({"coffee", "espresso", "cup", "mug", "wine", "glass", "bottle", "beer", "drink", "bar", "cafe", "tea"})
RESIDENTIAL_WALL = frozenset({"bookcase", "bookshelf", "wardrobe", "closet", "dresser", "drawer", "drawers", "commode", "sideboard",
                              "tv", "television", "fridge", "refrigerator", "oven", "stove", "cooker", "sink", "washer", "washing",
                              "dishwasher", "cabinet", "cupboard", "chest", "shelf", "shelves"})
EXTERIOR_HINTS = ("exterior", "urban", "vehicles", "roadways", "industrial", "historic", "vegetation", "city", "street", "road",
                  "parking", "traffic", "village", "survival", "apocalypse")
INTERIOR_HINTS = ("interior", "furniture", "food", "appliances", "electronics", "kitchen", "bedroom", "office", "dining", "bathroom",
                  "coffee", "bakery", "grocery", "shop", "living", "home", "house")
MAX_WALL_DEPTH_M = 0.8
MAX_COUNTER_DEPTH_M = 1.0


@dataclass(frozen=True, slots=True)
class Decision:
    row: dict[str, Any] | None
    reason: str


def words_of(name: str) -> list[str]:
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])", " ", name)
    return [w for w in re.split(r"[^a-z0-9]+", spaced.lower()) if w and not w.isdigit() and len(w) > 1]


def _with_singulars(words: list[str]) -> set[str]:
    """The words plus their singulars, so ``Barrels`` matches ``barrel`` and ``Boxes`` matches ``box``."""
    found = set(words)
    for word in words:
        if word.endswith("es") and len(word) > 4:
            found.add(word[:-2])
        if word.endswith("s") and len(word) > 3:
            found.add(word[:-1])
    return found


def _hint(text: str, hints: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(h in lower for h in hints)


def classify(name: str, size: tuple[float, float, float], package: str, category: str) -> Decision:
    """The catalogue row of one exported model (without id or file), or why it is skipped.

    ``size`` is length (x), depth (y) and height (z) in metres; ``package`` and ``category`` are the package's name
    and its Asset Store category folder, which decide an ambiguous word such as ``barrel`` or ``bin``.
    """
    words = _with_singulars(words_of(name))
    length, depth, height = size
    longest = max(size)
    if words & KIT_WORDS and not words & (INTERIOR_WORDS | VEHICLE_WORDS | STREET_WORDS | STORAGE_WORDS):
        return Decision(None, "kit_piece")
    if longest < 0.03 or longest > 12.0 or height <= 0.0:
        return Decision(None, "size")
    context = f"{package} {category}"
    exterior = _hint(context, EXTERIOR_HINTS) and not _hint(context, INTERIOR_HINTS)
    if words & VEHICLE_WORDS:
        if longest < 2.5:
            return Decision(None, "vehicle_too_small")
        return Decision(_row("vehicles", "vehicle", "street", "ground", ["curb", "carriageway", "sidewalk", "empty_lot"],
                             "single", f"Vehicle ({_label(name)}) from {package}, {longest:.1f} m long.", words), "vehicle")
    if words & STREET_WORDS:
        placement, surfaces = ("kerb", ["curb", "sidewalk", "plaza"]) if height > 0.3 else ("ground_cover", ["sidewalk", "carriageway", "plaza"])
        anchor = "flat" if placement == "ground_cover" else "ground"
        return Decision(_row("barriers", _first(words, STREET_WORDS), placement, anchor, surfaces, "single",
                             f"Street object ({_label(name)}) from {package}, about {longest:.2f} m.", words), "street")
    if words & STORAGE_WORDS and exterior:
        return Decision(_row("waste", _first(words, STORAGE_WORDS), "wall_foot", "ground", ["sidewalk", "yard", "empty_lot", "courtyard"],
                             "cluster", f"Storage or waste item ({_label(name)}) from {package}, about {longest:.2f} m.", words), "storage_outdoor")
    if words & (INTERIOR_WORDS | STORAGE_WORDS):
        row = _row("furniture", "room_furniture", "interior", "ground", ["interior"], "single",
                   f"Interior item ({_label(name)}) from {package}, about {longest:.2f} m on its longest side.", words)
        hint = _furnish_hint(words, length, depth, height, context)
        if hint is not None:
            row["furnish"] = hint
        return Decision(row, "interior")
    return Decision(None, "unclassified")


def _furnish_hint(words: set[str], length: float, depth: float, height: float, context: str) -> dict[str, Any] | None:
    """Where ``detail`` furnishing may use an interior row: a wall or counter slot, the shop sets and the floors."""
    if not 0.3 <= height <= 2.4 or length > 3.0:
        return None
    if words & COUNTER_WORDS and depth <= MAX_COUNTER_DEPTH_M:
        slot = "counter"
    elif words & WALL_WORDS and depth <= MAX_WALL_DEPTH_M:
        slot = "wall"
    else:
        return None
    lower = context.lower()
    sets = ["generic"]
    if words & FOOD_WORDS or "bakery" in lower:
        sets.append("bakery")
    if words & BAR_WORDS or "coffee" in lower or "cafe" in lower:
        sets.append("bar")
    return {"slot": slot, "shop_sets": sorted(sets), "floors": slot == "wall" and bool(words & RESIDENTIAL_WALL)}


def _first(words: set[str], vocabulary: frozenset[str]) -> str:
    """The first vocabulary word of ``words``; a row is only made when there is one, and words are lower-case letters."""
    return sorted(words & vocabulary)[0]


def _label(name: str) -> str:
    return " ".join(words_of(name))[:60]  # a row is only made when the name has a vocabulary word


def _row(category: str, role: str, placement: str, anchor: str, surfaces: list[str], grouping: str, description: str,
         words: set[str]) -> dict[str, Any]:
    tags = [w for w in sorted(words) if re.fullmatch(r"[a-z][a-z_]{1,23}", w)][:4]  # the vocabulary word is always one
    return {
        "category": category, "role": role, "surfaces": surfaces, "anchor": anchor, "lane": "forbid", "clearance_m": 0.0,
        "use": description[:120], "description": description[:140],
        "placement": placement, "grouping": grouping, "condition": "intact", "tags": tags,
    }
