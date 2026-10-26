"""Pipeline stage ``materials``: bind licensed texture URLs to catalogue IDs.

Does not regenerate facades, does not apply dress classify, and does not
import into Unity. Writes a new scene directory.
"""

from onward_generator.materials.plan import PlanError, TexturePlan, load_texture_plan
from onward_generator.materials.polyhaven import (
    DownloadError,
    TextureRecord,
    ensure_diffuse_1k,
    resolve_polyhaven_id,
)

__all__ = [
    "DownloadError",
    "PlanError",
    "TexturePlan",
    "TextureRecord",
    "ensure_diffuse_1k",
    "load_texture_plan",
    "resolve_polyhaven_id",
]
