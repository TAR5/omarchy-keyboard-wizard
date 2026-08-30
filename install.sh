#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
bin_dir="$HOME/.local/bin"
desktop_dir="$data_home/applications"
plugin_id="tar5.keyboard-wizard"
plugin_url="https://github.com/TAR5/omarchy-keyboard-wizard.git"

if ! command -v omarchy >/dev/null 2>&1 || ! command -v omarchy-shell >/dev/null 2>&1; then
  printf 'Keyboard Setup requires a current Omarchy installation.\n' >&2
  exit 1
fi

if omarchy plugin list --json | grep -Fq '"id":"tar5.keyboard-wizard"'; then
  omarchy plugin update "$plugin_id" --yes
  omarchy plugin enable "$plugin_id"
else
  omarchy plugin add "$plugin_url" --enable --yes
fi

install -Dm755 "$repo_dir/bin/omarchy-keyboard-wizard" "$bin_dir/omarchy-keyboard-wizard"
install -Dm644 \
  "$repo_dir/data/io.github.tar5.OmarchyKeyboardWizard.desktop" \
  "$desktop_dir/io.github.tar5.OmarchyKeyboardWizard.desktop"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$desktop_dir"
fi

printf 'Installed Keyboard Setup.\n'
printf 'Open it from the app launcher or run: omarchy-keyboard-wizard\n'
