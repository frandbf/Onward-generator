"""Asset Store classification: the whole row of each kind, the size and word boundaries, and the furnishing hints."""

from __future__ import annotations

from typing import Any

import pytest

from onward_generator.assetstore.classify import classify, words_of


def _row(name: str, size: tuple[float, float, float], package: str = "Misc", category: str = "3D ModelsProps") -> dict[str, Any]:
    decision = classify(name, size, package, category)
    assert decision.row is not None, decision.reason
    return decision.row


def _reason(name: str, size: tuple[float, float, float], package: str = "Misc", category: str = "3D ModelsProps") -> str:
    return classify(name, size, package, category).reason


# ---- whole rows ---------------------------------------------------------------------------------------------------------


def test_a_vehicle_row_is_complete() -> None:
    assert _row("Police_Car", (4.6, 1.9, 1.5), "Police Car", "3D ModelsVehiclesLand") == {
        "category": "vehicles",
        "role": "vehicle",
        "surfaces": ["curb", "carriageway", "sidewalk", "empty_lot"],
        "anchor": "ground",
        "lane": "forbid",
        "clearance_m": 0.0,
        "use": "Vehicle (police car) from Police Car, 4.6 m long.",
        "description": "Vehicle (police car) from Police Car, 4.6 m long.",
        "placement": "street",
        "grouping": "single",
        "condition": "intact",
        "tags": ["car", "police"],
    }


def test_a_tall_street_object_stands_on_the_kerb() -> None:
    assert _row("Traffic_Cone", (0.5, 0.5, 0.9), "Street Assets", "3D ModelsPropsExterior") == {
        "category": "barriers",
        "role": "cone",
        "surfaces": ["curb", "sidewalk", "plaza"],
        "anchor": "ground",
        "lane": "forbid",
        "clearance_m": 0.0,
        "use": "Street object (traffic cone) from Street Assets, about 0.90 m.",
        "description": "Street object (traffic cone) from Street Assets, about 0.90 m.",
        "placement": "kerb",
        "grouping": "single",
        "condition": "intact",
        "tags": ["cone", "traffic"],
    }


def test_a_low_street_object_lies_flat_as_ground_cover_up_to_exactly_0_3_m() -> None:
    row = _row("Manhole", (0.8, 0.8, 0.3), "Street Assets", "3D ModelsPropsExterior")
    assert row["placement"] == "ground_cover" and row["anchor"] == "flat"
    assert row["surfaces"] == ["sidewalk", "carriageway", "plaza"]
    assert row["category"] == "barriers" and row["role"] == "manhole" and row["grouping"] == "single"
    assert _row("Manhole", (0.8, 0.8, 0.31), "Street Assets")["placement"] == "kerb"


def test_an_outdoor_storage_row_is_complete() -> None:
    assert _row("Barrel_Rusty", (0.6, 0.6, 0.9), "Barrels", "3D ModelsEnvironmentsIndustrial") == {
        "category": "waste",
        "role": "barrel",
        "surfaces": ["sidewalk", "yard", "empty_lot", "courtyard"],
        "anchor": "ground",
        "lane": "forbid",
        "clearance_m": 0.0,
        "use": "Storage or waste item (barrel rusty) from Barrels, about 0.90 m.",
        "description": "Storage or waste item (barrel rusty) from Barrels, about 0.90 m.",
        "placement": "wall_foot",
        "grouping": "cluster",
        "condition": "intact",
        "tags": ["barrel", "rusty"],
    }


def test_an_interior_row_is_complete() -> None:
    assert _row("Wooden_Chair", (0.5, 0.5, 0.9), "Furniture", "3D ModelsPropsFurniture") == {
        "category": "furniture",
        "role": "room_furniture",
        "surfaces": ["interior"],
        "anchor": "ground",
        "lane": "forbid",
        "clearance_m": 0.0,
        "use": "Interior item (wooden chair) from Furniture, about 0.90 m on its longest side.",
        "description": "Interior item (wooden chair) from Furniture, about 0.90 m on its longest side.",
        "placement": "interior",
        "grouping": "single",
        "condition": "intact",
        "tags": ["chair", "wooden"],
    }


def test_the_role_is_the_first_matching_street_or_storage_word() -> None:
    assert _row("Bollard_Post", (0.2, 0.2, 1.0), "Street")["role"] == "bollard"
    assert _row("Barrel_Crate", (0.6, 0.6, 0.9), "Urban Props")["role"] == "barrel"


# ---- labels, tags and text lengths -------------------------------------------------------------------------------------


def test_the_label_keeps_sixty_characters_of_the_words() -> None:
    name = "_".join(["chair"] + ["abcdefghij"] * 6)
    label = " ".join(words_of(name))[:60]
    assert len(label) == 60
    row = _row(name, (0.5, 0.5, 0.9), "P")
    assert row["description"] == f"Interior item ({label}) from P, about 0.90 m on its longest side."


def test_tags_are_the_first_four_sorted_words() -> None:
    assert _row("Wood_Chair_Red_Big_Old", (0.5, 0.5, 0.9))["tags"] == ["big", "chair", "old", "red"]


def test_use_keeps_120_and_description_140_characters() -> None:
    package = "P" * 100
    row = _row("Chair", (0.5, 0.5, 0.9), package)
    text = f"Interior item (chair) from {package}, about 0.90 m on its longest side."
    assert len(text) > 140
    assert row["use"] == text[:120] and len(row["use"]) == 120
    assert row["description"] == text[:140] and len(row["description"]) == 140


def test_single_letters_and_numbers_are_not_words() -> None:
    assert words_of("A_Chair_7_b") == ["chair"]


# ---- singulars, seen through the tags ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "tags"),
    [
        ("Chair", ["chair"]),
        ("Chairs", ["chair", "chairs"]),
        ("Boxes", ["box", "boxe", "boxes"]),
        ("Cups", ["cup", "cups"]),
        ("Axes_Chair", ["axe", "axes", "chair"]),
    ],
)
def test_the_words_gain_their_singulars(name: str, tags: list[str]) -> None:
    assert _row(name, (0.5, 0.5, 0.9))["tags"] == tags


def test_a_three_letter_word_ending_in_s_keeps_no_singular() -> None:
    assert _row("Bus", (10.0, 2.5, 3.0), "City")["tags"] == ["bus"]


# ---- skips and boundaries ----------------------------------------------------------------------------------------------


def test_a_kit_word_with_a_street_vehicle_or_storage_word_is_not_a_kit_piece() -> None:
    assert _reason("Road_Cone", (0.5, 0.5, 0.9)) == "street"
    assert _reason("Street_Car", (4.0, 1.8, 1.5)) == "vehicle"
    assert _reason("Wall_Barrel", (0.6, 0.6, 0.9)) == "interior"


@pytest.mark.parametrize(
    ("size", "reason"),
    [
        ((0.03, 0.02, 0.02), "interior"),
        ((0.029, 0.02, 0.02), "size"),
        ((1.0, 1.0, 0.0), "size"),
        ((1.0, 1.0, 0.01), "interior"),
    ],
)
def test_the_size_limits_of_a_row(size: tuple[float, float, float], reason: str) -> None:
    assert _reason("Chair", size) == reason


@pytest.mark.parametrize(
    ("longest", "reason"), [(2.49, "vehicle_too_small"), (2.5, "vehicle"), (3.0, "vehicle"), (12.0, "vehicle"), (12.5, "size")]
)
def test_the_vehicle_length_limits(longest: float, reason: str) -> None:
    assert _reason("Car", (longest, 1.0, 1.0), "City") == reason


def test_storage_is_outdoor_only_in_an_exterior_package() -> None:
    assert _reason("Barrel", (0.6, 0.6, 0.9), "Misc", "3D ModelsProps") == "interior"
    assert _reason("Barrel", (0.6, 0.6, 0.9), "Urban Kitchen", "3D ModelsProps") == "interior"
    assert _reason("Basket", (0.4, 0.4, 0.3), "City Props", "3D ModelsProps") == "storage_outdoor"
    assert _reason("Chair", (0.5, 0.5, 0.9), "Urban Props", "3D ModelsProps") == "interior"


# ---- furnishing hints --------------------------------------------------------------------------------------------------


def _hint(name: str, size: tuple[float, float, float], package: str = "Misc") -> dict[str, Any] | None:
    hint: dict[str, Any] | None = _row(name, size, package).get("furnish")
    return hint


def _sets(name: str, package: str = "Misc") -> list[str]:
    hint = _hint(name, (1.0, 0.4, 1.5), package)
    assert hint is not None
    return list(hint["shop_sets"])


@pytest.mark.parametrize(
    ("size", "has_hint"),
    [
        ((1.0, 0.4, 0.3), True),
        ((1.0, 0.4, 0.29), False),
        ((1.0, 0.4, 2.4), True),
        ((1.0, 0.4, 2.5), False),
        ((1.0, 0.4, 3.0), False),
        ((3.0, 0.4, 1.5), True),
        ((3.5, 0.4, 1.5), False),
        ((1.0, 0.8, 1.5), True),
        ((1.0, 0.81, 1.5), False),
    ],
)
def test_a_wall_hint_needs_a_wall_sized_piece(size: tuple[float, float, float], has_hint: bool) -> None:
    assert (_hint("Display_Rack", size) is not None) is has_hint


def test_a_counter_hint_allows_one_metre_of_depth() -> None:
    assert _hint("Cashier_Counter", (1.6, 1.0, 1.0)) == {"slot": "counter", "shop_sets": ["generic"], "floors": False}
    assert _hint("Cashier_Counter", (1.6, 1.01, 1.0)) is None


def test_a_piece_without_a_wall_or_counter_word_has_no_hint() -> None:
    assert _hint("Chair", (0.5, 0.4, 1.0)) is None


def test_the_shop_sets_come_from_the_words_and_the_package() -> None:
    assert _sets("Display_Rack") == ["generic"]
    assert _sets("Cake_Display") == ["bakery", "generic"]
    assert _sets("Display_Rack", "Bakery Pack") == ["bakery", "generic"]
    assert _sets("Display_Rack", "Cafe Interior") == ["bar", "generic"]
    assert _sets("Display_Rack", "Coffee Pack") == ["bar", "generic"]
    assert _sets("Wine_Rack") == ["bar", "generic"]


def test_only_a_residential_wall_piece_serves_the_floors() -> None:
    assert _hint("Display_Rack", (1.0, 0.4, 1.5)) == {"slot": "wall", "shop_sets": ["generic"], "floors": False}
    assert _hint("Kitchen_Shelf", (1.0, 0.4, 1.5)) == {"slot": "wall", "shop_sets": ["generic"], "floors": True}
    assert _hint("Sink_Counter", (1.0, 0.6, 1.0)) == {"slot": "counter", "shop_sets": ["generic"], "floors": False}
