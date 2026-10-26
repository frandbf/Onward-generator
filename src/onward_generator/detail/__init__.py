"""Phase 04: generic facade templates, roofs, urban elements and selected interiors on phase 03 volumes."""

from onward_generator.detail.config import DetailConfig, DetailConfigError, config_from_document, load_detail_config
from onward_generator.detail.scene import DetailedScene, DetailError, detail_scene, load_base

__all__ = [
    "DetailConfig",
    "DetailConfigError",
    "DetailError",
    "DetailedScene",
    "config_from_document",
    "detail_scene",
    "load_base",
    "load_detail_config",
]
