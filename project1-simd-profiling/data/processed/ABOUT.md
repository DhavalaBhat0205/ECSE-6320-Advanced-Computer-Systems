# Derived measurements

`summary.csv` groups raw measurements by experiment, build, kernel, precision, element count, stride and shift. It reports sample count plus mean, median, sample SD, min and max for each metric. `speedups.csv` contains only exactly matched scalar-versus-SIMD pairs. Files were generated from `data/raw/measurements.csv` by `scripts/parse_perf.py`; they are not substitute raw observations.
