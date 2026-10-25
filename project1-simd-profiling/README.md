# SIMD Advantage Profiling

**ECSE 4320/6320 — Advanced Computer Systems · Class-wide Project 1**

Single-threaded experiments comparing ordinary scalar code, baseline compiler auto-vectorization, AVX2 and AVX-512 for SAXPY, dot product and elementwise multiplication. Both IEEE binary32 and binary64 are tested. The archive contains measured raw samples, processed tables, compiler-vectorization evidence, an independent bandwidth check and finished analysis—not empty result tables or generated example data.

## Main findings

This reference run used a virtualized Intel Xeon Platinum 8573C, GCC 14.2.0 and one pinned Linux CPU. The 1,320 recorded kernel-timing samples cover **440 distinct kernel/build/data-layout configurations**, including **330 scalar-versus-vector matched configurations**. All included timing configurations passed the numerical reference checks. Three timed repetitions were recorded per configuration; the final large SAXPY cases were run separately, with the same three-repetition protocol.

| Example | Scalar | AVX2 | AVX-512 | AVX-512 speedup |
|---|---:|---:|---:|---:|
| SAXPY f32, N=4,096 | 0.625 ns/elem | 0.083 | 0.049 | 12.79× |
| SAXPY f32, N=41,943,040 | 1.116 ns/elem | 0.847 | 0.762 | 1.47× |
| SAXPY f64, N=2,048 | 0.506 ns/elem | 0.156 | 0.108 | 4.70× |
| SAXPY f64, N=20,971,520 | 2.053 ns/elem | 1.835 | 1.945 | 1.06× |

Medians are for **this measured machine**, not guaranteed performance on a different CPU. The flattening of AVX-512 gains at large sizes is consistent with memory-system constraints; it cannot by itself establish that all transactions reached DRAM. An independent two-buffer libc copy produced **14.65 useful GiB/s (median of seven samples)**, which supplies a measured reference for a conditional roofline estimate. Hardware unhalted-core-cycle counters were unavailable; the CSV leaves `core_cycles_per_element` blank and reports TSC reference ticks separately.

**Negative case:** For f32 SAXPY at N=131,072, AVX-512's median time changed from **0.149 ns/element at stride 1** to **3.069 ns/element at stride 8**, while scalar changed from 0.565 to 3.374 ns/element. The compiler's AVX-512 report identifies a packed 64-byte contiguous loop but fails to vectorize the non-unit-stride SAXPY loop. This is not evidence of a vectorized gather loop becoming slower; it is a documented compiler/path selection failure, compounded by reduced cache-line utilization.

![Measured f32 speedup](plots/saxpy_speedup_f32.png)

A complete discussion, variability, disassembly interpretations, unexpected reversals, quantitative roofline calculation and the required AI-use statement appear in the **[eight-page PDF report](report/SIMD_Advantage_Profiling_Report.pdf)** and the corresponding **[editable Markdown report](report/methodology-and-analysis.md)**.

## Source and reproducibility layout

```text
project1-simd-profiling/
├── Makefile
├── README.md
├── requirements.txt
├── src/
│   ├── kernels.h                    Signature and aliasing contracts
│   ├── kernels.c                    Six precision-specific numerical kernels
│   └── main.c                       Validation, allocation, timing and CSV output
├── scripts/
│   ├── run_recorded_campaign.sh     Recorded single-core, cross-ISA sweep
│   ├── run_sweep.sh                 Extended configurable study
│   ├── measure_bandwidth.py         Independent, real large-buffer copy samples
│   ├── cache_sizes.py              Linux cache-capacity discovery
│   ├── parse_perf.py               Strict CSV, statistics and perf-log parser
│   ├── plot_results.py             Full matrix figures
│   ├── plot_focus.py               Readable targeted figures and roofline
│   ├── verify_vectors.py           Packed-instruction disassembly summary
│   └── self_test.sh                C edge-case regression checks
├── tests/test_parsers.py           Parser correctness regression tests
├── data/raw/
│   ├── measurements.csv            1,320 real timed kernel samples
│   ├── memcpy_bandwidth.csv        Seven large-buffer memory-copy samples
│   ├── environment.txt            Compiler, CPU, OS and experiment settings
│   └── validation.log             Unit tests and sanitizer run output
├── data/processed/
│   ├── summary.csv                Per-configuration median, mean, SD, min, max
│   └── speedups.csv               Matched scalar / SIMD comparisons
├── build/reports/
│   ├── *-vectorization.txt        Actual GCC compiler optimization reports
│   └── isa-summary.txt            Actual targeted object-code evidence
├── plots/                        Nine figures from recorded samples
└── report/methodology-and-analysis.md
```

The ZIP excludes compiled executable binaries because x86 ISA binaries are architecture- and OS-dependent. All source, compiler commands and evidence are included; `build/reports/` is retained as measured build evidence.

## Build and measurements

The benchmark is Linux x86-64 oriented. It uses GCC, GNU make, Python 3.10+, util-linux `taskset` and binutils `objdump`. Matplotlib is needed for the plots and is declared in `requirements.txt`. The baseline `auto` build targets portable x86-64 with SSE2-class vectors; the wider builds explicitly request `-mavx2` and `-mavx512f`. The scalar build disables tree vectorization and SLP. Every build keeps `-O3`, the same mathematical reassociation policy, and `-ffp-contract=off` to avoid a hidden FMA-versus-no-FMA comparison.

```bash
make all
make verify
make test
make sanitize
make campaign
make bandwidth
make summarize
make plot
python3 scripts/plot_focus.py
```

The packaged data was obtained using `REPS=3 TARGET_MS=6 bash scripts/run_recorded_campaign.sh`, followed by two larger contiguous SAXPY sizes for each ISA (f32 N=41,943,040; f64 N=20,971,520, three repeats), and `make bandwidth`. The extra large measurements have the same `locality` experiment identifier so that scalar and SIMD rows pair correctly. The runner archives earlier data before a fresh run. A new run changes the measurements and must not be presumed to reproduce these exact numbers.

The executable can also be inspected directly, for example:

```bash
taskset -c 0 build/avx2/benchmark --kernel saxpy --dtype f32 --sizes 1024,1025,8192 --stride 1 --offset-elements 1 --repetitions 3 --target-ms 6 --experiment alignment_tail
```

## Measurement and correctness contracts

1. **Work:** `N` is the number of *useful* elements. For stride `s`, physical index is `i*s`; gaps are not counted as arithmetic work. Shifted pointers use whole elements and remain naturally aligned; the raw backing allocations are 64-byte aligned. Inputs are nonaliasing by construction.
2. **Mathematics:** SAXPY is `y[i] = 0.125*x[i] + y[i]` (two operations); dot is `sum(x[i]*y[i])` (two operations per element by convention); multiply is `z[i] = x[i]*y[i]` (one). FMA is intentionally disabled. For contiguous float32, useful traffic is 12 B/element for SAXPY and multiply, and 8 B/element for dot; for float64 those counts double.
3. **Validation:** Each observed kernel/layout is first compared with extended-precision reference arithmetic. Gap slots are checked for non-unit strides; remainder and offset cases are explicitly tested. Dot products permit a documented error tolerance under compiler reassociation. The batch refuses to record an invalid result as PASS.
4. **Timing:** Each row is one timed batch measured with `CLOCK_MONOTONIC_RAW`; repeated function calls amortize timing overhead. The harness warms the arrays, resets mutable outputs outside timing, counts real loop iterations, consumes output after timing, and emits **ns/element**, **GFLOP/s**, useful **GiB/s**, TSC reference ticks/element, and, only if available, actual hardware core cycles/element. Large-array batch durations can exceed the minimum target, since an individual call cannot be subdivided.
5. **Statistics:** The parser checks the CSV schema and rejects duplicate repetitions, missing measurements and failed correctness rows. A scalar speedup is computed only for matching experiment, kernel, precision, N, stride and offset. Standard deviation reflects repeat-to-repeat variability, not a confidence interval over independently rebooted machines.
6. **Working-set interpretation:** The Linux guest reported 48 KiB L1D per visible private cache, 2 MiB private L2 and 260 MiB aggregate L3. Because this is a virtualized CPU, effective LLC capacity and host contention are not under experimental control. The largest SAXPY uses two active arrays totaling 320 MiB (three allocated arrays: 480 MiB), putting pressure above the reported L3, but cache residency is not a measured hardware event.

## Hypotheses recorded before results were evaluated

**H1 — wider lanes help when arithmetic and data are close to the core.** SSE-class vectors can operate on four floats or two doubles; AVX2 doubles those lane counts and AVX-512 doubles them again. The expected throughput gain is smaller than the lane ratio because of dependency chains, loads/stores, reductions, alignment dispatch and possible execution-frequency changes. Fast dot products require SIMD-friendly reassociation, which is permitted by the selected FP flags and bounded by explicit accuracy checks.

**H2 — SIMD speedup compresses beyond private caches.** At small and intermediate resident sizes, fewer arithmetic instructions should lower time/element. When useful traffic saturates the cache/memory path, arithmetic savings are no longer the dominant cost. A large N is evidence of a *large footprint*, not a hardware proof of DRAM misses.

**H3 — shifted starts and remainders may alter vector prologues/tails.** A one-element pointer shift defeats 64-byte starting alignment while remaining legal for scalar loads. Short loops could pay more for versioning, edge handling or branch behavior than long loops. The size difference between N=1024 and 1025 is intentionally orthogonal to the pointer shift.

**H4 — increasing stride wastes spatial locality.** With a 64-byte cache line, a float32 stride of eight can leave a large fraction of each fetched line unused; this worsens useful bytes per physical transfer and may eliminate contiguous compiler vectorization entirely. Hardware gather is not implied merely because source uses a non-unit stride.

**H5 — binary64 is likely to be slower per element.** The same register holds half as many doubles as floats, and the input/output working set is doubled for a fixed N. Any measured counterexample is still reported rather than discarded.

These pre-experiment claims are predictions, not substitutes for the evidence in the report.

## Scope, provenance and interpretation limits

The source assignment explicitly requests all three kernels, two precision modes, alignment/tail and stride sweeps, cache-size variation, scalar-versus-SIMD builds, compiler evidence, statistical timing, a roofline argument and a negative result. This repository includes each component. No GPU or multi-thread results are asserted. Hardware counters, governor configuration and DRAM-controller traffic are unavailable inside the reference sandbox; measurements of *useful bytes* and TSC ticks are consistently labeled so that they are not confused with memory-controller bytes or core cycles.

**AI assistance:** ChatGPT assisted with source, scripts, report structure and explanatory text. The packaged source was compiled, tested and benchmarked in the recorded execution environment, and the measurements were parsed rather than fabricated. The tool's assistance is disclosed because the course requires it; it is not presented as manually written source or measurements from another computer. Architectural findings are confined to the recorded data and stated assumptions.
