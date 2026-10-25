SHELL := /bin/bash
CC := gcc
PYTHON := python3
COMMON := -std=c11 -O3 -g -Wall -Wextra -Wpedantic -Wshadow -Wconversion -Wformat=2 -D_POSIX_C_SOURCE=200809L
# Permit associative floating-point reductions in all variants, with accuracy checked separately.
FPFLAGS := -fno-math-errno -fno-trapping-math -fno-signed-zeros -fassociative-math -ffp-contract=off
# The ISA flags here are deliberately explicit; do not replace them with -march=native.
scalar_FLAGS := -march=x86-64 -mtune=generic -fno-tree-vectorize -fno-tree-slp-vectorize
# Baseline auto-SIMD remains portable x86-64 (commonly SSE2 on this target).
auto_FLAGS := -march=x86-64 -mtune=generic -ftree-vectorize
avx2_FLAGS := -march=x86-64 -mavx2 -mfma -mtune=generic -ftree-vectorize
avx512_FLAGS := -march=x86-64 -mavx512f -mfma -mtune=generic -ftree-vectorize
VARIANTS := scalar auto avx2 avx512
.PHONY: all clean quick full campaign bandwidth summarize plot audit verify sanitize test help
all: $(addprefix build/,$(addsuffix /benchmark,$(VARIANTS)))
# Define independent build directories so compiler flags never silently mix object files.
define VARIANT_RULE
build/$(1):
	mkdir -p $$@ build/reports
build/$(1)/kernels.o: src/kernels.c src/kernels.h | build/$(1)
	$$(CC) $$(COMMON) $$(FPFLAGS) $$($(1)_FLAGS) -fopt-info-vec-all=build/reports/$(1)-vectorization.txt -c $$< -o $$@
build/$(1)/main.o: src/main.c src/kernels.h | build/$(1)
	$$(CC) $$(COMMON) $$(FPFLAGS) $$($(1)_FLAGS) -DBUILD_LABEL=\"$(1)\" -c $$< -o $$@
build/$(1)/benchmark: build/$(1)/kernels.o build/$(1)/main.o
	$$(CC) $$^ -o $$@ -lm
endef
$(foreach v,$(VARIANTS),$(eval $(call VARIANT_RULE,$(v))))
quick: all
	bash scripts/run_sweep.sh --quick
full: all
	bash scripts/run_sweep.sh --full
campaign: all
	bash scripts/run_recorded_campaign.sh
bandwidth:
	taskset -c $$(python3 -c "import os; print(min(os.sched_getaffinity(0)))") python3 scripts/measure_bandwidth.py --mib 192 --samples 7
summarize:
	$(PYTHON) scripts/parse_perf.py --input data/raw/measurements.csv --out-dir data/processed
plot:
	$(PYTHON) scripts/plot_results.py --summary data/processed/summary.csv --speedups data/processed/speedups.csv --out-dir plots
audit:
	$(PYTHON) scripts/audit_submission.py
verify: all
	$(PYTHON) scripts/verify_vectors.py --build-dir build --out build/reports/isa-summary.txt
test:
	$(PYTHON) -m unittest discover -s tests -v
	bash scripts/self_test.sh
sanitize:
	$(CC) -std=c11 -O1 -g -Wall -Wextra -Wpedantic -fno-omit-frame-pointer -fsanitize=address,undefined $(FPFLAGS) $(scalar_FLAGS) -DBUILD_LABEL=\"sanitizer\" src/kernels.c src/main.c -o build/sanitized-benchmark -lm
	ASAN_OPTIONS=detect_leaks=1 build/sanitized-benchmark --sizes 1,3,7,8,9,17,64,257,1025 --dtype both --kernel all --stride 8 --offset-elements 3 --validate-only --no-perf
clean:
	rm -rf build
help:
	@printf '%s\n' 'make all          - build scalar, auto, AVX2, AVX-512 binaries (do not run unsupported ISA)' 'make quick        - short validation/smoke experiments' 'make full         - extended cache, tail/alignment, stride experiment matrix' 'make campaign     - archived reference campaign including LLC pressure SAXPY' 'make bandwidth    - independent large-buffer copy throughput' 'make audit        - verify included results, plots and validation evidence' 'make summarize    - aggregate measured repetitions; never invent results' 'make plot         - create figures using measured CSV data' 'make verify       - summarize actual vector instructions in disassembly' 'make test         - parser unit tests and kernel edge-case validation' 'make sanitize     - run AddressSanitizer/UBSan correctness checks' 'make clean        - remove build outputs only; preserve all measured data'
