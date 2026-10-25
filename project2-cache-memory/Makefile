CC ?= gcc
CFLAGS ?= -O3 -std=c11 -Wall -Wextra -Wconversion -Wshadow -fno-tree-vectorize -fno-lto
LDFLAGS ?= -lm
BIN := bin/memory_bench
SRC := src/main.c src/memory_kernels.c
.PHONY: all test quick full summarize plot environment clean
all: $(BIN)
$(BIN): $(SRC) src/memory_kernels.h
	@mkdir -p bin
	$(CC) $(CFLAGS) $(SRC) -o $@ $(LDFLAGS)
test: all
	python3 scripts/test_project.py
quick: all
	bash scripts/run_experiments.sh quick
full: all
	bash scripts/run_experiments.sh full
summarize:
	python3 scripts/analyze.py
plot: summarize
	python3 scripts/plot.py
environment:
	bash scripts/capture_environment.sh
clean:
	rm -rf bin
