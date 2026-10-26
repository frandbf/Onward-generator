"""Document ids written by this generator.

New files use ``onward_generator.<kind>``. Readers also accept
``murcia_generator.<kind>`` so the Belluga map, built before the rename,
still loads. Murcia is that map's place, not the tool's name.
"""

from __future__ import annotations

CURRENT_PREFIX = "onward_generator."
LEGACY_PREFIX = "murcia_generator."


def is_schema(value: object, canonical: str) -> bool:
    """True when ``value`` is the current id or the pre-rename id of the same kind."""
    if not isinstance(value, str):
        return False
    if value == canonical:
        return True
    if canonical.startswith(CURRENT_PREFIX):
        return value == LEGACY_PREFIX + canonical[len(CURRENT_PREFIX):]
    return False
