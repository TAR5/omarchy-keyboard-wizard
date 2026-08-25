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
~/.config/hypr/input.lua.keyboard-wizard.bak.YYYYMMDD-HHMMSS
```

Copy the desired backup over `~/.config/hypr/input.lua`, then run the validation
commands above.
