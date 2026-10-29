#!/usr/bin/env bash
# Phase 05: build a generated Murcia scene for Quest with the official Onward
# SDK (Launcher), copy it to the headset and collect device measurements.
#
#   run_perf.sh [--platform macos|linux|auto] VARIANT_JSON SCENE_DIR LAYOUT_JSON step...
#   steps: tool scene launcher push session-start record drawcalls session-end
#
# Reuses the phase 0 project (SDK v1.15 imported, Launcher patched for POSIX hosts).
# The SDK Launcher always writes the local test map as unity-export.local.*;
# "push" backs up whatever the headset has under that name before replacing it.
set -euo pipefail

TOOL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$TOOL_DIR/../lib.sh"
resolve_host "$@"
shift "$HOST_SHIFT"
PROJECT="${PROJECT:-$HOME/Unity/OnwardPhase0Project}"
OUT_ROOT="${OUT_ROOT:-$HOME/Unity/MurciaPerfOut}"
LOG_DIR="${LOG_DIR:-$LOG_ROOT/murcia-perf}"
DEVICE_DIR="/sdcard/Android/data/com.downpourinteractive.onward/files/CustomContent"
METRICS_DIR="/sdcard/Android/data/com.oculus.ovrmonitormetricsservice/files/CapturedMetrics"
METRICS_RX="com.oculus.ovrmonitormetricsservice/.SettingsBroadcastReceiver"

log() { printf '[perf] %s\n' "$*"; }
usage() { echo "usage: $0 VARIANT_JSON SCENE_DIR LAYOUT_JSON (tool|scene|launcher|push|session-start|record|drawcalls|session-end)..." >&2; exit 2; }

[[ $# -ge 4 ]] || usage
OPTIONS="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
SCENE_DIR="$(cd "$2" && pwd)"
LAYOUT="$(cd "$(dirname "$3")" && pwd)/$(basename "$3")"
shift 3
VARIANT="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["variant"])' "$OPTIONS")"
[[ "$VARIANT" =~ ^[A-Za-z0-9_]+$ ]] || { log "invalid variant name '$VARIANT'"; exit 2; }
OUT="$OUT_ROOT/$VARIANT"

# Unity can exit 0 after a native crash, so steps also require "[MurciaPerf] OK".
# Lightmap baking needs a graphics device, so -nographics is not used.
run_unity() {
  local step="$1"; shift
  local logfile="$LOG_DIR/${VARIANT}-${step}.log"
  log "step '$step' -> $logfile"
  local code=0
  "$UNITY" -batchmode -projectPath "$PROJECT" -logFile "$logfile" -buildTarget Android "$@" || code=$?
  grep -E '\[MurciaPerf\]' "$logfile" || true
  if grep -q 'Native Crash Reporting' "$logfile"; then log "step '$step' FAILED: native crash"; exit 1; fi
  if [[ $code -ne 0 ]]; then
    grep -E 'error CS|Exception' "$logfile" | tail -20 || true
    log "step '$step' FAILED with exit $code"; exit "$code"
  fi
  grep -q '\[MurciaPerf\] OK' "$logfile" || { log "step '$step' FAILED: no OK marker"; exit 1; }
  log "step '$step' OK"
}

step_tool() {
  mkdir -p "$PROJECT/Assets/MurciaPerfTools/Editor"
  cp "$TOOL_DIR/Editor/MurciaPerfBuilder.cs" "$PROJECT/Assets/MurciaPerfTools/Editor/"
  log "copied MurciaPerfBuilder.cs into the project"
}

step_scene() {
  mkdir -p "$OUT"
  cp "$OPTIONS" "$OUT/options.json"
  cp "$LAYOUT" "$OUT/layout.json"
  file_sha256 "$SCENE_DIR/scene_visual.glb" "$SCENE_DIR/scene_collision.glb" > "$OUT/inputs.sha256"
  run_unity scene -executeMethod MurciaPerf.MurciaPerfBuilder.BuildScene \
    -perfScene "$SCENE_DIR" -perfOptions "$OPTIONS" -perfLayout "$LAYOUT" -perfStats "$OUT/unity_stats.json"
}

step_launcher() {
  mkdir -p "$OUT/launcher"
  run_unity launcher -executeMethod MurciaPerf.MurciaPerfBuilder.BuildQuestViaLauncher -perfOptions "$OPTIONS" -perfOut "$OUT/launcher"
  ls -l "$OUT/launcher"
  file_md5 "$OUT/launcher/unity-export.local.content3" > "$OUT/launcher/content3.md5"
}

step_push() {
  local content="$OUT/launcher/unity-export.local.content3" info="$OUT/launcher/unity-export.local.info3"
  [[ -f "$content" && -f "$info" ]] || { log "run the launcher step first"; exit 1; }
  "$ADB" get-state >/dev/null
  local backup
  backup="$OUT_ROOT/device_backup/$(date +%Y%m%dT%H%M%S)"
  mkdir -p "$backup"
  for f in unity-export.local.content3 unity-export.local.info3; do
    if "$ADB" shell "test -f '$DEVICE_DIR/$f'"; then "$ADB" pull "$DEVICE_DIR/$f" "$backup/$f" >/dev/null; fi
  done
  log "device backup: $backup ($(ls "$backup" | wc -l | tr -d ' ') files)"
  "$ADB" push "$content" "$info" "$DEVICE_DIR/" >/dev/null
  local local_md5 device_md5
  local_md5="$(file_md5 "$content")"
  device_md5="$("$ADB" shell md5sum "$DEVICE_DIR/unity-export.local.content3" | cut -d' ' -f1)"
  [[ "$local_md5" == "$device_md5" ]] || { log "md5 mismatch on device: $device_md5 != $local_md5"; exit 1; }
  log "pushed $VARIANT (md5 $local_md5 verified on device)"
}

# Session recorders run in the background until session-end:
#  - logcat streamed live (a dump at the end loses Onward's lines to buffer pruning);
#  - ovrgpuprofiler realtime metrics at 1 Hz: 2 GPU frequency, 4 GPU % utilisation,
#    26 vertices shaded/s, 33 fragments shaded/s.
# DRAWCALLS=1 also enables the driver's detailed profiling for Onward, required by
# the draw-call trace; its overhead is not measured, so such sessions are not used
# for frame-time results.
GPU_METRICS="2,4,26,33"

step_session_start() {
  "$ADB" shell pm list packages | grep -q com.oculus.ovrmonitormetricsservice || { log "OVR Metrics service missing"; exit 1; }
  "$ADB" shell pidof com.downpourinteractive.onward >/dev/null && { log "Onward is running; quit it first (metrics are per app launch)"; exit 1; }
  [[ -f "$OUT/session.pids" ]] && { log "a session is already running ($OUT/session.pids)"; exit 1; }
  local dev="$OUT/device"
  [[ -e "$dev" ]] && mv "$dev" "$OUT/device.$(date +%Y%m%dT%H%M%S)"
  mkdir -p "$dev"
  "$ADB" shell am broadcast -n "$METRICS_RX" -a com.oculus.ovrmonitormetricsservice.ENABLE_CSV >/dev/null
  if [[ "${DRAWCALLS:-0}" == "1" ]]; then
    "$ADB" shell ovrgpuprofiler -e com.downpourinteractive.onward > "$dev/detailed_mode.txt" 2>&1
    echo "detailed_profiling=1" > "$dev/session_mode.txt"
  else
    "$ADB" shell ovrgpuprofiler -d > /dev/null 2>&1 || true
    echo "detailed_profiling=0" > "$dev/session_mode.txt"
  fi
  "$ADB" shell "ls '$METRICS_DIR' 2>/dev/null" > "$dev/metrics_before.txt" || true
  "$ADB" logcat -c
  date -u +%Y-%m-%dT%H:%M:%SZ > "$dev/session_start.txt"
  log "session prepared ($(cat "$dev/session_mode.txt")). Run the 'record' step as a long-lived process, then launch Onward."
}

# Runs in the foreground until session-end stops it: the recorders must outlive the
# command that starts them, so this step is launched as its own long-lived process.
step_record() {
  local dev="$OUT/device"
  [[ -f "$dev/session_start.txt" ]] || { log "run session-start first"; exit 1; }
  "$ADB" logcat -v threadtime > "$dev/logcat.txt" 2>&1 &
  echo $! > "$OUT/session.pids"
  # Draw-call sessions leave the GPU to the trace: one ovrgpuprofiler at a time.
  if ! grep -q "detailed_profiling=1" "$dev/session_mode.txt"; then
    "$ADB" shell "ovrgpuprofiler -r=\"$GPU_METRICS\"" > "$dev/gpuprofiler.txt" 2>&1 &
    echo $! >> "$OUT/session.pids"
  fi
  log "recording logcat and ovrgpuprofiler (pids $(tr '\n' ' ' < "$OUT/session.pids"))"
  wait
}

step_drawcalls() {
  local dev="$OUT/device" n
  grep -q "detailed_profiling=1" "$dev/session_mode.txt" 2>/dev/null || { log "start the session with DRAWCALLS=1"; exit 1; }
  n=$(find "$dev" -maxdepth 1 -name 'drawcalls_*.txt' | wc -l | tr -d ' ')
  # The draw-call trace needs at least one metric ID ("-t -x" alone prints "No metrics
  # to track!"): 1 clocks, 21 vertices shaded. Default trace length 0.1 s.
  "$ADB" shell "ovrgpuprofiler -t -x=\"1,21\"" > "$dev/drawcalls_$n.txt" 2>&1
  date -u +%Y-%m-%dT%H:%M:%SZ >> "$dev/drawcalls_$n.txt"
  log "draw-call trace $n: $(wc -l < "$dev/drawcalls_$n.txt" | tr -d ' ') lines -> $dev/drawcalls_$n.txt"
}

step_session_end() {
  local dev="$OUT/device"
  if [[ -f "$OUT/session.pids" ]]; then
    while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$OUT/session.pids"
    rm -f "$OUT/session.pids"
  fi
  "$ADB" shell "pkill -f ovrgpuprofiler" >/dev/null 2>&1 || true
  if grep -q "detailed_profiling=1" "$dev/session_mode.txt" 2>/dev/null; then "$ADB" shell ovrgpuprofiler -d >/dev/null 2>&1 || true; fi
  "$ADB" shell "ls '$METRICS_DIR' 2>/dev/null" > "$dev/metrics_after.txt" || true
  local new
  new="$(comm -13 <(sort "$dev/metrics_before.txt") <(sort "$dev/metrics_after.txt") || true)"
  date -u +%Y-%m-%dT%H:%M:%SZ > "$dev/session_end.txt"
  [[ -n "$new" ]] || { log "no new OVR Metrics CSV on the device"; exit 1; }
  while IFS= read -r f; do
    [[ -n "$f" ]] && "$ADB" pull "$METRICS_DIR/$f" "$dev/$f" >/dev/null && log "pulled $f"
  done <<< "$new"
  log "logcat: $(wc -l < "$dev/logcat.txt" | tr -d ' ') lines; gpuprofiler: $(grep -sc 'Utilization' "$dev/gpuprofiler.txt" || true) samples"
}

mkdir -p "$LOG_DIR"
[[ -x "$UNITY" ]] || { log "Unity $UNITY_VERSION not found at $UNITY"; exit 1; }
for step in "$@"; do
  case "$step" in
    tool|scene|launcher|push) "step_$step" ;;
    session-start) step_session_start ;;
    record) step_record ;;
    drawcalls) step_drawcalls ;;
    session-end) step_session_end ;;
    *) usage ;;
  esac
done
