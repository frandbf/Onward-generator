"""One folder per map: name, centre and the paths the rest of the pipeline reads."""

from onward_generator.maps.workspace import MAP_SCHEMA, MAP_VERSION, MapError, create_map, load_map

__all__ = ["MAP_SCHEMA", "MAP_VERSION", "MapError", "create_map", "load_map"]
