#define _GNU_SOURCE /* Expose O_DIRECT and POSIX interfaces required by this Linux-only experiment. */
#include <errno.h> /* Preserve exact failure information for direct I/O errors. */
#include <fcntl.h> /* Open a file with O_DIRECT without touching a block device. */
#include <inttypes.h> /* Print fixed-width integers without format ambiguity. */
#include <math.h> /* Check numeric inputs and summary statistics. */
#include <pthread.h> /* Model independent synchronous I/O submitters. */
#include <stdatomic.h> /* Publish failures safely between worker threads. */
#include <stdbool.h> /* Make access-mode and validation decisions explicit. */
#include <stdint.h> /* Use fixed-width random state and timestamps. */
#include <stdio.h> /* Produce machine-readable measurements. */
#include <stdlib.h> /* Allocate aligned I/O buffers and histograms. */
#include <string.h> /* Parse modes and clear worker state. */
#include <sys/stat.h> /* Reject files that are not ordinary regular files. */
#include <time.h> /* Use elapsed monotonic time, not variable CPU frequency. */
#include <unistd.h> /* Issue pread/pwrite system calls on the disposable file. */

#define ALIGNMENT 4096UL /* Match direct-I/O alignment required by the test filesystem. */
#define MAX_WORKERS 64U /* Bound concurrency and per-worker memory consumption. */
#define MAX_BLOCK_SIZE (1024UL * 1024UL) /* Limit the largest aligned I/O buffer to 1 MiB. */

typedef struct { /* Retain individual observations to compute real tail percentiles. */
    double *values; /* A growable latency array avoids fabricated percentile estimates. */
    size_t used; /* The number of valid completion latency observations. */
    size_t capacity; /* The currently allocated sample capacity. */
} SampleSet; /* One set is maintained for each direction and each worker. */

typedef struct { /* Carry only immutable configuration and isolated worker results. */
    const char *path; /* A preexisting, non-symlink regular file inside the scratch directory. */
    uint64_t file_bytes; /* Bound all offsets to the disposable working-set length. */
    uint64_t block_bytes; /* Require a multiple of 4096 for direct I/O. */
    uint64_t deadline_ns; /* Stop when the monotonic clock passes the common deadline. */
    uint64_t rng; /* Give each worker a deterministic independent random stream. */
    uint64_t seq; /* Each worker advances its own sequential offset. */
    unsigned read_percent; /* Set random read/write probability independently per I/O. */
    bool random_access; /* Toggle sequential and random block offsets. */
    atomic_int *error; /* Share the first failure without a data race. */
    bool direct; /* Keep direct and intentionally buffered modes distinguishable. */
    SampleSet reads; /* Capture all completed read durations. */
    SampleSet writes; /* Capture all completed write durations. */
    uint64_t read_bytes; /* Count actual bytes returned by read operations. */
    uint64_t write_bytes; /* Count actual bytes accepted by write operations. */
} Worker; /* Synchronous calls mean workers are NOT an fio async iodepth. */

static uint64_t monotonic_ns(void) { /* Keep all elapsed measurements on one monotonic clock. */
    struct timespec ts; /* Read nanoseconds with a POSIX clock. */
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) { perror("clock_gettime"); exit(2); } /* Do not silently use an unreliable clock. */
    return (uint64_t)ts.tv_sec * UINT64_C(1000000000) + (uint64_t)ts.tv_nsec; /* Avoid floating-point accumulation in timing. */
} /* Timing helper. */

static uint64_t next_rand(uint64_t *state) { /* Use inexpensive deterministic offsets without libc global RNG state. */
    uint64_t x = *state; /* Read the worker-local generator state. */
    x ^= x >> 12; /* Diffuse low-order bits before forming block offsets. */
    x ^= x << 25; /* Advance the xorshift recurrence. */
    x ^= x >> 27; /* Prevent short runs of repeated addresses. */
    *state = x; /* Store the next state for this worker. */
    return x * UINT64_C(2685821657736338717); /* Mix bits for uniform bounded block selection. */
} /* Random helper. */

static bool add_sample(SampleSet *samples, double latency_us) { /* Retain per-I/O samples for p99.9 rather than inferring tail latency. */
    if (samples->used == samples->capacity) { /* Grow only when the actual count exceeds current capacity. */
        size_t next = samples->capacity ? samples->capacity * 2U : 4096U; /* Amortize allocations outside the tight I/O path. */
        if (next < samples->capacity || next > SIZE_MAX / sizeof(double)) return false; /* Guard allocation overflow. */
        double *new_values = realloc(samples->values, next * sizeof(double)); /* Extend only this worker's independent array. */
        if (!new_values) return false; /* Report memory exhaustion rather than losing samples. */
        samples->values = new_values; /* Publish the enlarged latency store. */
        samples->capacity = next; /* Record the number of usable entries. */
    } /* Growth branch. */
    samples->values[samples->used++] = latency_us; /* Preserve exact observed completion latency. */
    return true; /* Signal success to the worker loop. */
} /* Sample collection. */

static void *worker_main(void *arg) { /* Each worker issues only one blocking direct-I/O request at a time. */
    Worker *w = (Worker *)arg; /* Access one thread's disjoint measurement state. */
    int fd = open(w->path, O_RDWR | (w->direct ? O_DIRECT : 0) | O_CLOEXEC | O_NOFOLLOW); /* Switch cache bypass only for the controlled pitfall comparison. */
    if (fd < 0) { perror("direct open"); atomic_store(w->error, 1); return NULL; } /* Reject unsupported direct I/O rather than changing methodology. */
    void *buffer = NULL; /* Hold a correctly aligned transfer buffer. */
    if (posix_memalign(&buffer, ALIGNMENT, (size_t)w->block_bytes) != 0) { atomic_store(w->error, 1); close(fd); return NULL; } /* Enforce kernel alignment. */
    uint64_t *words = (uint64_t *)buffer; /* Initialize deterministic incompressible-like bytes for writes. */
    for (size_t i = 0; i < (size_t)w->block_bytes / sizeof(uint64_t); i++) words[i] = next_rand(&w->rng); /* Avoid an all-zero compressed payload. */
    const uint64_t blocks = w->file_bytes / w->block_bytes; /* Select only complete aligned blocks. */
    while (!atomic_load(w->error)) { /* Stop if any thread observes an I/O or memory error. */
        if (monotonic_ns() >= w->deadline_ns) break; /* Respect the common elapsed-time limit. */
        bool do_read = (unsigned)(next_rand(&w->rng) % 100U) < w->read_percent; /* Apply the requested probabilistic read mix. */
        uint64_t index = w->random_access ? next_rand(&w->rng) % blocks : w->seq++ % blocks; /* Keep all offsets within the scratch file. */
        off_t offset = (off_t)(index * w->block_bytes); /* Translate a block index into an aligned byte position. */
        uint64_t begin = monotonic_ns(); /* Start timing immediately before the blocking system call. */
        ssize_t done = do_read ? pread(fd, buffer, (size_t)w->block_bytes, offset) : pwrite(fd, buffer, (size_t)w->block_bytes, offset); /* Submit one synchronous request. */
        uint64_t end = monotonic_ns(); /* Include syscall and device-stack completion time. */
        if (done != (ssize_t)w->block_bytes) { perror(do_read ? "pread" : "pwrite"); atomic_store(w->error, 1); break; } /* Never count a short transfer as completed work. */
        SampleSet *dst = do_read ? &w->reads : &w->writes; /* Maintain direction-specific tail distributions. */
        if (!add_sample(dst, (double)(end - begin) / 1000.0)) { atomic_store(w->error, 1); break; } /* Record microseconds without rounding. */
        if (do_read) w->read_bytes += w->block_bytes; else w->write_bytes += w->block_bytes; /* Account only actual transfer bytes. */
    } /* Timed direct-I/O loop. */
    free(buffer); /* Release the aligned transfer allocation. */
    close(fd); /* Return the scratch-file descriptor. */
    return NULL; /* Return results through thread-local structures. */
} /* Worker implementation. */

static int compare_double(const void *a, const void *b) { /* Supply qsort with a total ordering on finite positive durations. */
    double x = *(const double *)a, y = *(const double *)b; /* Access one pair of completed I/O latencies. */
    return (x > y) - (x < y); /* Avoid overflow from subtracting floating-point values. */
} /* Sorting comparator. */

static double percentile(SampleSet *s, double fraction) { /* Use nearest-rank interpolation for the observed latency sample. */
    if (!s->used) return 0.0; /* A direction with no I/O has no valid latency percentile. */
    double pos = fraction * (double)(s->used - 1); /* Map percentiles into the sorted observation interval. */
    size_t lower = (size_t)floor(pos), upper = (size_t)ceil(pos); /* Identify neighboring empirical order statistics. */
    return s->values[lower] + (s->values[upper] - s->values[lower]) * (pos - (double)lower); /* Interpolate rather than using a guessed distribution. */
} /* Tail statistic. */

static void print_direction(const char *label, SampleSet *s, uint64_t bytes, double seconds) { /* Emit a stable JSON field group. */
    double sum = 0.0; /* Integrate actual latencies into the average. */
    for (size_t i = 0; i < s->used; i++) sum += s->values[i]; /* Preserve all observed latency contributions. */
    qsort(s->values, s->used, sizeof(double), compare_double); /* Rank samples before computing tail percentiles. */
    printf("  \"%s\": {\"operations\": %zu, \"bytes\": %" PRIu64 ", \"iops\": %.8f, \"bw_mib_s\": %.8f, \"mean_us\": %.8f, \"p50_us\": %.8f, \"p95_us\": %.8f, \"p99_us\": %.8f, \"p999_us\": %.8f, \"max_us\": %.8f}",
        label, s->used, bytes, (double)s->used / seconds, (double)bytes / seconds / 1048576.0,
        s->used ? sum / (double)s->used : 0.0, percentile(s, .50), percentile(s, .95), percentile(s, .99), percentile(s, .999), s->used ? s->values[s->used - 1] : 0.0); /* Label all throughput and latency units explicitly. */
} /* JSON formatter. */

static bool parse_u64(const char *s, uint64_t *out) { /* Reject partial and negative numeric inputs before allocating memory. */
    if (!s || !*s || *s == '-') return false; /* Do not accept negative geometry or missing arguments. */
    errno = 0; /* Reset errno before conversion. */
    char *end = NULL; /* Receive the end of the validated decimal string. */
    unsigned long long v = strtoull(s, &end, 10); /* Parse only integer parameters. */
    if (errno || !end || *end) return false; /* Reject overflow and trailing text. */
    *out = (uint64_t)v; /* Return the checked integer. */
    return true; /* Indicate a fully parsed value. */
} /* Argument validator. */

int main(int argc, char **argv) { /* Take only an already initialized regular file as the benchmark target. */
    if (argc != 9 && argc != 10) { fprintf(stderr, "usage: %s SCRATCH_FILE MODE BLOCK_BYTES FILE_BYTES THREADS READ_PERCENT SECONDS_MILLI SEED [DIRECT_1_OR_0]\n", argv[0]); return 2; } /* Publish a complete deterministic command schema. */
    const char *path = argv[1]; /* Point exclusively to the benchmark file passed by the guarded runner. */
    const char *tag = strstr(path, "/data/scratch/disposable-"); /* Require the dedicated disposable-filename convention. */
    if (!tag || strlen(tag + strlen("/data/scratch/disposable-")) != 36 || strcmp(path + strlen(path) - 4, ".bin") != 0) { fputs("refusing target outside disposable scratch naming convention\n", stderr); return 2; } /* Prevent accidental execution on personal filenames. */
    for (const char *c = tag + strlen("/data/scratch/disposable-"); c < tag + strlen("/data/scratch/disposable-") + 32; c++) if (!((*c >= 'a' && *c <= 'f') || (*c >= '0' && *c <= '9'))) { fputs("invalid scratch token\n", stderr); return 2; } /* Require the runner-generated 128-bit hex token. */
    bool direct = argc == 9 || !strcmp(argv[9], "1"); /* Use direct I/O by default, with buffered reads only as an explicit negative control. */
    if (argc == 10 && strcmp(argv[9], "0") && strcmp(argv[9], "1")) { fputs("invalid direct toggle\n", stderr); return 2; } /* Avoid silently changing the I/O path. */
    bool random_access = strstr(argv[2], "rand") != NULL; /* Preserve random or sequential access semantics. */
    bool valid_mode = !strcmp(argv[2], "read") || !strcmp(argv[2], "write") || !strcmp(argv[2], "randread") || !strcmp(argv[2], "randwrite") || !strcmp(argv[2], "randrw"); /* Reject any unrecognized operation. */
    if (!valid_mode) { fputs("invalid access mode\n", stderr); return 2; } /* Prevent silent mode substitution. */
    uint64_t bs = 0, file_bytes = 0, threads = 0, rp = 0, duration_ms = 0, seed = 0; /* Receive bounds-checked configuration. */
    if (!parse_u64(argv[3], &bs) || !parse_u64(argv[4], &file_bytes) || !parse_u64(argv[5], &threads) || !parse_u64(argv[6], &rp) || !parse_u64(argv[7], &duration_ms) || !parse_u64(argv[8], &seed)) { fputs("invalid numeric argument\n", stderr); return 2; } /* Fail closed on malformed parameters. */
    if (!bs || bs > MAX_BLOCK_SIZE || bs % ALIGNMENT || file_bytes < bs || file_bytes % bs || threads < 1 || threads > MAX_WORKERS || rp > 100 || duration_ms < 50 || duration_ms > 3600000 || !seed) { fputs("unsupported I/O geometry or runtime\n", stderr); return 2; } /* Block unsafe or misleading configurations. */
    if (!strcmp(argv[2], "read") || !strcmp(argv[2], "randread")) rp = 100; /* Force 100% reads for read-only modes. */
    if (!strcmp(argv[2], "write") || !strcmp(argv[2], "randwrite")) rp = 0; /* Force 100% writes for write-only modes. */
    struct stat file_st; /* Inspect the actual target before opening O_DIRECT. */
    if (lstat(path, &file_st) != 0 || !S_ISREG(file_st.st_mode) || file_st.st_size < 0 || (uint64_t)file_st.st_size < file_bytes || file_st.st_nlink != 1) { fputs("target must be a preexisting, single-link regular scratch file of sufficient size\n", stderr); return 2; } /* Refuse block devices, symlinks, and hardlinked data. */
    pthread_t *ids = calloc((size_t)threads, sizeof(pthread_t)); /* Allocate one identity per submitter. */
    Worker *workers = calloc((size_t)threads, sizeof(Worker)); /* Isolate all per-thread latency arrays to avoid locking the fast path. */
    if (!ids || !workers) { fputs("allocation failure\n", stderr); free(ids); free(workers); return 2; } /* Check memory pressure before issuing I/O. */
    atomic_int error = ATOMIC_VAR_INIT(0); /* Publish the first I/O failure across workers. */
    uint64_t start = monotonic_ns(); /* Start all workers from one common measurement epoch. */
    uint64_t deadline = start + duration_ms * UINT64_C(1000000); /* Convert requested milliseconds to nanoseconds. */
    unsigned created = 0; /* Track only successfully launched threads. */
    for (unsigned i = 0; i < (unsigned)threads; i++) { /* Create independent synchronous direct-I/O streams. */
        Worker *w = &workers[i]; /* Configure the current worker. */
        w->path = path; w->file_bytes = file_bytes; w->block_bytes = bs; w->deadline_ns = deadline; /* Keep all workers on the same bounded file and clock. */
        w->rng = seed ^ (UINT64_C(0x9e3779b97f4a7c15) * (uint64_t)(i + 1)); /* Avoid identical random offsets between workers. */
        w->seq = (uint64_t)i * 104729U; w->read_percent = (unsigned)rp; w->random_access = random_access; w->error = &error; w->direct = direct; /* Separate sequential starting points and read probabilities. */
        if (pthread_create(&ids[i], NULL, worker_main, w) != 0) { atomic_store(&error, 1); break; } /* Stop construction on failure. */
        created++; /* Remember how many workers must be joined. */
    } /* Launch loop. */
    for (unsigned i = 0; i < created; i++) pthread_join(ids[i], NULL); /* Collect every finished I/O sample before reporting. */
    uint64_t end = monotonic_ns(); /* Measure actual elapsed time including synchronization overhead. */
    double seconds = (double)(end - start) / 1e9; /* Convert elapsed nanoseconds to throughput denominator. */
    size_t rc = 0, wc = 0; uint64_t rb = 0, wb = 0; /* Track independent read and write totals. */
    for (unsigned i = 0; i < created; i++) { rc += workers[i].reads.used; wc += workers[i].writes.used; rb += workers[i].read_bytes; wb += workers[i].write_bytes; } /* Aggregate only completed requests. */
    SampleSet read_all = {0}, write_all = {0}; /* Assemble empirical distributions for mixed workloads. */
    read_all.values = malloc((rc ? rc : 1) * sizeof(double)); read_all.capacity = rc; /* Reserve exactly the completed read observations. */
    write_all.values = malloc((wc ? wc : 1) * sizeof(double)); write_all.capacity = wc; /* Reserve exactly the completed write observations. */
    if (!read_all.values || !write_all.values) atomic_store(&error, 1); /* Do not publish incomplete latency statistics. */
    if (!atomic_load(&error)) for (unsigned i = 0; i < created; i++) { /* Concatenate each worker's exact samples without averaging percentiles. */
        SampleSet *r = &workers[i].reads, *w = &workers[i].writes; /* Access this worker's disjoint samples. */
        memcpy(read_all.values + read_all.used, r->values, r->used * sizeof(double)); read_all.used += r->used; /* Preserve the complete read distribution. */
        memcpy(write_all.values + write_all.used, w->values, w->used * sizeof(double)); write_all.used += w->used; /* Preserve the complete write distribution. */
    } /* Distribution aggregation. */
    if (!atomic_load(&error)) { /* Emit results only if all direct I/O operations succeeded. */
        printf("{\n \"engine\": \"native-posix-sync\", \"direct\": %s, \"mode\": \"%s\", \"block_bytes\": %" PRIu64 ", \"file_bytes\": %" PRIu64 ", \"workers\": %" PRIu64 ", \"read_percent\": %" PRIu64 ", \"runtime_s\": %.8f, \"seed\": %" PRIu64 ",\n", direct ? "true" : "false", argv[2], bs, file_bytes, threads, rp, seconds, seed); /* Disclose synchronous-worker concurrency explicitly. */
        print_direction("read", &read_all, rb, seconds); printf(",\n"); /* Print read-side IOPS, throughput, and latency percentiles. */
        print_direction("write", &write_all, wb, seconds); printf("\n}\n"); /* Preserve separate write-side tail latency in mixed workloads. */
    } /* JSON result output. */
    for (unsigned i = 0; i < created; i++) { free(workers[i].reads.values); free(workers[i].writes.values); } /* Free all per-worker samples. */
    free(read_all.values); free(write_all.values); free(workers); free(ids); /* Free aggregate arrays and worker structures. */
    return atomic_load(&error) ? 1 : 0; /* Make a failed workload detectable to automation. */
} /* End of native direct-I/O timing harness. */
