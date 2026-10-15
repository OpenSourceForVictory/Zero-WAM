#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
case "${1:-status}" in
  status) cat runtime_config.json ;;
  stop) printf '{"emergency_stop": true}\n' > runtime_config.json.tmp; mv runtime_config.json.tmp runtime_config.json ;;
  resume) printf '{"emergency_stop": false}\n' > runtime_config.json.tmp; mv runtime_config.json.tmp runtime_config.json ;;
  *) echo 'Usage: bash runtime_config.sh status|stop|resume'; exit 2 ;;
esac
