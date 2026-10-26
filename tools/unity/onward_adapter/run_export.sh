#!/usr/bin/env bash
# Phase 07: Onward export adapter (onward_unity 0.1.0).
#
#   run_export.sh PACKAGE_DIR step...
#   steps: tool scene launcher push
#
# PACKAGE_DIR is written by `onward-generator export package`; its files are
# verified (Python and C#) before every Unity step. Outputs go to
# PACKAGE_DIR/build/ and are never overwritten.
# "push" replaces the headset's local test map (the SDK Launcher always names it
# unity-export.local.*): it requires CONFIRM_PUSH=yes and backs up the device copy first.
set -euo pipefail

UNITY_VERSION="2021.3.23f1"
UNITY="${UNITY:-/Applications/Unity/Hub/Editor/${UNITY_VERSION}/Unity.app/Contents/MacOS/Unity}"
ADB="${ADB:-/Applications/Unity/Hub/Editor/${UNITY_VERSION}/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb}"
PROJECT="${PROJECT:-$HOME/Unity/OnwardPhase0Project}"
LOG_DIR="${LOG_DIR:-$HOME/Library/Logs/onward-export}"
TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$TOOL_DIR/../../.." && pwd)"
PYTHON="${PYTHON:-$REPO/.venv/bin/python}"
DEVICE_DIR="/sdcard/Android/data/com.downpourinteractive.onward/files/CustomContent"

log() { printf '[export] %s\n' "$*"; }
usage() { echo "usage: $0 PACKAGE_DIR (tool|scene|launcher|push)..." >&2; exit 2; }

[[ $# -ge 2 ]] || usage
PACKAGE="$(cd "$1" && pwd)"
shift
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
  local code=0
  "$UNITY" -batchmode -projectPath "$PROJECT" -logFile "$logfile" -buildTarget Android "$@" || code=$?
  grep -E '\[OnwardAdapter\]' "$logfile" || true
  if grep -q 'Native Crash Reporting' "$logfile"; then log "step '$step' FAILED: native crash"; exit 1; fi
  if [[ $code -ne 0 ]]; then
    grep -E 'error CS|Exception' "$logfile" | tail -20 || true
    log "step '$step' FAILED with exit $code"; exit "$code"
  fi
  grep -q '\[OnwardAdapter\] OK' "$logfile" || { log "step '$step' FAILED: no OK marker"; exit 1; }
  log "step '$step' OK"
}

step_tool() {
  mkdir -p "$PROJECT/Assets/OnwardGenerator/Editor/Thumbnails"
  cp "$TOOL_DIR/Editor/OnwardMapAdapter.cs" "$PROJECT/Assets/OnwardGenerator/Editor/"
  local thumb="$TOOL_DIR/Editor/Thumbnails/apocalipsis_murciana.png"
  [[ -f "$thumb" ]] && cp "$thumb" "$PROJECT/Assets/OnwardGenerator/Editor/Thumbnails/"
  "$PYTHON" "$TOOL_DIR/patch_content_builder.py" \
    "$PROJECT/Assets/OnwardCustomContent/Internal/Scripts/Editor/ContentBuilder.cs"
  log "copied OnwardMapAdapter.cs and listing thumbnail into the project"
}

step_scene() {
  verify
  [[ -e "$BUILD/scene_report.json" ]] && { log "$BUILD/scene_report.json exists; the scene step runs once per package"; exit 1; }
  mkdir -p "$BUILD"
  run_unity scene -executeMethod OnwardGenerator.OnwardMapAdapter.BuildScene \
    -exportPackage "$PACKAGE" -exportReport "$BUILD/scene_report.json"
}

step_launcher() {
  verify
  [[ -e "$BUILD/launcher" ]] && { log "$BUILD/launcher exists; launcher output is never overwritten"; exit 1; }
  run_unity launcher -executeMethod OnwardGenerator.OnwardMapAdapter.BuildQuestViaLauncher \
    -exportPackage "$PACKAGE" -exportOut "$BUILD/launcher"
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
  md5 -q "$BUILD/launcher/unity-export.local.content3" > "$BUILD/content3.md5"
  shasum -a 256 "$BUILD/launcher/unity-export.local.content3" "$BUILD/launcher/unity-export.local.info3" > "$BUILD/launcher.sha256"
  log "content3 md5 $(cat "$BUILD/content3.md5")"
}

step_push() {
  [[ "${CONFIRM_PUSH:-}" == "yes" ]] || { log "push replaces the local test map on the headset; re-run with CONFIRM_PUSH=yes"; exit 2; }
  local content="$BUILD/launcher/unity-export.local.content3" info="$BUILD/launcher/unity-export.local.info3"
  [[ -f "$content" && -f "$info" ]] || { log "run the launcher step first"; exit 1; }
  "$ADB" get-state >/dev/null
  local backup
  backup="$BUILD/device_backup/$(date +%Y%m%dT%H%M%S)"
  mkdir -p "$backup"
  for f in unity-export.local.content3 unity-export.local.info3; do
    if "$ADB" shell "test -f '$DEVICE_DIR/$f'"; then "$ADB" pull "$DEVICE_DIR/$f" "$backup/$f" >/dev/null; fi
  done
  log "device backup: $backup ($(ls "$backup" | wc -l | tr -d ' ') files)"
  "$ADB" push "$content" "$info" "$DEVICE_DIR/" >/dev/null
  local thumb="$BUILD/launcher/thumbnail.png"
  [[ -f "$thumb" ]] || { log "launcher thumbnail is missing; export package should contain thumbnail.png"; exit 1; }
  "$ADB" push "$thumb" "$DEVICE_DIR/unity-export.local.png" >/dev/null
  local thumb_md5 device_thumb
  thumb_md5="$(md5 -q "$thumb")"
  device_thumb="$("$ADB" shell md5sum "$DEVICE_DIR/unity-export.local.png" | cut -d' ' -f1)"
  [[ "$thumb_md5" == "$device_thumb" ]] || { log "thumbnail md5 mismatch on device: $device_thumb != $thumb_md5"; exit 1; }
  local local_md5 device_md5
  local_md5="$(md5 -q "$content")"
  device_md5="$("$ADB" shell md5sum "$DEVICE_DIR/unity-export.local.content3" | cut -d' ' -f1)"
  [[ "$local_md5" == "$device_md5" ]] || { log "md5 mismatch on device: $device_md5 != $local_md5"; exit 1; }
  printf '%s pushed %s md5 %s backup %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$PACKAGE_ID" "$local_md5" "$backup" >> "$BUILD/push.log"
  log "pushed $PACKAGE_ID (md5 $local_md5 verified on device)"
}

mkdir -p "$LOG_DIR"
[[ -x "$UNITY" ]] || { log "Unity $UNITY_VERSION not found at $UNITY"; exit 1; }
for step in "$@"; do
  case "$step" in
    tool|scene|launcher|push) "step_$step" ;;
    *) usage ;;
  esac
done
