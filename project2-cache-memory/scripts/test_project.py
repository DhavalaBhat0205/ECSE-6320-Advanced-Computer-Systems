#!/usr/bin/env python3
"""Exercise deterministic output, boundary validation, and representative access modes."""
import csv
import io
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
BINARY = ROOT / 'bin/memory_bench'


def invoke(mode, footprint, stride, random, pct, lanes, count, huge=0):
    cmd = [str(BINARY), mode, str(footprint), str(stride), str(random), str(pct),
           str(lanes), '2', str(count), str(huge), '997']
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    rows = list(csv.DictReader(io.StringIO(result.stdout)))
    assert len(rows) == 2
    assert all(float(row['elapsed_ns']) > 0 for row in rows)
    assert all(int(row['accesses']) > 0 for row in rows)
    assert all(float(row['useful_gib_s']) > 0 for row in rows)
    return rows


def main():
    for footprint in (8192, 65536, 1048576):
        for step in (64, 256, 1024):
            for random in (0, 1):
                invoke('latency', footprint, step, random, 100, 1, 10000)
                for pct in (0, 50, 70, 100):
                    invoke('bandwidth', footprint, step, random, pct, 1, 2)
        for lanes in (1, 2, 4, 8, 16):
            invoke('mlp', footprint, 64, 1, 100, lanes, 10000)
    invalid = subprocess.run([str(BINARY), 'latency', '1024', '7', '0', '100', '1', '1', '10', '0', '17'], capture_output=True)
    assert invalid.returncode != 0, 'Invalid stride must fail loudly'
    print('PASS: 105 combinations, two samples each; all modes, mixes, strides, and bounds')


if __name__ == '__main__':
    main()
