#!/usr/bin/env bash
set -e
source /data5T/d00942093/zero-wam/activate.sh
exec python /data5T/d00942093/zero-wam/scripts/infer_npu.py "$@"
