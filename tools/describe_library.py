"""Write the ``semantic`` block (overgrow/semantics.py, ADR-036) on every row of every catalogue under ``assets/``.

The shared catalogues are committed with their blocks; the local Asset Store ones (``assets/asset_store_*``,
gitignored) are rewritten in place. A row whose block a person marked ``origin: reviewed`` keeps it. Prints the kinds.
Run: ``python tools/describe_library.py`` (add ``--check`` to fail when a manifest would change).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from onward_generator.io import atomic_write_json
from onward_generator.overgrow.manifest import check_manifest
from onward_generator.overgrow.semantics import annotate, counts

ASSETS = Path(__file__).resolve().parents[1] / "assets"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 when a manifest's blocks are missing or stale")
    args = parser.parse_args(argv)
    stale, rows = [], []
    for manifest in sorted(ASSETS.glob("*/manifest.json")):
        document = json.loads(manifest.read_text(encoding="utf-8"))
        annotated = annotate(document, manifest.parent.name, manifest.parent)
        rows.extend(r for r in annotated.get("assets") or [] if isinstance(r, dict))
        if annotated != document:
            stale.append(manifest.parent.name)
            if not args.check:
                check_manifest(annotated, manifest.parent.name)
                atomic_write_json(manifest, annotated)
    print(f"{len(rows)} rows in {len(list(ASSETS.glob('*/manifest.json')))} catalogues; {len(stale)} manifests {'stale' if args.check else 'rewritten'}")
    print(", ".join(f"{kind} {n}" for kind, n in counts(rows).items()))
    return 1 if args.check and stale else 0


if __name__ == "__main__":
    sys.exit(main())
