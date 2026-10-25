#!/usr/bin/env python3
"""Fail closed if final measured evidence, build reports, or analysis artifacts are absent."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
RAW = BASE / 'data/raw/measurements.csv'
BANDWIDTH = BASE / 'data/raw/memcpy_bandwidth.csv'
SUMMARY = BASE / 'data/processed/summary.csv'
SPEEDUPS = BASE / 'data/processed/speedups.csv'
NEEDED_PLOTS = ('alignment_tail.png', 'locality_ns_per_element.png', 'scalar_vs_simd_speedup.png',
                'stride_penalty.png', 'throughput_gflops.png', 'negative_stride_saxpy_f32.png',
                'roofline_large_saxpy.png', 'saxpy_speedup_f32.png', 'saxpy_speedup_f64.png')

def load(path: Path) -> list[dict[str, str]]:
    if not path.is_file() or not path.stat().st_size:
        raise RuntimeError(f'Missing or empty artifact: {path.relative_to(BASE)}')
    with path.open(encoding='utf-8', newline='') as file:
        return list(csv.DictReader(file))

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main() -> None:
    raw, bandwidth, summary, speedups = map(load, (RAW, BANDWIDTH, SUMMARY, SPEEDUPS))
    variants = ('scalar', 'auto', 'avx2', 'avx512')
    kernels = ('saxpy', 'dot', 'mul')
    precisions = ('f32', 'f64')
    if any(r['status'] != 'PASS' for r in raw):
        raise RuntimeError('Not all benchmark rows passed validation')
    sample_counts: dict[tuple[str, ...], set[int]] = {}
    for r in raw:
        key = tuple(r[k] for k in ('experiment', 'build', 'kernel', 'dtype', 'n', 'stride', 'offset_elements'))
        sample_counts.setdefault(key, set()).add(int(r['repetition']))
    if len(raw) != sum(map(len, sample_counts.values())):
        raise RuntimeError('Duplicate sample keys were found')
    if not all(samples == {0, 1, 2} for samples in sample_counts.values()):
        raise RuntimeError('Expected three complete recorded repetitions for each configuration')
    for variant in variants:
        for kernel in kernels:
            for precision in precisions:
                cases = [r for r in raw if r['build'] == variant and r['kernel'] == kernel and r['dtype'] == precision]
                if not cases or not {'locality', 'alignment_tail', 'stride'} <= {r['experiment'] for r in cases}:
                    raise RuntimeError(f'Incomplete experimental coverage: {variant} {kernel} {precision}')
    if len(summary) != len(sample_counts):
        raise RuntimeError('Aggregated configuration count does not match the raw records')
    if not speedups or len(bandwidth) < 3:
        raise RuntimeError('Matched speedups or independent bandwidth samples are missing')
    evidence = [BASE / 'data/raw/environment.txt', BASE / 'data/raw/validation.log',
                BASE / 'build/reports/isa-summary.txt', BASE / 'README.md',
                BASE / 'report/methodology-and-analysis.md']
    evidence += [BASE / 'build/reports' / f'{v}-vectorization.txt' for v in variants if v != 'scalar']
    # GCC emits no vectorization diagnostics when both scalar vectorization passes are explicitly off.
    evidence.append(BASE / 'build/reports/scalar-build-notes.txt')
    evidence += [BASE / 'plots' / plot for plot in NEEDED_PLOTS]
    for entry in evidence:
        if not entry.is_file() or not entry.stat().st_size:
            raise RuntimeError(f'Missing required output: {entry.relative_to(BASE)}')
    result = {
        'status': 'PASS', 'raw_sample_count': len(raw), 'configuration_count': len(sample_counts),
        'paired_scalar_simd_count': len(speedups), 'independent_copy_samples': len(bandwidth),
        'repetitions_per_configuration': 3, 'variants': variants,
        'kernels': kernels, 'precisions': precisions, 'figure_count': len(NEEDED_PLOTS),
        'sha256_measurements_csv': digest(RAW), 'sha256_memcpy_bandwidth_csv': digest(BANDWIDTH),
        'hardware_core_cycles_recorded': sum(bool(r['core_cycles_per_element']) for r in raw),
        'measurement_scope': 'virtualized_reference_machine_not_student_local_cpu',
    }
    output = BASE / 'data/processed/audit.json'
    output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
