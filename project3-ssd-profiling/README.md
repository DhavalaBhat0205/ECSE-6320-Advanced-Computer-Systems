# ECSE 4320/6320 — Project 3: SSD Performance Profiling

This is a complete, reproducible storage-I/O profiling repository with working source code, guarded shell and Python runners, a FIO `libaio` execution path, original measurements, aggregation and plotting tools, an experimental analysis, and a finished eleven-page PDF report. The tests use disposable regular files exclusively. They do not touch `/dev/nvme*`, `/dev/sd*`, mounted partitions, or other existing user data.

## Principal outcome

The captured experiment includes **59 tested workload configurations, 118 successful observations, two repetitions per configuration, and nine measured figures**. The native C benchmark uses aligned buffers and `O_DIRECT` on a regular file, with controlled synchronous-worker concurrency. Its observed 4 KiB random-read throughput grew from **3,412 IOPS with one worker** to **41,486 IOPS with 32 workers**, while mean observed latency increased from **293 to 766 microseconds**. The data did **not** identify a throughput saturation knee before 32 workers. The measured negative case was clearer: single-worker, 128 KiB buffered sequential reads reported **4,070 MiB/s**, versus **345 MiB/s** using `O_DIRECT` on the same file path—a **11.8x** difference that makes it inappropriate to report the buffered result as physical SSD bandwidth.

**Measurement scope is important.** The available environment is a KVM virtual machine exposing an **overlayfs-backed file**, not an identifiable direct-attached NVMe drive. Although `O_DIRECT` was accepted on the file path, neither the physical SSD model nor its firmware, temperature, flash utilization or controller-level latency was visible. The native implementation uses synchronous `pread/pwrite` worker threads. It does **not** establish actual FIO asynchronous queue depth, and its worker count is described as concurrency rather than controller queue depth throughout the report. This distinction is retained in plots, logs and legends.

The Flexible I/O Tester (`fio` 3.x) executable was **not available** in this environment; a different installed `fio` command belongs to the Fiona Python package. The repository includes a complete **FIO/libaio runner**, workload references and validated FIO JSON parsing tests, but **no FIO-generated performance results**. No throughput, latency or queue-depth values have been attributed to unexecuted FIO runs. The 118 recorded measurements were collected using the native Linux POSIX direct-I/O backend, identified separately as `native` in every raw manifest and processed CSV.

## Repository contents

| Folder / file | Function |
|---|---|
| `src/direct_io_bench.c` | Linux synchronous POSIX aligned direct-file-I/O engine; sequential/random offsets, read/write mixes, multiple independent submitters, per-I/O p50/p95/p99/p99.9 tails |
| `Makefile` | Reproducible compile, regression tests, quick/full campaigns, aggregation, plots, and PDF generation |
| `scripts/run_experiments.py` | File-safety checks, unique preinitialization, workload matrix, bounded repetitions, run manifests, environment provenance and guarded backend detection |
| `scripts/run_experiments.sh` | Standalone shell wrapper for the experiment campaign |
| `scripts/run_fio.sh` | FIO-only wrapper; fails explicitly when genuine FIO is not installed |
| `scripts/analyze.py` | Verifies native or FIO JSON, uses correct latency/bandwidth units, aggregates matched configurations and retains trial-to-trial deviations |
| `scripts/plot.py` | Nine labeled data-driven figures with uncertainty where available |
| `scripts/build_report.py` | Reconstructs the finished Markdown/PDF report from saved measurements |
| `scripts/test_project.py` | Regression tests for workload coverage, raw-data schema, FIO units, invalid results, and statistical summaries |
| `jobs/01_qd1_baselines.fio`, `02_queue_depth_and_mix.fio`, `03_block_size_study.fio` | Complete bounded FIO jobfile examples using only the project-local disposable scratch file; included as unexecuted source |
| `data/raw/` | Immutable original per-trial JSON outputs, manifests and run environment details |
| `data/processed/` | `summary.csv`, `individual_samples.csv` and an analysis metadata record |
| `plots/` | Nine completed charts showing baselines, block size, intensity, tails, mix, LBA range, buffering, and write-window drift |
| `evidence/` | Measurement provenance, environment and compilation/validation evidence |
| `report/SSD_Performance_Profiling_Report.md` | Complete hypothesis-led analysis with actual numerical findings |
| `report/SSD_Performance_Profiling_Report.pdf` | Typeset eleven-page technical report |

## Built and validated experiment workflow

The code compiles with Linux GCC and POSIX threads. The native command `make all` builds `bin/direct_io_bench`; `make test` executes regression checks. The native campaign is available through `make quick` and `make full`. The full campaign adds write-concurrency and mixed-intensity variants and uses longer runs with three repetitions. The separate FIO runner in `scripts/run_fio.sh` requires a genuine `fio-3.x` executable; it refuses to substitute Fiona. `make analyze`, `make plot`, and `make report` regenerate derived products from retained raw data. Re-running measurements appends a new uniquely named raw run and never silently overwrites earlier observations. All benchmark target paths are created automatically inside `data/scratch` and removed after a successful run; records are retained in `data/raw`.

The normal profile covers the 4 KiB random and 128 KiB sequential read/write single-worker baselines; four patterns across 4/16/32/64/128/256 KiB blocks; 100/0, 70/30, 50/50 and 0/100 random read/write mixes; concurrency from 1 to 32; p99 and p99.9 at selected load levels; 16/64/128 MiB accessed ranges; four short repeated write windows; and buffered/direct cache-control pairs. The experimental matrix deliberately separates these studies with explicit identifiers rather than combining all workloads into a single incomparable speedup ratio.

## Measurement semantics and safeguards

The scratch file is **newly created** with a random 128-bit filename in a dedicated directory. Filesystem free space is checked; a 128 MiB file is filled with pseudorandom bytes and synchronized before benchmark timing. The C program refuses symlinks, device nodes and unexpected file names, and requires a regular single-link file. Only exact complete `pread`/`pwrite` operations contribute to statistics. Misaligned transfer lengths, invalid read/write mixes and unexpected input values terminate with an error. Output CSVs are derived from on-disk JSON samples without substituting missing or unsuccessful operations.

The primary path uses 4,096-byte aligned I/O and `O_DIRECT`, which is distinct from verified physical flash access. The buffered negative control deliberately sets direct I/O off; this is documented in its configuration and makes the cache-related failure observable. Data are recorded with elapsed monotonic timing; reported latency includes syscall and filesystem/device-stack completion, not only firmware service time. The absolute numbers can vary with shared host load, virtual-device allocation, caching and frequency policy.

For the actual included data, the first run was interrupted after completing 25 configurations; its **50 valid JSON records** were preserved and given a complete recovered manifest. A separate run supplied **68 additional valid JSON records** for the remaining 34 configurations. The two-run protocol has two repetitions per configuration; the first run uses 600 ms/request window and the second 400 ms. Each configuration's measurements are paired within its own run, and no cross-profile claim of perfectly matched sampling duration is made. The statistics are medians of per-run values with sample standard deviations across the two repetitions. A reported p99.9 is the median of independently computed run-level p99.9 values, not a pooled quantile across trials.

## Theory-to-measurement bridge

Storage throughput can be expressed as application bytes per operation multiplied by completed operations per second. Little's Law states **L = λW** for a stable system, where L is average in-flight work, λ is throughput (I/O operations per second), and W is mean response time (seconds). For 32 native blocking 4 KiB random-read workers, the recorded 41,486 IOPS and 766 µs latency imply approximately 31.8 simultaneous active requests. This agreement is a useful internal timing consistency check but is **not a measurement of device hardware queue depth**.

For a saturation knee, throughput must flatten while latency continues to increase. In the observed 1–32 worker range, throughput continued increasing; the knee remained unresolved. The read/write mixture, short-window and range results therefore support a limited workload-path analysis, not a full SSD controller characterization. The report includes alternative, falsifiable explanations for the cache failure and unexpected random-write tails rather than suppressing anomalous observations.

## Technical report and primary sources

The report follows the Project 3 requirements of the *ECSE 4320/6320 Advanced Computer Systems Class-wide Projects* specification (pages 6–7), including pre-experiment hypotheses, safety, baseline latency, IOPS and throughput, block-size and access-pattern sweeps, read/write mixtures, intensity, tail statistics, LBA range, device-state limits and a measured negative case. FIO syntax and JSON-unit interpretation follow the official documentation: https://fio.readthedocs.io/en/master/fio_doc.html.

## AI-use declaration

OpenAI ChatGPT assisted in implementing and debugging the C kernels and benchmark harness, the Bash/Python scripts, the FIO configuration path, regression checks, plotting, technical interpretation, and PDF report formatting. The C program was compiled in the available development environment, the parser and orchestration tests passed, and actual native I/O measurements were used to produce the 118-sample dataset and all numerical findings. The genuine FIO engine was not present or run; no physical SSD identity, device temperatures, SMART counters, controller queue depths or genuine steady-state measurements are claimed. The complete machine record, data provenance and methodological limitations remain available in the repository.

**Direction-specific zero values:** a zero p99 or IOPS in the unused direction of a read-only or write-only case indicates that no I/O of that direction was completed, not that measured latency was zero. The completed direction is the only applicable latency distribution for those workloads.
