#ifndef SIMD_KERNELS_H // Prevent repeated declarations across translation units.
#define SIMD_KERNELS_H // Establish one interface for every compiler/ISA variant.
#include <stddef.h> // Use size_t for addressable element counts and strides.
void kernel_saxpy_f32(size_t n, float a, const float *restrict x, float *restrict y, size_t stride); // Update one float output per logical element.
void kernel_saxpy_f64(size_t n, double a, const double *restrict x, double *restrict y, size_t stride); // Update one double output per logical element.
float kernel_dot_f32(size_t n, const float *restrict x, const float *restrict y, size_t stride); // Reduce products with float accumulation.
double kernel_dot_f64(size_t n, const double *restrict x, const double *restrict y, size_t stride); // Reduce products with double accumulation.
void kernel_mul_f32(size_t n, const float *restrict x, const float *restrict y, float *restrict z, size_t stride); // Store independent float products.
void kernel_mul_f64(size_t n, const double *restrict x, const double *restrict y, double *restrict z, size_t stride); // Store independent double products.
#endif // SIMD_KERNELS_H // Close the shared kernel interface guard.
