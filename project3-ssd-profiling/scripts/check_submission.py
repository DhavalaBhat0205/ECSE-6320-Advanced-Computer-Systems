#!/usr/bin/env python3
"""Fail the repository audit when expected code, measurements or final figures are absent."""
from __future__ import annotations
import csv
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
NEEDED=["Makefile","README.md","src/direct_io_bench.c","scripts/run_experiments.py",
        "scripts/run_experiments.sh","scripts/run_fio.sh","scripts/analyze.py","scripts/plot.py",
        "scripts/build_report.py","scripts/test_project.py",
        "jobs/01_qd1_baselines.fio","jobs/02_queue_depth_and_mix.fio","jobs/03_block_size_study.fio",
        "data/processed/summary.csv","data/processed/individual_samples.csv",
        "evidence/environment.txt","evidence/validation.txt",
        "report/SSD_Performance_Profiling_Report.md","report/SSD_Performance_Profiling_Report.pdf"]


def main():
    missing=[p for p in NEEDED if not (ROOT/p).is_file() or (ROOT/p).stat().st_size==0]
    assert not missing, f"missing required deliverable(s): {missing}"
    with (ROOT/'data/processed/individual_samples.csv').open() as f:
        samples=list(csv.DictReader(f))
    with (ROOT/'data/processed/summary.csv').open() as f:
        groups=list(csv.DictReader(f))
    assert len(samples)==118 and len(groups)==59, (len(samples),len(groups))
    assert all(x['backend']=='native' for x in samples), 'unverified fio or mixed-engine data'
    assert {x['study'] for x in groups}=={'qd1_baseline','block_sweep','read_write_mix','intensity',
                                          'tail_latency','lba_range','burst_vs_sustained','pagecache_pitfall'}
    assert all(int(x['repetitions'])==2 for x in groups)
    for rec in samples:
        raw=ROOT/rec['raw_file']
        assert raw.is_file() and int(json.loads(raw.read_text())['read']['operations']+
                                     json.loads(raw.read_text())['write']['operations'])>0
    plots=list((ROOT/'plots').glob('*.png'))
    assert len(plots)==9 and all(f.stat().st_size>10000 for f in plots), 'incomplete plots'
    assert not list((ROOT/'data/scratch').glob('disposable-*.bin')), 'benchmark scratch file not cleaned up'
    pdfinfo=subprocess.run(['pdfinfo',str(ROOT/'report/SSD_Performance_Profiling_Report.pdf')],
                           capture_output=True,text=True,check=True).stdout
    assert 'Pages:          11' in pdfinfo or 'Pages:           11' in pdfinfo, 'report page count mismatch'
    for p in ROOT.rglob('*.md'):
        t=p.read_text().lower()
        assert 'todo:' not in t and 'fill in' not in t and 'insert your' not in t, f'unfinished writing in {p}'
    print(f"PASS: {len(samples)} completed native I/O samples, {len(groups)} configurations, "+
          f"{len(plots)} full plots, 11-page PDF, source/configuration files and raw links verified")


if __name__=='__main__':
    main()
