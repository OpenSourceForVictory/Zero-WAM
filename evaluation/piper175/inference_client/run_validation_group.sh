#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
group=${1:?Usage: bash run_validation_group.sh 002 [--shadow|--execute] [client options]}
shift
case "$group" in 002|008|016|018|041|048) ;; *) echo 'Allowed validation groups: 002 008 016 018 041 048' >&2; exit 2 ;; esac
export ZERO_WAM_ICL_LATENT_PATH="/mnt/sfs_turbo/lijianwen/Datasets/piperpika_10_16_zerowam_joint_v2/human_latents/run_eval/group_${group}.pth"
export ZERO_WAM_VIDEO_GUIDANCE_SCALE=1
export ZERO_WAM_ICL_GUIDANCE_SCALE=5
unset ZERO_WAM_ICL_ID
mode=${1:---shadow}
if [[ $# -gt 0 ]]; then shift; fi
case "$mode" in --shadow|--execute|--check) ;; *) echo 'Expected --shadow, --execute or --check' >&2; exit 2 ;; esac
exec bash run.sh "$mode" --action-contract piperpika --record-continuous \
  --prompt 'pick object to the container' --max_chunks 1 "$@"
