#!/usr/bin/env python3
"""Create actual-host cache-boundary N values without fabricating cache sizes."""
from __future__ import annotations
import argparse
import re
from pathlib import Path

CACHE_ROOT = Path('/sys/devices/system/cpu/cpu0/cache')

def decode_bytes(text: str) -> int:
    match = re.fullmatch(r'\s*(\d+)\s*([KMG]?)B?\s*', text, re.IGNORECASE)
    if not match:
        raise ValueError(f'Unrecognized cache capacity: {text!r}')
    return int(match.group(1)) * (1024 ** {'': 0, 'K': 1, 'M': 2, 'G': 3}[match.group(2).upper()])

def cache_capacities() -> dict[int, int]:
    cache: dict[int, int] = {}
    for directory in sorted(CACHE_ROOT.glob('index*')):
        try:
            level = int((directory / 'level').read_text().strip())
            kind = (directory / 'type').read_text().strip()
            capacity = decode_bytes((directory / 'size').read_text())
        except (OSError, ValueError):
            continue
        if kind in ('Data', 'Unified'):
            cache[level] = max(cache.get(level, 0), capacity)
    return cache

def build_sizes(caches: dict[int, int], elem_bytes: int, streams: int, max_mib: int) -> list[int]:
    max_n = max(256, max_mib * 1024 * 1024 // (3 * elem_bytes))
    sizes = {256, 257, 1024, 1025}
    capacity = 256
    while capacity <= max_n:
        sizes.add(capacity)
        capacity *= 2
    for level, count in sorted(caches.items()):
        if level not in (1, 2, 3):
            continue
        nominal = max(1, count // (streams * elem_bytes))
        for numerator, denominator in ((1, 2), (7, 8), (1, 1), (9, 8), (2, 1)):
            size = max(256, nominal * numerator // denominator)
            if size <= max_n:
                sizes.add(size)
    return sorted(size for size in sizes if size <= max_n)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dtype', choices=('f32', 'f64'), required=True)
    parser.add_argument('--kernel', choices=('saxpy', 'dot', 'mul'), required=True)
    parser.add_argument('--max-mib', type=int, default=192, help='Maximum combined three-array allocation in MiB at stride=1')
    args = parser.parse_args()
    if args.max_mib < 1:
        parser.error('--max-mib must be positive')
    cache = cache_capacities()
    if not cache:
        raise SystemExit('No readable cache data under /sys; specify --sizes directly to benchmark.')
    streams = 2 if args.kernel == 'dot' else 3
    elements = build_sizes(cache, 4 if args.dtype == 'f32' else 8, streams, args.max_mib)
    print(','.join(map(str, elements)))

if __name__ == '__main__':
    main()
