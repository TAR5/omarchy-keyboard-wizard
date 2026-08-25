# Architecture

## Input capture

The wizard uses a GTK 4 `EventControllerKey` in the capture phase. GTK receives
bare modifier presses that a terminal normally consumes or represents only as
state changes.

Calibration is based on the hardware keycode rather than the translated keysym.
This distinction matters when an existing XKB option already corrects a firmware
swap: the visible keysym may look correct, but the hardware code still reveals
what the keyboard emitted.

GDK normally reports XKB keycodes on Wayland, which are Linux evdev codes plus
eight. The backend accepts both forms and normalizes them before comparison.

## Device discovery

Hyprland's `hyprctl -j devices` output includes media controls, power buttons,
virtual keyboards, and other devices alongside physical keyboards. The wizard
cross-checks those entries with `/proc/bus/input/devices` and requires normal
typing capabilities such as Enter, A, Z, and Space. It also removes known
control-only endpoints and collapses duplicate USB keyboard endpoints.

## Mapping model

Alphanumeric behavior comes from the selected XKB layout and variant. The key
walkthrough concentrates on setup-relevant control and modifier keys.

Recognized pair swaps are translated to standard `xkeyboard-config` options,
including:

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

Unrecognized mappings are reported in the review screen instead of silently
guessing at a correction.

## Configuration safety

Each keyboard gets a generated block identified by a short SHA-256 digest of
the Hyprland device name. Updating one keyboard replaces only its own marked
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
