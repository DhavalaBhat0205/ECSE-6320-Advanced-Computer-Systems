#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
make all
exec python3 scripts/run_experiments.py --backend auto --profile "${1:-quick}"
