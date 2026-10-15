#!/usr/bin/env bash
# Dedicated offline-conversion environment; never modifies pika/piper_new.
set -euo pipefail
if [[ "${1:-}" != "--execute" ]]; then
  echo 'Creates ~/miniconda3/envs/pika_export (Python 3.12), LeRobot 0.4.4 and CPU PyTorch.'
  echo 'Run with --execute to install. No arm, ROS, dataset upload or control commands.'
  exit 0
fi
export_env="$HOME/miniconda3/envs/pika_export"
if [[ ! -x "$export_env/bin/python" ]]; then
  "$HOME/miniconda3/bin/conda" create -y -n pika_export python=3.12 pip
fi
"$export_env/bin/python" -m pip install 'torch==2.10.0' 'torchvision==0.25.0' --index-url https://download.pytorch.org/whl/cpu
"$export_env/bin/python" -m pip install 'lerobot==0.4.4' 'av==15.1.0' 'pyarrow==24.0.0' 'h5py==3.16.0'
"$export_env/bin/python" -c 'from lerobot.datasets.lerobot_dataset import CODEBASE_VERSION; assert CODEBASE_VERSION == "v3.0"; print("LeRobot v3.0 ready")'
