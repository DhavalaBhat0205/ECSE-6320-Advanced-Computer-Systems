#!/usr/bin/env python3
"""Plot measured speedup, throughput/locality, alignment, stride and variability without synthetic data."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path


def read_table(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(f'No processed measurements at {path}; run make summarize')
    with path.open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))

def as_float(row: dict[str, str], name: str, fallback: float = 0.0) -> float:
    value = row.get(name, '')
    return float(value) if value else fallback

def plot_curves(plt, rows, out: Path, *, metric: str, label: str, ylabel: str, logx: bool = True, filter_rows=None) -> bool:
    selected = [r for r in rows if (filter_rows(r) if filter_rows else True)]
    if not selected:
        return False
    fig, ax = plt.subplots(figsize=(9, 5))
    keys = sorted({(r['kernel'], r['dtype'], r['build']) for r in selected})
    plotted = 0
    for kernel, dtype, build in keys:
        curve = sorted((r for r in selected if r['kernel'] == kernel and r['dtype'] == dtype and r['build'] == build), key=lambda r: int(r['n']))
        xs = [int(r['n']) for r in curve]
        ys = [as_float(r, metric) for r in curve]
        if xs:
            errors = [as_float(r, metric.replace('_median', '_sd')) for r in curve] if metric.endswith('_median') else None
            if errors and any(errors):
                ax.errorbar(xs, ys, yerr=errors, capsize=2, marker='o', label=f'{kernel} {dtype} {build}')
            else:
                ax.plot(xs, ys, marker='o', label=f'{kernel} {dtype} {build}')
            plotted += 1
    if not plotted:
        plt.close(fig)
        return False
    if logx:
        ax.set_xscale('log', base=2)
    ax.set_xlabel('Logical elements N')
    ax.set_ylabel(ylabel)
    ax.set_title(label)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize='small', ncol=2)
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    plt.close(fig)
    return True

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, required=True)
    parser.add_argument('--speedups', type=Path, required=True)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as exc:
        raise SystemExit('Plotting requires matplotlib: python3 -m pip install matplotlib') from exc
    summary = read_table(args.summary)
    pairs = read_table(args.speedups)
    if not summary:
        raise SystemExit('No processed samples; plots would be misleading')
    args.out_dir.mkdir(parents=True, exist_ok=True)
    base = lambda row: row['stride'] == '1' and row['offset_elements'] == '0' and row['experiment'] in ('locality', 'smoke')
    made = []
    if plot_curves(plt, summary, args.out_dir / 'locality_ns_per_element.png', metric='ns_per_element_median', label='Latency per useful element by working-set size (SD of repetitions)', ylabel='Nanoseconds / element', filter_rows=base):
        made.append('locality_ns_per_element.png')
    if plot_curves(plt, summary, args.out_dir / 'throughput_gflops.png', metric='gflops_median', label='Measured arithmetic throughput (fixed unit stride/alignment)', ylabel='GFLOP/s', filter_rows=base):
        made.append('throughput_gflops.png')
    if pairs and plot_curves(plt, pairs, args.out_dir / 'scalar_vs_simd_speedup.png', metric='speedup_scalar_over_variant', label='Scalar baseline / selected ISA median runtime', ylabel='Speedup (greater than 1 means faster SIMD)', filter_rows=base):
        made.append('scalar_vs_simd_speedup.png')
    # At an equal N, line groups show the stride penalty directly rather than mixing sizes.
    candidates = [r for r in summary if r['experiment'] == 'stride' and int(r['n']) == 131072 and r['offset_elements'] == '0']
    if candidates:
        fig, ax = plt.subplots(figsize=(8, 5))
        for key in sorted({(r['kernel'], r['dtype'], r['build']) for r in candidates}):
            seq = sorted((r for r in candidates if (r['kernel'], r['dtype'], r['build']) == key), key=lambda r: int(r['stride']))
            ax.plot([int(r['stride']) for r in seq], [float(r['ns_per_element_median']) for r in seq], marker='o', label=' '.join(key))
        ax.set_xlabel('Physical stride (elements)')
        ax.set_ylabel('Median ns / useful element')
        ax.set_title('Stride penalty at N = 131072 (where measured)')
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize='x-small', ncol=2)
        fig.tight_layout()
        fig.savefig(args.out_dir / 'stride_penalty.png', dpi=180)
        plt.close(fig)
        made.append('stride_penalty.png')
    tails = [r for r in summary if r['experiment'] == 'alignment_tail' and int(r['n']) in (1024, 1025, 4096, 4097, 8192, 8193) and r['stride'] == '1']
    if tails:
        fig, ax = plt.subplots(figsize=(9, 5))
        for key in sorted({(r['kernel'], r['dtype'], r['build'], r['offset_elements']) for r in tails}):
            seq = sorted((r for r in tails if (r['kernel'], r['dtype'], r['build'], r['offset_elements']) == key), key=lambda r: int(r['n']))
            ax.plot([int(r['n']) for r in seq], [float(r['ns_per_element_median']) for r in seq], marker='o', label=' '.join(key[:3]) + f' offset={key[3]}')
        ax.set_xlabel('Logical elements N (paired aligned/tail sizes)')
        ax.set_ylabel('Median ns / element')
        ax.set_title('Alignment and non-multiple tail study')
        ax.grid(True, alpha=0.25)
        ax.legend(fontsize='xx-small', ncol=3)
        fig.tight_layout()
        fig.savefig(args.out_dir / 'alignment_tail.png', dpi=180)
        plt.close(fig)
        made.append('alignment_tail.png')
    print('Wrote measured figures: ' + (', '.join(made) if made else 'none; check available sweep configurations'))

if __name__ == '__main__':
    main()
