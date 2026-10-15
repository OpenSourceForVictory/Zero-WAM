#!/usr/bin/env bash
# Use a dedicated LeRobot v3.0 environment; do not change the control environment.
set -euo pipefail
cd "$(dirname "$0")"
: "${LEROBOT_PYTHON:?Set LEROBOT_PYTHON to a Python environment with LeRobot v3.0}"
exec "$LEROBOT_PYTHON" convert_lerobot.py "$@"
