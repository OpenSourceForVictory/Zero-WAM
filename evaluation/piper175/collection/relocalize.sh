#!/usr/bin/env bash
set -eo pipefail
cd "$(dirname "$0")"
unset PYTHONNOUSERSITE PYTHONPATH LD_PRELOAD
source /opt/ros/humble/setup.bash
source "$HOME/pika_ros/install/setup.bash"
export LD_PRELOAD="$HOME/miniconda3/envs/pika/lib/libstdc++.so.6"
exec "$HOME/miniconda3/envs/pika/bin/python" -u relocalize.py
