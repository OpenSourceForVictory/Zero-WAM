#!/usr/bin/env bash
# Receive-only CAN capture. Does not enable, move, stop, or reset either arm.
set -euo pipefail
cd "$(dirname "$0")"
seconds="${1:-60}"
if ! [[ "$seconds" =~ ^[1-9][0-9]*$ ]] || (( seconds > 3600 )); then
  echo 'Usage: bash capture_can_passive.sh [seconds: 1..3600]' >&2
  exit 2
fi
read -r left right < <(bash run.sh -c 'from arm import channel_for; print(channel_for("left"), channel_for("right"))')
output="incident_checks/$(date +%Y%m%d_%H%M%S)_passive_can_$$"
mkdir -p "$output"
printf 'left=%s\nright=%s\nseconds=%s\n' "$left" "$right" "$seconds" > "$output/mapping.txt"
ip -details -statistics link show "$left" > "$output/left_before.txt"
ip -details -statistics link show "$right" > "$output/right_before.txt"
echo "Receive-only recording: $output"
rc=0
timeout --signal=INT --kill-after=3s "${seconds}s" candump -L -d -r 1048576 \
  "$left,0:0,#1FFFFFFF" "$right,0:0,#1FFFFFFF" \
  > "$output/frames.log" 2> "$output/capture_errors.log" || rc=$?
ip -details -statistics link show "$left" > "$output/left_after.txt"
ip -details -statistics link show "$right" > "$output/right_after.txt"
printf '%s\n' "$rc" > "$output/capture_exit_code.txt"
wc -l "$output/frames.log"
if [[ "$rc" != 0 && "$rc" != 124 ]]; then exit "$rc"; fi
