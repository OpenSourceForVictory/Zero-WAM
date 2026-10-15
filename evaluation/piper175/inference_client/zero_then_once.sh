#!/usr/bin/env bash
# Actual motion: caller must ensure site clearance and local emergency-stop access.
set -euo pipefail
cd "$(dirname "$0")"
demo="${1:-bread_test000}"
case "$demo" in bread_000|bread_002|bread_003|bread_004|bread_test000|bread_test001) ;; *) echo 'Unsupported demo' >&2; exit 2;; esac
# Fail before moving if camera/model connectivity is unavailable; does not reset model.
bash run_human_demo.sh "$demo" --check
run_dir="../zero_checks/$(date +%Y%m%d_%H%M%S)_zero_then_once_$$"
mkdir -p "$run_dir"
bash ../run.sh go_zero.py --arm left --execute --output "$run_dir/left"
bash ../run.sh go_zero.py --arm right --execute --output "$run_dir/right"
# The client resets the server and captures NEW frames after both zero moves finish.
exec bash operate.sh once "$demo"
