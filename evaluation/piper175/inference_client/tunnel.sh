#!/usr/bin/env bash
set -euo pipefail
# Run on 175. Uses SSH login, never stores the password. Foreground tunnel.
exec ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
  -L 127.0.0.1:39056:127.0.0.1:39056 agilex@192.168.8.174
