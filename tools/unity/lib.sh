# shellcheck shell=bash
# Host platform for the run_*.sh scripts. Sourced, never executed.
#
#   source "$TOOL_DIR/../lib.sh"
#   resolve_host "$@"; shift "$HOST_SHIFT"
#
# Precedence (same as src/onward_generator/platform.py):
#   1. --platform macos|linux|auto as the first argument (removed by the caller's shift);
#   2. ONWARD_PLATFORM;
#   3. uname -s (Darwin -> macos, Linux -> linux).
# "auto" counts as not given. Any other value exits with code 2.
# UNITY, ADB, LOG_ROOT and ASSET_STORE_CACHE still override single paths.
# Decision: docs/decisions/ADR-011-host-platform.md.

UNITY_VERSION="2021.3.23f1"
HOST_SHIFT=0

resolve_host() {
  local name=""
  HOST_SHIFT=0
  if [[ "${1:-}" == "--platform" ]]; then
    name="${2:-}"; HOST_SHIFT=2
  elif [[ "${1:-}" == --platform=* ]]; then
    name="${1#--platform=}"; HOST_SHIFT=1
  fi
  [[ $HOST_SHIFT -eq 0 || -n "$name" ]] || { echo "--platform needs a value: auto, macos or linux" >&2; exit 2; }
  [[ "$name" == "auto" ]] && name=""
  if [[ -z "$name" ]]; then
    name="${ONWARD_PLATFORM:-}"
    [[ "$name" == "auto" ]] && name=""
  fi
  if [[ -z "$name" ]]; then
    case "$(uname -s)" in
      Darwin) name=macos ;;
      Linux) name=linux ;;
      *) echo "unsupported host system '$(uname -s)'; pass --platform macos or --platform linux" >&2; exit 2 ;;
    esac
  fi
  local editor
  case "$name" in
    macos)
      editor="/Applications/Unity/Hub/Editor/${UNITY_VERSION}"
      UNITY="${UNITY:-$editor/Unity.app/Contents/MacOS/Unity}"
      ADB="${ADB:-$editor/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb}"
      LOG_ROOT="${LOG_ROOT:-$HOME/Library/Logs}"
      ASSET_STORE_CACHE="${ASSET_STORE_CACHE:-$HOME/Library/Unity/Asset Store-5.x}"
      UNITY_RESOURCES="$(dirname "$UNITY")/../Resources"
      ;;
    linux)
      editor="$HOME/Unity/Hub/Editor/${UNITY_VERSION}/Editor"
      UNITY="${UNITY:-$editor/Unity}"
      ADB="${ADB:-$editor/Data/PlaybackEngines/AndroidPlayer/SDK/platform-tools/adb}"
      LOG_ROOT="${LOG_ROOT:-${XDG_STATE_HOME:-$HOME/.local/state}/onward-generator}"
      ASSET_STORE_CACHE="${ASSET_STORE_CACHE:-$HOME/.local/share/unity3d/Asset Store-5.x}"
      UNITY_RESOURCES="$(dirname "$UNITY")/Data/Resources"
      ;;
    *)
      echo "unknown platform '$name'; use auto, macos or linux" >&2
      exit 2
      ;;
  esac
  ONWARD_PLATFORM="$name"
  export ONWARD_PLATFORM UNITY ADB LOG_ROOT ASSET_STORE_CACHE UNITY_RESOURCES
}

# MD5 of one file as 32 hex characters: `md5 -q` where installed (macOS), else md5sum.
file_md5() {
  if command -v md5 >/dev/null 2>&1; then
    md5 -q "$1"
  else
    md5sum "$1" | cut -d' ' -f1
  fi
}

# SHA-256 lines in the `shasum -a 256` format ("<hex>  <path>") for each argument.
file_sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$@"
  else
    sha256sum "$@"
  fi
}
