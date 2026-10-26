"""Drop the Hunt baker from the scene that the Quest bundle contains.

Onward reads ``LevelDescriptor.AI_NavBake``. The baker object references
``CustomContentNavigationMap``, and the Quest reports that script as missing.
Hunt then runs with no look target. The JSON is copied first. The baker is
removed only for the bundle save, then put back so a second launcher still
finds it.
"""

from __future__ import annotations

import sys
from pathlib import Path

OLD_ASSIGN = """\
                // Wipe existing stuff
                levelDescriptor.AI_NavBake = "";

                if (navMapBaker != null && navMapBaker.NavMap != null)
                {
                    levelDescriptor.AI_NavBake = navMapBaker.NavMap.GetSerializedVersion();
                }
"""

NEW_ASSIGN = """\
                // The Quest reads this JSON. It does not load the baker: that
                // script is reported missing, and Hunt then looks along a zero
                // vector. Copy the nodes, drop the baker from the bundled scene,
                // and restore it after the bundle so a second launcher still
                // finds the asset.
                string huntNavJson = "";
                CustomContentNavigationMap huntNavAsset = null;
                if (navMapBaker != null && navMapBaker.NavMap != null)
                {
                    huntNavJson = navMapBaker.NavMap.GetSerializedVersion();
                    if (string.IsNullOrEmpty(huntNavJson) || huntNavJson.IndexOf("\\"Nodes\\":[{", StringComparison.Ordinal) < 0)
                        throw new Exception("Hunt navigation JSON has no nodes; the Quest would spawn AI with no map.");
                    huntNavAsset = navMapBaker.NavMap;
                    UnityEngine.Object.DestroyImmediate(navMapBaker.gameObject);
                }
                levelDescriptor.AI_NavBake = huntNavJson;
"""

OLD_BUILD = """\
                if (!Build(sceneData.TargetPlatform))
                {
                    return false;
                }
"""

NEW_BUILD = """\
                bool huntBundleBuilt = Build(sceneData.TargetPlatform);
                if (huntNavAsset != null)
                {
                    var restoredNav = new GameObject("NavigationMap");
                    restoredNav.AddComponent<CustomContentNavigationMapGenerator>().NavMap = huntNavAsset;
                    EditorSceneManager.SaveScene(EditorSceneManager.GetActiveScene());
                }
                if (!huntBundleBuilt)
                {
                    return false;
                }
"""


def patch_text(text: str) -> str:
    """Return ContentBuilder.cs with the baker kept out of the bundle."""
    newline = "\r\n" if "\r\n" in text else "\n"
    body = text.replace("\r\n", "\n")
    if NEW_ASSIGN in body and NEW_BUILD in body:
        return text
    if body.count(OLD_ASSIGN) != 1 or body.count(OLD_BUILD) != 1:
        raise ValueError("ContentBuilder.cs does not match SDK v1.15; the navigation bundle patch was not applied")
    body = body.replace(OLD_ASSIGN, NEW_ASSIGN, 1).replace(OLD_BUILD, NEW_BUILD, 1)
    if newline == "\n":
        return body
    return body.replace("\n", newline)


def project_root(path: Path) -> Path:
    """Unity project that contains this script under ``Assets/``."""
    for parent in path.parents:
        if parent.name == "Assets":
            return parent.parent
    raise ValueError(f"{path} is not under an Assets directory")


def patch_file(path: Path) -> str:
    original = path.read_text(encoding="utf-8")
    updated = patch_text(original)
    if updated != original:
        path.write_text(updated, encoding="utf-8", newline="")
        return "applied"
    return "already applied"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: patch_content_builder.py <ContentBuilder.cs>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    if path.name != "ContentBuilder.cs":
        print(f"refusing to patch {path}", file=sys.stderr)
        return 2
    backup = project_root(path) / "ContentBuilder.cs.sdk-v1.15.orig"
    if not backup.is_file():
        backup.write_bytes(path.read_bytes())
    try:
        result = patch_file(path)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
