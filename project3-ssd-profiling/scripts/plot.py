#!/usr/bin/env python3
"""Create data-derived storage figures with explicit units, method, and dispersion."""
from __future__ import annotations
import csv
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "plots"
OUT.mkdir(exist_ok=True)


def load_rows() -> list[dict]:
    with (ROOT / "data/processed/summary.csv").open() as f:
        return [dict((key, float(val) if key not in ("backend", "study", "mode", "source_runs") and key not in ("direct",) else val)
                     for key, val in row.items()) for row in csv.DictReader(f)]


def select(rows: list[dict], study: str, **kwargs) -> list[dict]:
    return [r for r in rows if r["study"] == study and all(r[k] == v for k, v in kwargs.items())]


def figure_title(ax, title: str, backend: str) -> None:
    ax.set_title(title, weight="semibold", pad=14)
    ax.grid(alpha=.2)
    ax.text(.01, -.20, "Measured on a virtualized overlay filesystem; engine: " + backend,
            transform=ax.transAxes, fontsize=8, color="0.4", va="top")


def save(fig, name: str):
    fig.savefig(OUT / name, bbox_inches="tight", dpi=170)
    plt.close(fig)


def make_plots(rows: list[dict]) -> list[str]:
    backends = sorted({r["backend"] for r in rows})
    produced = []
    for engine in backends:
        r = [row for row in rows if row["backend"] == engine]
        base = select(r, "qd1_baseline")
        if base:
            labels = [f"{q['mode']}\n{int(q['block_kib'])} KiB" for q in base]
            ios = [q["total_iops"] for q in base]
            fig, ax = plt.subplots(figsize=(8,4.2))
            ax.bar(labels, ios)
            ax.set_ylabel("Completed I/O operations/s")
            figure_title(ax, "QD1-equivalent synchronous baseline" if engine == "native" else "FIO QD=1 baseline", engine)
            save(fig, f"01_baseline_{engine}.png"); produced.append(f"01_baseline_{engine}.png")
        block = select(r, "block_sweep")
        if block:
            fig, ax = plt.subplots(figsize=(8.6, 5))
            for mode in ("randread", "randwrite", "read", "write"):
                subset = sorted((v for v in block if v["mode"] == mode), key=lambda v: v["block_kib"])
                if subset:
                    ax.errorbar([v["block_kib"] for v in subset], [v["total_mib_s"] for v in subset],
                                yerr=[v["total_mib_s_stdev"] for v in subset], label=mode, marker="o", capsize=2)
            ax.set_xscale("log", base=2); ax.set_xlabel("Transfer size (KiB)"); ax.set_ylabel("Application throughput (MiB/s)"); ax.legend()
            figure_title(ax, "Block size vs throughput (4 concurrent synchronous threads)" if engine == "native" else "Block size vs throughput (FIO iodepth=4)", engine)
            save(fig, f"02_block_size_{engine}.png"); produced.append(f"02_block_size_{engine}.png")
        intensity = select(r, "intensity")
        if intensity:
            fig, ax = plt.subplots(figsize=(8.6,4.8))
            for mode in ("randread", "read"):
                subset = sorted((v for v in intensity if v["mode"] == mode), key=lambda v: v["workers"])
                ax.errorbar([v["workers"] for v in subset], [v["total_iops"] for v in subset],
                            yerr=[v["total_iops_stdev"] for v in subset], label=f"{mode} {int(subset[0]['block_kib'])} KiB", marker="o", capsize=2)
            ax.set_xlabel("Concurrent synchronous submitters" if engine == "native" else "Requested FIO iodepth")
            ax.set_ylabel("Completed I/O operations/s"); ax.legend()
            figure_title(ax,"I/O intensity and throughput", engine)
            save(fig, f"03_intensity_{engine}.png"); produced.append(f"03_intensity_{engine}.png")
            fig, ax = plt.subplots(figsize=(8.6, 4.8))
            for mode in ("randread", "read"):
                subset = sorted((v for v in intensity if v["mode"] == mode), key=lambda v: v["workers"])
                ax.plot([v["total_avg_latency_us"] for v in subset], [v["total_iops"] for v in subset], "o-", label=mode)
                for point in subset:
                    ax.annotate(str(int(point["workers"])), (point["total_avg_latency_us"], point["total_iops"]), fontsize=8)
            ax.set_xlabel("Mean observed I/O latency (microseconds)"); ax.set_ylabel("Completed I/O operations/s")
            ax.legend(); figure_title(ax, "Throughput-latency tradeoff (annotations are concurrency)", engine)
            save(fig, f"04_throughput_latency_{engine}.png"); produced.append(f"04_throughput_latency_{engine}.png")
            fig, ax = plt.subplots(figsize=(8.6,4.7))
            for mode in ("randread", "read"):
                subset = sorted((v for v in intensity if v["mode"] == mode), key=lambda v: v["workers"])
                ax.semilogx([v["workers"] for v in subset], [v["read_p99_us"] for v in subset], "o-", base=2, label=mode)
            ax.set_xlabel("Concurrent synchronous submitters" if engine == "native" else "Requested FIO iodepth")
            ax.set_ylabel("Read completion p99 (microseconds)"); ax.legend(); figure_title(ax,"Tail latency vs offered concurrency", engine)
            save(fig, f"05_tail_latency_{engine}.png"); produced.append(f"05_tail_latency_{engine}.png")
        mixes = sorted(select(r, "read_write_mix"), key=lambda v: v["read_pct"])
        if mixes:
            fig, ax = plt.subplots(figsize=(7.7,4.4)); x = np.arange(len(mixes))
            reads = [v["read_iops"] for v in mixes]; writes = [v["write_iops"] for v in mixes]
            ax.bar(x, reads, label="Read IOPS"); ax.bar(x, writes, bottom=reads, label="Write IOPS")
            ax.set_xticks(x, [f"{int(v['read_pct'])}/{100-int(v['read_pct'])}" for v in mixes]); ax.set_xlabel("Read/write request mix (%)")
            ax.set_ylabel("Completed I/O operations/s"); ax.legend(); figure_title(ax,"Mixed random 4 KiB I/O", engine)
            save(fig, f"06_mix_{engine}.png"); produced.append(f"06_mix_{engine}.png")
        footprint = sorted(select(r, "lba_range"), key=lambda v: v["range_mib"])
        if footprint:
            fig, ax = plt.subplots(figsize=(7.7,4.4))
            ax.errorbar([v["range_mib"] for v in footprint], [v["total_iops"] for v in footprint],
                        yerr=[v["total_iops_stdev"] for v in footprint], marker="o", capsize=3)
            ax.set_xlabel("Active file range (MiB)"); ax.set_ylabel("Random-read IOPS")
            figure_title(ax,"Effect of accessed LBA/file range", engine)
            save(fig, f"07_lba_range_{engine}.png"); produced.append(f"07_lba_range_{engine}.png")
        pitfall = select(r, "pagecache_pitfall")
        if pitfall:
            fig, ax = plt.subplots(figsize=(8,4.8))
            modes = ("randread", "read"); x = np.arange(len(modes))
            for idx, direct in enumerate((True, False)):
                vals = [next((v["total_iops"] for v in pitfall if v["mode"] == mode and str(v["direct"]).lower() == str(direct).lower()), 0) for mode in modes]
                ax.bar(x + (idx-.5)*.35, vals, width=.35, label="O_DIRECT=1" if direct else "Buffered O_DIRECT=0")
            ax.set_xticks(x, ["Random read 4 KiB", "Sequential read 128 KiB"])
            ax.set_ylabel("Completed I/O operations/s"); ax.legend()
            figure_title(ax,"Negative control: page cache can mislead a storage benchmark", engine)
            save(fig, f"08_buffered_pitfall_{engine}.png"); produced.append(f"08_buffered_pitfall_{engine}.png")
        phases = sorted(select(r, "burst_vs_sustained"), key=lambda v: v["phase"])
        if phases:
            fig, ax = plt.subplots(figsize=(7.5,4.4))
            ax.errorbar([v["phase"] for v in phases], [v["write_iops"] for v in phases],
                        yerr=[v["write_iops_stdev"] for v in phases], marker="o", capsize=3)
            ax.set_xticks([v["phase"] for v in phases]); ax.set_xlabel("Consecutive bounded write window")
            ax.set_ylabel("Completed random-write IOPS")
            figure_title(ax,"Short-window drift (not proof of steady state)", engine)
            save(fig, f"09_window_drift_{engine}.png"); produced.append(f"09_window_drift_{engine}.png")
    return produced


if __name__ == "__main__":
    print("\n".join(make_plots(load_rows())))
