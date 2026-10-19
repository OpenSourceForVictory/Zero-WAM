#!/usr/bin/env bash
set -euo pipefail
cd /mnt/sfs_turbo/lijianwen/Codes/Zero-WAM
export ASCEND_RT_VISIBLE_DEVICES=${DECODER_NPU:-5}
export OMP_NUM_THREADS=2
exec /mnt/sfs_turbo/public/apps/miniforge3/envs/lingbot-vggt/bin/python -u \
  -m evaluation.piperpika.decode_predictions \
  --root outputs/prediction_traces \
  --checkpoint outputs/piperpika_checkpoint_eval_final/best_model --device npu:0 "$@"
