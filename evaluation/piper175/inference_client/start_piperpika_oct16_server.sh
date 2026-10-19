#!/usr/bin/env bash
set -euo pipefail
cd /mnt/sfs_turbo/lijianwen/Codes/Zero-WAM
export MODEL_PATH="$PWD/outputs/piperpika_checkpoint_eval_final/best_model"
export PIPERPIKA_PREPARED_ROOT=/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_16_zerowam_joint_v2
export ZERO_WAM_PREDICTION_ROOT="$PWD/outputs/prediction_traces"
export OMP_NUM_THREADS=4
export ASCEND_RT_VISIBLE_DEVICES=${ASCEND_RT_VISIBLE_DEVICES:-7}
export PORT=${PORT:-29056}
bash evaluation/piperpika/launch_server.sh --steps 20 --chunk-frames 2 "$@"
