# Project 2 — Cache & Memory Performance Profiling

**ECSE 4320/6320 · Advanced Computer Systems · Class-wide Project 2**

A reproducible Linux/x86-64 microbenchmark study of serialized memory latency, spatial locality, read/write throughput, memory-level parallelism, transparent huge-page advice, and benchmark pitfalls. This repository includes C source, shell entry points, raw timing samples, processed statistics, figures, recorded machine information, and a completed technical report. It does not substitute speculative values for inaccessible hardware counters.

## Main results

The observed single-chain random pointer latency rose from **2.53 ns/access** at 32 KiB to **30.06 ns/access** at 2 MiB and **224.02 ns/access** at 512 MiB. At 512 MiB, eight independent streams reduced aggregate time per access to **25.81 ns**, illustrating the difference between serial latency and throughput under memory-level parallelism. The sample environment reports a 260 MiB shared L3 cache, so the 512 MiB footprint exceeds reported LLC capacity. These values are from an **un-pinned, virtualized Intel Xeon Platinum 8573C development environment** and do not constitute measurements of any other system. The ability to assign a precise latency to L1/L2/L3 is limited by prefetching, replacement policy, page tables, virtualization, and cache sharing.

## Repository contents

| Path | Contents |
| --- | --- |
| `src/memory_kernels.c` | Serialized pointer chase, independent chains, deterministic access permutations, and read/write memory kernel |
| `src/memory_kernels.h` | Stable kernel API |
| `src/main.c` | Aligned allocation, page-advice switch, correctness-visible checksums, monotonic timing, per-trial CSV |
| `Makefile` | Build, regression tests, quick/full experiments, summaries, and plots |
| `scripts/run_experiments.sh` | Locality, granularity, read/write, intensity, and huge-page configuration sweeps |
| `scripts/capture_environment.sh` | Compiler, kernel, cache hierarchy, topology hints, CPU affinity and hugepage policy |
| `scripts/collect_perf.sh` | Explicit PMU counter experiment when Linux perf is available |
| `scripts/analyze.py` | Strict raw CSV validation, sample aggregation, median and standard deviation |
| `scripts/plot.py` | Measured-data plots with units and uncertainty where feasible |
| `scripts/test_project.py` | Reproducibility and boundary-value regression suite |
| `data/raw/` | Original measurement output; never silently overwritten |
| `data/processed/summary.csv` | Aggregated measured configurations |
| `plots/` | Five finished figures |
| `evidence/environment.txt` | Actual development test system information |
| `report/Cache_Memory_Performance_Report.md` | Finished experiment, AMAT, Little's Law and failure-mode analysis |
| `report/Cache_Memory_Performance_Report.pdf` | Formatted report with evidence charts |

## Build and repeatability

The target is **Linux on x86-64 with GCC and Python 3 plus Matplotlib**. `make all`, `make test`, `make quick`, `make full`, `make summarize`, and `make plot` are complete commands. `make full` gathers a wider configuration matrix; each invocation writes a separate timestamped raw CSV. If CPU affinity is desired, the runner accepts `BENCH_CPU` as an existing CPU identifier, without changing a machine's governor or privilege policy. All data in this repository was collected without affinity, as recorded in `evidence/environment.txt`.

C compile flags are explicitly recorded in the Makefile: `-O3 -std=c11 -Wall -Wextra -Wconversion -Wshadow -fno-tree-vectorize -fno-lto`. Vectorization is disabled because the dependent pointer-chasing latency kernel must not be confused with SIMD throughput; the bandwidth kernel still uses the same scalar build for methodological consistency. The output is **elapsed nanoseconds**, not unverified CPU-core cycles. Useful GiB/s is computed as 8 bytes per issued read or store; it excludes cache-line transfer amplification, write allocation, page-walk overhead, and physical DRAM transaction volume.

## Experimental hypotheses, committed before result interpretation

1. **L1/L2/LLC/DRAM transition:** A randomized, dependent access chain should show increasing nanoseconds per load as its actively accessed footprint exceeds each cache's effective capacity. Because a dependent load cannot initiate the next address until it completes, memory-level parallelism is intentionally near one in this experiment.
2. **Sequential versus random:** Sequential access should benefit from spatial locality and hardware prefetch; random access should reduce those benefits, but no single footprint cleanly guarantees a specific level of the hierarchy.
3. **64/256/1024-byte stride:** Larger strides should reduce the density of useful bytes per fetched cache line and degrade useful throughput; the nominal footprint and number of visited nodes must be distinguished.
4. **Read/write mix:** All-read, all-write, 70/30, and 50/50 should differ because stores interact with write-allocate, dirty evictions, store buffers, and branch prediction. A simple useful-byte metric cannot be equated with physical read/write bandwidth.
5. **Concurrency:** Independent pointer chains should increase attainable operations per second without reducing intrinsic single-load latency. The first saturation knee may reflect line-fill buffers, MSHRs, interconnect limits, or measurement overhead rather than DRAM peak bandwidth.
6. **Page locality:** A 4096-byte access spacing should place greater pressure on TLB translation than 64-byte spacing. `MADV_HUGEPAGE` is only a request; speedup alone does not establish that huge pages were actually allocated.
7. **Failure/pitfall:** A nominally large allocation with a small cache-resident active set, repeated traversal, or excessive warmup may be measuring cache reuse instead of DRAM. A serialized latency result must not be marketed as sustained system bandwidth.

## Correctness and measurement controls

The kernels use aligned buffers and deterministic shuffles. Pointer chasing always forms one full-cycle permutation of the sampled nodes; every load consumes the previous load's index. The parallel kernel maintains independent start positions on that cycle. The mixed-memory kernel performs a load *or* a store at each visited site according to the configured ratio; pure-store trials do not secretly load the data operand. Every trial emits its parameters and an observable checksum. Regression tests exercise 105 distinct configurations and verify positive, parseable measurements and rejection of invalid strides. The 32-bit index scheme sets a finite supported upper bound, checked at run time.

Each configuration was run three times; bars on locality plots represent ±1 sample standard deviation. Results may shift with CPU allocation, thermal conditions, shared-cache users, turbo policy, and virtual machine scheduling. `perf` was not installed and exact cache-miss, dTLB-miss, IPC, memory-controller traffic, and achieved page-size counters therefore have **not** been measured. An unavailable counter is not assigned a zero value.

## Negative-case decision

The negative case is **misidentifying 64 MiB as a DRAM-resident working set**. On this machine, the reported LLC is 260 MiB. An earlier reading of 64 MiB as “DRAM bandwidth” would have been invalid; the repository includes a separately recorded 512 MiB comparison beyond the reported LLC capacity. Even that case may contain cache hits over repeated passes and must be described as *beyond-LLC nominal footprint*, not uncontaminated DRAM traffic. Full discussion and falsifiable alternatives appear in the report.

## AI-use declaration

OpenAI ChatGPT assisted in authoring and debugging the C, shell, Python, analysis prose, and document layout. The source was compiled and its regression tests were executed in the available environment; the CSV samples, figures, and numerical report statements derive from those executions. No results on a different machine, no PMU statistics, and no confirmed huge-page allocations are claimed. Authors submitting this work remain responsible for understanding and independently validating it.
