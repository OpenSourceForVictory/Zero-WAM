#!/usr/bin/env bash
set -eo pipefail
if [[ $# -lt 1 ]]; then
  echo 'Usage: bash run_human_demo.sh bread_test000 [--prompt breakfast --max_chunks 0]'
  exit 2
fi
demo="$1"
shift
case "$demo" in
  bread_000|bread_002|bread_003|bread_004) demo_split=run_train ;;
  bread_test000|bread_test001) demo_split=run_eval ;;
  *) echo "Unknown demonstration: $demo"; exit 2 ;;
esac
source /opt/ros/noetic/setup.bash
source /home/agilex/miniconda3/etc/profile.d/conda.sh
conda activate piper_new
export LD_PRELOAD="/usr/lib/x86_64-linux-gnu/libffi.so.7${LD_PRELOAD:+:$LD_PRELOAD}"
unset ZERO_WAM_ICL_ID
export ZERO_WAM_ICL_LATENT_PATH="/mnt/sfs_turbo/lijianwen/Datasets/aloha_zerowam_v3/human_latents/${demo_split}/${demo}.pth"
cd /home/agilex/models/lingbot-va-piper/client
python - <<'PY'
from websockets.sync.client import connect
with connect('ws://127.0.0.1:39056',open_timeout=10,close_timeout=3) as ws:
    ws.recv(timeout=10)
print('Inference tunnel ready')
PY
exec python -u real_robot_inference_merged.py --prompt breakfast --control_mode movej --max_chunks 0 "$@"
