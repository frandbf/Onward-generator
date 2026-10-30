#!/usr/bin/env bash
# Phase 07: Onward export adapter (onward_unity 0.1.0).
#
#   run_export.sh [--platform macos|linux|auto] PACKAGE_DIR step... [--listing MODE]
#   steps: tool scene launcher push inspect
#
# PACKAGE_DIR is written by `onward-generator export package`; its files are
# verified (Python and C#) before every Unity step. Outputs go to
# PACKAGE_DIR/build/ and are never overwritten.
# "push" replaces the headset's local test map (the SDK Launcher always names it
# unity-export.local.*): it requires CONFIRM_PUSH=yes and backs up the device copy first.
# The listing mode (push only) comes from --listing, else ONWARD_QUEST_LISTING, else
# "local": today's install, the one that loaded Hunt. local-thumb, named and
# named-thumb are unverified candidates for the map's own name and thumbnail
# (ADR-007, amendment 1). Every mode backs up first and never deletes a device file.
# "inspect" is read-only: it lists the game's folder and copies its .info3 files.
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$TOOL_DIR/../lib.sh"
resolve_host "$@"
shift "$HOST_SHIFT"
PROJECT="${PROJECT:-$HOME/Unity/OnwardPhase0Project}"
LOG_DIR="${LOG_DIR:-$LOG_ROOT/onward-export}"
REPO="$(cd "$TOOL_DIR/../../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
DEVICE_DIR="/sdcard/Android/data/com.downpourinteractive.onward/files/CustomContent"

log() { printf '[export] %s\n' "$*"; }
usage() { echo "usage: $0 PACKAGE_DIR (tool|scene|launcher|push|inspect)... [--listing ${QUEST_LISTINGS// /|}]" >&2; exit 2; }

[[ $# -ge 2 ]] || usage
PACKAGE="$(cd "$1" && pwd)"
shift
LISTING="${ONWARD_QUEST_LISTING:-local}"
STEPS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --listing) [[ $# -ge 2 ]] || usage; LISTING="$2"; shift 2 ;;
    --listing=*) LISTING="${1#--listing=}"; shift ;;
    *) STEPS+=("$1"); shift ;;
  esac
done
[[ ${#STEPS[@]} -ge 1 ]] || usage
check_quest_listing "$LISTING"
[[ -f "$PACKAGE/onward_export.json" ]] || { log "$PACKAGE is not an export package"; exit 2; }
PACKAGE_ID="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["package_id"])' "$PACKAGE/onward_export.json")"
[[ "$PACKAGE_ID" =~ ^[A-Za-z0-9_]+$ ]] || { log "invalid package_id '$PACKAGE_ID'"; exit 2; }
BUILD="$PACKAGE/build"

verify() { PYTHONPATH="$REPO/src" "$PYTHON" -m onward_generator export verify "$PACKAGE" >/dev/null || { log "package verification failed"; exit 1; }; }

# Unity can exit 0 after a native crash, so steps also require "[OnwardAdapter] OK".
run_unity() {
  local step="$1"; shift
  local logfile="$LOG_DIR/${PACKAGE_ID}-${step}.log"
  [[ -e "$logfile" ]] && mv "$logfile" "$logfile.$(date +%Y%m%dT%H%M%S)"
  log "step '$step' -> $logfile"
  local code=0 started=$SECONDS
  "$UNITY" -batchmode -projectPath "$PROJECT" -logFile "$logfile" -buildTarget Android "$@" || code=$?
  grep -E '\[OnwardAdapter\]' "$logfile" || true
  if grep -q 'Native Crash Reporting' "$logfile"; then log "step '$step' FAILED after $(elapsed "$started"): native crash"; exit 1; fi
  if [[ $code -ne 0 ]]; then
    grep -E 'error CS|Exception' "$logfile" | tail -20 || true
    log "step '$step' FAILED after $(elapsed "$started") with exit $code"; exit "$code"
  fi
  grep -q '\[OnwardAdapter\] OK' "$logfile" || { log "step '$step' FAILED after $(elapsed "$started"): no OK marker"; exit 1; }
  log "step '$step' OK in $(elapsed "$started")"
}

# Copy only a file that differs: an unchanged script keeps Unity from recompiling and reloading.
copy_if_changed() {
  local src="$1" dst="$2"
  if cmp -s "$src" "$dst"; then return 0; fi
  cp "$src" "$dst"
  COPIED+=("$(basename "$src")")
}

step_tool() {
  mkdir -p "$PROJECT/Assets/OnwardGenerator/Editor/Thumbnails"
  COPIED=()
  copy_if_changed "$TOOL_DIR/Editor/OnwardMapAdapter.cs" "$PROJECT/Assets/OnwardGenerator/Editor/OnwardMapAdapter.cs"
  local thumb="$TOOL_DIR/Editor/Thumbnails/apocalipsis_murciana.png"
  if [[ -f "$thumb" ]]; then
    copy_if_changed "$thumb" "$PROJECT/Assets/OnwardGenerator/Editor/Thumbnails/apocalipsis_murciana.png"
  fi
  local patched
  patched="$("$PYTHON" "$TOOL_DIR/patch_content_builder.py" \
    "$PROJECT/Assets/OnwardCustomContent/Internal/Scripts/Editor/ContentBuilder.cs")"
  log "ContentBuilder.cs patch: $patched"
  log "copied ${#COPIED[@]} changed file(s) into the project: ${COPIED[*]:-none, all unchanged}"
}

step_scene() {
  verify
  [[ -e "$BUILD/scene_report.json" ]] && { log "$BUILD/scene_report.json exists; the scene step runs once per package"; exit 1; }
  mkdir -p "$BUILD"
  run_unity scene -executeMethod OnwardGenerator.OnwardMapAdapter.BuildScene \
    -exportPackage "$PACKAGE" -exportReport "$BUILD/scene_report.json"
  # A GPU lightmapper without a usable OpenCL device bakes on the CPU; Unity says so in its own log.
  # The adapter's own "Lightmapper: ... fallback no" line is left out.
  grep -iE 'fall(ing)? ?back' "$LOG_DIR/${PACKAGE_ID}-scene.log" | grep -v '\[OnwardAdapter\]' | grep -iE 'cpu|lightmapper|opencl' \
    | head -3 | sed 's/^/[export] lightmapper: /' || true
}

step_launcher() {
  verify
  [[ -e "$BUILD/launcher" ]] && { log "$BUILD/launcher exists; launcher output is never overwritten"; exit 1; }
  local report="$BUILD/launcher_report.json"
  [[ -e "$report" ]] && mv "$report" "$report.$(date +%Y%m%dT%H%M%S)"
  run_unity launcher -executeMethod OnwardGenerator.OnwardMapAdapter.BuildQuestViaLauncher \
    -exportPackage "$PACKAGE" -exportOut "$BUILD/launcher" -exportReport "$report"
  ls -l "$BUILD/launcher"
  PYTHONPATH="$REPO/src" "$PYTHON" - "$PACKAGE/onward_export.json" "$PACKAGE/layout.json" "$BUILD/launcher/unity-export.local.info3" <<'PY'
import json, sys
from pathlib import Path
from onward_generator.export.info3 import apply_local_listing
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
layout = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
environment = layout.get("environment") if layout.get("schema") == "onward_generator.gameplay_layout" else None
apply_local_listing(sys.argv[3], title=manifest["map"]["title"], description=manifest["map"]["description"], environment=environment)
print(f"info3 MapName='Unity Export' package_title={manifest['map']['title']!r} environment={environment}")
PY
  local thumb="$PACKAGE/thumbnail.png"
  if [[ ! -f "$thumb" ]]; then
    thumb="$TOOL_DIR/Editor/Thumbnails/apocalipsis_murciana.png"
  fi
  [[ -f "$thumb" ]] && cp "$thumb" "$BUILD/launcher/thumbnail.png"
  file_md5 "$BUILD/launcher/unity-export.local.content3" > "$BUILD/content3.md5"
  file_sha256 "$BUILD/launcher/unity-export.local.content3" "$BUILD/launcher/unity-export.local.info3" > "$BUILD/launcher.sha256"
  log "content3 md5 $(cat "$BUILD/content3.md5")"
}

# Pull each named device file that exists into a new backup folder. Never deletes.
device_backup() {
  local backup
  mkdir -p "$BUILD/device_backup"
  backup="$(mktemp -d "$BUILD/device_backup/$(date +%Y%m%dT%H%M%S)-XXXX")"
  local f
  for f in "$@"; do
    if "$ADB" shell "test -f '$DEVICE_DIR/$f'"; then "$ADB" pull "$DEVICE_DIR/$f" "$backup/$f" >/dev/null; fi
  done
  log "device backup: $backup ($(ls "$backup" | wc -l | tr -d ' ') files)"
  BACKUP="$backup"
}

# Copy one file to the device under NAME and check its MD5 there.
push_checked() {
  local src="$1" name="$2"
  "$ADB" push "$src" "$DEVICE_DIR/$name" >/dev/null
  local want got
  want="$(file_md5 "$src")"
  got="$("$ADB" shell md5sum "$DEVICE_DIR/$name" | cut -d' ' -f1)"
  [[ "$want" == "$got" ]] || { log "md5 mismatch on device for $name: $got != $want"; exit 1; }
}

step_push() {
  [[ "${CONFIRM_PUSH:-}" == "yes" ]] || { log "push replaces the local test map on the headset; re-run with CONFIRM_PUSH=yes"; exit 2; }
  local content="$BUILD/launcher/unity-export.local.content3" info="$BUILD/launcher/unity-export.local.info3"
  [[ -f "$content" && -f "$info" ]] || { log "run the launcher step first"; exit 1; }
  local thumb="$BUILD/launcher/thumbnail.png"
  [[ -f "$thumb" ]] || { log "launcher thumbnail is missing; export package should contain thumbnail.png"; exit 1; }
  local item="unity-export.local" listing="$info"
  if [[ "$LISTING" != "local" ]]; then
    # Checked before the device is touched: a 512x256 PNG, BundleHash = MD5 of the bundle.
    # The listing is a new file under build/listings/; the launcher output is not changed.
    local written
    written="$(PYTHONPATH="$REPO/src" "$PYTHON" - "$PACKAGE/onward_export.json" "$info" "$content" "$thumb" "$BUILD/listings/$LISTING" "$LISTING" <<'PY'
import json, sys
from pathlib import Path
from onward_generator.export.info3 import check_listing_thumbnail, write_candidate_listing
manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
check_listing_thumbnail(sys.argv[4])
path = write_candidate_listing(sys.argv[2], sys.argv[5], mode=sys.argv[6], title=manifest["map"]["title"],
                          map_id=manifest["map"]["id"], content3=sys.argv[3])
print(path.stem)
print(path)
PY
)" || { log "listing for '$LISTING' was refused"; exit 1; }
    item="$(sed -n 1p <<<"$written")"
    listing="$(sed -n 2p <<<"$written")"
    [[ -n "$item" && -f "$listing" ]] || { log "listing for '$LISTING' was not written"; exit 1; }
    log "listing $LISTING: id $item, listing $listing"
  fi
  "$ADB" get-state >/dev/null
  local names=(unity-export.local.content3 unity-export.local.info3 unity-export.local.png)
  [[ "$item" != "unity-export.local" ]] && names+=("$item.content3" "$item.info3" "$item.png")
  device_backup "${names[@]}"
  push_checked "$content" "$item.content3"
  push_checked "$listing" "$item.info3"
  push_checked "$thumb" "$item.png"
  local local_md5
  local_md5="$(file_md5 "$content")"
  printf '%s pushed %s listing %s id %s md5 %s backup %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PACKAGE_ID" "$LISTING" "$item" "$local_md5" "$BACKUP" >> "$BUILD/push.log"
  log "pushed $PACKAGE_ID (listing $LISTING, id $item, md5 $local_md5 verified on device)"
}

# Read-only: the game's folder (names and sizes) and every .info3 in
# CustomContent (small JSON listings), to compare a Workshop download with the
# local listing. Bundles (.content3) are never copied.
step_inspect() {
  "$ADB" get-state >/dev/null
  local out
  out="$BUILD/device_inspect/$(date +%Y%m%dT%H%M%S)"
  mkdir -p "$out"
  "$ADB" shell "ls -laR '${DEVICE_DIR%/CustomContent}'" > "$out/files_tree.txt" 2>&1 || true
  local f
  while IFS= read -r f; do
    f="${f%$'\r'}"
    [[ "$f" == *.info3 ]] || continue
    "$ADB" pull "$DEVICE_DIR/$f" "$out/$f" >/dev/null
  done < <("$ADB" shell "ls '$DEVICE_DIR'")
  log "device inspect: $out ($(ls "$out" | wc -l | tr -d ' ') files)"
}

mkdir -p "$LOG_DIR"
[[ -x "$UNITY" ]] || { log "Unity $UNITY_VERSION not found at $UNITY"; exit 1; }
for step in ${STEPS[@]+"${STEPS[@]}"}; do
  case "$step" in
    tool|scene|launcher|push|inspect) "step_$step" ;;
    *) usage ;;
  esac
done
