#!/usr/bin/env bash
# Fixed, cross-ISA experimental matrix used for the archived reference-machine study.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
command -v taskset >/dev/null || { echo 'taskset (util-linux) is required' >&2; exit 1; }
CPU="$(python3 -c 'import os; print(min(os.sched_getaffinity(0)))')"
REPS="${REPS:-5}"
TARGET_MS="${TARGET_MS:-8}"
[[ "$REPS" =~ ^[1-9][0-9]*$ && "$TARGET_MS" =~ ^[1-9][0-9]*(\.[0-9]+)?$ ]] || { echo 'Invalid repetition or timing setting' >&2; exit 2; }
make -j4 all
mkdir -p data/raw data/processed build/reports plots
RAW=data/raw/measurements.csv
META=data/raw/environment.txt
if [[ -s "$RAW" || -s "$META" ]]; then
    stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
    mkdir -p data/raw/archive
    [[ ! -e "$RAW" ]] || mv "$RAW" "data/raw/archive/${stamp}-measurements.csv"
    [[ ! -e "$META" ]] || mv "$META" "data/raw/archive/${stamp}-environment.txt"
fi
{
    printf 'Reference campaign run UTC: '; date -u '+%FT%TZ'
    printf 'Runner: scripts/run_recorded_campaign.sh\nPinned logical CPU: %s\nRepetitions per case: %s\nTarget batch duration (ms): %s\n' "$CPU" "$REPS" "$TARGET_MS"
    printf '\nSystem and kernel:\n'; uname -a
    printf '\nCompiler:\n'; gcc --version | head -1
    printf '\nPython:\n'; python3 --version
    printf '\nProcessor/cache/NUMA:\n'; lscpu
    printf '\nLinux perf_event_paranoid:\n'; cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || true
    printf '\nSMT active:\n'; cat /sys/devices/system/cpu/smt/active 2>/dev/null || true
    printf '\nCPU governor:\n'; cat "/sys/devices/system/cpu/cpu${CPU}/cpufreq/scaling_governor" 2>/dev/null || echo 'not exposed'
    printf '\nSystem virtualization:\n'; lscpu | grep -E 'Hypervisor|Virtualization' || true
    printf '\nBuild policy:\n'; grep -E '^(COMMON|FPFLAGS|scalar_FLAGS|auto_FLAGS|avx2_FLAGS|avx512_FLAGS) :=' Makefile
} > "$META"
FIRST=1
run() {
    local build="$1" kernel="$2" dtype="$3" sizes="$4" stride="$5" offset="$6" experiment="$7"
    local args=(--kernel "$kernel" --dtype "$dtype" --sizes "$sizes" --stride "$stride" --offset-elements "$offset" --repetitions "$REPS" --target-ms "$TARGET_MS" --experiment "$experiment")
    if (( FIRST == 0 )); then args+=(--no-header); fi
    echo "$build $kernel $dtype $experiment N=$sizes stride=$stride offset=$offset" >&2
    taskset -c "$CPU" "build/$build/benchmark" "${args[@]}" >> "$RAW"
    FIRST=0
}
has_flag() {
    awk -v required="$1" '$1 == "flags" { for (i = 3; i <= NF; i++) if ($i == required) {found = 1; exit} } END {exit !found}' /proc/cpuinfo
}
for build in scalar auto avx2 avx512; do
    if [[ "$build" == avx2 ]] && ! has_flag avx2; then echo 'AVX2 not supported: skipping' | tee -a "$META"; continue; fi
    if [[ "$build" == avx512 ]] && ! has_flag avx512f; then echo 'AVX-512F not supported: skipping' | tee -a "$META"; continue; fi
    for kernel in saxpy dot mul; do
        for dtype in f32 f64; do
            if [[ "$dtype" == f32 ]]; then
                sizes='1024,4096,16384,131072,524288,2097152'
                alignment_sizes='1024,1025,8192,8193'
            else
                sizes='512,2048,8192,65536,262144,1048576'
                alignment_sizes='1024,1025,8192,8193'
            fi
            run "$build" "$kernel" "$dtype" "$sizes" 1 0 locality
            for offset in 0 1; do
                run "$build" "$kernel" "$dtype" "$alignment_sizes" 1 "$offset" alignment_tail
            done
            for stride in 1 2 4 8; do
                run "$build" "$kernel" "$dtype" '131072' "$stride" 0 stride
            done
        done
    done
done
# The extended SAXPY cases test two active buffers totalling 320 MiB, above this reference guest's nominal LLC.
# They use the same validation, pinning, build variants and three-repetition protocol as the smaller matrix.
for build in scalar auto avx2 avx512; do
    if [[ "$build" == avx2 ]] && ! has_flag avx2; then continue; fi
    if [[ "$build" == avx512 ]] && ! has_flag avx512f; then continue; fi
    run "$build" saxpy f32 '41943040' 1 0 locality
    run "$build" saxpy f64 '20971520' 1 0 locality
done
printf '\nAdditional LLC-pressure SAXPY cases: f32 N=41943040 and f64 N=20971520, three repetitions per build.\n' >> "$META"
python3 scripts/parse_perf.py --input "$RAW" --out-dir data/processed
python3 scripts/verify_vectors.py --build-dir build --out build/reports/isa-summary.txt
python3 scripts/plot_results.py --summary data/processed/summary.csv --speedups data/processed/speedups.csv --out-dir plots
printf 'Campaign complete: %s\n' "$(date -u '+%FT%TZ')" >> "$META"
echo 'Recorded full campaign and generated raw/processed/figure outputs.'
