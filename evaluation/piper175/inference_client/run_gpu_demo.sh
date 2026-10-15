#!/usr/bin/env bash
set -euo pipefail
# Default human demonstration: run_train/bread_000.
# Explicit demonstration argument still overrides the default.
demo="${ZERO_WAM_HUMAN_DEMO:-bread_000}"
if [[ $# -gt 0 && "$1" != --* ]]; then
  demo="$1"
  shift
fi
export ZERO_WAM_SERVER_DATA_ROOT="${ZERO_WAM_SERVER_DATA_ROOT:-/home/huawei/Datasets/aloha_zerowam_v3}"
exec bash "$(dirname "$0")/run_human_demo.sh" "$demo" \
  --server_host "${ZERO_WAM_SERVER_HOST:-192.168.8.177}" \
  --server_port "${ZERO_WAM_SERVER_PORT:-29056}" "$@"
