"""Catastro INSPIRE building data: explicit acquisition, parsing and matching to OSM."""

from onward_generator.cadastre.acquire import (
    CATASTRO_BU,
    CadastreError,
    DownloadRecord,
    bbox_from_scene,
    building_parts_url,
    download_building_parts,
    load_download_record,
    split_bbox,
)
from onward_generator.cadastre.gml import BuildingPart, parse_building_parts, reproject_parts
from onward_generator.cadastre.match import MatchOptions, match_buildings, match_report, overrides_document
from onward_generator.cadastre.zones import CatastroZone, catastro_zone

__all__ = [
    "CATASTRO_BU",
    "BuildingPart",
    "CadastreError",
    "CatastroZone",
    "DownloadRecord",
    "MatchOptions",
    "bbox_from_scene",
    "building_parts_url",
    "catastro_zone",
    "download_building_parts",
    "load_download_record",
    "match_buildings",
    "match_report",
    "overrides_document",
    "parse_building_parts",
    "reproject_parts",
    "split_bbox",
]
