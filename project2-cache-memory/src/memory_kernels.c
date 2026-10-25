#include "memory_kernels.h" /* Import explicit interfaces and fixed-width types. */
#include <stdlib.h> /* Allocate the permutation without stack-overflow risk. */
#include <stdio.h> /* Report unrecoverable allocation errors. */
volatile uint64_t memory_sink = 0; /* Preserve architecturally visible work across calls. */
static uint64_t rng_next(uint64_t *state) { /* Make random access sequences repeatable by seed. */
    uint64_t x = *state; /* Read local generator state once per step. */
    x ^= x >> 12; /* Scramble high-order address correlations. */
    x ^= x << 25; /* Scramble low-order address correlations. */
    x ^= x >> 27; /* Complete xorshift transformation without a library dependency. */
    *state = x; /* Retain state for the next deterministic shuffle. */
    return x * UINT64_C(2685821657736338717); /* Avoid weak low-bit output when choosing addresses. */
} /* Finish constant-cost deterministic generator. */
void memory_build_chain(uint32_t *next, size_t slots, size_t gap, int randomize, uint64_t seed) { /* Fill one full-coverage pointer-chasing cycle. */
    uint32_t *order = malloc(slots * sizeof(*order)); /* Keep temporary permutation outside timed measurement. */
    if (!order) { /* Refuse to benchmark an incomplete dependency cycle. */
        fprintf(stderr, "permutation allocation failed\n"); /* Make the failure distinguishable from measurement noise. */
        exit(2); /* Stop rather than silently change memory-access behavior. */
    } /* End allocation guard. */
    for (size_t i = 0; i < slots; ++i) order[i] = (uint32_t)i; /* Cover every configured slot exactly once. */
    if (randomize) { /* Randomize traversal while keeping identical footprint. */
        uint64_t state = seed ? seed : UINT64_C(0x9e3779b97f4a7c15); /* Prevent the zero-state generator trap. */
        for (size_t i = slots - 1; i > 0; --i) { /* Fisher-Yates produces a true permutation, not random repeats. */
            size_t j = (size_t)(rng_next(&state) % (i + 1)); /* Select a predecessor without touching timed data. */
            uint32_t tmp = order[i]; /* Save a slot index before swapping. */
            order[i] = order[j]; /* Move an index to a randomly chosen location. */
            order[j] = tmp; /* Complete a bijective shuffle. */
        } /* Finish building the random order. */
    } /* End randomness branch. */
    for (size_t i = 0; i < slots; ++i) { /* Link every cache-line-separated node into one loop. */
        size_t physical = (size_t)order[i] * gap; /* Translate the logical slot to a real array index. */
        size_t following = (size_t)order[(i + 1) % slots] * gap; /* Choose next address with no discontinuity. */
        next[physical] = (uint32_t)following; /* Store the next pointer as an array index, not a virtual address. */
    } /* Finish pointer chain construction. */
    free(order); /* Remove permutation scratch footprint before timing. */
} /* Finish deterministic single-cycle construction. */
__attribute__((noinline)) uint32_t memory_chase(const uint32_t *next, uint32_t start, size_t steps) { /* Force each load to depend on its predecessor. */
    uint32_t cursor = start; /* Initialize the dependent chain outside the timed loop body. */
    for (size_t i = 0; i < steps; ++i) cursor = next[cursor]; /* Prevent speculative parallel traversal of the chain. */
    memory_sink ^= cursor; /* Block dead-code elimination without a store per measured access. */
    return cursor; /* Expose the final state for correctness checks. */
} /* Complete serialized pointer chasing. */
__attribute__((noinline)) uint64_t memory_parallel(const uint32_t *next, const uint32_t *starts, unsigned lanes, size_t rounds) { /* Vary outstanding independent streams. */
    uint32_t cursor[32]; /* Bound register-pressure study to at most 32 chains. */
    for (unsigned j = 0; j < lanes; ++j) cursor[j] = starts[j]; /* Start chains at separated points on the same cycle. */
    for (size_t i = 0; i < rounds; ++i) { /* Repeat a fixed amount of work for each intensity level. */
        for (unsigned j = 0; j < lanes; ++j) cursor[j] = next[cursor[j]]; /* Dependencies remain serial within each stream. */
    } /* Finish all requested round trips. */
    uint64_t checksum = 0; /* Accumulate the outputs after timing-relevant loads. */
    for (unsigned j = 0; j < lanes; ++j) checksum += cursor[j]; /* Make each chain's result architecturally observable. */
    memory_sink ^= checksum; /* Defeat elimination of any otherwise-unused stream. */
    return checksum; /* Allow the harness to verify deterministic traversal. */
} /* End multiple-chain latency/throughput kernel. */
__attribute__((noinline)) uint64_t memory_stream(uint64_t *dst, const uint64_t *src, const uint32_t *order, size_t count, size_t stride, unsigned read_percent, size_t rounds) { /* Control read-versus-write operations explicitly. */
    uint64_t checksum = 0; /* Consume reads without an extra write for each access. */
    for (size_t r = 0; r < rounds; ++r) { /* Amortize clock overhead while preserving the chosen footprint. */
        for (size_t i = 0; i < count; ++i) { /* Visit configured distinct positions each pass. */
            size_t slot = order ? (size_t)order[i] : i; /* Separate random ordering from physical stride. */
            size_t offset = slot * stride; /* Set access granularity in 64-bit elements. */
            if (((i + r) % 100) < read_percent) { /* Select the requested read percentage by access count. */
                checksum += src[offset]; /* Force the requested read to execute and affect a live result. */
            } else { /* Perform a write without secretly reading the destination. */
                dst[offset] = (uint64_t)(i + r + 1); /* Change stored data to make destination writes observable. */
            } /* Preserve true read-only and write-only endpoint cases. */
        } /* Complete one memory traversal. */
    } /* Finish all passes. */
    memory_sink ^= checksum; /* Keep read results visible across benchmark calls. */
    return checksum; /* Permit checksums to detect invalid optimizations. */
} /* End mixed sequential/random memory throughput kernel. */
