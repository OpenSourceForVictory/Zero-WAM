#!/usr/bin/env bash
set -euo pipefail
# Preserve hardware identity even if Linux enumeration changes after reboot.
for port in 1-2.2:1.0 1-2.1:1.0; do
  found=0
  for path in /sys/class/net/*; do
    [[ "$(basename "$(readlink -f "$path/device")")" == "$port" ]] || continue
    iface="$(basename "$path")"
    found=1
    if ip -details link show "$iface" | grep -q 'bitrate 1000000' && ip link show "$iface" | grep -q 'state UP' && ! ip -details link show "$iface" | grep -q LISTEN-ONLY; then
      echo "$port -> $iface already UP at 1 Mbps"
    else
      sudo ip link set "$iface" down
      sudo ip link set "$iface" type can bitrate 1000000 listen-only off
      sudo ip link set "$iface" up
      echo "$port -> $iface activated at 1 Mbps"
    fi
  done
  [[ "$found" == 1 ]] || { echo "Missing USB-CAN $port" >&2; exit 1; }
done
