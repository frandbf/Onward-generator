"""Project configuration: strict JSON loading, JSON Schema and semantic validation."""

from onward_generator.config.diagnostics import Diagnostic
from onward_generator.config.models import GeneratorConfig
from onward_generator.config.validation import (
    ConfigValidationError,
    ValidationResult,
    load_config,
    validate_config_file,
)

__all__ = [
    "ConfigValidationError",
    "Diagnostic",
    "GeneratorConfig",
    "ValidationResult",
    "load_config",
    "validate_config_file",
]
