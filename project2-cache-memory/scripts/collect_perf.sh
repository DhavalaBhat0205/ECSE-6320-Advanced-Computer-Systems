#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v perf >/dev/null || { printf 'perf is unavailable; hardware counters were not collected.\n' >&2; exit 3; }
mkdir -p "$ROOT/evidence"
# Counter availability is privilege- and CPU-dependent; a failing command must not become zero events.
perf stat -x, -e cycles,instructions,cache-references,cache-misses,dTLB-load-misses,dTLB-store-misses \
  -o "$ROOT/evidence/perf_stat.csv" -- "$ROOT/bin/memory_bench" bandwidth 67108864 64 0 100 1 5 4 0 17377
