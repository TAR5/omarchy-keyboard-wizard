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
diagnostic: the chosen XKB layout and variant remain responsible for producing
the characters.

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

## Configuration safety

Each keyboard gets a generated block identified by a short SHA-256 digest of
its Hyprland device name. Updating one keyboard replaces only its own marked
block.

The write path is:

1. Read the current `~/.config/hypr/input.lua`.
2. Create a timestamped backup next to it.
3. Write the updated content to a temporary file in the same directory.
4. Atomically replace the configuration file.
5. Run `hyprctl reload` and `hyprctl configerrors`.
6. Restore the previous content and reload again if Hyprland reports errors.

If the compositor socket cannot be reached, the file is retained and the user
is shown the exact manual validation commands.
