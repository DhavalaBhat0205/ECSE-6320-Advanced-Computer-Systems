#!/usr/bin/env bash
# Run individually pinned, supported-ISA experiments and retain raw provenance.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
MODE=quick
CPU="$(python3 -c 'import os; print(min(os.sched_getaffinity(0)))')"
MAX_MIB=192
REPS=5
TARGET_MS=20
VARIANTS=scalar,auto,avx2,avx512
while (( $# )); do
    case "$1" in
        --quick) MODE=quick; shift ;;
        --full) MODE=full; shift ;;
        --cpu) CPU="${2:?missing CPU}"; shift 2 ;;
        --max-mib) MAX_MIB="${2:?missing MiB cap}"; shift 2 ;;
        --repetitions) REPS="${2:?missing repetitions}"; shift 2 ;;
        --target-ms) TARGET_MS="${2:?missing target-ms}"; shift 2 ;;
        --variants) VARIANTS="${2:?missing variants}"; shift 2 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
[[ "$CPU" =~ ^[0-9]+$ && "$MAX_MIB" =~ ^[0-9]+$ && "$REPS" =~ ^[0-9]+$ ]] || { echo 'Invalid numeric option' >&2; exit 2; }
command -v taskset >/dev/null || { echo 'taskset is required for controlled core pinning' >&2; exit 1; }
taskset -c "$CPU" true || { echo 'Requested CPU is outside the allowed affinity mask' >&2; exit 1; }
mkdir -p data/raw data/processed plots build/reports
make all
RAW='data/raw/measurements.csv'
META='data/raw/environment.txt'
# Archive rather than overwrite previous raw data and corresponding provenance.
if [[ -s "$RAW" || -s "$META" ]]; then
    stamp="$(date -u '+%Y%m%dT%H%M%SZ')-$$"
    mkdir -p data/raw/archive
    if [[ -e "$RAW" ]]; then mv "$RAW" "data/raw/archive/${stamp}-measurements.csv"; fi
    if [[ -e "$META" ]]; then mv "$META" "data/raw/archive/${stamp}-environment.txt"; fi
fi
: > "$RAW"
: > "$META"
record_environment() {
    {
        printf 'Date UTC: '; date -u '+%FT%TZ'
        printf 'Mode: %s\nBuild variants: %s\nCPU affinity core: %s\nMax main-sweep allocation MiB: %s\nRepetitions: %s\nTarget batch ms: %s\n' "$MODE" "$VARIANTS" "$CPU" "$MAX_MIB" "$REPS" "$TARGET_MS"
        printf '\nOperating system:\n'; uname -a
        printf '\nCompiler:\n'; gcc --version | head -n 1
        printf '\nPython:\n'; python3 --version
        printf '\nCPU architecture and cache topology:\n'; lscpu || true
        printf '\nCore SMT state:\n'; cat /sys/devices/system/cpu/smt/active 2>/dev/null || echo unavailable
        printf '\nFrequency governor by core:\n'; cat "/sys/devices/system/cpu/cpu${CPU}/cpufreq/scaling_governor" 2>/dev/null || echo unavailable
        printf '\nFrequency info by core (kHz):\n'; cat "/sys/devices/system/cpu/cpu${CPU}/cpufreq/scaling_cur_freq" 2>/dev/null || echo unavailable
        printf '\nPerf paranoid setting:\n'; cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo unavailable
        printf '\nNUMA nodes:\n'; cat /sys/devices/system/node/online 2>/dev/null || echo unavailable
        printf '\nBuilt exact flags:\n'; make -np all | grep -E '^((COMMON|FPFLAGS|scalar_FLAGS|auto_FLAGS|avx2_FLAGS|avx512_FLAGS) ?[:?+]?=)' | head -n 6 || true
    } > "$META"
}
has_core_flag() {
    awk -v core="$CPU" -v required="$1" '
        $1 == "processor" && $2 == ":" { selected = ($3 == core) }
        selected && $1 == "flags" { for (i = 3; i <= NF; ++i) if ($i == required) found = 1 }
        END { exit !found }
    ' /proc/cpuinfo
}
is_supported() {
    case "$1" in
        avx2) has_core_flag avx2 ;;
        avx512) has_core_flag avx512f ;;
        *) return 0 ;;
    esac
}
FIRST=1
run_case() {
    local build="$1" kernel="$2" dtype="$3" sizes="$4" stride="$5" offset="$6" experiment="$7"
    local -a args=("--kernel" "$kernel" "--dtype" "$dtype" "--sizes" "$sizes" "--stride" "$stride" "--offset-elements" "$offset" "--repetitions" "$REPS" "--target-ms" "$TARGET_MS" "--experiment" "$experiment")
    if (( FIRST == 0 )); then args+=(--no-header); fi
    printf '[%s] %s %s %s N=%s stride=%s offset=%s\n' "$MODE" "$build" "$kernel" "$dtype" "$sizes" "$stride" "$offset" >&2
    taskset -c "$CPU" "build/$build/benchmark" "${args[@]}" >> "$RAW"
    FIRST=0
}
record_environment
IFS="," read -r -a requested_builds <<< "$VARIANTS"
for build in "${requested_builds[@]}"; do
    case "$build" in scalar|auto|avx2|avx512) ;; *) printf "Unsupported build variant: %s\n" "$build" >&2; exit 2;; esac
    if ! is_supported "$build"; then
        printf 'SKIPPED unsupported ISA: %s\n' "$build" | tee -a "$META" >&2
        continue
    fi
    for dtype in f32 f64; do
        for kernel in saxpy dot mul; do
            if [[ "$MODE" == quick ]]; then
                run_case "$build" "$kernel" "$dtype" '257,4096,65536' 1 0 smoke
            else
                cache_sizes="$(python3 scripts/cache_sizes.py --dtype "$dtype" --kernel "$kernel" --max-mib "$MAX_MIB")"
                run_case "$build" "$kernel" "$dtype" "$cache_sizes" 1 0 locality
                # Compare aligned/tail and shifted/tail configurations at fixed strides.
                for offset in 0 1; do
                    run_case "$build" "$kernel" "$dtype" '1024,1025,4096,4097,8192,8193' 1 "$offset" alignment_tail
                done
                # Keep stride experiments small enough to limit physical allocations.
                for stride in 1 2 4 8; do
                    run_case "$build" "$kernel" "$dtype" '8192,131072,524288' "$stride" 0 stride
                done
            fi
        done
    done
done
python3 scripts/parse_perf.py --input "$RAW" --out-dir data/processed
python3 scripts/verify_vectors.py --build-dir build --out build/reports/isa-summary.txt
printf 'Raw data: %s\nEnvironment: %s\nProcessed: data/processed/\n' "$RAW" "$META"
