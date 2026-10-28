#!/usr/bin/env bash
# Run on the Raspberry Pi from inside the copied project folder:  sudo bash pi/setup.sh
# Assumes Raspberry Pi OS, user "pi", project in /home/pi/depot.
set -euo pipefail
[ "$EUID" -eq 0 ] || { echo "Run with sudo."; exit 1; }
APP=/home/pi/depot
apt-get update
apt-get install -y hostapd dnsmasq python3-venv openssl rfkill
systemctl disable --now hostapd dnsmasq 2>/dev/null || true
systemctl unmask hostapd 2>/dev/null || true
# Stop the Pi's normal Wi-Fi manager from using wlan0
command -v nmcli >/dev/null && nmcli device set wlan0 managed no || true
grep -q "denyinterfaces wlan0" /etc/dhcpcd.conf 2>/dev/null || echo "denyinterfaces wlan0" >> /etc/dhcpcd.conf || true

mkdir -p "$APP"
cp -r server.py requirements.txt web "$APP"/
chown -R pi:pi "$APP"
sudo -u pi python3 -m venv "$APP/venv"
sudo -u pi "$APP/venv/bin/pip" install -r "$APP/requirements.txt"

install -m 644 pi/hostapd.conf /etc/hostapd/depot.conf
install -m 644 pi/dnsmasq-depot.conf /etc/dnsmasq-depot.conf
install -m 644 pi/depot-ap.service pi/depot-dns.service pi/depot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now depot-ap depot-dns depot
echo "Done. Join Wi-Fi 'depot' (password depot321) and open http://10.42.0.1"
