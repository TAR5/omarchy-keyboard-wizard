#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_dir="$HOME/.local/bin"
app_dir="$data_home/omarchy-keyboard-wizard"
desktop_dir="$data_home/applications"

install -Dm755 "$repo_dir/src/wizard.py" "$app_dir/wizard.py"
install -Dm755 "$repo_dir/bin/omarchy-keyboard-wizard" "$bin_dir/omarchy-keyboard-wizard"
install -Dm644 \
  "$repo_dir/data/io.github.tar5.OmarchyKeyboardWizard.desktop" \
  "$desktop_dir/io.github.tar5.OmarchyKeyboardWizard.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$desktop_dir"
fi

printf 'Installed Keyboard Setup.\n'
printf 'Open it from the app launcher or run: omarchy-keyboard-wizard\n'
