"""Synthetic parser fixtures only; these are not benchmark data or hardware measurements."""
from __future__ import annotations
import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('simd_parse_perf', ROOT / 'scripts' / 'parse_perf.py')
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

class ParserContractTests(unittest.TestCase):
    def test_supported_and_missing_counters(self) -> None:
        self.assertEqual(MODULE.clean_numeric('1.2e+03'), 1200.0)
        self.assertIsNone(MODULE.clean_numeric('<not counted>'))
        self.assertIsNone(MODULE.clean_numeric('not a number'))
        self.assertIsNone(MODULE.clean_numeric('nan'))

    def test_process_scope_for_perf_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.log'
            path.write_text('1234;;cycles;0;100.00;\n<not supported>;;cache-misses;0;100.00;\n')
            actual = MODULE.parse_perf(path, 'synthetic_fixture')
            self.assertEqual(len(actual), 2)
            self.assertEqual(actual[0]['value'], 1234.0)
            self.assertEqual(actual[1]['value'], '')
            self.assertIn('entire_perf_wrapped_process', actual[0]['scope'])

    def test_speedup_pairs_require_matching_configuration(self) -> None:
        common = {'experiment': 'synthetic_fixture', 'kernel': 'mul', 'dtype': 'f32',
                  'n': '1024', 'stride': '1', 'offset_elements': '0', 'samples': 3}
        summary = [{**common, 'build': 'scalar', 'ns_per_element_median': 4.0},
                   {**common, 'build': 'avx2', 'ns_per_element_median': 2.0},
                   {**common, 'build': 'avx512', 'stride': '8', 'ns_per_element_median': 1.0}]
        paired = MODULE.speedups(summary)
        self.assertEqual(len(paired), 1)
        self.assertEqual(paired[0]['speedup_scalar_over_variant'], 2.0)
        self.assertEqual(paired[0]['build'], 'avx2')

    def test_csv_duplicate_repetition_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'raw.csv'
            fields = ['schema_version', 'experiment', 'build', 'kernel', 'dtype', 'n', 'stride',
                      'offset_elements', 'repetition', 'iterations', 'seconds', 'ns_per_element',
                      'tsc_ticks_per_element', 'gflops', 'logical_gib_per_s', 'core_cycles_per_element', 'status']
            record = {'schema_version': '1', 'experiment': 'synthetic_fixture', 'build': 'scalar',
                      'kernel': 'dot', 'dtype': 'f32', 'n': '17', 'stride': '1', 'offset_elements': '0',
                      'repetition': '0', 'iterations': '10', 'seconds': '0.5', 'ns_per_element': '2.0',
                      'tsc_ticks_per_element': '5.0', 'gflops': '1.0', 'logical_gib_per_s': '1.0',
                      'core_cycles_per_element': '', 'status': 'PASS'}
            with path.open('w', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader()
                writer.writerow(record)
                writer.writerow(record)
            with self.assertRaisesRegex(ValueError, 'Duplicate measured repetition'):
                MODULE.load_rows(path)

if __name__ == '__main__':
    unittest.main()
