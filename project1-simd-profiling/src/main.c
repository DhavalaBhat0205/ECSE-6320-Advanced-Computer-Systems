#define _GNU_SOURCE // Expose Linux-specific perf-event and affinity-compatible interfaces.
#include "kernels.h" // Compile the exact same kernel ABI for every ISA variant.
#include <errno.h> // Diagnose invalid inputs and denied performance-counter access.
#include <inttypes.h> // Print portable 64-bit cycle and timestamp values.
#include <math.h> // Validate floating-point error and reject non-finite results.
#include <stdbool.h> // Keep control options independent of integer sentinel values.
#include <stdint.h> // Measure fixed-width TSC and hardware cycle counters.
#include <stdio.h> // Emit machine-readable CSV without a plotting dependency.
#include <stdlib.h> // Allocate aligned buffers and parse numeric CLI options.
#include <string.h> // Parse size lists and match explicitly supported kernel names.
#include <time.h> // Measure elapsed wall time with the Linux raw monotonic clock.
#include <unistd.h> // Close Linux performance-counter file descriptors.
#include <x86intrin.h> // Read the x86 TSC and bracket readings with LFENCE.
#ifdef __linux__ // Compile optional actual-core-cycle instrumentation on Linux.
#include <linux/perf_event.h> // Select the per-thread CPU-cycles hardware event.
#include <sys/ioctl.h> // Reset, enable, and disable performance counters precisely.
#include <sys/syscall.h> // Access perf_event_open without an external library.
#endif // Keep performance counters optional when kernel policy forbids them.
#ifndef BUILD_LABEL // Allow source inspection or direct local compilation.
#define BUILD_LABEL "unspecified" // Mark an unclassified binary instead of implying SIMD.
#endif // End build-label fallback.
#define MAX_SIZES 48U // Bound CLI-driven sweep complexity and stack allocations.
#define ALIGNMENT 64U // Allocate a cache-line-aligned base before intentional shifts.
#define DEFAULT_SIZES "256,1024,4096,16384,65536,262144,1048576,4194304" // Cover nominal cache and memory scales.
static volatile double observable_sink = 0.0; // Make reductions and final arrays observably used after timing.
typedef struct { // Group independent experimental knobs for one reproducible run.
    size_t sizes[MAX_SIZES]; // Store logical element counts, not physical array capacity.
    size_t n_sizes; // Make an empty or malformed size sweep impossible to overlook.
    size_t stride; // Specify physical element spacing as an integer of at least one.
    size_t offset; // Shift aligned base addresses by natural elements, never raw unaligned bytes.
    unsigned repetitions; // Keep independent batches available for error bars.
    double target_ms; // Control approximate measurement duration for each batch.
    const char *experiment; // Distinguish locality, alignment, stride, and smoke-test batches.
    const char *kernel; // Select saxpy, dot, mul, or all without recompilation.
    const char *dtype; // Select f32, f64, or both for SIMD-lane comparison.
    bool validate_only; // Permit inexpensive correctness tests before profiling.
    bool skip_perf; // Make a denied perf_event policy nonfatal and auditable.
    bool no_header; // Support concatenating multiple runs into one CSV.
} Options; // Preserve the exact selected knobs in each emitted record.
typedef struct { // Keep the allocation roots separate from shifted typed pointers.
    void *base[3]; // Retain unshifted addresses needed by free().
    void *ptr[3]; // Expose naturally aligned, intentionally shifted array views.
    size_t capacity; // Track the number of physical elements allocated per array.
    size_t allocated_bytes; // Report the full three-array allocation footprint.
} Buffers; // Avoid conflating useful traffic with allocated physical memory.
static void usage(const char *program) { // Document all controls that affect experimental reproducibility.
    fprintf(stderr, "Usage: %s [--sizes N,N,...] [--kernel all|saxpy|dot|mul] [--dtype both|f32|f64]\n", program); // Describe workload selection.
    fprintf(stderr, "       [--stride 1|2|4|8] [--offset-elements 0|1|...] [--repetitions 5]\n"); // Describe locality and alignment knobs.
    fprintf(stderr, "       [--target-ms 20] [--experiment locality] [--validate-only] [--no-perf] [--no-header]\n"); // Describe timing and output controls.
} // Finish CLI usage without running a benchmark.
static size_t parse_size(const char *text, const char *name) { // Reject negative, fractional, and overflowed element counts.
    errno = 0; // Clear stale errors before decimal conversion.
    char *end = NULL; // Retain the first invalid character for strict validation.
    unsigned long long parsed = strtoull(text, &end, 10); // Avoid silent atoi truncation.
    if (errno || text == end || *end || text[0] == '-' || parsed == 0 || parsed > SIZE_MAX) { // Accept only positive size_t values.
        fprintf(stderr, "invalid %s: %s\n", name, text); // Preserve the input that needs correction.
        exit(EXIT_FAILURE); // Never run a benchmark with a corrupted dimension.
    } // Close strict integer validation.
    return (size_t)parsed; // Narrow only after proving the representation is safe.
} // End checked integer parser.
static void parse_sizes(Options *options, const char *text) { // Turn a comma-separated workload sweep into a verified index list.
    char *copy = strdup(text); // Own writable storage instead of changing an argv string.
    if (!copy) { // Treat allocation failure as a hard setup error.
        perror("strdup"); // Surface the underlying system failure.
        exit(EXIT_FAILURE); // Avoid producing a partial experiment.
    } // Finish size-token allocation check.
    options->n_sizes = 0; // Reset sizes if --sizes appears more than once.
    char *save = NULL; // Store tokenizer progress without global strtok state.
    for (char *part = strtok_r(copy, ",", &save); part; part = strtok_r(NULL, ",", &save)) { // Parse one logical size at a time.
        if (options->n_sizes == MAX_SIZES) { // Prevent overrunning the fixed sweep buffer.
            fprintf(stderr, "too many sizes (maximum %u)\n", MAX_SIZES); // Explain the explicit bound.
            exit(EXIT_FAILURE); // Stop rather than dropping sizes silently.
        } // Finish list-capacity check.
        options->sizes[options->n_sizes++] = parse_size(part, "size"); // Validate each token before storage.
    } // Finish tokenizing the sweep.
    if (!options->n_sizes || text[0] == ',' || text[strlen(text) - 1] == ',' || strstr(text, ",,")) { // Reject empty tokens and lists.
        fprintf(stderr, "malformed size list: %s\n", text); // Describe the exact input error.
        exit(EXIT_FAILURE); // Reject ambiguous case coverage.
    } // Finish empty-list guard.
    free(copy); // Return temporary parsing storage to the allocator.
} // End size-list parser.
static Options get_options(int argc, char **argv) { // Capture reproducible controls once before any allocation.
    Options options = {.n_sizes = 0, .stride = 1, .offset = 0, .repetitions = 5, .target_ms = 20.0, .experiment = "manual", .kernel = "all", .dtype = "both", .validate_only = false, .skip_perf = false, .no_header = false}; // Define conservative defaults.
    parse_sizes(&options, DEFAULT_SIZES); // Populate a nominal L1-through-DRAM progression.
    for (int i = 1; i < argc; ++i) { // Read long-form options without silent positional arguments.
        const char *arg = argv[i]; // Keep each diagnostic tied to its actual token.
        if (!strcmp(arg, "--help")) { usage(argv[0]); exit(EXIT_SUCCESS); } // Provide local usage without measurement.
        if (!strcmp(arg, "--validate-only")) { options.validate_only = true; continue; } // Skip timing but still test every case.
        if (!strcmp(arg, "--no-perf")) { options.skip_perf = true; continue; } // Explicitly suppress restricted hardware events.
        if (!strcmp(arg, "--no-header")) { options.no_header = true; continue; } // Prevent duplicate CSV header rows.
        if (i + 1 == argc) { fprintf(stderr, "missing value for %s\n", arg); exit(EXIT_FAILURE); } // Reject incomplete option pairs.
        const char *value = argv[++i]; // Consume exactly one value for a valued option.
        if (!strcmp(arg, "--sizes")) { parse_sizes(&options, value); } // Allow tailored cache-boundary sweeps.
        else if (!strcmp(arg, "--stride")) { options.stride = parse_size(value, "stride"); } // Express spacing in elements.
        else if (!strcmp(arg, "--offset-elements")) { if (!strcmp(value, "0")) { options.offset = 0; } else { options.offset = parse_size(value, "offset"); } } // Accept zero or a positive natural-element shift.
        else if (!strcmp(arg, "--repetitions")) { size_t n = parse_size(value, "repetitions"); if (n > 1000) { fprintf(stderr, "repetitions exceed 1000\n"); exit(EXIT_FAILURE); } options.repetitions = (unsigned)n; } // Bound experiment run count.
        else if (!strcmp(arg, "--experiment")) { options.experiment = value; } // Tag data to prevent unrelated batches from being pooled.
        else if (!strcmp(arg, "--kernel")) { options.kernel = value; } // Select only supported workload identities below.
        else if (!strcmp(arg, "--dtype")) { options.dtype = value; } // Preserve the requested floating-point storage type.
        else if (!strcmp(arg, "--target-ms")) { char *end = NULL; errno = 0; options.target_ms = strtod(value, &end); if (errno || end == value || *end || !isfinite(options.target_ms) || options.target_ms <= 0.0 || options.target_ms > 60000.0) { fprintf(stderr, "invalid target-ms: %s\n", value); exit(EXIT_FAILURE); } } // Enforce finite, positive batch durations.
        else { fprintf(stderr, "unrecognized option: %s\n", arg); exit(EXIT_FAILURE); } // Reject typos before measuring anything.
    } // Finish parsing the entire command line.
    if (strcmp(options.kernel, "all") && strcmp(options.kernel, "saxpy") && strcmp(options.kernel, "dot") && strcmp(options.kernel, "mul")) { fprintf(stderr, "unknown kernel: %s\n", options.kernel); exit(EXIT_FAILURE); } // Require an implemented kernel.
    if (strcmp(options.dtype, "both") && strcmp(options.dtype, "f32") && strcmp(options.dtype, "f64")) { fprintf(stderr, "unknown dtype: %s\n", options.dtype); exit(EXIT_FAILURE); } // Require a supported data representation.
    if (!*options.experiment || strlen(options.experiment) > 48U) { fprintf(stderr, "invalid experiment identifier length\n"); exit(EXIT_FAILURE); } // Require a compact CSV-safe scenario label.
    for (const char *p = options.experiment; *p; ++p) { if (!( (*p >= 'a' && *p <= 'z') || (*p >= '0' && *p <= '9') || *p == '_' || *p == '-')) { fprintf(stderr, "experiment identifier must use lowercase letters, digits, underscore or hyphen\n"); exit(EXIT_FAILURE); } } // Prevent malformed CSV records.
    return options; // Return fully validated experiment selections.
} // End option parser.
static double monotonic_seconds(void) { // Use elapsed monotonic time instead of a frequency-derived guess.
    struct timespec stamp; // Preserve nanosecond resolution in the raw clock domain.
    if (clock_gettime(CLOCK_MONOTONIC_RAW, &stamp) != 0) { perror("clock_gettime"); exit(EXIT_FAILURE); } // Avoid silently returning an invalid measurement.
    return (double)stamp.tv_sec + (double)stamp.tv_nsec * 1e-9; // Convert the POSIX time fields to seconds.
} // End calibrated-independent wall-timer accessor.
static uint64_t read_tsc(void) { // Record invariant-TSC ticks as a *proxy*, not guaranteed core cycles.
    _mm_lfence(); // Order earlier loads and instructions ahead of the timestamp.
    uint64_t ticks = __rdtsc(); // Read the x86 reference counter without syscall overhead.
    _mm_lfence(); // Bound speculation around the timestamp sample.
    return ticks; // Return an unsigned 64-bit TSC timestamp.
} // End TSC sample helper.
static int open_cpu_cycles(void) { // Try Linux per-thread actual core-cycle counting, which may be restricted.
#ifdef __linux__ // Keep perf_event declarations within the supported operating system.
    struct perf_event_attr attr = {0}; // Start from a documented zeroed perf-event configuration.
    attr.size = sizeof(attr); // Supply the ABI structure size for kernel version compatibility.
    attr.type = PERF_TYPE_HARDWARE; // Use a CPU hardware performance-monitoring event.
    attr.config = PERF_COUNT_HW_CPU_CYCLES; // Request unhalted core cycles rather than TSC reference ticks.
    attr.disabled = 1; // Count only when an individual timed batch explicitly enables the event.
    attr.exclude_kernel = 1; // Avoid unrelated OS kernel execution in the requested user workload.
    attr.exclude_hv = 1; // Exclude hypervisor activity when the platform exposes that distinction.
    return (int)syscall(__NR_perf_event_open, &attr, 0, -1, -1, 0); // Bind counter to this one benchmark thread.
#else // Refuse to pretend hardware counters exist on unsupported operating systems.
    return -1; // Report unavailable core cycles while retaining reliable elapsed time.
#endif // End Linux-only performance counter setup.
} // End optional event-opening helper.
static double sample_x(size_t index) { // Use deterministic, binary-exact fractions without a random-number workload.
    return (double)((int)(index % 97U) - 48) / 64.0; // Keep values bounded and cheap to reproduce in a reference calculation.
} // End x-array initializer.
static double sample_y(size_t index) { // Make second input nonconstant and independent of the kernel result.
    return (double)((int)((index % 89U * 7U) % 89U) - 44) / 128.0; // Avoid overflow by reducing the index before multiplication.
} // End y-array initializer.
static void allocate_buffers(Buffers *b, size_t n, size_t stride, size_t offset, size_t element_bytes) { // Allocate enough memory for strided and shifted addresses.
    memset(b, 0, sizeof(*b)); // Make cleanup safe even after a failed partial allocation.
    if (offset >= SIZE_MAX - 1U || n > (SIZE_MAX - offset - 1U) / stride + 1U) { fprintf(stderr, "physical index calculation would overflow\n"); exit(EXIT_FAILURE); } // Bound index arithmetic.
    size_t last = (n - 1U) * stride; // Compute the last requested logical element's physical offset.
    if (last > SIZE_MAX - offset - 1U) { fprintf(stderr, "capacity overflow\n"); exit(EXIT_FAILURE); } // Guard addition after multiplication.
    b->capacity = last + offset + 1U; // Reserve offset elements before and at least one element at the last address.
    if (b->capacity > SIZE_MAX / element_bytes / 3U) { fprintf(stderr, "allocation size overflow\n"); exit(EXIT_FAILURE); } // Prevent bytes and total bytes overflow.
    size_t bytes = b->capacity * element_bytes; // Compute one complete physical array's storage requirement.
    b->allocated_bytes = 3U * bytes; // Distinguish allocated footprint from logical traffic.
    for (size_t a = 0; a < 3U; ++a) { // Keep input and output backing arrays nonaliasing.
        int rc = posix_memalign(&b->base[a], ALIGNMENT, bytes); // Start each buffer on a 64-byte boundary.
        if (rc) { fprintf(stderr, "posix_memalign failed (%d) for %zu bytes\n", rc, bytes); exit(EXIT_FAILURE); } // Stop if physical memory cannot be reserved.
        b->ptr[a] = (unsigned char *)b->base[a] + offset * element_bytes; // Shift by naturally aligned scalar elements.
    } // Finish allocating all three input/output streams.
} // End checked strided-alignment allocator.
static void release_buffers(Buffers *b) { // Prevent memory retention across cache-size cases.
    for (size_t a = 0; a < 3U; ++a) { free(b->base[a]); b->base[a] = NULL; b->ptr[a] = NULL; } // Release original bases, never shifted views.
} // End buffer cleanup.
static void initialize_buffers(Buffers *b, bool f64) { // Fault in backing pages and make all active inputs reproducible.
    for (size_t i = 0; i < b->capacity; ++i) { // Initialize holes too, making page placement independent of stride.
        if (f64) { ((double *)b->base[0])[i] = sample_x(i); ((double *)b->base[1])[i] = sample_y(i); ((double *)b->base[2])[i] = -777.0; } // Populate all double-precision physical slots.
        else { ((float *)b->base[0])[i] = (float)sample_x(i); ((float *)b->base[1])[i] = (float)sample_y(i); ((float *)b->base[2])[i] = -777.0f; } // Populate all float-precision physical slots.
    } // Finish faulting and initializing all physical elements.
} // End deterministic initialization.
static void reset_output(Buffers *b, bool f64, int kernel) { // Reset only mutable data outside the timed interval.
    if (kernel == 1) { return; } // A dot product writes no array output.
    for (size_t i = 0; i < b->capacity; ++i) { // Restore an identical initial state for every timed repetition.
        if (kernel == 0) { if (f64) { ((double *)b->base[1])[i] = sample_y(i); } else { ((float *)b->base[1])[i] = (float)sample_y(i); } } // Restore SAXPY's read-modify-write input.
        else { if (f64) { ((double *)b->base[2])[i] = -777.0; } else { ((float *)b->base[2])[i] = -777.0f; } } // Restore multiplication output sentinels.
    } // Finish restoration without counting its bandwidth as kernel time.
} // End output reset helper.
static double call_kernel(Buffers *b, bool f64, int kernel, size_t n, size_t stride) { // Dispatch work outside compiler-specialized numeric loops.
    if (f64) { // Call the independent double-precision kernel implementation.
        double *x = b->ptr[0]; double *y = b->ptr[1]; double *z = b->ptr[2]; // Bind typed views after alignment-safe allocation.
        if (kernel == 0) { kernel_saxpy_f64(n, 0.125, x, y, stride); return y[0]; } // Perform double SAXPY and retain a readback.
        if (kernel == 1) { return kernel_dot_f64(n, x, y, stride); } // Return the actual double reduction result.
        kernel_mul_f64(n, x, y, z, stride); return z[0]; // Run double multiply and retain an output readback.
    } // Finish float64 dispatch.
    float *x = b->ptr[0]; float *y = b->ptr[1]; float *z = b->ptr[2]; // Bind naturally aligned float32 views.
    if (kernel == 0) { kernel_saxpy_f32(n, 0.125f, x, y, stride); return y[0]; } // Perform float SAXPY without hidden conversion in the loop.
    if (kernel == 1) { return kernel_dot_f32(n, x, y, stride); } // Return the float accumulation for observation.
    kernel_mul_f32(n, x, y, z, stride); return z[0]; // Run independent float products and read back one result.
} // End external kernel call wrapper.
static bool validate(Buffers *b, bool f64, int kernel, size_t n, size_t stride, double *max_error, double *tolerance) { // Compare a single invocation with higher-precision scalar arithmetic.
    double reduction = call_kernel(b, f64, kernel, n, stride); // Execute the *actual* selected binary before measuring.
    long double reference_sum = 0.0L; // Avoid comparing a float reduction against the same accumulation algorithm.
    long double sum_abs = 0.0L; // Scale reduction tolerance by total product magnitudes, not cancellation-prone output.
    *max_error = 0.0; // Track the worst mismatch across all logical elements.
    *tolerance = 0.0; // Report the allowed error in the result's units.
    for (size_t i = 0; i < n; ++i) { // Verify tails and strided elements, not only the vector-sized prefix.
        size_t j = i * stride; // Access the same physical element as the measured kernel.
        long double x = f64 ? ((double *)b->ptr[0])[j] : ((float *)b->ptr[0])[j]; // Read the exact stored first operand.
        size_t shifted_index = j + ((uintptr_t)b->ptr[1] - (uintptr_t)b->base[1]) / (f64 ? sizeof(double) : sizeof(float)); // Map the logical offset back to the initialized physical index.
        long double y = f64 ? (long double)sample_y(shifted_index) : (long double)(float)sample_y(shifted_index); // Recover the unmodified, binary-exact second operand.
        long double expected_x = (long double)sample_x(shifted_index); // Recompute the original input rather than trusting a potentially corrupted operand.
        if (x != expected_x) { *max_error = (double)fabsl(x - expected_x); *tolerance = 0.0; return false; } // Reject out-of-contract changes to the read-only x input.
        if (kernel != 0) { long double actual_y = f64 ? ((double *)b->ptr[1])[j] : ((float *)b->ptr[1])[j]; if (actual_y != y) { *max_error = (double)fabsl(actual_y - y); *tolerance = 0.0; return false; } } // Reject accidental writes to dot/multiply's read-only y input.
        if (kernel == 1) { reference_sum += x * y; sum_abs += fabsl(x * y); continue; } // Build the reference dot with extended precision.
        long double expected = kernel == 0 ? 0.125L * x + y : x * y; // Model SAXPY or multiply without depending on SIMD code.
        long double observed = f64 ? (kernel == 0 ? ((double *)b->ptr[1])[j] : ((double *)b->ptr[2])[j]) : (kernel == 0 ? ((float *)b->ptr[1])[j] : ((float *)b->ptr[2])[j]); // Read the calculated array value.
        double error = (double)fabsl(expected - observed); // Express absolute element error in a parseable scalar.
        double bound = (f64 ? 2e-12 : 2e-5) * (1.0 + (double)fabsl(expected)); // Allow ordinary rounding but reject index and tail bugs.
        if (error > *max_error) { *max_error = error; } // Retain the worst output disagreement.
        if (bound > *tolerance) { *tolerance = bound; } // Preserve the largest documented per-element tolerance.
        if (!isfinite((double)observed) || error > bound) { return false; } // Abort measurement when any output is wrong.
    } // Finish checking all active logical outputs.
    if (kernel != 1 && (stride > 1U || b->ptr[0] != b->base[0])) { // Test that strided updates do not silently modify inactive gap elements.
        size_t offset = ((uintptr_t)b->ptr[0] - (uintptr_t)b->base[0]) / (f64 ? sizeof(double) : sizeof(float)); // Locate the shifted active origin within the complete allocation.
        for (size_t physical = 0; physical < b->capacity; ++physical) { // Inspect all physical gaps outside the N selected results.
            if (physical >= offset && (physical - offset) % stride == 0U) { continue; } // Skip slots intentionally written by this kernel.
            long double observed_gap = f64 ? (kernel == 0 ? ((double *)b->base[1])[physical] : ((double *)b->base[2])[physical]) : (kernel == 0 ? ((float *)b->base[1])[physical] : ((float *)b->base[2])[physical]); // Read an output outside the active index set.
            long double expected_gap = kernel == 0 ? (long double)sample_y(physical) : -777.0L; // Require the initial input or sentinel to remain unchanged.
            if (observed_gap != expected_gap) { *max_error = (double)fabsl(observed_gap - expected_gap); *tolerance = 0.0; return false; } // Catch excess vector stores and indexing bugs.
        } // End inspection of inactive array slots.
    } // Complete out-of-bounds-within-allocation corruption testing.
    if (kernel == 1) { // Validate the numerically order-sensitive reduction separately.
        *max_error = (double)fabsl(reference_sum - (long double)reduction); // Quantify drift against the extended-precision reference.
        *tolerance = (double)((f64 ? 2e-12L : 2e-5L) * (sum_abs + 1.0L)); // Permit legal vectorized floating-point reassociation.
        return isfinite(reduction) && *max_error <= *tolerance; // Reject non-finite and unexpectedly inaccurate reductions.
    } // Finish reduction-specific correctness handling.
    return true; // Report successful full-array validation for independent-element kernels.
} // End reference correctness check.
static double checksum_output(Buffers *b, bool f64, int kernel, size_t n, size_t stride, double last) { // Consume the result so the work cannot be treated as dead.
    if (kernel == 1) { return last; } // A reduction's observable result is already scalar.
    long double total = 0.0L; // Sum the active output only after timing has stopped.
    for (size_t i = 0; i < n; ++i) { // Read exactly those locations written by the selected kernel.
        size_t j = i * stride; // Respect gap elements when constructing the checksum.
        total += f64 ? (kernel == 0 ? ((double *)b->ptr[1])[j] : ((double *)b->ptr[2])[j]) : (kernel == 0 ? ((float *)b->ptr[1])[j] : ((float *)b->ptr[2])[j]); // Make output stores externally observable.
    } // Finish consuming the measured array.
    return (double)total; // Return a compact reproducibility fingerprint, not a correctness proof.
} // End post-measurement checksum.
static void run_case(const Options *options, const char *dtype, int kernel, size_t n, int perf_fd) { // Handle one fully identified benchmark case and its repetitions.
    bool f64 = strcmp(dtype, "f64") == 0; // Select data type without guessing from machine vector width.
    size_t element_bytes = f64 ? sizeof(double) : sizeof(float); // Convert logical operations into physical byte units.
    Buffers b; // Keep allocation and shifted-array state local to this case.
    allocate_buffers(&b, n, options->stride, options->offset, element_bytes); // Recreate identical address placement for every case.
    initialize_buffers(&b, f64); // Fault in and initialize every allocated page before timing.
    double max_error = 0.0; double tolerance = 0.0; // Retain correctness evidence alongside measured data.
    bool correct = validate(&b, f64, kernel, n, options->stride, &max_error, &tolerance); // Verify each numerical kernel before sampling.
    if (!correct) { fprintf(stderr, "VALIDATION FAILED: build=%s kernel=%d dtype=%s n=%zu stride=%zu offset=%zu error=%.12g tolerance=%.12g\n", BUILD_LABEL, kernel, dtype, n, options->stride, options->offset, max_error, tolerance); release_buffers(&b); exit(EXIT_FAILURE); } // Contain failures by refusing to time broken code.
    if (options->validate_only) { fprintf(stderr, "PASS build=%s kernel=%d dtype=%s n=%zu stride=%zu offset=%zu error=%.5g tolerance=%.5g\n", BUILD_LABEL, kernel, dtype, n, options->stride, options->offset, max_error, tolerance); release_buffers(&b); return; } // Support validation-only CI checks.
    reset_output(&b, f64, kernel); // Remove correctness-test mutations from the timing input.
    double last = 0.0; // Preserve the last observable return from the external kernel.
    unsigned warmup_count = n >= 131072U ? 2U : 8U; // Avoid dozens of full DRAM sweeps solely for calibration.
    for (unsigned w = 0; w < warmup_count; ++w) { last = call_kernel(&b, f64, kernel, n, options->stride); } // Warm the instruction path with workload-scaled warmup cost.
    reset_output(&b, f64, kernel); // Eliminate accumulation performed during warmup.
    double pilot_start = monotonic_seconds(); // Measure overhead of a batch of calls rather than one noisy invocation.
    unsigned pilot_count = n >= 131072U ? 1U : (n >= 8192U ? 4U : 32U); // Keep pilot traffic negligible for large memory footprints.
    for (unsigned p = 0; p < pilot_count; ++p) { last = call_kernel(&b, f64, kernel, n, options->stride); } // Calibrate without making 32 DRAM traversals.
    double pilot_elapsed = monotonic_seconds() - pilot_start; // Determine iterations per useful measurement batch.
    double per_call = fmax(pilot_elapsed / (double)pilot_count, 1e-9); // Prevent division by zero for clock-resolution-limited kernels.
    double proposed = options->target_ms * 1e-3 / per_call; // Select a batch of approximately constant duration.
    size_t iterations = (size_t)fmin(1000000.0, fmax(1.0, floor(proposed))); // Bound potentially expensive or overflow-prone repeats.
    reset_output(&b, f64, kernel); // Make the timed SAXPY input independent of the pilot batch.
    const char *names[3] = {"saxpy", "dot", "mul"}; // Emit stable kernel labels for the downstream parser.
    unsigned flops = kernel == 2 ? 1U : 2U; // Define one multiply for mul, and two operations for saxpy/dot.
    unsigned streams = kernel == 1 ? 2U : 3U; // Count two logical streams for dot and three for write-producing kernels.
    for (unsigned r = 0; r < options->repetitions; ++r) { // Collect independent batches for variability estimates.
        reset_output(&b, f64, kernel); // Restore deterministic mutable state outside each timed batch.
        if (perf_fd >= 0) { ioctl(perf_fd, PERF_EVENT_IOC_RESET, 0); ioctl(perf_fd, PERF_EVENT_IOC_ENABLE, 0); } // Begin counting this thread's unhalted core cycles.
        uint64_t ticks_start = read_tsc(); // Capture reference counter before the work batch.
        double seconds_start = monotonic_seconds(); // Capture raw-monotonic wall time before the work batch.
        for (size_t it = 0; it < iterations; ++it) { last = call_kernel(&b, f64, kernel, n, options->stride); } // Repeat the exact selected kernel with no I/O inside its timing loop.
        double seconds_end = monotonic_seconds(); // End wall timing before any result analysis.
        uint64_t ticks_end = read_tsc(); // Capture final reference-clock ticks after the same batch.
        uint64_t cpu_cycles = 0; bool cycles_valid = false; // Differentiate unavailable counters from a legitimate zero value.
        if (perf_fd >= 0) { ioctl(perf_fd, PERF_EVENT_IOC_DISABLE, 0); cycles_valid = read(perf_fd, &cpu_cycles, sizeof(cpu_cycles)) == (ssize_t)sizeof(cpu_cycles); } // Read actual user-thread CPU cycles if accessible.
        double elapsed = seconds_end - seconds_start; // Calculate time using one stable clock domain.
        if (!(elapsed > 0.0)) { fprintf(stderr, "nonpositive measurement duration\n"); release_buffers(&b); exit(EXIT_FAILURE); } // Reject invalid timing samples.
        double logical_elements = (double)n * (double)iterations; // Include every repeated useful operation in the denominator.
        double gflops = logical_elements * (double)flops / elapsed / 1e9; // Express achieved arithmetic rate in decimal GFLOP/s.
        double logical_gib = logical_elements * (double)streams * (double)element_bytes / elapsed / (1024.0 * 1024.0 * 1024.0); // Report logical traffic, not measured DRAM transactions.
        double checksum = checksum_output(&b, f64, kernel, n, options->stride, last); // Consume every active output after timing ends.
        observable_sink = checksum; // Make checksum observable across translation-unit optimization boundaries.
        printf("1,%s,%s,%s,%s,%zu,%zu,%zu,%zu,%zu,%u,%zu,%.9f,%.9f,%.9f,%.9f,%.9f,", options->experiment, BUILD_LABEL, names[kernel], dtype, n, options->stride, options->offset, options->offset * element_bytes % ALIGNMENT, b.allocated_bytes, r, iterations, elapsed, elapsed * 1e9 / logical_elements, (double)(ticks_end - ticks_start) / logical_elements, gflops, logical_gib); // Emit measured fields with fixed units and column order.
        if (cycles_valid) { printf("%.9f,", (double)cpu_cycles / logical_elements); } else { printf(","); } // Leave actual core cycles blank when not permitted.
        printf("%.12g,%.12g,%.12g,PASS\n", checksum, max_error, tolerance); // Write correctness evidence and status for every observation.
        fflush(stdout); // Keep samples recoverable even if a long sweep is interrupted.
    } // Finish repeated timing of the current configuration.
    release_buffers(&b); // Release memory before progressing to the next cache-size case.
} // End end-to-end measured case.
int main(int argc, char **argv) { // Orchestrate serial, reproducible benchmark cases from validated CLI input.
    Options options = get_options(argc, argv); // Reject experimental setup errors before allocating arrays.
    int perf_fd = options.skip_perf ? -1 : open_cpu_cycles(); // Try actual core cycles without assuming perf permissions.
    if (!options.skip_perf && perf_fd < 0) { fprintf(stderr, "NOTICE: per-thread core cycles unavailable (%s); cycles_per_element left empty; TSC ticks are NOT core cycles.\n", strerror(errno)); } // Expose rather than conceal hardware-counter limitations.
    if (!options.no_header && !options.validate_only) { puts("schema_version,experiment,build,kernel,dtype,n,stride,offset_elements,offset_bytes_mod64,allocated_bytes,repetition,iterations,seconds,ns_per_element,tsc_ticks_per_element,gflops,logical_gib_per_s,core_cycles_per_element,checksum,validation_max_abs_error,validation_tolerance,status"); } // Write a stable, documented CSV schema.
    for (size_t s = 0; s < options.n_sizes; ++s) { // Sweep nominally from L1 residency to DRAM pressure in ascending user order.
        for (int d = 0; d < 2; ++d) { // Compare 32-bit and 64-bit lane density where selected.
            const char *dtype = d == 0 ? "f32" : "f64"; // Identify the stored floating-point format.
            if (strcmp(options.dtype, "both") && strcmp(options.dtype, dtype)) { continue; } // Skip unrequested data types without changing order.
            for (int k = 0; k < 3; ++k) { // Benchmark SAXPY, reduction, and independent multiply.
                const char *names[3] = {"saxpy", "dot", "mul"}; // Keep selection names stable across data types.
                if (strcmp(options.kernel, "all") && strcmp(options.kernel, names[k])) { continue; } // Skip unrequested kernels explicitly.
                run_case(&options, dtype, k, options.sizes[s], perf_fd); // Validate and time one complete experimental configuration.
            } // Finish selected kernels for this data type.
        } // Finish data-type comparisons at the current n.
    } // Finish all user-specified size cases.
    if (perf_fd >= 0) { close(perf_fd); } // Release optional kernel PMU resources cleanly.
    return observable_sink == HUGE_VAL ? EXIT_FAILURE : EXIT_SUCCESS; // Retain an observable result without manufacturing performance outcomes.
} // End single-threaded benchmark process.
