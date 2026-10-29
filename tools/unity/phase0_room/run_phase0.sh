#!/usr/bin/env bash
# Phase 0 probe: build Phase0_Room for Quest with the official Onward SDK v1.15.
# Each step runs Unity in batch mode and writes its own log under $LOG_DIR.
# The project and outputs live outside the repository on purpose: the SDK is
# third-party content and must not be committed.
#
#   run_phase0.sh [--platform macos|linux|auto] [all | step...]
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$TOOL_DIR/../lib.sh"
resolve_host "$@"
shift "$HOST_SHIFT"
SDK_PACKAGE="${SDK_PACKAGE:-$HOME/Unity/OnwardSDK/OnwardCustomContent_SDK_v1.15.unitypackage}"
SDK_SHA256="ad1f6d37725dbcbf4928bff19ad5df8a71ed3fe11320979a7ed4aba12c475315"
PROJECT="${PROJECT:-$HOME/Unity/OnwardPhase0Project}"
OUT_DIR="${OUT_DIR:-$HOME/Unity/OnwardPhase0Out}"
LOG_DIR="${LOG_DIR:-$LOG_ROOT/onward-phase0}"

STEPS=(create import patch tool xr configure room launcher)

usage() {
  echo "usage: $0 [all | ${STEPS[*]}]..." >&2
  exit 2
}

log() { printf '[phase0] %s\n' "$*"; }

require_unity() {
  [[ -x "$UNITY" ]] || { log "Unity $UNITY_VERSION not found at $UNITY"; exit 1; }
}

# Unity can exit 0 after a native crash, so steps that run Phase0RoomBuilder
# must also log "[Phase0] OK". The import step has no such marker.
# Lightmap baking needs a graphics device, so -nographics is not used.
run_unity() {
  local step="$1" marker="$2"; shift 2
  local logfile="$LOG_DIR/${step}.log"
  log "step '$step' -> $logfile"
  local code=0
  "$UNITY" -batchmode -projectPath "$PROJECT" -logFile "$logfile" "$@" || code=$?
  grep -E '\[Phase0\]' "$logfile" || true
  if grep -q 'Native Crash Reporting' "$logfile"; then
    log "step '$step' FAILED: Unity native crash (exit $code); see $logfile"
    exit 1
  fi
  if [[ $code -ne 0 ]]; then
    log "step '$step' FAILED with exit $code; see $logfile"
    grep -E 'error CS|Exception|Aborting' "$logfile" | tail -20 || true
    exit "$code"
  fi
  if [[ "$marker" == "marker" ]] && ! grep -q '\[Phase0\] OK' "$logfile"; then
    log "step '$step' FAILED: exit 0 but no '[Phase0] OK' marker; see $logfile"
    exit 1
  fi
  log "step '$step' OK"
}

# The guide's "3D Sample Scene (URP)" template is not bundled with the
# 2021.3.23f1 editor (only 2D, 3D and 2D URP are). This step uses the bundled
# 3D template and pins URP to the version the guide requires.
URP_VERSION="12.1.11"

step_create() {
  [[ -e "$PROJECT" ]] && { log "project already exists at $PROJECT; skipping create"; return; }
  local resources builtin tgz tmp
  resources="$UNITY_RESOURCES/PackageManager"
  builtin="$resources/BuiltInPackages"
  tgz="$(ls "$resources"/ProjectTemplates/com.unity.template.3d-*.tgz 2>/dev/null | head -1 || true)"
  [[ -n "$tgz" ]] || { log "3D template not found in $resources/ProjectTemplates"; exit 1; }
  log "creating project from template $(basename "$tgz") with URP $URP_VERSION"
  tmp="$(mktemp -d)"
  tar -xzf "$tgz" -C "$tmp" --exclude 'package/ProjectData~/Library'
  mkdir -p "$PROJECT"
  cp -R "$tmp/package/ProjectData~/." "$PROJECT/"
  rm -rf "$tmp"
  python3 - "$PROJECT/Packages/manifest.json" "$builtin" "$URP_VERSION" <<'PY'
import json, os, sys
manifest_path, builtin_dir, urp = sys.argv[1:4]
with open(manifest_path) as f:
    manifest = json.load(f)
deps = manifest.setdefault("dependencies", {})
deps["com.unity.render-pipelines.universal"] = urp
for name in sorted(os.listdir(builtin_dir)):
    if name.startswith("com.unity.modules."):
        deps.setdefault(name, "1.0.0")
manifest["dependencies"] = dict(sorted(deps.items()))
with open(manifest_path, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
PY
  log "URP pinned: $(grep -o '"com.unity.render-pipelines.universal": *"[^"]*"' "$PROJECT/Packages/manifest.json")"
}

step_import() {
  local actual
  actual="$(file_sha256 "$SDK_PACKAGE" | cut -d' ' -f1)"
  [[ "$actual" == "$SDK_SHA256" ]] || { log "SDK checksum mismatch: $actual"; exit 1; }
  run_unity import no-marker -importPackage "$SDK_PACKAGE" -quit
}

step_tool() {
  mkdir -p "$PROJECT/Assets/Phase0Tools/Editor"
  cp "$TOOL_DIR/Editor/Phase0RoomBuilder.cs" "$PROJECT/Assets/Phase0Tools/Editor/"
  log "copied Phase0RoomBuilder.cs into the project"
}

# Local deviation from SDK v1.15: OnwardLauncher.cs converts the bundle path
# to Windows separators before File.Copy, which fails on macOS and Linux. Only that
# conversion is removed; the original file is kept outside Assets/.
step_patch() {
  local target="$PROJECT/Assets/OnwardCustomContent/Internal/Scripts/Editor/OnwardLauncher.cs"
  local backup="$PROJECT/OnwardLauncher.cs.sdk-v1.15.orig"
  [[ -f "$target" ]] || { log "OnwardLauncher.cs not found; import the SDK first"; exit 1; }
  [[ -f "$backup" ]] || cp "$target" "$backup"
  python3 - "$target" <<'PY'
import sys
path = sys.argv[1]
old = "ContentBuilder.FullAssetBundlePath).Replace('/', '\\\\');"
new = "ContentBuilder.FullAssetBundlePath);"
with open(path, encoding="utf-8-sig", newline="") as f:
    text = f.read()
if new in text and old not in text:
    print("[phase0] patch already applied")
    sys.exit(0)
if text.count(old) != 1:
    sys.exit("[phase0] expected exactly one Windows-separator conversion; SDK file differs from v1.15")
with open(path, "w", encoding="utf-8-sig", newline="") as f:
    f.write(text.replace(old, new))
print("[phase0] patch applied")
PY
  diff -u "$backup" "$target" || true
}

step_xr()        { run_unity xr        marker -executeMethod OnwardPhase0.Phase0RoomBuilder.InstallXrPackages; }
step_configure() { run_unity configure marker -buildTarget Android -executeMethod OnwardPhase0.Phase0RoomBuilder.ConfigureProject; }
step_room()      { run_unity room      marker -buildTarget Android -executeMethod OnwardPhase0.Phase0RoomBuilder.BuildRoom; }

step_launcher() {
  mkdir -p "$OUT_DIR"
  run_unity launcher marker -buildTarget Android -executeMethod OnwardPhase0.Phase0RoomBuilder.BuildQuestViaLauncher -phase0Out "$OUT_DIR"
  log "output folder:"
  ls -l "$OUT_DIR"
}

main() {
  mkdir -p "$LOG_DIR"
  require_unity
  local requested=("$@")
  [[ ${#requested[@]} -eq 0 || "${requested[0]}" == "all" ]] && requested=("${STEPS[@]}")
  for step in "${requested[@]}"; do
    case "$step" in
      create|import|patch|tool|xr|configure|room|launcher) "step_$step" ;;
      *) usage ;;
    esac
  done
}

main "$@"
