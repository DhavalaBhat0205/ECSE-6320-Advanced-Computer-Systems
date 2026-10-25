#!/usr/bin/env python3
"""Generate plots only from measured, validated CSV summaries."""
import csv
from collections import defaultdict
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
with (ROOT / 'data/processed/summary.csv').open(newline='') as f:
    rows = list(csv.DictReader(f))
PLOTS = ROOT / 'plots'
PLOTS.mkdir(exist_ok=True)


def points(mode, experiment, stride=None, random=None, pct=None, huge=None):
    found = []
    for row in rows:
        if row['mode'] != mode or row['experiment'] != experiment:
            continue
        if stride is not None and int(row['stride_bytes']) != stride:
            continue
        if random is not None and int(row['random']) != random:
            continue
        if pct is not None and int(row['read_percent']) != pct:
            continue
        if huge is not None and int(row['huge_advice']) != huge:
            continue
        found.append(row)
    return found


def finish(name, ylabel):
    plt.xlabel('Nominal array footprint (MiB)')
    plt.ylabel(ylabel)
    plt.xscale('log', base=2)
    plt.grid(True, which='both', alpha=0.3)
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(PLOTS / name, dpi=180)
    plt.close()


for rand in (0, 1):
    subset = points('latency', 'locality', stride=64, random=rand)
    if subset:
        subset.sort(key=lambda r: int(r['bytes']))
        plt.figure(figsize=(8, 4.5))
        plt.errorbar([int(r['bytes']) / 1048576 for r in subset],
                     [float(r['mean_ns_access']) for r in subset],
                     yerr=[float(r['sd_ns_access']) for r in subset],
                     marker='o', capsize=3, label='Serial pointer chain ±1 SD')
        finish(f'locality_{"random" if rand else "sequential"}.png', 'ns per dependent load')

fig, ax = plt.subplots(figsize=(8, 4.5))
for pct in (0, 50, 70, 100):
    subset = sorted(points('bandwidth', 'rw_mix', stride=64, random=0, pct=pct), key=lambda r: int(r['bytes']))
    if subset:
        ax.plot([int(r['bytes']) / 1048576 for r in subset],
                [float(r['median_useful_gib_s']) for r in subset], marker='o', label=f'{pct}% reads')
ax.set_xscale('log', base=2)
ax.set_xlabel('Nominal array footprint (MiB)')
ax.set_ylabel('Useful-byte throughput (GiB/s)')
ax.set_title('Single-core read/write mix; not physical DRAM traffic')
ax.grid(alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig(PLOTS / 'read_write_mix.png', dpi=180)
plt.close(fig)

fig, ax = plt.subplots(figsize=(8, 4.5))
for stride in (64, 256, 1024):
    subset = sorted(points('bandwidth', 'rw_mix', stride=stride, random=0, pct=100), key=lambda r: int(r['bytes']))
    if subset:
        ax.plot([int(r['bytes']) / 1048576 for r in subset],
                [float(r['median_useful_gib_s']) for r in subset], marker='o', label=f'{stride}-byte stride')
ax.set_xscale('log', base=2)
ax.set_xlabel('Nominal array footprint (MiB)')
ax.set_ylabel('Useful-byte throughput (GiB/s)')
ax.grid(alpha=0.3)
ax.legend()
fig.tight_layout()
fig.savefig(PLOTS / 'stride_throughput.png', dpi=180)
plt.close(fig)

mlp = points('mlp', 'intensity', stride=64, random=1)
if mlp:
    by_size = defaultdict(list)
    for row in mlp:
        by_size[int(row['bytes'])].append(row)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for bytes, series in sorted(by_size.items()):
        series.sort(key=lambda row: int(row['lanes']))
        ax.plot([int(row['lanes']) for row in series],
                [float(row['median_ns_access']) for row in series], marker='o', label=f'{bytes/1048576:g} MiB')
    ax.set_xlabel('Independent pointer chains')
    ax.set_ylabel('ns per access (aggregate; not individual load latency)')
    ax.set_title('Concurrency and memory-level parallelism')
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(PLOTS / 'mlp_latency.png', dpi=180)
    plt.close(fig)

print(f'Generated {len(list(PLOTS.glob("*.png")))} evidence plots')
