"""Tag-based building category and landmark detection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from onward_generator.heights.config import UNKNOWN, WILDCARD, HeightConfig


@dataclass(frozen=True, slots=True)
class Classification:
    category: str
    matched_tag: str | None
    landmark_reason: str | None

    @property
    def landmark(self) -> bool:
        return self.landmark_reason is not None


def classify(tags: Mapping[str, str], config: HeightConfig) -> Classification:
    """First configured key whose value maps to a category wins; otherwise ``unknown``."""
    category, matched = UNKNOWN, None
    for key in config.category_key_priority:
        value = tags.get(key)
        mapping = config.categories.get(key)
        if value is None or mapping is None:
            continue
        hit = mapping.get(value, mapping.get(WILDCARD))
        if hit is not None:
            category, matched = hit, f"{key}={value}"
            break

    reason = None
    for key in config.landmark_keys:
        if key in tags:
            reason = f"{key}={tags[key]}"
            break
    if reason is None:
        for key, values in sorted(config.landmark_tags.items()):
            if tags.get(key) in values:
                reason = f"{key}={tags[key]}"
                break
    return Classification(category, matched, reason)
