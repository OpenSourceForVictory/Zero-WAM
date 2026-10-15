#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec "$HOME/miniconda3/envs/pika_export/bin/python" -u web_console.py "$@"
