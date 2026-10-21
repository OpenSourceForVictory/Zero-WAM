#!/usr/bin/env bash
# Run from the Zero-WAM repository with its inference Python environment active.
# No robot connection or motion. Explicit inputs; never picks a checkpoint for you.
set -euo pipefail
if [[ $# -lt 5 || $# -gt 6 ]]; then
  echo 'Usage: bash validate_bread.sh CHECKPOINT CONTRACT OBSERVATION_NPZ BUNDLE NEW_OUTPUT [npu:0|cuda:0]' >&2
  exit 2
fi
tools_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${PWD}${PYTHONPATH:+:${PYTHONPATH}}"
python_bin="${PYTHON_BIN:-python}"
"$python_bin" "$tools_dir/bread_eval.py" check --bundle "$4" --contract "$2"
"$python_bin" "$tools_dir/bread_eval.py" run --checkpoint "$1" --contract "$2" \
  --observation "$3" --bundle "$4" --output "$5" --device "${6:-npu:0}" \
  --prompt 'Follow the task shown in the human demonstration.'
"$python_bin" "$tools_dir/decode_bread_eval.py" --run "$5" --checkpoint "$1" --device "${6:-npu:0}"
