# ADR-011 — Host platform: macOS or Linux by flag

Status: accepted · 29 October 2026

## Context

Every host-specific path assumed macOS: the Unity editor and adb under `/Applications`, logs under `~/Library/Logs`, the Asset Store cache under `~/Library/Unity`, and `md5 -q` and `shasum` in the Unity scripts. Running on Linux meant editing paths. The Python stages already run on Linux (CI runs them in Docker).

## Decision

1. **One profile, two hosts.** `src/onward_generator/platform.py` resolves a frozen `HostProfile` (`macos` or `linux`) with the Unity executable, adb, log root, and Asset Store cache. `tools/unity/lib.sh` applies the same rules to the shell scripts.
2. **Precedence.** `--platform macos|linux|auto` first (`auto` counts as not given), then `ONWARD_PLATFORM`, then `platform.system()` or `uname -s`. The global flag goes before the command and is removed from the arguments before a module command sees them. It sets `ONWARD_PLATFORM`, so every child process agrees. `run` also takes `--platform`. A Unity script takes it as its first argument.
3. **Unknown values are errors.** A misspelt platform or an unsupported system exits with `2`. It is never ignored and never falls back to macOS. `doctor` reports it as a failed `host:platform` check.
4. **Single-path overrides keep working.** `UNITY`, `ADB`, `LOG_ROOT`, and `ASSET_STORE_CACHE` override one path after the host is chosen. `LOG_DIR` still overrides one script's log folder. `run` passes `ONWARD_PLATFORM`, `UNITY`, `ADB`, and `LOG_ROOT` to `run_export.sh`, so Python and the script use the same paths.
5. **macOS behaviour does not change.** Its defaults are the previous constants, and its hashes still use `md5 -q` and `shasum -a 256`. Linux uses `md5sum` and `sha256sum` when `shasum` is missing. Both print the same format.
6. **No browser data.** The profile holds no browser or keyring path. A download that needs an account uses that service's API token ([ADR-030](ADR-030-sketchfab-download-api.md)).
7. **No host configuration file.** `generator.schema.json` describes map projects, not machines, and the variable already gives a persistent choice.

## Rejected alternatives

- Detecting only, with no flag. A Linux machine that keeps Unity elsewhere, or a test, could not choose.
- A host section in the project configuration. A map would then carry a machine path.
- Falling back to `adb` on `PATH` when the Unity copy is missing. It would change which adb a macOS run uses without saying so. `ADB=adb` gives that choice explicitly.

## Consequences

- The Linux Unity, adb, and Asset Store paths are the Unity Hub defaults. They are unverified until someone runs Unity 2021.3.23f1 on Linux; `UNITY` and `ADB` cover a different install.
- The Launcher path patch (`run_phase0.sh patch`) is needed on any non-Windows host, so it applies on Linux too.
- Windows is not a host. Adding one needs its own profile, script helpers, and tests.
