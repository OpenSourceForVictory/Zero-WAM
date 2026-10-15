#!/usr/bin/env bash
# Explicitly invoked recovery + sequential homing for this installed Piper pair.
set -euo pipefail
cd "$(dirname "$0")"
mode="${1:---check}"
if [[ $# -gt 1 || ( "$mode" != --check && "$mode" != --execute ) ]]; then
  echo 'Usage: bash recover_and_zero.sh [--check|--execute]' >&2
  exit 2
fi
exec 9> .recover_and_zero.lock
flock -n 9 || { echo 'Another recovery/homing is running' >&2; exit 1; }
if pgrep -f '[p]ython.*(real_robot_inference_merged.py|go_zero.py|recover_control.py)' >/dev/null; then
  echo 'A robot client is running; stop it before recovery/homing.' >&2
  exit 1
fi
log_dir="recovery_checks/$(date +%Y%m%d_%H%M%S)_recover_and_zero_$$"
mkdir -p "$log_dir"
exec > >(tee "$log_dir/console.log") 2>&1
echo "Mode: $mode; logs: $PWD/$log_dir"
if [[ "$mode" == --check ]]; then
  # No CAN activation, reset, enable, software-stop changes or joint targets.
  bash run.sh recover_control.py --output "$log_dir/preflight"
  bash run.sh arm.py status --arm left
  bash run.sh arm.py status --arm right
  echo 'CHECK PASSED; no hardware commands sent. Execute with --execute when ready.'
  exit 0
fi
finish() {
  result=$?
  trap - EXIT
  # Only a software gate: never electronic-stop/reset/disable here.
  bash inference_client/runtime_config.sh stop || true
  printf '%s\n' "$result" > "$log_dir/exit_code.txt"
  if [[ "$result" != 0 ]]; then
    echo 'FAILED: no automatic retry; inspect logs and onsite hardware before restarting.'
  fi
  exit "$result"
}
trap finish EXIT
# Gate future inference while preparing; this script never launches inference.
bash inference_client/runtime_config.sh stop
bash can_up.sh
# Validate BOTH arms (firmware, feedback, teaching/fault state) before recovering either.
bash run.sh recover_control.py --output "$log_dir/preflight"
bash run.sh recover_control.py --execute --output "$log_dir/recover"
# Clear software gate only for the explicitly requested zero moves.
bash inference_client/runtime_config.sh resume
bash run.sh go_zero.py --arm left --execute --output "$log_dir/left_zero"
bash run.sh go_zero.py --arm right --execute --output "$log_dir/right_zero"
bash run.sh arm.py status --arm left > "$log_dir/final_left.json"
bash run.sh arm.py status --arm right > "$log_dir/final_right.json"
bash run.sh - "$log_dir" <<'PY'
import json
import sys
from pathlib import Path
root = Path(sys.argv[1])
for side in ('left', 'right'):
    data = json.loads((root / f'final_{side}.json').read_text())
    if not all(data['enabled']) or max(map(abs, data['joint_deg'])) > .5:
        raise RuntimeError(f'{side}: final zero/enable verification failed')
    if 'ctrl_mode: CAN_CTRL' not in data['status'] or 'arm_status: NORMAL' not in data['status']:
        raise RuntimeError(f'{side}: final controller state verification failed')
    print(f"{side}: {data['joint_deg']} deg, all enabled, CAN_CTRL/NORMAL")
PY
echo 'SUCCESS: both arms at joint zero (within 0.5 deg), motors remain enabled.'
echo 'No gripper targets or calibration writes. Inference remains stopped.'
