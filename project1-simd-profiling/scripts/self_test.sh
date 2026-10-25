#!/usr/bin/env bash
# Exercise tails, naturally aligned shifts and non-unit strides on all supported binaries.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
make all >/dev/null
is_supported() {
    case "$1" in
        avx2) grep -m1 -qw avx2 /proc/cpuinfo ;;
        avx512) grep -m1 -qw avx512f /proc/cpuinfo ;;
        *) return 0 ;;
    esac
}
for build in scalar auto avx2 avx512; do
    if ! is_supported "$build"; then
        printf 'Skipping unsupported ISA %s\n' "$build" >&2
        continue
    fi
    for stride in 1 2 4 8; do
        for offset in 0 1 3; do
            "build/$build/benchmark" --sizes 1,3,7,8,9,17,64,257,1025 --dtype both --kernel all --stride "$stride" --offset-elements "$offset" --validate-only --no-perf 2>&1 | awk '/^PASS / {count++} /^VALIDATION FAILED/ {print > "/dev/stderr"; exit 1} END {if (count != 54) {printf "Expected 54 PASS cases but saw %d\n",count > "/dev/stderr"; exit 1}}'
        done
    done
    printf 'Validation-only suite passed: %s\n' "$build"
done
