#!/usr/bin/env python3
"""Measure single-process, single-thread large-buffer libc copy throughput as a roofline input."""
from __future__ import annotations
import argparse
import csv
import ctypes
import os
import platform
import statistics
import time
from pathlib import Path


def copy_samples(mib: int, count: int) -> list[dict[str, object]]:
    """Touch both arrays before timing and record useful copied input/output bytes."""
    libc = ctypes.CDLL(None)
    libc.memmove.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t)
    libc.memmove.restype = ctypes.c_void_p
    libc.memset.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t)
    libc.memset.restype = ctypes.c_void_p
    bytes_per_buffer = mib * 1024 * 1024
    source = ctypes.create_string_buffer(bytes_per_buffer)
    destination = ctypes.create_string_buffer(bytes_per_buffer)
    libc.memset(source, 0x3A, bytes_per_buffer)
    libc.memset(destination, 0xC1, bytes_per_buffer)
    source_address = ctypes.addressof(source)
    target_address = ctypes.addressof(destination)
    libc.memmove(target_address, source_address, bytes_per_buffer)
    result = []
    for trial in range(count):
        start = time.perf_counter_ns()
        libc.memmove(target_address, source_address, bytes_per_buffer)
        seconds = (time.perf_counter_ns() - start) * 1e-9
        if destination[0] != b':':
            raise RuntimeError('Independent copy reference byte failed')
        logical_bytes = 2 * bytes_per_buffer
        result.append({'trial': trial, 'buffer_mib': mib, 'total_two_buffer_mib': 2*mib,
                       'useful_traffic_bytes': logical_bytes, 'seconds': f'{seconds:.9f}',
                       'useful_gib_per_s': f'{logical_bytes / seconds / 2**30:.9f}',
                       'scope': 'libc_memmove_two_warmed_buffers_not_IMC_bytes'})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mib', type=int, default=192)
    parser.add_argument('--samples', type=int, default=7)
    parser.add_argument('--output', type=Path, default=Path('data/raw/memcpy_bandwidth.csv'))
    args = parser.parse_args()
    if not 16 <= args.mib <= 1024 or not 2 <= args.samples <= 100:
        parser.error('Require 16..1024 MiB and 2..100 real samples')
    records = copy_samples(args.mib, args.samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('w', newline='', encoding='utf-8') as destination:
        writer = csv.DictWriter(destination, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    bw = [float(record['useful_gib_per_s']) for record in records]
    print(f'Actual libc memory-copy results ({platform.machine()}, CPU affinity {sorted(os.sched_getaffinity(0))}):')
    print(f'  {len(bw)} samples, two-buffer set {args.mib*2} MiB, median {statistics.median(bw):.3f} GiB/s, SD {statistics.stdev(bw):.3f} GiB/s')
    print(f'  saved to {args.output}; this is useful read+write traffic, NOT memory-controller bytes')

if __name__ == '__main__':
    main()
