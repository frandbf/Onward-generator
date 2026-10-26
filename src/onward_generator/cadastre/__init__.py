"""Catastro INSPIRE building data: explicit acquisition, parsing and matching to OSM."""

from onward_generator.cadastre.acquire import (
    CATASTRO_BU,
    CadastreError,
    DownloadRecord,
    bbox_from_scene,
    building_parts_url,
    download_building_parts,
    load_download_record,
)
from onward_generator.cadastre.gml import BuildingPart, parse_building_parts
from onward_generator.cadastre.match import MatchOptions, match_buildings, match_report, overrides_document

__all__ = [
    "CATASTRO_BU",
    "BuildingPart",
    "CadastreError",
    "DownloadRecord",
    "MatchOptions",
    "bbox_from_scene",
    "building_parts_url",
    "download_building_parts",
    "load_download_record",
    "match_buildings",
    "match_report",
    "overrides_document",
    "parse_building_parts",
]
