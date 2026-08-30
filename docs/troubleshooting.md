# Troubleshooting

## No keyboards are listed

Run the wizard inside the active Omarchy desktop session. It needs access to the
Hyprland socket used by:

```bash
hyprctl -j devices
```

Remote shells and TTYs commonly lack the required Hyprland environment.

## A keyboard is missing

The picker intentionally filters media controls, headsets, power buttons,
virtual input devices, and control-only USB endpoints. Check whether the device
has normal typing capabilities in `/proc/bus/input/devices` and appears in
`hyprctl -j devices`.

## A key reports “needs attention”

The wizard automatically corrects common two-key modifier swaps. Arbitrary
firmware layers and multi-key permutations require a custom XKB symbols file or
a lower-level remapper. The wizard still allows the selected layout to be
applied without guessing at an unsafe remap.

## A requested character is not accepted

Type the character as you normally would, including Shift or AltGr. The wizard
waits for the entire chord to be released. On a dead-key layout, tilde may need
the tilde dead key followed by Space.

The panel compares the actual text produced by the selected XKB layout. If the
wrong symbol appears—or the chord produces no text—release the chord and choose
**Use this key for …** to create an override. You can instead type another chord,
or skip the character and apply the layout with a warning. Overrides affect only
the primary layout.

## A character override is rejected

Character overrides support normal, Shift, Right Alt/AltGr, and
Shift+Right-Alt levels. Ctrl, left-Alt, and Super/Meta chords are rejected. A
single physical chord can produce only one character, so assigning two requested
characters to the same chord is also rejected during review.

If `xkbcli` rejects the generated keymap, select a standard layout/variant and
scan again. No configuration is written until the generated keymap compiles.

## The panel does not receive bare modifier keys

Make sure the plugin is opened through `omarchy-shell`, not by running the QML
file as a standalone window:

```bash
omarchy-shell shell summon tar5.keyboard-wizard '{}'
```

The Omarchy panel host gives the layer surface exclusive keyboard focus while
the wizard is visible.

## The layout changed but the bar indicator did not appear

The Omarchy keyboard-layout widget hides itself when only one layout is active.
With two layouts configured, reload Hyprland and click the indicator to cycle:

```bash
hyprctl reload
hyprctl configerrors
```

## Validation could not reach Hyprland

The wizard has already saved the file and backup. From a terminal inside the
desktop session, run:

```bash
hyprctl reload
hyprctl configerrors
```

No output from `hyprctl configerrors` means the configuration is clean.

## Restore a backup manually

Backups are stored beside the input file:

```text
~/.config/hypr/input.lua.keyboard-wizard.bak.YYYYMMDD-HHMMSS-ffffff
~/.config/hypr/keyboard-wizard/*.xkb.keyboard-wizard.bak.YYYYMMDD-HHMMSS-ffffff
```

Restore both the desired keymap backup (when `kb_file` is present) and the input
configuration backup, then run the validation commands above.
