#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mode=${1:---shadow}
case "$mode" in --shadow|--execute|--check) shift || true ;; *) echo 'Usage: bash run_piperpika_eval_group002.sh [--shadow|--execute|--check] [client options]' >&2; exit 2 ;; esac
export ZERO_WAM_ICL_LATENT_PATH=/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_16_zerowam_joint_v2/human_latents/run_eval/group_002.pth
export ZERO_WAM_VIDEO_GUIDANCE_SCALE=1
export ZERO_WAM_ICL_GUIDANCE_SCALE=5
unset ZERO_WAM_ICL_ID
exec bash run.sh "$mode" --action-contract piperpika --record-continuous \
  --prompt 'pick object to the container' --max_chunks 1 "$@"
