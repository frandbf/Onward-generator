"""Phase 03 procedural geometry: ground partition, streets, sidewalks and building volumes.

Independent of the Onward export adapter; outputs are intermediate GLB files
plus a provenance manifest and a validation report.
"""

from onward_generator.generation.config import GenerationConfig, GenerationConfigError, load_generation_config
from onward_generator.generation.scene import GeneratedScene, GenerationError, generate_scene

__all__ = [
    "GeneratedScene",
    "GenerationConfig",
    "GenerationConfigError",
    "GenerationError",
    "generate_scene",
    "load_generation_config",
]
