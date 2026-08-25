#!/usr/bin/env bash
set -euo pipefail

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
app_file="$data_home/omarchy-keyboard-wizard/wizard.py"
app_dir="$data_home/omarchy-keyboard-wizard"
desktop_file="$data_home/applications/io.github.tar5.OmarchyKeyboardWizard.desktop"
bin_file="$HOME/.local/bin/omarchy-keyboard-wizard"

for target in "$app_file" "$desktop_file" "$bin_file"; do
  if [[ -e "$target" ]]; then
    rm -- "$target"
  fi
done

if [[ -d "$app_dir" ]] && [[ -z "$(find "$app_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  rmdir -- "$app_dir"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$data_home/applications"
fi

printf 'Uninstalled Keyboard Setup. Hyprland input settings and backups were left untouched.\n'
