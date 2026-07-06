#!/usr/bin/env bash
# Start GNG Development Studio's local server (dry-run, local-only, no automation).
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 studio.py
