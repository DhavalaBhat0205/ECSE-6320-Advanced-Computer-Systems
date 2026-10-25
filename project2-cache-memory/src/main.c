#define _GNU_SOURCE /* Enable Linux-specific huge-page advisory APIs. */
#include "memory_kernels.h" /* Call separately compiled kernels for disassembly inspection. */
#include <errno.h> /* Identify failed allocation and clock operations. */
#include <inttypes.h> /* Print fixed-width checksums without undefined formatting. */
#include <limits.h> /* Bound index arithmetic to supported widths. */
#include <stdint.h> /* Maintain comparable address and byte widths. */
#include <stdio.h> /* Emit structured CSV and errors. */
#include <stdlib.h> /* Allocate aligned buffers and parse numeric arguments. */
#include <string.h> /* Parse stable operation names. */
#include <time.h> /* Use monotonic raw time independent of wall-clock adjustments. */
#include <sys/mman.h> /* Request transparent huge pages without claiming adoption. */
#include <unistd.h> /* Query the Linux host's page size. */
static double now_ns(void) { /* Minimize timer conversion error across samples. */
    struct timespec t; /* Store seconds and nanoseconds without truncation. */
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &t)) { /* Detect unavailable or failed raw-clock operations. */
        perror("clock_gettime"); /* Keep failures separate from apparent zero-latency results. */
        exit(2); /* A benchmark with a failed timer must never be recorded. */
    } /* Finish timer error handling. */
    return (double)t.tv_sec * 1e9 + (double)t.tv_nsec; /* Express elapsed time in nanoseconds. */
} /* End monotonic measurement clock. */
static size_t parse_size(const char *s, const char *name) { /* Reject silent overflow and malformed CLI sizes. */
    char *end = NULL; /* Track the full decimal representation. */
    errno = 0; /* Clear stale errors before integer conversion. */
    unsigned long long n = strtoull(s, &end, 10); /* Parse positive byte and iteration counts. */
    if (errno || end == s || *end || n == 0 || n > SIZE_MAX) { /* Validate both syntax and address range. */
        fprintf(stderr, "invalid %s: %s\n", name, s); /* Make input problems actionable. */
        exit(2); /* Never benchmark silently truncated memory sizes. */
    } /* End numeric validation. */
    return (size_t)n; /* Use native size width after checking overflow. */
} /* End strict positive integer parser. */
static void *buffer(size_t bytes, int huge) { /* Allocate cache-line-aligned first-touch storage. */
    void *p = NULL; /* Avoid treating allocation failure as zero pointer work. */
    if (posix_memalign(&p, 4096, bytes)) { /* Align pages so stride footprints are predictable. */
        fprintf(stderr, "allocation failed for %zu bytes\n", bytes); /* Identify resource-limited runs. */
        exit(2); /* Keep failed samples out of CSV. */
    } /* Complete allocation check. */
    if (huge) { /* Treat transparent huge pages as an advisory experiment. */
        if (madvise(p, bytes, MADV_HUGEPAGE)) fprintf(stderr, "warning: huge-page advice rejected\n"); /* Report rejection without assuming page size. */
    } /* End optional Linux page hint. */
    return p; /* Return ready-to-touch aligned storage. */
} /* End deterministic aligned allocation. */
static void help(const char *p) { /* Document all experiment knobs in the executable itself. */
    fprintf(stderr, "usage: %s <latency|mlp|bandwidth> <bytes> <stride_bytes> <random:0|1> <read_percent:0..100> <lanes:1..32> <samples> <steps_or_passes> <huge:0|1> <seed>\n", p); /* Expose positional protocol to wrappers. */
    exit(2); /* Exit nonzero when parameters are missing. */
} /* End command usage. */
int main(int argc, char **argv) { /* Run one reproducible configuration per process. */
    if (argc != 11) help(argv[0]); /* Avoid ambiguous defaults that contaminate results. */
    const char *mode = argv[1]; /* Identify latency, MLP, or mixed-bandwidth experiment. */
    size_t bytes = parse_size(argv[2], "bytes"); /* Determine the nominal data array footprint. */
    size_t stride_b = parse_size(argv[3], "stride_bytes"); /* Control distance between visited memory positions. */
    size_t random = (size_t)strtoull(argv[4], NULL, 10); /* Distinguish streaming from reordered accesses. */
    size_t read_pct = (size_t)strtoull(argv[5], NULL, 10); /* Preserve zero for all-write measurements. */
    size_t lanes = parse_size(argv[6], "lanes"); /* Set independent loads for Little's Law study. */
    size_t samples = parse_size(argv[7], "samples"); /* Retain per-trial output for error bars. */
    size_t work = parse_size(argv[8], "steps_or_passes"); /* Fix measured iterations independently of timing. */
    size_t huge = (size_t)strtoull(argv[9], NULL, 10); /* Record explicit THP advisory setting. */
    uint64_t seed = (uint64_t)strtoull(argv[10], NULL, 10); /* Reproduce the random permutation. */
    if (random > 1 || huge > 1 || read_pct > 100 || lanes > 32 || stride_b < 8 || stride_b % 8 || bytes < stride_b || bytes % stride_b) { /* Reject unsupported physical strides. */
        fprintf(stderr, "invalid option combination\n"); /* Show explicit invalid-configuration result. */
        return 2; /* Ensure wrappers cannot mistake errors for measurements. */
    } /* End common parameter checks. */
    int latency = !strcmp(mode, "latency"); /* Use serialized pointer chasing for zero-queue baseline. */
    int mlp = !strcmp(mode, "mlp"); /* Use independent pointer chains to vary outstanding work. */
    int bw = !strcmp(mode, "bandwidth"); /* Use explicit read/write memory operations for throughput. */
    if (!(latency || mlp || bw)) help(argv[0]); /* Prevent unimplemented modes from producing rows. */
    size_t slots = bytes / stride_b; /* Count distinct visited positions. */
    size_t stride = stride_b / 8; /* Convert requested byte distance to 64-bit element indices. */
    if (slots < 2 || slots > (size_t)UINT32_MAX / (stride_b / 4)) { /* Keep 32-bit indices and shuffle allocation safe. */
        fprintf(stderr, "array exceeds supported index range or has too few slots\n"); /* Report bounds rather than overflow. */
        return 2; /* Refuse invalid pointer arithmetic. */
    } /* Finish footprint safety check. */
    uint64_t *src = buffer(bytes, (int)huge); /* Create page-aligned input and index-buffer backing. */
    uint64_t *dst = bw ? buffer(bytes, (int)huge) : NULL; /* Avoid a second array for latency-only tests. */
    for (size_t i = 0; i < bytes / 8; ++i) src[i] = (uint64_t)(i * 17 + 7); /* Pre-fault and initialize every input page. */
    if (bw) for (size_t i = 0; i < bytes / 8; ++i) dst[i] = 0; /* Pre-fault output pages before measurement. */
    uint32_t *order = NULL; /* Keep optional random-index metadata separate from payload. */
    uint32_t *links = (uint32_t *)src; /* Reuse storage as dependent pointer indices for chasing. */
    if (latency || mlp) { /* Construct a full cycle for dependent-load experiments. */
        if (stride_b % 4) { fprintf(stderr, "invalid pointer stride\n"); return 2; } /* Enforce exact 32-bit slot indices. */
        memory_build_chain(links, slots, stride_b / 4, (int)random, seed); /* Include deterministic address permutation. */
    } else if (random) { /* Build separate index order for randomized throughput. */
        order = malloc(slots * sizeof(*order)); /* Allocate index metadata outside timed kernel. */
        if (!order) { fprintf(stderr, "order allocation failed\n"); return 2; } /* Refuse an accidental sequential fallback. */
        for (size_t i = 0; i < slots; ++i) order[i] = (uint32_t)i; /* Visit every configured physical offset once. */
        uint64_t state = seed ? seed : UINT64_C(1); /* Seed a deterministic index shuffle. */
        for (size_t i = slots - 1; i > 0; --i) { /* Shuffle using reproducible xorshift state. */
            state ^= state >> 12; state ^= state << 25; state ^= state >> 27; /* Advance without libc's unspecified rand sequence. */
            size_t j = (size_t)((state * UINT64_C(2685821657736338717)) % (i + 1)); /* Choose unbiased-enough experimental slot order. */
            uint32_t tmp = order[i]; order[i] = order[j]; order[j] = tmp; /* Exchange indexes without moving payload bytes. */
        } /* Finish random traversal selection. */
    } /* End randomized order initialization. */
    uint32_t starts[32] = {0}; /* Hold independent-stream starting offsets. */
    if (mlp) { /* Sample well-separated logical offsets for multiple dependent chains. */
        uint32_t cur = 0; /* Walk pre-built cycle without violating its order. */
        for (unsigned j = 0; j < lanes; ++j) { /* Create chain starts in distinct cycle regions. */
            starts[j] = cur; /* Snapshot this lane's first pointer. */
            size_t advance = slots / lanes; /* Space lanes rather than sharing the same cache lines immediately. */
            for (size_t k = 0; k < advance; ++k) cur = links[cur]; /* Traverse pointer dependency outside timing. */
        } /* End start-offset construction. */
    } /* Finish MLP configuration. */
    if (latency) (void)memory_chase(links, 0, slots); /* Prime all visited data ahead of cold-neutral measurements. */
    if (mlp) (void)memory_parallel(links, starts, (unsigned)lanes, slots); /* Prime pages and instruction cache. */
    if (bw) (void)memory_stream(dst, src, order, slots, stride, (unsigned)read_pct, 1); /* Prime page tables and stores. */
    printf("mode,bytes,stride_bytes,random,read_percent,lanes,huge_advice,seed,sample,accesses,elapsed_ns,ns_per_access,useful_gib_s,checksum\n"); /* Define a machine-readable stable schema. */
    for (size_t sample = 0; sample < samples; ++sample) { /* Preserve every repeat for uncertainty calculations. */
        double begin = now_ns(); /* Take timestamp immediately before measured work. */
        uint64_t check = 0; /* Return a value so each kernel's work is observable. */
        if (latency) check = memory_chase(links, 0, work); /* Execute work serially through one dependency. */
        if (mlp) check = memory_parallel(links, starts, (unsigned)lanes, work); /* Execute independent streams for loaded latency. */
        if (bw) check = memory_stream(dst, src, order, slots, stride, (unsigned)read_pct, work); /* Execute chosen read/write mix. */
        double elapsed = now_ns() - begin; /* Measure elapsed wall-clock duration without cycle-frequency assumptions. */
        size_t accesses = bw ? slots * work : mlp ? work * lanes : work; /* Count actual requested load or store operations. */
        if (!(elapsed > 0.0) || accesses == 0) { fprintf(stderr, "invalid timing\n"); return 2; } /* Exclude zero and overflow anomalies. */
        double gib_s = (double)accesses * 8.0 * 1e9 / (elapsed * 1073741824.0); /* Count useful 8-byte operations, not DRAM bus traffic. */
        printf("%s,%zu,%zu,%zu,%zu,%zu,%zu,%" PRIu64 ",%zu,%zu,%.3f,%.6f,%.6f,%" PRIu64 "\n", mode, bytes, stride_b, random, read_pct, lanes, huge, seed, sample, accesses, elapsed, elapsed / (double)accesses, gib_s, check); /* Emit one honest trial with explicit units. */
    } /* Finish raw repeated observations. */
    free(order); /* Release randomized traversal metadata only after measurement. */
    free(dst); /* Release optional output buffer. */
    free(src); /* Release backing pages and pointers. */
    return 0; /* Signal a fully completed experiment. */
} /* End single-configuration benchmark driver. */
