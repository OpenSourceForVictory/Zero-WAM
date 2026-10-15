#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo 'Usage: bash run_human_demo.sh bread_test000 [--check|--shadow|--execute] [--max_chunks 0]'
  exit 2
fi
demo="$1"
shift
case "$demo" in
  bread_000|bread_002|bread_003|bread_004) demo_split=run_train ;;
  bread_test000|bread_test001) demo_split=run_eval ;;
  *) echo "Unknown demonstration: $demo"; exit 2 ;;
esac
unset ZERO_WAM_ICL_ID
server_data_root="${ZERO_WAM_SERVER_DATA_ROOT:-/mnt/sfs_turbo/lijianwen/Datasets/aloha_zerowam_v3}"
export ZERO_WAM_ICL_LATENT_PATH="${server_data_root}/human_latents/${demo_split}/${demo}.pth"
mode=(--shadow)
for arg in "$@"; do
  case "$arg" in --check|--shadow|--execute) mode=();; esac
done
exec bash "$(dirname "$0")/run.sh" --prompt breakfast "${mode[@]}" "$@"
