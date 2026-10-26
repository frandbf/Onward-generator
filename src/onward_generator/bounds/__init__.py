"""Playable envelope: a collision barrier copied onto a generate or detail scene.

The barrier is not drawn. Does not rewrite the phase 03 base. ``detail --base``
must keep pointing at the original generate directory.
"""

from onward_generator.bounds.envelope import BoundsError, apply_bounds

__all__ = ["BoundsError", "apply_bounds"]
