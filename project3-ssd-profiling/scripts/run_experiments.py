#!/usr/bin/env python3
"""Run bounded, non-destructive storage profiling against ONE newly created disposable regular file."""
from __future__ import annotations
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRATCH_DIR = ROOT / "data" / "scratch"
RAW_DIR = ROOT / "data" / "raw"
MIB = 1024 * 1024
SEED = 731483


def locate_real_fio() -> str | None:
    """Reject Fiona's unrelated `fio` executable; require Flexible I/O Tester's identity."""
    candidate = shutil.which("fio")
    if not candidate:
        return None
    proc = subprocess.run([candidate, "--version"], text=True, capture_output=True, timeout=5, check=False)
    if proc.returncode == 0 and re.search(r"\bfio[- ]3\.\d+\b", proc.stdout + proc.stderr):
        return candidate
    return None


def matrix(profile: str) -> list[dict]:
    """Cover zero-queue, block size, mixes, load intensity, LBA range and repeated bursts."""
    cases: list[dict] = []
    def add(study: str, mode: str, block_kib: int, workers: int = 1,
            read_pct: int | None = None, range_mib: int = 128, phase: int = 0, direct: bool = True) -> None:
        if read_pct is None:
            read_pct = 0 if mode.endswith("write") or mode == "write" else 100
        cases.append(dict(study=study, mode=mode, block_kib=block_kib, workers=workers,
                          read_pct=read_pct, range_mib=range_mib, phase=phase, direct=direct))
    for mode, bs in (("randread", 4), ("randwrite", 4), ("read", 128), ("write", 128)):
        add("qd1_baseline", mode, bs)
    for bs in (4, 16, 32, 64, 128, 256):
        for mode in ("randread", "randwrite", "read", "write"):
            add("block_sweep", mode, bs, 4)
    for pct in (100, 70, 50, 0):
        add("read_write_mix", "randrw", 4, 4, pct)
    for q in (1, 2, 4, 8, 16, 32):
        add("intensity", "randread", 4, q)
        add("intensity", "read", 128, q)
    for mode in ("randread", "randwrite"):
        for q in (4, 16):
            add("tail_latency", mode, 4, q)
    for size in (16, 64, 128):
        add("lba_range", "randread", 4, 4, range_mib=size)
    for phase in range(1, 5):
        add("burst_vs_sustained", "randwrite", 4, 4, phase=phase)
    for mode, bs in (("randread", 4), ("read", 128)):
        add("pagecache_pitfall", mode, bs, 1, direct=False)
        add("pagecache_pitfall", mode, bs, 1, direct=True)
    if profile == "quick":
        return cases
    for bs in (4, 128):
        for q in (1, 2, 4, 8, 16, 32):
            add("write_intensity", "randwrite" if bs == 4 else "write", bs, q)
    for pct in (50, 70):
        for q in (1, 4, 16, 32):
            add("mixed_intensity", "randrw", 4, q, pct)
    return cases


def create_scratch(size: int) -> Path:
    """Never accept arbitrary block-device paths, and never reuse a preexisting user file."""
    SCRATCH_DIR.mkdir(parents=True, exist_ok=True)
    if SCRATCH_DIR.is_symlink() or not SCRATCH_DIR.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("scratch location must be an ordinary folder inside this repository")
    free = shutil.disk_usage(SCRATCH_DIR).free
    if free < size * 3 + 512 * MIB:
        raise RuntimeError("insufficient free filesystem space for a bounded disposable test file")
    path = SCRATCH_DIR / f"disposable-{uuid.uuid4().hex}.bin"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        # Initialize every block to reproducible non-zero content, avoiding sparse/unwritten reads.
        # Changing the device outside this file is neither needed nor permitted.
        block = bytearray(MIB)
        x = SEED
        for j in range(0, MIB, 8):
            x ^= (x << 13) & ((1 << 64) - 1)
            x ^= x >> 7
            x ^= (x << 17) & ((1 << 64) - 1)
            block[j:j+8] = (x & ((1 << 64) - 1)).to_bytes(8, "little")
        for _ in range(size // MIB):
            view = memoryview(block)
            while len(view):
                written = os.write(fd, view)
                if written < 1:
                    raise OSError("scratch initialization made no progress")
                view = view[written:]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise
    os.close(fd)
    return path


def make_fio_command(fio: str, case: dict, path: Path, seconds: float, seed: int) -> list[str]:
    """Configure real fio libaio, explicit direct I/O, shared file and latency percentiles."""
    bs = case["block_kib"] * 1024
    return [fio, "--name=project3", f"--filename={path}", "--allow_file_create=0",
            "--ioengine=libaio", f"--direct={int(case['direct'])}", "--thread=1", "--group_reporting=1",
            f"--rw={case['mode']}", f"--rwmixread={case['read_pct']}",
            f"--bs={bs}", f"--size={case['range_mib']}m", f"--iodepth={case['workers']}",
            "--numjobs=1", "--time_based=1", f"--runtime={max(1, int(round(seconds)))}",
            "--ramp_time=0", "--norandommap=1", "--randrepeat=0", f"--randseed={seed}",
            "--refill_buffers=1", "--buffer_compress_percentage=0",
            "--percentile_list=50:95:99:99.9", "--clat_percentiles=1", "--lat_percentiles=1",
            "--output-format=json"]


def run_one(case: dict, scratch: Path, backend: str, fio: str | None, ms: int, seed: int) -> tuple[dict, list[str]]:
    """Collect stdout as an immutable JSON result; failed I/O never becomes a zero data point."""
    if backend == "fio":
        assert fio
        command = make_fio_command(fio, case, scratch, ms / 1000, seed)
    else:
        command = [str(ROOT / "bin" / "direct_io_bench"), str(scratch), case["mode"],
                   str(case["block_kib"] * 1024), str(case["range_mib"] * MIB),
                   str(case["workers"]), str(case["read_pct"]), str(ms), str(seed), str(int(case["direct"]))]
    proc = subprocess.run(command, text=True, capture_output=True, timeout=max(40, ms / 1000 * 8), check=False)
    if proc.returncode:
        raise RuntimeError(f"I/O failure for {case}: {proc.stderr[-1000:]}")
    try:
        record = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"unparseable result: {proc.stdout[:200]}") from exc
    if backend == "fio" and (not record.get("jobs") or any(job.get("error") for job in record["jobs"])):
        raise RuntimeError(f"fio reported job error for {case}")
    return record, command


def capture_env(folder: Path, backend: str, fio: str | None, scratch: Path) -> None:
    """Record kernel, virtual filesystem, tool presence and benchmark provenance."""
    import platform
    def cmd(args: list[str]) -> str:
        try:
            return subprocess.run(args, capture_output=True, text=True, timeout=8, check=False).stdout[:30000]
        except (OSError, subprocess.TimeoutExpired):
            return "not accessible"
    lines = [f"timestamp_utc={dt.datetime.now(dt.timezone.utc).isoformat()}",
             f"backend={backend}", f"fio_binary={fio or 'not installed (fiona is not fio)'}",
             f"native_engine={'synchronous O_DIRECT per thread' if backend == 'native' else 'none'}",
             f"scratch_filesystem={cmd(['stat', '-f', '-c', '%T (block size=%s)', str(scratch)]).strip()}",
             f"scratch_parent={SCRATCH_DIR}", f"host_platform={platform.platform()}",
             f"python={platform.python_version()}",
             f"device_identity=not attributable to one physical SSD (overlay filesystem)" ,
             "\n== lscpu ==\n" + cmd(["lscpu"]),
             "\n== lsblk ==\n" + cmd(["lsblk", "-o", "NAME,TYPE,SIZE,MODEL,ROTA,MOUNTPOINTS"]),
             "\n== mounts ==\n" + cmd(["findmnt", "-T", str(scratch), "-o", "TARGET,FSTYPE,SOURCE,OPTIONS"]),
             "\n== kernel ==\n" + cmd(["uname", "-a"]),
             "\n== storage utilization ==\n" + cmd(["df", "-h", str(scratch)]),
             "\n== fio detection ==\n" + cmd([shutil.which("fio") or "false", "--version"]),
             "\n== available governors ==\n" + cmd(["bash", "-c", "grep . /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor 2>/dev/null | head -20"]) ]
    (folder / "environment.txt").write_text("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backend", choices=("auto", "fio", "native"), default="auto")
    ap.add_argument("--profile", choices=("quick", "full"), default="quick")
    ap.add_argument("--sample-ms", type=int, default=None)
    ap.add_argument("--repetitions", type=int, default=None)
    args = ap.parse_args()
    fio = locate_real_fio()
    backend = ("fio" if fio else "native") if args.backend == "auto" else args.backend
    if backend == "fio" and not fio:
        raise SystemExit("Flexible I/O Tester unavailable; Fiona's 'fio' command is not a substitute")
    if backend == "native" and not (ROOT / "bin" / "direct_io_bench").is_file():
        raise SystemExit("native harness not built: make all")
    reps = args.repetitions if args.repetitions is not None else (2 if args.profile == "quick" else 3)
    sample_ms = args.sample_ms if args.sample_ms is not None else (400 if args.profile == "quick" else 2000)
    if reps < 1 or reps > 20 or sample_ms < 100 or sample_ms > 3600000:
        raise SystemExit("repetitions must be 1..20 and duration 100..3600000 milliseconds")
    if backend == "fio" and sample_ms < 1000:
        sample_ms = 1000  # fio's runtime argument is measured in seconds.
    cases = matrix(args.profile)
    label = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    out = RAW_DIR / label
    out.mkdir(parents=True, exist_ok=False)
    scratch = create_scratch(128 * MIB)
    start = time.monotonic()
    try:
        capture_env(out, backend, fio, scratch)
        manifest = []
        for i, case in enumerate(cases):
            for rep in range(reps):
                seed = SEED + i * 113 + rep * 29
                result, command = run_one(case, scratch, backend, fio, sample_ms, seed)
                name = f"case{i:03d}-r{rep:02d}.json"
                (out / name).write_text(json.dumps(result, indent=2) + "\n")
                manifest.append({**case, "repetition": rep, "file": name, "backend": backend,
                                 "requested_duration_ms": sample_ms, "command": ["<SCRATCH_PATH>" if arg == str(scratch) or str(arg).startswith("--filename=") else arg for arg in command]})
                (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            if (i + 1) % 15 == 0:
                print(f"completed {i+1}/{len(cases)} workload configurations", flush=True)
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (out / "execution.json").write_text(json.dumps({"backend": backend, "profile": args.profile,
                                                          "cases": len(cases), "repetitions": reps,
                                                          "samples": len(manifest), "elapsed_s": time.monotonic() - start}, indent=2) + "\n")
    finally:
        # Only the random UUID file created by this process is deleted; raw results remain intact.
        scratch.unlink(missing_ok=True)
    print(f"completed {len(cases)} configurations x {reps} repetitions; raw result set: {out}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
