#!/usr/bin/env bash
# Run on workstation with an existing local model forward (default :49056).
# Foreground SSH: Ctrl-C closes the relay. Password/key handled by SSH, never stored.
set -euo pipefail
robot="${PIPER_SSH_TARGET:-piperpika@192.168.8.175}"
local_port="${MODEL_LOCAL_PORT:-49056}"
robot_port="${PIPER_MODEL_PORT:-39056}"
for port in "$local_port" "$robot_port"; do
  [[ "$port" =~ ^[0-9]+$ ]] && (( port > 0 && port <= 65535 )) || { echo 'Invalid port' >&2; exit 2; }
done
if ! timeout 3 bash -c 'exec 3<>/dev/tcp/127.0.0.1/"$1"' _ "$local_port"; then
  echo "No model forward at workstation 127.0.0.1:$local_port" >&2
  exit 1
fi
exec ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 -R "127.0.0.1:${robot_port}:127.0.0.1:${local_port}" "$robot"
