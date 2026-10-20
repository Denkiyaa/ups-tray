#!/bin/bash
# Per-user install: ./install.sh [--autostart] | ./install.sh --uninstall
set -euo pipefail
cd "$(dirname "$0")"
BIN=~/.local/bin/ups-tray
APPS=~/.local/share/applications
ICON=~/.local/share/icons/hicolor/scalable/apps/ups-tray.svg
AUTO=~/.config/autostart/ups-tray.desktop

if [ "${1:-}" = --uninstall ]; then
  rm -f "$BIN" "$APPS/ups-tray.desktop" "$ICON" "$AUTO"
  echo "Removed. History is kept in ~/.local/share/ups-tray"
  exit 0
fi

python3 -c 'import PyQt6' 2>/dev/null || { echo "PyQt6 missing (Arch: pacman -S python-pyqt6, Debian/Ubuntu: apt install python3-pyqt6)"; exit 1; }
command -v upsc >/dev/null || command -v apcaccess >/dev/null || echo "Warning: neither upsc (NUT) nor apcaccess (apcupsd) found"

install -Dm755 ups_tray.py "$BIN"
install -Dm644 assets/ups-tray.svg "$ICON"
install -Dm644 assets/ups-tray.desktop "$APPS/ups-tray.desktop"
sed -i "s|^Exec=ups-tray|Exec=$BIN|" "$APPS/ups-tray.desktop"
if [ "${1:-}" = --autostart ]; then
  install -Dm644 "$APPS/ups-tray.desktop" "$AUTO"
  sed -i 's| --show||' "$AUTO"
fi
update-desktop-database "$APPS" 2>/dev/null || true
echo "Installed. Launch \"UPS Tray\" from the app menu."
