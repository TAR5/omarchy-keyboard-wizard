#!/usr/bin/env bash
set -euo pipefail

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
desktop_file="$data_home/applications/io.github.tar5.OmarchyKeyboardWizard.desktop"
bin_file="$HOME/.local/bin/omarchy-keyboard-wizard"
legacy_app="$data_home/omarchy-keyboard-wizard/wizard.py"
legacy_dir="$data_home/omarchy-keyboard-wizard"

if command -v omarchy >/dev/null 2>&1 \
  && omarchy plugin list --json | grep -Fq '"id":"tar5.keyboard-wizard"'; then
  omarchy plugin remove tar5.keyboard-wizard --yes
fi

for target in "$desktop_file" "$bin_file" "$legacy_app"; do
  if [[ -e "$target" ]]; then
    rm -- "$target"
  fi
done

if [[ -d "$legacy_dir" ]] && [[ -z "$(find "$legacy_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
  rmdir -- "$legacy_dir"
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$data_home/applications"
fi

printf 'Uninstalled Keyboard Setup. Hyprland input settings and backups were left untouched.\n'
