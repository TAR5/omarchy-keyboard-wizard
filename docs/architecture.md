# Architecture

## Quickshell panel

`Panel.qml` is an Omarchy panel plugin. Omarchy injects the shell API and plugin
manifest, while the manifest source directory locates `src/backend.py`. The
panel uses the installed Omarchy colors, spacing, typography, surfaces, buttons,
and dropdown components rather than carrying a second UI toolkit.

While open, its Wayland layer surface requests exclusive keyboard focus. A
focused QML item handles `Keys.onPressed` and `Keys.onReleased`, including bare
modifier events that terminals cannot observe reliably.

## Input capture

Modifier calibration uses `KeyEvent.nativeScanCode`, not the translated symbol.
This distinction matters when an existing XKB option already corrects a firmware
swap: the visible symbol may look correct, while the scan code still reveals
what the keyboard emitted.

Qt normally reports XKB keycodes on Wayland, which are Linux evdev codes plus
eight. The Python backend accepts both forms and normalizes them before
comparison.

Character verification uses `KeyEvent.text`. The panel tracks every scan code
held in a chord and accepts a match only after all keys are released. That lets
Shift and AltGr combinations settle cleanly. A dead-key sequence can span
multiple presses; for example, tilde may be composed by pressing its dead key
and then Space.

The verified set is `@ { } [ ] > < | ~` and backslash (`\`). These captures are
diagnostic when the chosen XKB layout produces the character. For a mismatch or
a chord that produces no text, the panel retains the produced text, target scan
code, Qt modifier mask, and all held scan codes. Nothing is remapped until the
user selects **Use this key**.

## Python backend

The backend is a small JSON command-line service using only the Python standard
library:

- `state` discovers keyboards and XKB choices and returns the capture steps.
- `review <json>` analyzes scan codes and character output without writing.
- `apply <json>` renders, writes, reloads, validates, and if necessary rolls
  back the Hyprland configuration.
- `self-test` performs dependency-free backend checks.

Each command writes exactly one JSON object to stdout so Quickshell can use a
short-lived `Process` and `StdioCollector` rather than maintaining a daemon.

## Device discovery

Hyprland's `hyprctl -j devices` output includes media controls, power buttons,
virtual keyboards, and other devices alongside physical keyboards. The backend
cross-checks those entries with `/proc/bus/input/devices` and requires normal
typing capabilities such as Enter, A, Z, and Space. It also removes known
control-only endpoints and collapses duplicate USB keyboard endpoints.

## Mapping model

Recognized pair swaps are translated to standard `xkeyboard-config` options:

| Detected swap | XKB option |
|---|---|
| Left Alt / Left Super | `altwin:swap_lalt_lwin` |
| Right Alt / Right Super | `altwin:swap_ralt_rwin` |
| Both Alt / Super pairs | `altwin:swap_alt_win` |
| Caps Lock / Escape | `caps:swapescape` |
| Caps Lock / Left Ctrl | `ctrl:swapcaps` |
| Left Alt / Left Ctrl | `ctrl:swap_lalt_lctl` |
| Right Alt / Right Ctrl | `ctrl:swap_ralt_rctl` |
| Left Super / Left Ctrl | `ctrl:swap_lwin_lctl` |
| Right Super / Right Ctrl | `ctrl:swap_rwin_rctl` |

Unrecognized mappings are reported during review instead of being guessed.

### Character overrides

Accepted character conflicts are compiled into a complete, device-specific XKB
keymap. The backend:

1. Compiles the selected layouts, variants, options, and modifier corrections
   with `xkbcli compile-keymap`.
2. Resolves the captured native scan code to its symbolic XKB key name.
3. Derives level 1–4 from the held Shift and Right Alt/AltGr scan codes, with
   the Qt modifier flags as a fallback.
4. Forces the calibrated physical Right Alt/AltGr scan code to remain an
   `ISO_Level3_Shift` key whenever a level 3 or 4 override is present. This is
   applied after firmware-swap options so Alt/Super-swapped keyboards retain a
   reachable AltGr level.
5. Replaces only the confirmed character level in group 1, preserving
   secondary-layout symbols.
6. Compiles the result again with `xkbcli --test` before allowing it to be
   written.

Ctrl, left-Alt, Super/Meta, missing keycodes, and two characters assigned to the
same chord are rejected during review. Hyprland loads the result through the
device's `kb_file` setting. No background remapper or privileged service is
used.

## Configuration safety

Each keyboard gets a generated block identified by a short SHA-256 digest of
its Hyprland device name. Updating one keyboard replaces only its own marked
block.

The write path is:

1. Generate and validate any required keymap entirely in memory.
2. Read the current keymap and `~/.config/hypr/input.lua`.
3. Create timestamped backups for files that already exist.
4. Write each generated file through a temporary file in the same directory and
   atomically replace it.
5. Run `hyprctl reload` and `hyprctl configerrors`.
6. Restore both the previous keymap and input configuration, then reload again,
   if Hyprland reports errors.

If the compositor socket cannot be reached, the file is retained and the user
is shown the exact manual validation commands.
