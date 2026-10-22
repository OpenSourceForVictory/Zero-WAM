#!/usr/bin/env bash
set -euo pipefail
export CUDA_VISIBLE_DEVICES=""
export OMP_NUM_THREADS=4 MKL_NUM_THREADS=4
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec /home/huawei/.venvs/zero-wam/bin/python -u \
 /home/huawei/Zero-WAM/tools/prediction_decoder/decode_predictions.py \
 --root /home/huawei/Zero-WAM/outputs/piperpika_prediction_traces \
 --checkpoint /data/Zero-WAM/models/piperpika_checkpoint_eval_20261021/best_model \
 --since "${DECODER_SINCE:-0}" "$@"
