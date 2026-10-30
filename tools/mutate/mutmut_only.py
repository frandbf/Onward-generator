"""The mutmut CLI, mutating only the files in MUTMUT_ONLY_MUTATE (comma-separated globs).

MUTMUT_REUSE_MUTANTS=1 skips the coverage run when the mutants were generated before.

mutmut 3.8 has no command-line flag for ``only_mutate``. Without it, ``mutmut run``
rewrites every file under src/ and traces all of them while it maps tests to
functions, which took most of the time when only a few files changed.
"""

import dataclasses
import os
import sys

from mutmut import configuration
from mutmut.__main__ import cli


def main() -> int:
    only = [p for p in os.environ.get("MUTMUT_ONLY_MUTATE", "").split(",") if p]
    changes: dict[str, object] = {}
    if only:
        changes["only_mutate"] = only
    if os.environ.get("MUTMUT_REUSE_MUTANTS") == "1":
        # The mutants exist already. Covered lines only decide which mutants are generated,
        # and measuring them runs the whole suite under coverage again.
        changes["mutate_only_covered_lines"] = False
    if changes:
        # configuration.config() loads pyproject.toml once and caches it in configuration._config.
        configuration._config = dataclasses.replace(configuration.config(), **changes)
    return cli()


if __name__ == "__main__":
    sys.exit(main())
