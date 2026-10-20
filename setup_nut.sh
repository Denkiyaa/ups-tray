#!/bin/bash
# Minimal standalone NUT setup for a single USB UPS. Usage: sudo bash setup_nut.sh [ups-name]
# Disables apcupsd (both cannot use the USB device at the same time).
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo: sudo bash $0"; exit 1; }
command -v upsc >/dev/null || { echo "Install NUT first (Arch: pacman -S nut, Debian/Ubuntu: apt install nut)"; exit 1; }

NAME=${1:-ups}
C=/etc/nut
PASS=$(head -c 12 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')

sed -i 's/^MODE=.*/MODE=standalone/' $C/nut.conf
grep -q '^MODE=' $C/nut.conf || echo 'MODE=standalone' >> $C/nut.conf

grep -q "^\[$NAME\]" $C/ups.conf || printf '\n[%s]\n  driver = usbhid-ups\n  port = auto\n' "$NAME" >> $C/ups.conf
grep -q '^LISTEN' $C/upsd.conf || echo 'LISTEN 127.0.0.1 3493' >> $C/upsd.conf

if ! grep -q '^\[upsmon\]' $C/upsd.users; then
  printf '\n[upsmon]\n  password = %s\n  upsmon primary\n' "$PASS" >> $C/upsd.users
  sed -i '/^MONITOR /d' $C/upsmon.conf
  echo "MONITOR $NAME@localhost 1 upsmon $PASS primary" >> $C/upsmon.conf
fi

chown root:nut $C/*.conf $C/upsd.users
chmod 640 $C/*.conf $C/upsd.users

udevadm control --reload-rules
udevadm trigger --subsystem-match=usb --action=add

systemctl disable --now apcupsd 2>/dev/null || true
systemctl enable --now nut-driver-enumerator.service nut-driver-enumerator.path
upsdrvsvcctl resync 2>/dev/null || true
systemctl enable --now nut-driver.target nut-server.service nut-monitor.service nut.target
sleep 3
upsc "$NAME@localhost" 2>&1 | grep -E 'ups.status|battery.charge:|battery.runtime:|input.voltage:|ups.load|device.model'
