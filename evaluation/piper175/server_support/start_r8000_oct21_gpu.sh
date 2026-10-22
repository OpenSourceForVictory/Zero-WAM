#!/usr/bin/env bash
set -euo pipefail

code=/home/huawei/Zero-WAM/releases/piperpika-20261021
model=/data/Zero-WAM/models/piperpika_checkpoint_eval_20261021/best_model
prepared=/data/Zero-WAM/prepared/piperpika_10_16_10_20_zerowam_joint_v1
traces=/home/huawei/Zero-WAM/outputs/piperpika_prediction_traces

test -f "$model/piperpika_action_contract.json"
test -f "$prepared/train/piperpika/meta/action_stats.json"
test -f "$prepared/preparation.json"
mkdir -p "$traces"
cd "$code"

export PYTHON_BIN=/home/huawei/.venvs/zero-wam/bin/python
export MODEL_PATH="$model"
export PIPERPIKA_PREPARED_ROOT="$prepared"
export ZERO_WAM_PREDICTION_ROOT="$traces"
export OMP_NUM_THREADS=4 CUDA_VISIBLE_DEVICES=0 PORT=29056 MASTER_PORT=29611
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
unset ASCEND_RT_VISIBLE_DEVICES

exec bash evaluation/piperpika/launch_server.sh --host 0.0.0.0 --video-steps 4 --action-steps 10 --chunk-frames 2
