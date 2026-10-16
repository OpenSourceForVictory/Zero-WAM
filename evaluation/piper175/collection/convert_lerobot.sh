#!/usr/bin/env bash
# Use the dedicated export environment; do not change the control environment.
set -euo pipefail
cd "$(dirname "$0")"
: "${LEROBOT_PYTHON:?Set LEROBOT_PYTHON to a Python environment with the export dependencies}"
exec "$LEROBOT_PYTHON" convert_lerobot.py "$@"
