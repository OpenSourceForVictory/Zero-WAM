#!/usr/bin/env bash
set -e
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/script/ascend/activate.sh"
exec python "$ZERO_WAM_ROOT/script/ascend/infer_npu.py" "$@"
