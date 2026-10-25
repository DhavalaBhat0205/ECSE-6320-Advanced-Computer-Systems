#!/usr/bin/env python3
"""Validate immutable benchmark JSON and summarize actual, separately labeled I/O engines."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import statistics as stats

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
OUTPUT = ROOT / "data/processed"
IDENTITY = ("backend", "study", "mode", "block_kib", "workers", "read_pct", "range_mib", "phase", "direct")
METRICS = ("read_iops", "write_iops", "total_iops", "read_bw_mib_s", "write_bw_mib_s", "total_mib_s",
           "read_p50_us", "read_p95_us", "read_p99_us", "read_p999_us", "read_mean_us",
           "write_p50_us", "write_p95_us", "write_p99_us", "write_p999_us", "write_mean_us",
           "total_avg_latency_us", "estimated_inflight")


def fio_direction(job: dict, direction: str) -> dict:
    """Interpret fio's documented JSON units, including clat_ns, clat_us and latency fallback."""
    item = job.get(direction, {})
    count = int(item.get("total_ios", 0))
    iops = float(item.get("iops", 0))
    throughput = float(item.get("bw_bytes", float(item.get("bw", 0)) * 1024)) / 1048576
    latency = next((item[k] for k in ("lat_ns", "lat_us", "clat_ns", "clat_us")
                    if isinstance(item.get(k), dict) and item[k].get("mean") is not None), {})
    scale = 0.001 if "lat_ns" in item and item["lat_ns"] is latency or "clat_ns" in item and item["clat_ns"] is latency else 1.0
    pct = latency.get("percentile", {})
    def pick(x: float) -> float:
        options = [(abs(float(k) - x), float(v)) for k, v in pct.items()]
        return min(options)[1] * scale if options and min(options)[0] < .01 else 0.0
    return dict(operations=count, iops=iops, bw_mib_s=throughput,
                mean_us=float(latency.get("mean", 0)) * scale,
                p50_us=pick(50), p95_us=pick(95), p99_us=pick(99), p999_us=pick(99.9))


def normalize(data: dict, meta: dict, raw_path: Path) -> dict:
    """Ensure raw failures or missing transfer statistics cannot become fabricated successes."""
    backend = meta["backend"]
    if backend == "native":
        if data.get("direct") != meta["direct"]:
            raise ValueError(f"direct mismatch in {raw_path}")
        directions = {direction: data[direction] for direction in ("read", "write")}
        duration = float(data["runtime_s"])
    elif backend == "fio":
        jobs = data.get("jobs", [])
        if len(jobs) != 1 or int(jobs[0].get("error", 0)):
            raise ValueError(f"fio needs exactly one combined error-free group: {raw_path}")
        directions = {direction: fio_direction(jobs[0], direction) for direction in ("read", "write")}
        duration = max([float(jobs[0].get(d, {}).get("runtime", 0)) / 1000 for d in ("read", "write")] + [.001])
    else:
        raise ValueError(f"unexpected benchmark engine in {raw_path}")
    a, b = directions["read"], directions["write"]
    if duration <= 0 or (a["operations"] + b["operations"]) <= 0:
        raise ValueError(f"zero duration or zero completed I/O in {raw_path}")
    if any(float(item[k]) < 0 for item in (a, b) for k in ("iops", "bw_mib_s", "mean_us", "p95_us", "p99_us")):
        raise ValueError(f"negative latency or bandwidth in {raw_path}")
    total_iops = float(a["iops"]) + float(b["iops"])
    mean = (float(a["iops"]) * float(a["mean_us"]) + float(b["iops"]) * float(b["mean_us"])) / total_iops
    row = {k: meta[k] for k in IDENTITY}
    row.update(repetition=meta["repetition"], raw_file=str(raw_path.relative_to(ROOT)) if raw_path.is_relative_to(ROOT) else str(raw_path), duration_s=duration,
               read_operations=a["operations"], write_operations=b["operations"])
    for direction, item in directions.items():
        for k in ("iops", "bw_mib_s", "mean_us", "p50_us", "p95_us", "p99_us", "p999_us"):
            row[f"{direction}_{k}"] = round(float(item[k]), 7)
    row.update(total_iops=round(total_iops, 7),
               total_mib_s=round(float(a["bw_mib_s"]) + float(b["bw_mib_s"]), 7),
               total_avg_latency_us=round(mean, 7),
               estimated_inflight=round(total_iops * mean / 1e6, 7))
    return row


def process(raw_dir: Path = RAW, output: Path = OUTPUT) -> dict:
    """Keep raw trials immutable and aggregate only identically configured observations."""
    all_rows: list[dict] = []
    runs = []
    for manifest in sorted(raw_dir.glob("*/manifest.json")):
        cases = json.loads(manifest.read_text())
        if not isinstance(cases, list):
            raise ValueError(f"bad manifest: {manifest}")
        runs.append(manifest.parent.name)
        for meta in cases:
            target = manifest.parent / meta["file"]
            if not target.is_file() or target.parent.resolve() != manifest.parent.resolve():
                raise ValueError(f"missing result: {target}")
            all_rows.append(normalize(json.loads(target.read_text()), meta, target))
    if not all_rows:
        raise ValueError("no completed benchmark run has been recorded")
    output.mkdir(parents=True, exist_ok=True)
    with (output / "individual_samples.csv").open("w", newline="") as fd:
        writer = csv.DictWriter(fd, fieldnames=list(all_rows[0])); writer.writeheader(); writer.writerows(all_rows)
    buckets: dict[tuple, list[dict]] = {}
    for row in all_rows:
        buckets.setdefault(tuple(row[k] for k in IDENTITY), []).append(row)
    summary = []
    for key, rows in sorted(buckets.items(), key=lambda pair: str(pair[0])):
        record = dict(zip(IDENTITY, key))
        record["repetitions"] = len(rows)
        record["source_runs"] = ";".join(sorted(set(Path(row["raw_file"]).parent.name for row in rows)))
        for name in METRICS:
            observations = [float(row[name]) for row in rows]
            record[name] = round(stats.median(observations), 6)
            record[name + "_stdev"] = round(stats.stdev(observations), 6) if len(observations) > 1 else 0.0
        summary.append(record)
    with (output / "summary.csv").open("w", newline="") as fd:
        writer = csv.DictWriter(fd, fieldnames=list(summary[0])); writer.writeheader(); writer.writerows(summary)
    info = {"runs": runs, "raw_samples": len(all_rows), "configurations": len(summary),
            "fio_samples": sum(row["backend"] == "fio" for row in all_rows),
            "native_samples": sum(row["backend"] == "native" for row in all_rows),
            "aggregation": "median of per-run metrics, with across-run sample standard deviation; not pooled latency percentiles"}
    (output / "analysis_metadata.json").write_text(json.dumps(info, indent=2) + "\n")
    return info


if __name__ == "__main__":
    print(json.dumps(process(), indent=2))
