#!/usr/bin/env python3
"""Validate raw benchmark rows, aggregate repetitions, pair scalar/SIMD speedups, and parse perf -x ';' logs."""
from __future__ import annotations
import argparse
import csv
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

REQUIRED = (
    'schema_version', 'experiment', 'build', 'kernel', 'dtype', 'n', 'stride', 'offset_elements',
    'repetition', 'iterations', 'seconds', 'ns_per_element', 'tsc_ticks_per_element',
    'gflops', 'logical_gib_per_s', 'core_cycles_per_element', 'status',
)
GROUP = ('experiment', 'build', 'kernel', 'dtype', 'n', 'stride', 'offset_elements')
METRICS = ('ns_per_element', 'tsc_ticks_per_element', 'gflops', 'logical_gib_per_s')
PERF_NUMBER = re.compile(r'^\s*[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)(?:[eE][+-]?\d+)?\s*$')
EVENT_PATTERN = re.compile(r'(?:cycles|instructions|cache-misses|cache-references|branches|branch-misses|L1-dcache-load-misses|LLC-load-misses)', re.I)

def clean_numeric(raw: str) -> float | None:
    """Convert a metric if meaningful, preserving unsupported counters as missing."""
    token = raw.strip().replace('\u202f', '').replace(' ', '')
    if not token or token.startswith('<') or token.lower() in ('nan', 'n/a', 'notcounted', 'notsupported'):
        return None
    if not PERF_NUMBER.fullmatch(token):
        return None
    if ',' in token and '.' not in token:
        # perf --no-big-num typically has no grouping; a single comma may be decimal in locales.
        token = token.replace(',', '.')
    try:
        number = float(token)
    except ValueError:
        return None
    return number if math.isfinite(number) else None

def load_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f'No measured data file exists: {path}')
    with path.open(newline='', encoding='utf-8') as stream:
        reader = csv.DictReader(stream)
        missing = set(REQUIRED) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f'Missing required raw CSV fields: {sorted(missing)}')
        records = list(reader)
    if not records:
        raise ValueError('Input contains no measured samples; do not create empty or synthetic plots')
    seen = set()
    for index, row in enumerate(records, start=2):
        if None in row:
            raise ValueError(f'Raw CSV row {index} has unexpected extra fields')
        if row['schema_version'] != '1' or row['status'] != 'PASS':
            raise ValueError(f'Invalid schema/status at row {index}: {row}')
        key = tuple(row[field] for field in GROUP + ('repetition',))
        if key in seen:
            raise ValueError(f'Duplicate measured repetition for {key}')
        seen.add(key)
        for field in ('n', 'stride', 'iterations'):
            try:
                integer = int(row[field])
            except ValueError as exc:
                raise ValueError(f'Invalid {field} at row {index}') from exc
            if integer <= 0:
                raise ValueError(f'Nonpositive {field} at row {index}')
        for field in ('seconds',) + METRICS:
            metric = clean_numeric(row[field])
            if metric is None or metric <= 0:
                raise ValueError(f'Invalid measured {field} at row {index}')
        if row['core_cycles_per_element'] and clean_numeric(row['core_cycles_per_element']) is None:
            raise ValueError(f'Invalid core cycle count at row {index}')
    return records

def metric_values(rows: list[dict[str, str]], field: str) -> list[float]:
    return [value for row in rows if (value := clean_numeric(row.get(field, ''))) is not None]

def summarize(rows: list[dict[str, str]]) -> list[dict[str, str | float | int]]:
    groups: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
    for record in rows:
        groups[tuple(record[field] for field in GROUP)].append(record)
    output = []
    for key, group in sorted(groups.items(), key=lambda entry: (entry[0][0], entry[0][2], entry[0][3], int(entry[0][4]), int(entry[0][5]), int(entry[0][6]), entry[0][1])):
        summary: dict[str, str | float | int] = dict(zip(GROUP, key))
        summary['samples'] = len(group)
        for metric in METRICS + ('core_cycles_per_element',):
            values = metric_values(group, metric)
            summary[f'{metric}_median'] = statistics.median(values) if values else ''
            summary[f'{metric}_mean'] = statistics.fmean(values) if values else ''
            summary[f'{metric}_sd'] = statistics.stdev(values) if len(values) > 1 else ''
            summary[f'{metric}_min'] = min(values) if values else ''
            summary[f'{metric}_max'] = max(values) if values else ''
        output.append(summary)
    return output

def speedups(summary: list[dict[str, str | float | int]]) -> list[dict[str, str | float]]:
    baseline = {}
    for row in summary:
        if row['build'] == 'scalar':
            baseline[tuple(str(row[field]) for field in GROUP if field != 'build')] = row
    paired = []
    for row in summary:
        if row['build'] == 'scalar':
            continue
        key = tuple(str(row[field]) for field in GROUP if field != 'build')
        if key not in baseline:
            continue  # Never invent an absent scalar reference.
        reference = baseline[key]
        scalar_ns = float(reference['ns_per_element_median'])
        vector_ns = float(row['ns_per_element_median'])
        paired.append({**{field: row[field] for field in GROUP}, 'scalar_ns_median': scalar_ns,
                       'variant_ns_median': vector_ns, 'speedup_scalar_over_variant': scalar_ns / vector_ns,
                       'scalar_samples': reference['samples'], 'variant_samples': row['samples']})
    return paired

def parse_perf(path: Path, label: str) -> list[dict[str, str | float]]:
    """Parse Linux `perf stat -x ';' --no-big-num`, without conflating whole-process and kernel-only events."""
    if not path.is_file():
        raise FileNotFoundError(path)
    output = []
    with path.open(encoding='utf-8', errors='replace') as stream:
        for lineno, line in enumerate(stream, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            fields = next(csv.reader([stripped], delimiter=';'))
            if len(fields) < 3:
                continue
            count = clean_numeric(fields[0])
            event = fields[2].strip()
            if not EVENT_PATTERN.search(event):
                continue
            unit = fields[1].strip()
            output.append({'log_label': label, 'line_number': lineno, 'event': event, 'unit': unit,
                           'value': count if count is not None else '',
                           'scope': 'entire_perf_wrapped_process_including_setup'})
    return output

def write_csv(path: Path, records: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    parser.add_argument('--perf-log', action='append', default=[], metavar='LABEL=FILE', help='Optional perf stat -x ";" log (process-scope diagnostics only)')
    args = parser.parse_args()
    records = load_rows(args.input)
    summary = summarize(records)
    pairs = speedups(summary)
    summary_keys = list(summary[0])
    write_csv(args.out_dir / 'summary.csv', summary, summary_keys)
    paired_keys = list(pairs[0]) if pairs else list(GROUP) + ['scalar_ns_median', 'variant_ns_median', 'speedup_scalar_over_variant', 'scalar_samples', 'variant_samples']
    write_csv(args.out_dir / 'speedups.csv', pairs, paired_keys)
    counters = []
    for value in args.perf_log:
        if '=' not in value:
            parser.error('--perf-log must have LABEL=FILE format')
        label, name = value.split('=', 1)
        if not label or not name:
            parser.error('--perf-log requires nonempty label and file')
        counters.extend(parse_perf(Path(name), label))
    if args.perf_log:
        write_csv(args.out_dir / 'perf_events.csv', counters, ['log_label', 'line_number', 'event', 'unit', 'value', 'scope'])
    print(f'Validated {len(records)} real samples; {len(summary)} configurations; {len(pairs)} scalar-versus-SIMD comparisons.')

if __name__ == '__main__':
    main()
