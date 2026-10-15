#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONNOUSERSITE=1
unset PYTHONPATH LD_PRELOAD
exec "$HOME/miniconda3/envs/piper_new/bin/python" "$@"
