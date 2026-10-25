#!/usr/bin/env python3
"""Create uncluttered single-kernel figures and an illustrative measured-bandwidth roofline."""
from __future__ import annotations
import csv
from pathlib import Path
import statistics
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE = Path(__file__).resolve().parents[1]
with (BASE / 'data/processed/summary.csv').open(newline='') as handle:
    rows = list(csv.DictReader(handle))
with (BASE / 'data/processed/speedups.csv').open(newline='') as handle:
    pairs = list(csv.DictReader(handle))
with (BASE / 'data/raw/memcpy_bandwidth.csv').open(newline='') as handle:
    bandwidth_samples = list(csv.DictReader(handle))
OUT = BASE / 'plots'
OUT.mkdir(exist_ok=True)

for dtype in ('f32', 'f64'):
    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    for build in ('auto', 'avx2', 'avx512'):
        selected = sorted([r for r in pairs if r['experiment'] == 'locality' and r['kernel'] == 'saxpy'
                          and r['dtype'] == dtype and r['build'] == build], key=lambda r: int(r['n']))
        ax.plot([int(r['n']) for r in selected], [float(r['speedup_scalar_over_variant']) for r in selected],
                marker='o', linewidth=1.75, label=build)
    ax.axhline(1.0, color='#555555', linestyle='--', linewidth=1.0)
    ax.set(xscale='log', xlabel='Logical array length N', ylabel='Scalar / SIMD median runtime',
           title=f'SAXPY speedup as the working set grows — {dtype}')
    ax.grid(alpha=.25)
    ax.legend(title='Build')
    fig.tight_layout()
    fig.savefig(OUT / f'saxpy_speedup_{dtype}.png', dpi=200)
    plt.close(fig)

fig, ax = plt.subplots(figsize=(8.1, 4.8))
for build in ('scalar', 'avx512'):
    selected = sorted([r for r in rows if r['experiment'] == 'stride' and r['kernel'] == 'saxpy'
                       and r['dtype'] == 'f32' and r['build'] == build], key=lambda r: int(r['stride']))
    ax.errorbar([int(r['stride']) for r in selected], [float(r['ns_per_element_median']) for r in selected],
                yerr=[float(r['ns_per_element_sd']) for r in selected], capsize=3, marker='o', label=build)
ax.set(xticks=[1, 2, 4, 8], xlabel='Stride (float32 elements)', ylabel='ns / useful element',
       title='Negative case: access spacing overwhelms SIMD advantage (N = 131,072)')
ax.grid(alpha=.25)
ax.legend()
fig.tight_layout()
fig.savefig(OUT / 'negative_stride_saxpy_f32.png', dpi=200)
plt.close(fig)

bw_gib = statistics.median(float(r['useful_gib_per_s']) for r in bandwidth_samples)
fig, ax = plt.subplots(figsize=(7.7, 4.6))
for x, dtype, n, elem_size in [(0, 'f32', 41943040, 4), (1, 'f64', 20971520, 8)]:
    candidate = next(r for r in rows if r['experiment'] == 'locality' and r['kernel'] == 'saxpy'
                     and r['dtype'] == dtype and r['build'] == 'avx512' and int(r['n']) == n)
    intensity = 2.0 / (3.0 * elem_size)
    roof = bw_gib * (2**30) * intensity / 1e9
    measured = float(candidate['gflops_median'])
    ax.bar(x - .18, roof, width=.35, label='Bandwidth × ideal arithmetic intensity' if x == 0 else None)
    ax.bar(x + .18, measured, width=.35, label='Measured AVX-512 SAXPY GFLOP/s' if x == 0 else None)
    ax.text(x - .18, roof + .035, f'{roof:.2f}', ha='center', fontsize=9)
    ax.text(x + .18, measured + .035, f'{measured:.2f}', ha='center', fontsize=9)
ax.set(xticks=[0, 1], xticklabels=['float32', 'float64'], ylabel='GFLOP/s', ylim=(0, 3.4),
       title=f'Large-working-set bandwidth comparison\nIndependent copy median = {bw_gib:.2f} useful GiB/s')
ax.legend(loc='upper right', fontsize=9)
ax.grid(axis='y', alpha=.2)
fig.tight_layout()
fig.savefig(OUT / 'roofline_large_saxpy.png', dpi=200)
plt.close(fig)
print('Created focused speedup, stride-failure, and bandwidth roofline comparison figures.')
