#!/usr/bin/env bash
# Run on 175. Never clears a stop flag or starts motion implicitly.
set -euo pipefail
cd "$(dirname "$0")"
action="${1:-check}"
if [[ $# -gt 0 ]]; then shift; fi
case "$action" in
  prepare)
    bash ../can_up.sh
    systemctl --user start piper-cameras.service
    exec bash run.sh --check --skip-server "$@"
    ;;
  check) exec bash run.sh --check "$@" ;;
  shadow|once|execute)
    demo="${1:-bread_test000}"
    if [[ $# -gt 0 ]]; then shift; fi
    case "$action" in
      shadow) exec bash run_human_demo.sh "$demo" --shadow "$@" ;;
      once) exec bash run_human_demo.sh "$demo" --execute --max_chunks 1 "$@" ;;
      execute) exec bash run_human_demo.sh "$demo" --execute "$@" ;;
    esac
    ;;
  stop|resume|status) exec bash runtime_config.sh "$action" ;;
  *) echo 'Usage: bash operate.sh prepare|check|shadow|once|execute|stop|resume|status [demo] [client options]'; exit 2 ;;
esac
