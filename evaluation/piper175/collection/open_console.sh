#!/usr/bin/env bash
set -euo pipefail
systemctl --user start piper-collection-web.service
exec xdg-open http://127.0.0.1:8081/
