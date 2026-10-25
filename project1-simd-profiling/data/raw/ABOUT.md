# Raw data provenance

`measurements.csv` contains 1,320 rows emitted directly by the compiled benchmark binaries. The original order, repetitions and measured quantities are preserved. Each line includes `PASS` from a reference check carried out before the timed batch. An empty `core_cycles_per_element` means the hardware PMU event was unavailable, **not** zero cycles. `tsc_ticks_per_element` measures invariant reference ticks and is not the core-clock rate.

`memcpy_bandwidth.csv` holds seven actual pinned libc-copy measurements over two 192 MiB buffers; values are useful bytes moved, not hardware-controller byte counters. `environment.txt` records Linux, compiler and guest CPU provenance. `validation.log` records executed correctness and sanitizer commands.
