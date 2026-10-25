#include "kernels.h" // Keep function signatures identical across build variants.
void kernel_saxpy_f32(size_t n, float a, const float *restrict x, float *restrict y, size_t stride) { // Isolate the float32 update from harness overhead.
    if (stride == 1) { // Expose a contiguous loop for ordinary compiler vectorization.
        for (size_t i = 0; i < n; ++i) { // Traverse exactly n valid logical elements, including any vector tail.
            y[i] = a * x[i] + y[i]; // Perform two floating-point operations and one read-modify-write.
        } // Close the unit-stride loop without assuming a vector width.
    } else { // Distinguish non-unit access costs from contiguous SIMD throughput.
        for (size_t i = 0; i < n; ++i) { // Keep the logical element count independent of physical stride.
            size_t j = i * stride; // Convert logical index to its physical array offset.
            y[j] = a * x[j] + y[j]; // Load and update one intentionally spaced element.
        } // Close the strided update loop.
    } // Complete stride dispatch outside the innermost loop.
} // Return after updating only addresses selected by n and stride.
void kernel_saxpy_f64(size_t n, double a, const double *restrict x, double *restrict y, size_t stride) { // Expose the double-precision lane-count tradeoff.
    if (stride == 1) { // Make contiguous double loads visible to auto-vectorization.
        for (size_t i = 0; i < n; ++i) { // Preserve valid tail handling for arbitrary n.
            y[i] = a * x[i] + y[i]; // Model two double operations per element.
        } // Finish the contiguous double update.
    } else { // Exercise wider byte gaps between useful double elements.
        for (size_t i = 0; i < n; ++i) { // Retain n as the number of arithmetic operations.
            size_t j = i * stride; // Address the selected double within the full allocation.
            y[j] = a * x[j] + y[j]; // Update the selected double without touching the gaps.
        } // Finish the strided double update.
    } // Keep the stride branch out of the selected inner loop.
} // End double-precision SAXPY.
float kernel_dot_f32(size_t n, const float *restrict x, const float *restrict y, size_t stride) { // Return an observable float reduction result.
    float accumulator = 0.0f; // Use the stated float32 arithmetic format for the reduction.
    if (stride == 1) { // Permit compilers to identify a straightforward reduction pattern.
        for (size_t i = 0; i < n; ++i) { // Include every logical product, including a short tail.
            accumulator += x[i] * y[i]; // Count one multiply and one addition per element.
        } // Close the contiguous reduction loop.
    } else { // Compare scalar/gather-style behavior with contiguous loads.
        for (size_t i = 0; i < n; ++i) { // Keep operation count constant across strides.
            size_t j = i * stride; // Select the two operands at their physical offsets.
            accumulator += x[j] * y[j]; // Preserve numerical reduction semantics within compiler FP flags.
        } // Close the strided reduction loop.
    } // Finish access pattern dispatch.
    return accumulator; // Prevent elimination by returning the complete reduction.
} // End float32 dot-product kernel.
double kernel_dot_f64(size_t n, const double *restrict x, const double *restrict y, size_t stride) { // Test half-width SIMD lanes for float64.
    double accumulator = 0.0; // Accumulate in double rather than silently widening precision.
    if (stride == 1) { // Express the fast path as a regular double reduction.
        for (size_t i = 0; i < n; ++i) { // Process all requested elements without padding assumptions.
            accumulator += x[i] * y[i]; // Generate one product and one sum per element.
        } // Finish contiguous double reduction.
    } else { // Reveal gather/scattered-load penalties where supported.
        for (size_t i = 0; i < n; ++i) { // Ensure a comparable arithmetic workload for each stride.
            size_t j = i * stride; // Convert to the physical double address.
            accumulator += x[j] * y[j]; // Fold the selected product into the double accumulator.
        } // Finish the strided reduction.
    } // End selected access pattern.
    return accumulator; // Retain the reduction as a caller-visible result.
} // End float64 dot-product kernel.
void kernel_mul_f32(size_t n, const float *restrict x, const float *restrict y, float *restrict z, size_t stride) { // Benchmark a dependency-free, write-only result stream.
    if (stride == 1) { // Expose a vector-friendly three-array streaming loop.
        for (size_t i = 0; i < n; ++i) { // Store exactly n floats rather than an assumed vector multiple.
            z[i] = x[i] * y[i]; // Complete one floating-point operation per logical element.
        } // Close the contiguous product loop.
    } else { // Test how non-unit address spacing harms effective cache-line use.
        for (size_t i = 0; i < n; ++i) { // Retain a fixed count of useful products across patterns.
            size_t j = i * stride; // Map a logical product to separated physical addresses.
            z[j] = x[j] * y[j]; // Write only the selected output, leaving holes unchanged.
        } // Close the spaced-out product loop.
    } // End multiplication stride dispatch.
} // End float32 elementwise multiply.
void kernel_mul_f64(size_t n, const double *restrict x, const double *restrict y, double *restrict z, size_t stride) { // Benchmark the corresponding double-precision traffic pattern.
    if (stride == 1) { // Let the compiler build a contiguous float64 SIMD loop.
        for (size_t i = 0; i < n; ++i) { // Include all valid elements even when n has a remainder.
            z[i] = x[i] * y[i]; // Complete one double multiply and one output store.
        } // Close unit-stride double multiplication.
    } else { // Isolate the effect of accessing fewer useful doubles per cache line.
        for (size_t i = 0; i < n; ++i) { // Perform n independent multiplications at fixed separation.
            size_t j = i * stride; // Compute the physical offset after the stride decision.
            z[j] = x[j] * y[j]; // Write the active output without modifying intervening elements.
        } // Close the non-unit-stride loop.
    } // End float64 stride dispatch.
} // End float64 elementwise multiplication.
