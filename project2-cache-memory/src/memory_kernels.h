#ifndef MEMORY_KERNELS_H /* Prevent duplicate declarations across translation units. */
#define MEMORY_KERNELS_H /* Keep the ABI identical for the harness and kernels. */
#include <stddef.h> /* Provide size_t for cache-line and element counts. */
#include <stdint.h> /* Use fixed-width elements for reproducible footprints. */
extern volatile uint64_t memory_sink; /* Make consumed read results observable to the optimizer. */
void memory_build_chain(uint32_t *next, size_t slots, size_t gap, int randomize, uint64_t seed); /* Construct one serial dependency cycle. */
uint32_t memory_chase(const uint32_t *next, uint32_t start, size_t steps); /* Time serialized loads without index arithmetic in the inner loop. */
uint64_t memory_parallel(const uint32_t *next, const uint32_t *starts, unsigned lanes, size_t rounds); /* Expose memory-level parallelism through independent chains. */
uint64_t memory_stream(uint64_t *dst, const uint64_t *src, const uint32_t *order, size_t count, size_t stride, unsigned read_percent, size_t rounds); /* Exercise verified read/write mixes. */
#endif /* MEMORY_KERNELS_H */
