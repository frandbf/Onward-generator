"""Merge the overrides of several plugins into one file for ``heights`` (ADR-018).

``config/sources.json`` ``priority`` is the only ranking. For each building
the entry of the first ranked source that has one wins whole. When that entry
has ``height_m`` and no ``levels``, ``levels`` comes from the next ranked
source that has them, so a LiDAR height keeps the cadastre storey count for
the facade; its ``source`` and ``license`` then name both. A ``height_m`` is
never added to a levels-only winner: that would put a lower-ranked height
above a higher-ranked storey count in ``heights``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from onward_generator.sources.plugin import SourceError

VERSION = "1.0.0"
MERGED_FILE = "heights_overrides.json"
REPORT_FILE = "sources_report.json"
REPORT_SCHEMA = "onward_generator.sources_report"


def _ranked(documents: Mapping[str, Mapping[str, Any]], priority: Sequence[str]) -> list[str]:
    unranked = sorted(set(documents) - set(priority))
    if unranked:
        raise SourceError(f"overrides from {', '.join(unranked)} have no rank in config/sources.json priority")
    return [source for source in priority if source in documents]


def _combined(winner: Mapping[str, Any], donor: Mapping[str, Any]) -> dict[str, Any]:
    entry = dict(winner)
    entry["levels"] = donor["levels"]
    entry["source"] = f"{winner['source']}; levels: {donor['source']}"
    if donor["license"] != winner["license"]:
        entry["license"] = f"{winner['license']} + {donor['license']}"
    note = f"levels {donor['levels']} from {donor['source']} ({donor['license']})"
    entry["note"] = f"{winner['note']}; {note}" if winner.get("note") else note
    return entry


def merge_overrides(
    documents: Mapping[str, Mapping[str, Any]], priority: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One overrides document from ``{plugin id: overrides document}``, and the counts per source."""
    order = _ranked(documents, priority)
    by_source: dict[str, dict[str, Mapping[str, Any]]] = {}
    for source in order:
        entries: dict[str, Mapping[str, Any]] = {}
        for entry in documents[source]["overrides"]:
            if entry["id"] in entries:
                raise SourceError(f"{source}: duplicate override id {entry['id']}")
            entries[entry["id"]] = entry
        by_source[source] = entries
    won = dict.fromkeys(order, 0)
    lent = dict.fromkeys(order, 0)
    merged: list[dict[str, Any]] = []
    for building in sorted({key for entries in by_source.values() for key in entries}):
        ranked = [source for source in order if building in by_source[source]]
        winner = by_source[ranked[0]][building]
        won[ranked[0]] += 1
        donor = next((s for s in ranked[1:] if by_source[s][building].get("levels") is not None), None)
        if winner.get("height_m") is not None and winner.get("levels") is None and donor is not None:
            merged.append(_combined(winner, by_source[donor][building]))
            lent[donor] += 1
        else:
            merged.append(dict(winner))
    counts = [
        {"source": source, "entries": len(by_source[source]), "won": won[source], "levels_lent": lent[source]}
        for source in order
    ]
    return {"version": VERSION, "overrides": merged}, {"priority": order, "sources": counts, "buildings": len(merged)}
