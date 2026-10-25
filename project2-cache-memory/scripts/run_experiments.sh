#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-quick}"
[[ "$MODE" == quick || "$MODE" == full ]] || { echo 'usage: run_experiments.sh quick|full' >&2; exit 2; }
mkdir -p "$ROOT/data/raw" "$ROOT/evidence"
"$ROOT/scripts/capture_environment.sh" > "$ROOT/evidence/environment.txt"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
OUT="$ROOT/data/raw/${RUN_ID}.csv"
echo 'experiment,mode,bytes,stride_bytes,random,read_percent,lanes,huge_advice,seed,sample,accesses,elapsed_ns,ns_per_access,useful_gib_s,checksum' > "$OUT"
if [[ "$MODE" == quick ]]; then
  sizes=(32768 262144 2097152 16777216 67108864)
  reps=3
  steps=500000
else
  sizes=(8192 32768 65536 262144 1048576 2097152 8388608 33554432 134217728 268435456)
  reps=7
  steps=2000000
fi
# Pin only when the user supplies an accessible CPU ID; never assume CPU 0 is permitted.
RUN=("$ROOT/bin/memory_bench")
if [[ -n "${BENCH_CPU:-}" ]]; then RUN=(taskset -c "$BENCH_CPU" "$ROOT/bin/memory_bench"); fi
run() {
  local tag="$1" kind="$2" bytes="$3" stride="$4" random="$5" pct="$6" lanes="$7" huge="$8" work="$9"
  "${RUN[@]}" "$kind" "$bytes" "$stride" "$random" "$pct" "$lanes" "$reps" "$work" "$huge" 17377 | awk -v tag="$tag" 'NR>1 {print tag "," $0}' >> "$OUT"
}
for bytes in "${sizes[@]}"; do
  for rand in 0 1; do run locality latency "$bytes" 64 "$rand" 100 1 0 "$steps"; done
  for stride in 64 256 1024; do
    for rand in 0 1; do
      run granularity latency "$bytes" "$stride" "$rand" 100 1 0 "$steps"
      for pct in 0 50 70 100; do run rw_mix bandwidth "$bytes" "$stride" "$rand" "$pct" 1 0 2; done
    done
  done
  for lanes in 1 2 4 8 16; do run intensity mlp "$bytes" 64 1 100 "$lanes" 0 "$((steps / lanes))"; done
  if [[ "$bytes" -ge 2097152 ]]; then
    for huge in 0 1; do run hugepage latency "$bytes" 4096 1 100 1 "$huge" "$steps"; done
  fi
done
printf 'raw measurements: %s\n' "$OUT"
