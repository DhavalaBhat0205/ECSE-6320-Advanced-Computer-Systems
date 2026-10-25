#!/usr/bin/env python3
"""Validate raw sample schemas; write pooled summaries and Little's Law-style MLP curves."""
import csv
import math
import pathlib
import statistics
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / 'data/raw'
OUT = ROOT / 'data/processed'
KEYS = ('experiment', 'mode', 'bytes', 'stride_bytes', 'random', 'read_percent', 'lanes', 'huge_advice')
FIELDS = ['experiment', 'mode', 'bytes', 'stride_bytes', 'random', 'read_percent', 'lanes',
          'huge_advice', 'samples', 'median_ns_access', 'mean_ns_access', 'sd_ns_access',
          'median_useful_gib_s', 'min_ns_access', 'max_ns_access']


def read_measurements():
    groups = defaultdict(list)
    sources = sorted(RAW.glob('*.csv'))
    if not sources:
        raise SystemExit('No real raw measurements found; refusing to create fabricated summaries.')
    for source in sources:
        with source.open(newline='') as file:
            for row in csv.DictReader(file):
                key = tuple(row[k] for k in KEYS)
                latency = float(row['ns_per_access'])
                throughput = float(row['useful_gib_s'])
                if not (math.isfinite(latency) and math.isfinite(throughput) and latency > 0 and throughput > 0):
                    raise ValueError(f'{source}: invalid measurement {row}')
                groups[key].append((latency, throughput))
    return groups


def main():
    groups = read_measurements()
    OUT.mkdir(parents=True, exist_ok=True)
    filename = OUT / 'summary.csv'
    with filename.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for key, records in sorted(groups.items()):
            delays = [v[0] for v in records]
            rates = [v[1] for v in records]
            row = dict(zip(KEYS, key))
            row.update(samples=len(records), median_ns_access=statistics.median(delays),
                       mean_ns_access=statistics.mean(delays),
                       sd_ns_access=statistics.stdev(delays) if len(delays) > 1 else 0,
                       median_useful_gib_s=statistics.median(rates),
                       min_ns_access=min(delays), max_ns_access=max(delays))
            writer.writerow(row)
    print(f'Wrote {len(groups)} measured configurations to {filename}')


if __name__ == '__main__':
    main()
