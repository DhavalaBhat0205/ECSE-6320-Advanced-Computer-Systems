SHELL := /bin/bash
CC ?= gcc
CFLAGS := -std=c11 -O2 -g -Wall -Wextra -Wconversion -Wshadow -Wpedantic -D_FILE_OFFSET_BITS=64
LDLIBS := -pthread -lm
.PHONY: all test quick full analyze plot report clean
all: bin/direct_io_bench
bin/direct_io_bench: src/direct_io_bench.c
	@mkdir -p bin
	$(CC) $(CFLAGS) $< -o $@ $(LDLIBS)
test: all
	python3 -m unittest discover -s scripts -p 'test_*.py' -v
quick: all
	python3 scripts/run_experiments.py --backend auto --profile quick
full: all
	python3 scripts/run_experiments.py --backend auto --profile full
analyze:
	python3 scripts/analyze.py
plot: analyze
	python3 scripts/plot.py
report: plot
	python3 scripts/build_report.py
clean:
	rm -rf bin __pycache__ scripts/__pycache__
