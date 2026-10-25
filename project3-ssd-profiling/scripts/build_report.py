#!/usr/bin/env python3
"""Generate a finished evidence-driven Markdown and typeset PDF report from recorded CSV samples."""
from __future__ import annotations
import csv
import datetime as dt
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image, KeepTogether

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / 'report'; REPORT.mkdir(exist_ok=True)
FIGURES = ROOT / 'plots'


def read_rows() -> list[dict]:
    with (ROOT/'data/processed/summary.csv').open() as f:
        return list(csv.DictReader(f))


def pick(rows, study, mode=None, bs=None, workers=None, read_pct=None, direct=None, phase=None, range_mib=None):
    subset = [r for r in rows if r['study'] == study and (mode is None or r['mode'] == mode)
              and (bs is None or int(r['block_kib']) == bs)
              and (workers is None or int(r['workers']) == workers)
              and (read_pct is None or int(r['read_pct']) == read_pct)
              and (direct is None or r['direct'].lower() == str(direct).lower())
              and (phase is None or int(r['phase']) == phase)
              and (range_mib is None or int(r['range_mib']) == range_mib)]
    if len(subset) != 1:
        raise ValueError(f'expected one measured record for study={study}, mode={mode}, bs={bs}, workers={workers}; got {len(subset)}')
    return subset[0]


def number(row, key, precision=0):
    return f'{float(row[key]):,.{precision}f}'


def generate_markdown(rows: list[dict]):
    baseline = [(mode, bs, pick(rows,'qd1_baseline',mode,bs)) for mode,bs in
                [('randread',4),('randwrite',4),('read',128),('write',128)]]
    d = pick(rows,'pagecache_pitfall','read',128,direct=True)
    b = pick(rows,'pagecache_pitfall','read',128,direct=False)
    r1 = pick(rows,'intensity','randread',4,1)
    r16 = pick(rows,'intensity','randread',4,16)
    r32 = pick(rows,'intensity','randread',4,32)
    l16 = pick(rows,'lba_range','randread',4,4,range_mib=16)
    l128 = pick(rows,'lba_range','randread',4,4,range_mib=128)
    rwrite4 = pick(rows,'tail_latency','randwrite',4,4)
    rwrite16 = pick(rows,'tail_latency','randwrite',4,16)
    ratio = float(b['total_mib_s']) / float(d['total_mib_s'])
    baseline_md = '\n'.join(
        f'| {mode} | {bs} | {number(v,"total_iops")} | {number(v,"total_mib_s",1)} | {number(v,"total_avg_latency_us",1)} | '
        f'{number(v,("read" if "read" in mode else "write")+"_p99_us",1)} |'
        for mode,bs,v in baseline)
    qd_md = '\n'.join(
        f'| {n} | {number(v,"total_iops")} | {number(v,"total_avg_latency_us",1)} | {number(v,"read_p99_us",1)} | {number(v,"estimated_inflight",2)} |'
        for n,v in ((1,r1),(16,r16),(32,r32)))
    mix_md = '\n'.join(
        f'| {pct}/{100-pct} | {number(v,"read_iops")} | {number(v,"write_iops")} | {number(v,"total_iops")} | {number(v,"total_mib_s",1)} |'
        for pct,v in [(k,pick(rows,'read_write_mix','randrw',4,4,read_pct=k)) for k in (100,70,50,0)])
    phases = [(i,pick(rows,'burst_vs_sustained','randwrite',4,4,phase=i)) for i in range(1,5)]
    phase_str = ', '.join(f'window {i}: {number(v,"write_iops")} IOPS' for i,v in phases)
    text = f'''# SSD Performance Profiling — Experimental Report

**ECSE 4320/6320 | Advanced Computer Systems | Class-wide Project 3**

**Measurement environment:** virtualized Intel Xeon Platinum 8573C with overlayfs-backed disposable file; 128 MiB preinitialized file, Linux synchronous POSIX `pread`/`pwrite`, `O_DIRECT=1` unless specified. **These are measured file-I/O stack observations, not verified results from an identifiable SSD.** The Flexible I/O Tester binary was unavailable; this repository includes guarded, runnable fio configurations but contains **zero fio-generated performance records**. The local `fio` executable belongs to the unrelated Fiona Python package.

## Executive summary

The recorded experiment contains **118 completed samples across 59 workload configurations**, with two independent runs per configuration. Four one-worker baseline patterns, 24 size/pattern combinations, read/write mixtures, concurrent submitter sweeps, LBA range, short-window writes, tail-latency comparisons, and a buffered-I/O negative control were evaluated. Nine plots and direction-specific p50/p95/p99/p99.9 measures were generated. The clearest methodological failure was {number(b,'total_mib_s',1)} MiB/s for buffered sequential 128 KiB reads versus {number(d,'total_mib_s',1)} MiB/s for direct reads ({ratio:.2f}x). The first path can exploit page cache and therefore must not be labeled SSD bandwidth.

## 1. Pre-experiment hypotheses

1. **QD1 baseline:** serialized 4 KiB random reads/writes should have low bandwidth compared with 128 KiB transfers, because each operation incurs fixed request-processing overhead. A single synchronous worker has approximately one outstanding request.
2. **Size and pattern:** small random requests should be limited by per-request overhead and IOPS; large sequential transfers should amortize this overhead and increase MiB/s until another bottleneck dominates. Monotonicity is not guaranteed under contention.
3. **Read/write ratio:** increasing writes should alter throughput and tail latency because allocation, device staging, writeback, filesystem metadata and interference differ from read paths. The actual I/O mix is stochastic in the native generator.
4. **Concurrency and queueing:** additional independent synchronous workers should initially increase throughput while increasing mean and tail latency after storage-path contention develops. These workers must not be misrepresented as fio asynchronous queue depth.
5. **Tail distribution:** p95, p99 and p99.9 can grow much faster than the median when the storage path experiences shared-host scheduling, writeback or burst throttling.
6. **Working-set and device state:** smaller active ranges may benefit from caching below the application page cache; short write windows may change as buffers, mapping tables and queues evolve. Four short windows do not constitute steady-state SSD preconditioning.
7. **Failure hypothesis:** a buffered sequential-read benchmark can report RAM/page-cache throughput instead of storage latency. With `O_DIRECT`, the result should differ significantly if page-cache reuse was distorting the comparison.

## 2. Experimental design and measurement discipline

The implementation initializes a new 128 MiB, permission-restricted regular file in `data/scratch` and deletes only that uniquely named file after measurement. It never opens a raw disk/partition. The file is filled before timing with a nonzero 1 MiB pseudorandom data block repeated across the file, then synchronized; deduplication below the filesystem remains possible. Test requests are aligned to 4,096 bytes, and the native C harness requires exact full-length `pread` or `pwrite` completion. The runner rejects non-Flexible-I/O-Tester executables. The captured filesystem is overlayfs, and no physical SSD identity or NVMe device temperature is available.

All samples are measured with `CLOCK_MONOTONIC` around synchronous system calls. Each thread issues at most one blocking transfer. The offered-concurrency sweep of 1, 2, 4, 8, 16 and 32 threads tests aggregate in-flight operations, **not** an asynchronous FIO `iodepth=1..32` sweep. The independent run containing the first 25 cases used 600 ms per configuration; the follow-up run containing the remaining 34 cases used 400 ms. Every configuration has two repetitions, and the stored summary is the median of run-level estimates with sample standard deviation. Run-level latency percentiles are aggregated by median; they are not pooled percentiles across raw I/Os from different repetitions. The short windows are sufficient for a controlled exploratory comparison, not for a steady-state SSD certification.

`fio` scripts supplied in the package use `ioengine=libaio`, direct I/O, one job, explicit requested `iodepth`, a bounded regular scratch file, `group_reporting`, latency percentiles, block-size and workload parameters. This environment did not execute those scripts because the genuine tool was absent. The reported statistics are from the separately labeled native POSIX engine. CPU affinity, device SMART telemetry, drive temperature, erase counts and actual controller queue depth were not measured; there is no basis to imply otherwise.

## 3. Single-outstanding-I/O baselines

| Pattern | Transfer KiB | IOPS | MiB/s | Mean latency µs | Direction p99 µs |
|---|---:|---:|---:|---:|---:|
{baseline_md}

The 128 KiB transfer carries 32 times as many application bytes as the 4 KiB transfer. Accordingly, sequential throughput can rise even when the number of I/O operations per second falls. The bandwidth values are application byte counts divided by elapsed time, not PCIe throughput or measured flash-channel traffic. Median/tail measurements are preserved separately for reads and writes in `data/processed/summary.csv`.

![Single-request baselines](../plots/01_baseline_native.png)

## 4. Block-size and access-pattern sweep

For each of 4, 16, 32, 64, 128 and 256 KiB, random read, random write, sequential read and sequential write modes were measured with four synchronous submitters. The 4 KiB random-read workload produced {number(pick(rows,'block_sweep','randread',4,4),'total_mib_s',1)} MiB/s, whereas 256 KiB random reads produced {number(pick(rows,'block_sweep','randread',256,4),'total_mib_s',1)} MiB/s. This increase mostly reflects larger transfer granularity: total IOPS need not rise. Anomalous non-monotone points cannot be attributed uniquely to flash behavior on a shared virtual filesystem.

![Block-size sweep](../plots/02_block_size_native.png)

## 5. Concurrency, throughput–latency and Little's Law

| Synchronous workers | Random read 4 KiB IOPS | Mean latency µs | Read p99 µs | λW (requests) |
|---:|---:|---:|---:|---:|
{qd_md}

Little's Law in stable operation is **L = λW**, where L is average outstanding work, λ is completed I/O/s, and W is end-to-end I/O response time in seconds. For 32 synchronous workers, λW = {number(r32,'total_iops',1)} × {float(r32['total_avg_latency_us'])/1e6:.7f} ≈ **{number(r32,'estimated_inflight',2)}**. One worker yields ≈ {number(r1,'estimated_inflight',2)}. This is an internal consistency check of a fixed-worker, blocking I/O loop, not proof of the hardware controller's queue depth. The 32-worker throughput is {float(r32['total_iops'])/float(r1['total_iops']):.2f}x the one-worker throughput while mean latency grows from {number(r1,'total_avg_latency_us',1)} to {number(r32,'total_avg_latency_us',1)} µs. Measured throughput continued growing up to 32 workers, so a definitive saturation knee was **not observed** within the supported range. A knee should not be invented by extrapolation.

![Intensity](../plots/03_intensity_native.png)
![Throughput vs latency](../plots/04_throughput_latency_native.png)

## 6. Tail latency under load

The 4 KiB random-read p99 rose from {number(r1,'read_p99_us',1)} µs at one worker to {number(r32,'read_p99_us',1)} µs at 32 workers. Read p99.9 rose from {number(r1,'read_p999_us',1)} to {number(r32,'read_p999_us',1)} µs. For 4 KiB random writes, the dedicated 4-worker negative-load case showed p99 {number(rwrite4,'write_p99_us',1)} µs, compared with p99 {number(rwrite16,'write_p99_us',1)} µs for 16 workers, while write throughput did not scale proportionally. This suggests contention or host throttling but does not establish whether the limiting component is the device, filesystem, hypervisor or shared tenant activity.

A p99.9 computed from a few thousand observations is intrinsically noisy; the raw per-run distributions and run counts have therefore been preserved. No synthetic tail latency, 99.99th percentile or undocumented warm-state claim was inserted.

![Tail latency](../plots/05_tail_latency_native.png)

## 7. Read/write mixture

| Target read/write (%) | Read IOPS | Write IOPS | Total IOPS | Total MiB/s |
|---|---:|---:|---:|---:|
{mix_md}

The reported throughput is completed I/O, not the requested mixture label converted into fictitious rates. Both read and write latency distributions are retained separately. The 50/50 mode showed {number(pick(rows,'read_write_mix','randrw',4,4,read_pct=50),'total_iops')} total IOPS, while the read-only mode showed {number(pick(rows,'read_write_mix','randrw',4,4,read_pct=100),'total_iops')} total IOPS. Interference between directions and the virtualized storage backend are plausible causes, not verified controller-level mechanisms.

![Mixed I/O](../plots/06_mix_native.png)

## 8. Accessed range and short-window state

Random 4 KiB read throughput changed from {number(l16,'total_iops')} IOPS for a 16 MiB file range to {number(l128,'total_iops')} IOPS across 128 MiB. The difference is modest relative to the large disparity between buffered and direct I/O in the negative control. With `O_DIRECT`, software page-cache effects should be reduced, but backend caches and virtualization remain possible.

The four consecutive short random-write phases measured {phase_str}. There is no progressive reduction large enough here to establish SLC exhaustion or thermal throttling. The work file, virtual disk sharing, short duration and lack of SMART measurements prevent steady-state or endurance claims.

![Range sensitivity](../plots/07_lba_range_native.png)
![Short window drift](../plots/09_window_drift_native.png)

## 9. Verified negative case: buffered sequential reads

Under otherwise matched 128 KiB single-worker sequential reads, buffered I/O produced **{number(b,'total_mib_s',1)} MiB/s**, compared with **{number(d,'total_mib_s',1)} MiB/s** under `O_DIRECT=1`. The **{ratio:.2f}x** difference is evidence that caching or a different software I/O path materially affects benchmark results. Treating the buffered number as SSD sequential throughput would therefore be misleading. The 4 KiB random-read negative control did not show the same benefit: buffered {number(pick(rows,'pagecache_pitfall','randread',4,direct=False),'total_iops')} IOPS, direct {number(pick(rows,'pagecache_pitfall','randread',4,direct=True),'total_iops')} IOPS. This distinction avoids a blanket claim that buffered I/O always appears faster.

Two falsifiable explanations exist. **(i)** Cached file pages and sequential readahead satisfy many buffered calls without storage access. **(ii)** Linux overlayfs or hypervisor storage implementation follows differing copy and mapping paths for the two modes even without ordinary page-cache hits. Page-cache hit/miss instrumentation or `mincore`/cache-drop experiments on a dedicated system could separate these mechanisms; a direct device/host trace could further resolve them. Neither counter set was available here. These are competing explanations, not proven diagnoses.

![Buffered versus direct negative case](../plots/08_buffered_pitfall_native.png)

## 10. Limitations, anomaly containment, and sources

**Storage identity:** the measured file resides on overlayfs over virtualized storage; an SSD model, firmware, physical interface, controller counters and SMART temperature are unavailable. Numerical values cannot be generalized to physical NVMe SSD performance. **Queue depth:** native concurrency was implemented with blocking independent threads, not libaio or io_uring; requested fio depth is defined in scripts but not validated with fio counters here. **Steady state:** 400–600 ms trials, no long conditioning phase, and four short write windows do not establish burst-to-steady-state behavior. **Workload effects:** one 128 MiB file and repeated pseudorandom initialization can interact with deduplication and backend caches. **Quantiles:** p99.9 is sensitive to sample count and outliers. **Isolation:** no host-level CPU or device isolation was granted. Two interrupted collection sessions were recovered without manufacturing missing records; all included configurations have two successful measurements. **Transfer semantics:** `O_DIRECT` succeeding on overlayfs is evidence of an accepted request flag, not independent proof of physical media traffic.

**Source basis:** course project description, pages 6–7, *ECSE 4320/6320 Advanced Computer Systems: Class-wide Projects*; official fio documentation at https://fio.readthedocs.io/en/master/fio_doc.html; original raw run JSON and `evidence/environment.txt` packaged here. No external hardware measurements were imported.

**AI-use declaration:** OpenAI ChatGPT assisted in writing and debugging the C benchmark, guarded FIO runner, parsing and plotting scripts, the technical prose and the report layout. The native program was compiled and exercised; automated tests passed and all numerical findings came from the saved raw observations. The fio execution path was written and parser-tested against an explicit synthetic schema example, but real fio I/O did not run on this machine. No SSD model, PMU/SMART telemetry, verified asynchronous queue depth, or steady-state values were fabricated.
'''
    (REPORT/'SSD_Performance_Profiling_Report.md').write_text(text)
    return text


STYLES = getSampleStyleSheet()
NAVY = colors.HexColor('#18324C')
BLUE = colors.HexColor('#2C5A82')
TEXT = colors.HexColor('#253648')
MUTED = colors.HexColor('#526477')
LIGHT = colors.HexColor('#ECF2F6')
STYLES.add(ParagraphStyle(name='CoverTitle3', fontName='Helvetica-Bold', fontSize=26, leading=33, textColor=NAVY, spaceAfter=16))
STYLES.add(ParagraphStyle(name='CoverSub3', fontName='Helvetica', fontSize=13, leading=19, textColor=BLUE, spaceAfter=16))
STYLES.add(ParagraphStyle(name='H13', fontName='Helvetica-Bold', fontSize=15.5, leading=20, textColor=NAVY, spaceBefore=13, spaceAfter=10))
STYLES.add(ParagraphStyle(name='H23', fontName='Helvetica-Bold', fontSize=10.8, leading=15, textColor=BLUE, spaceBefore=8, spaceAfter=6))
STYLES.add(ParagraphStyle(name='Body3', fontName='Helvetica', fontSize=9.4, leading=14, textColor=TEXT, spaceAfter=9))
STYLES.add(ParagraphStyle(name='Small3', fontName='Helvetica', fontSize=8, leading=11.5, textColor=MUTED, spaceAfter=8))
STYLES.add(ParagraphStyle(name='Figure3', fontName='Helvetica-Oblique', fontSize=8.2, leading=12, textColor=MUTED, spaceBefore=2, spaceAfter=12))


def p(text: str, small=False):
    return Paragraph(text, STYLES['Small3' if small else 'Body3'])


def h(text: str):
    return Paragraph(text, STYLES['H13'])


def table(headers, rows, widths=None):
    source = [[p(str(x), True) for x in headers]] + [[p(str(x), True) for x in row] for row in rows]
    t = Table(source, colWidths=widths, hAlign='LEFT', repeatRows=1, spaceAfter=13)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),LIGHT),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#D1DEE8')),
                           ('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),
                           ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)]))
    return t


def fig(name, caption, width=6.55):
    from PIL import Image as PILImage
    path=FIGURES/name
    im=PILImage.open(path)
    w,hpx=im.size
    height=width*hpx/w
    return [Image(str(path), width=width*inch, height=height*inch), Paragraph(caption,STYLES['Figure3'])]


def make_pdf(rows):
    baseline=[(mode,bs,pick(rows,'qd1_baseline',mode,bs)) for mode,bs in [('randread',4),('randwrite',4),('read',128),('write',128)]]
    b=pick(rows,'pagecache_pitfall','read',128,direct=False)
    d=pick(rows,'pagecache_pitfall','read',128,direct=True)
    r1=pick(rows,'intensity','randread',4,1)
    r16=pick(rows,'intensity','randread',4,16)
    r32=pick(rows,'intensity','randread',4,32)
    ratio=float(b['total_mib_s'])/float(d['total_mib_s'])
    doc = SimpleDocTemplate(str(REPORT/'SSD_Performance_Profiling_Report.pdf'),pagesize=letter,
                            rightMargin=48,leftMargin=48,topMargin=55,bottomMargin=48,
                            title='SSD Performance Profiling - ECSE 4320/6320 Project 3', author='ECSE 4320/6320 Performance Study')
    story=[]
    def add(*blocks): story.extend(blocks)
    def page(): story.append(PageBreak())
    add(Spacer(1,1.05*inch),Paragraph('SSD PERFORMANCE<br/>PROFILING',STYLES['CoverTitle3']),
        Paragraph('ECSE 4320/6320 | Advanced Computer Systems<br/>Class-wide Project 3',STYLES['CoverSub3']),Spacer(1,.14*inch),
        p('<b>Scope:</b> 4-256 KiB transfers, random/sequential reads and writes, controlled concurrency, read/write mixtures, request latency tails, range and cache failure modes.'),
        p('<b>Study basis:</b> 59 configurations, 118 real measured samples, 2 trials/configuration, 9 figures.'),
        p('<b>Measured system:</b> virtualized Intel Xeon, overlayfs regular file, synchronous O_DIRECT requests.'),
        p('<b>Validity boundary:</b> This is measured file-I/O performance, not hardware-verified NVMe/SSD performance. Genuine FIO was unavailable; the package contains FIO experiments but zero FIO-run result records.'),
        Spacer(1,.3*inch),p('All reported figures derive from the raw JSON and statistical CSV files included with this submission. The actual device identity and physical-media throughput are unknown.'))
    page()
    add(h('1. Experimental intent and hypotheses'),
        p('<b>Fixed-overhead hypothesis.</b> One outstanding 4 KiB operation should have much lower throughput in MiB/s than a 128 KiB transfer; the larger transfer amortizes per-I/O overhead.'),
        p('<b>Access pattern.</b> Sequential I/O is expected to benefit from locality and request coalescing, whereas randomized requests create more offset-discontinuous traffic.'),
        p('<b>Read/write mixtures.</b> Inserting writes may increase service time and tails through filesystem allocation and writeback. The controller-specific cause cannot be determined on a virtual filesystem.'),
        p('<b>Concurrency.</b> More independent I/O streams can increase throughput but eventually increase latency. Any knee must be identified from observations rather than presumed.'),
        p('<b>Failure case.</b> Buffered sequential read results can reflect page-cache reuse rather than storage-device bandwidth.'),
        h('2. Experimental platform and controls'),
        table(['Component','Recorded test setting'],[
            ('CPU','Intel Xeon Platinum 8573C, virtualized KVM'),
            ('Filesystem / target','overlayfs; 128 MiB newly created regular file, not a block device'),
            ('Kernel I/O','POSIX synchronous pread/pwrite, O_DIRECT for primary results'),
            ('Concurrent operation','1, 2, 4, 8, 16, 32 independent blocking I/O workers'),
            ('Buffer / data pattern','4096-byte alignment; repeated nonzero pseudorandom 1 MiB chunk'),
            ('Trials','2 samples/configuration; 600 ms first 25 cases, 400 ms remaining cases'),
            ('Timing','CLOCK_MONOTONIC; per-I/O syscall time and total wall time'),
            ('Software availability','Native C engine run and validated; FIO command was Fiona, not fio'),
            ('Unobserved','Physical SSD model, SMART temperature, NVMe counters, real asynchronous queue depth')],[1.55*inch,5.02*inch]))
    page()
    add(h('3. Single-outstanding-I/O baseline'),
        p('The baseline uses one synchronous worker and an active 128 MiB file range. MiB/s counts application bytes completed per elapsed second; this is not measured flash or PCIe traffic.'),
        table(['Operation','KiB','IOPS','MiB/s','Mean us','p99 us'],
              [[mode,str(bs),number(v,'total_iops'),number(v,'total_mib_s',1),number(v,'total_avg_latency_us',1),
                number(v,('read' if 'read' in mode else 'write')+'_p99_us',1)] for mode,bs,v in baseline],
              [1.15*inch,.55*inch,.92*inch,.78*inch,.92*inch,1.15*inch]),
        p(f'The 128 KiB single-worker sequential-read configuration delivered {number(baseline[2][2],"total_mib_s",1)} MiB/s compared with {number(baseline[0][2],"total_mib_s",1)} MiB/s for 4 KiB random reads. The larger transfer carries 32x more application data per I/O.'),
        *fig('01_baseline_native.png','Figure 1. Observed IOPS for the four single-worker baseline patterns.',5.9))
    page()
    add(h('4. Block size and access pattern'),
        p('Four modes (random read, random write, sequential read and sequential write) were measured with transfer sizes of 4, 16, 32, 64, 128 and 256 KiB and four synchronous workers. The relationship between IOPS and throughput is B = IOPS x transfer_bytes.'),
        *fig('02_block_size_native.png','Figure 2. Application throughput for the full 24-point block-size matrix.',6.35),
        p(f'Random reads rose from {number(pick(rows,"block_sweep","randread",4,4),"total_mib_s",1)} MiB/s with 4 KiB transfers to {number(pick(rows,"block_sweep","randread",256,4),"total_mib_s",1)} MiB/s with 256 KiB transfers. Non-monotone points are retained. Shared filesystem and host contention prohibit a unique controller-level explanation.'))
    page()
    add(h("5. Throughput, concurrency, and Little's Law"),
        table(['Threads','4 KiB random IOPS','Mean latency (us)','Read p99 (us)','lambda * W'],
              [[str(q),number(v,'total_iops'),number(v,'total_avg_latency_us',1),number(v,'read_p99_us',1),number(v,'estimated_inflight',2)]
               for q,v in ((1,r1),(16,r16),(32,r32))],[.68*inch,1.35*inch,1.35*inch,1.1*inch,1.0*inch]),
        p(f'Little\'s Law predicts L = lambda x W. For 32 blocking workers, '
          f'{number(r32,"total_iops",1)} requests/s x {float(r32["total_avg_latency_us"])/1e6:.7f} s = '
          f'{number(r32,"estimated_inflight",2)} active requests. This checks internal accounting, not hardware controller queue occupancy. '
          f'The measured throughput remained increasing through 32 workers; the saturation knee is unresolved.'),
        *fig('03_intensity_native.png','Figure 3. Throughput versus number of concurrent synchronous workers.',5.65))
    page()
    add(h('6. Throughput-latency tradeoff and tails'),
        *fig('04_throughput_latency_native.png','Figure 4. Offered concurrency labels show the throughput-latency tradeoff.',5.8),
        p(f'For 4 KiB random reads, mean latency increased from {number(r1,"total_avg_latency_us",1)} us at one worker to {number(r32,"total_avg_latency_us",1)} us at 32 workers. The read p99 increased from {number(r1,"read_p99_us",1)} to {number(r32,"read_p99_us",1)} us. These are synchronous syscall-level observations, not isolated SSD service times.'),
        *fig('05_tail_latency_native.png','Figure 5. Direction-specific read p99 versus offered concurrency.',5.8))
    page()
    add(h('7. Read/write mixtures'),
        table(['Read/write %','Read IOPS','Write IOPS','Total IOPS','Total MiB/s'],
              [[f'{pct}/{100-pct}', number(v,'read_iops'),number(v,'write_iops'),number(v,'total_iops'),number(v,'total_mib_s',1)]
               for pct,v in [(pct,pick(rows,'read_write_mix','randrw',4,4,read_pct=pct)) for pct in (100,70,50,0)]],
              [1.13*inch,1.13*inch,1.2*inch,1.18*inch,1.14*inch]),
        p('Read and write latencies were sampled separately, with percentage labels indicating requested probabilities rather than invented per-direction rates. The mix study isolates application behavior but cannot distinguish mapping, writeback and hypervisor scheduling overhead.'),
        *fig('06_mix_native.png','Figure 6. Measured read and write operations per second for four request mixes.',6.1))
    page()
    add(h('8. Range and consecutive write windows'),
        p('The active file range was limited to 16, 64 or 128 MiB for 4 KiB random reads with four synchronous submitters. Backend caches can remain active with O_DIRECT, so these ranges cannot establish that device flash was being accessed on every request.'),
        *fig('07_lba_range_native.png','Figure 7. Active file range versus application random-read IOPS.',5.9),
        p('Four sequential short write windows showed broadly similar rates rather than a clearly established decline. The absence of a declining trend is not proof of steady state or absence of thermal throttling. Drive temperature, firmware state and flash preconditioning were not observed.'),
        *fig('09_window_drift_native.png','Figure 8. Repeated bounded write windows, not an SSD endurance test.',5.9))
    page()
    add(h('9. Negative case: page cache and misleading throughput'),
        p(f'Buffered 128 KiB sequential reads achieved {number(b,"total_mib_s",1)} MiB/s, compared with '
          f'{number(d,"total_mib_s",1)} MiB/s under O_DIRECT=1. The ratio was '
          f'<b>{ratio:.2f}x</b>. Calling the buffered number physical SSD bandwidth would be methodologically invalid.'),
        *fig('08_buffered_pitfall_native.png','Figure 9. Same workload shape, different cache path: a demonstrated benchmark pitfall.',6.3),
        p('<b>Competing explanation A:</b> pages left resident after initialization or sequential readahead can satisfy buffered reads from memory. '
          '<b>Competing explanation B:</b> overlayfs and the hypervisor have distinct software paths for buffered versus direct operations. '
          'No kernel page-cache counters or backend trace was available to adjudicate. Notably, 4 KiB random buffered I/O did not show the same improvement; no general buffered-is-faster claim is justified.'))
    page()
    add(h('10. Interpretation, limitations, and reproducibility'),
        p('<b>What was actually measured.</b> 118 successful native synchronous direct-I/O or explicitly buffered control trials, '
          '59 configurations, two repetitions, nine figures and per-run p50/p95/p99/p99.9 distributions. The first run was interrupted after completed cases; intact measurements were recovered, and the remaining cases were measured separately. '
          'All 59 configurations have two saved observations and no missing values were synthesized.'),
        p('<b>What the data cannot establish.</b> The virtual overlay backing has no verified physical SSD identity, firmware, or temperature. '
          'O_DIRECT was requested and accepted for primary measurements; this does not prove actual flash reads/writes. '
          'The native concurrent worker count is not FIO asynchronous queue depth. No true FIO-produced sample, SMART thermal curve, '
          'SSD steady state, controller-level knee, interface limit or p99.9 robustness beyond the observed short windows can be claimed.'),
        p('<b>Dispersion.</b> Values in report tables are medians across two run-level estimates, with sample standard deviations '
          'in data/processed/summary.csv. The p99.9 figures reflect limited completion counts and should be regarded as exploratory.'),
        h('11. Source references and AI-use note'),
        p('Course specification: ECSE 4320/6320 Advanced Computer Systems, Class-wide Projects, Project 3 (pages 6-7). '
          'FIO JSON units and job configuration are based on the official documentation: '
          'https://fio.readthedocs.io/en/master/fio_doc.html. All plotted data originate in archived raw JSON files.'),
        p('OpenAI ChatGPT assisted with the C implementation, FIO safety wrappers, statistical scripts, tests, report text and PDF layout. '
          'The native implementation was compiled and executed with its available tests; full native measurements and charts '
          'were generated from actual collected data. The FIO command path was authored and JSON parsing checked by unit tests; '
          'it was not executed because the genuine binary was absent. No physical-drive metrics were invented.'),
        Spacer(1, .25*inch), p('Repository outputs: src/, jobs/, scripts/, evidence/, data/raw/, data/processed/, plots/, report/.' ,True))
    def decorate(c,doc):
        c.saveState(); c.setStrokeColor(colors.HexColor('#CED9E2')); c.line(48,39,565,39)
        c.setFont('Helvetica',7.5); c.setFillColor(MUTED)
        c.drawString(48,28,'ECSE 4320/6320  |  Project 3  |  Measured virtualized storage path')
        c.drawRightString(562,28,f'Page {doc.page}'); c.restoreState()
    doc.build(story,onFirstPage=decorate,onLaterPages=decorate)
    return REPORT/'SSD_Performance_Profiling_Report.pdf'


if __name__=='__main__':
    values=read_rows(); generate_markdown(values)
    print(make_pdf(values))
