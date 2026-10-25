#!/usr/bin/env bash
set -euo pipefail
printf 'UTC timestamp: '; date -u +%FT%TZ
printf 'Kernel: '; uname -a
printf 'Compiler: '; gcc -dumpfullversion -dumpversion
printf 'Python: '; python3 --version
printf 'CPU model: '; grep -m1 '^model name' /proc/cpuinfo | cut -d: -f2-
printf 'Allowed affinity: '; taskset -pc $$ 2>/dev/null || true
printf 'SMT status: '; cat /sys/devices/system/cpu/smt/active 2>/dev/null || echo unavailable
printf 'CPU governor(s): '; (cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null || true) | sort -u | tr '\n' ' '; echo
printf 'Cache hierarchy: \n'
for d in /sys/devices/system/cpu/cpu0/cache/index*; do
  [[ -d "$d" ]] || continue
  printf '  %s %s %s\n' "$(cat "$d/level")" "$(cat "$d/type")" "$(cat "$d/size")"
done
printf 'Memory: '; grep -m1 MemTotal /proc/meminfo
printf 'Transparent hugepages: '; cat /sys/kernel/mm/transparent_hugepage/enabled 2>/dev/null || echo unavailable
printf 'perf executable: '; command -v perf || echo unavailable
printf 'perf_event_paranoid: '; cat /proc/sys/kernel/perf_event_paranoid 2>/dev/null || echo unavailable
printf 'BENCH_CPU: %s\n' "${BENCH_CPU:-unbound}"
