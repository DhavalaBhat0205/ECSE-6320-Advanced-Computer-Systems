"""Regression checks for safety, statistical grouping and fio/native parser semantics."""
import json
import os
import subprocess
import uuid
import tempfile
from pathlib import Path
import unittest
from run_experiments import locate_real_fio, matrix, make_fio_command
from analyze import normalize, process


class RepositoryTests(unittest.TestCase):
    def test_native_safety_rejects_raw_devices_and_unexpected_file(self):
        binary = Path(__file__).resolve().parents[1] / "bin/direct_io_bench"
        for invalid in ("/dev/null", "/etc/hosts", "/tmp/arbitrary-user-file.bin"):
            proc = subprocess.run([str(binary), invalid, "randread", "4096", "4096", "1", "100", "100", "99"],
                                  capture_output=True, timeout=5)
            self.assertNotEqual(proc.returncode, 0)

    def test_native_real_direct_io_smoke(self):
        base = Path(__file__).resolve().parents[1] / "data/scratch"
        base.mkdir(parents=True, exist_ok=True)
        scratch = base / ("disposable-" + uuid.uuid4().hex + ".bin")
        binary = Path(__file__).resolve().parents[1] / "bin/direct_io_bench"
        try:
            with scratch.open("xb") as handle:
                handle.write(bytes((i % 251) for i in range(1024 * 1024)))
                handle.flush()
                os.fsync(handle.fileno())
            proc = subprocess.run([str(binary), str(scratch), "randread", "4096", "1048576", "1", "100", "120", "99", "1"],
                                  capture_output=True, text=True, timeout=10)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            data = json.loads(proc.stdout)
            self.assertTrue(data["direct"])
            self.assertGreater(data["read"]["operations"], 0)
        finally:
            scratch.unlink(missing_ok=True)

    def test_matrix_has_required_factors(self):
        m = matrix("quick")
        self.assertGreater(len(m), 40)
        self.assertEqual(set(x["block_kib"] for x in m if x["study"] == "block_sweep"), {4,16,32,64,128,256})
        self.assertEqual({x["read_pct"] for x in m if x["study"] == "read_write_mix"}, {0,50,70,100})
        self.assertEqual({x["workers"] for x in m if x["study"] == "intensity"}, {1,2,4,8,16,32})
        self.assertTrue(any(not x["direct"] for x in m))

    def test_fio_command_never_names_a_block_device(self):
        c = next(x for x in matrix("quick") if x["study"] == "qd1_baseline")
        args = make_fio_command("/usr/bin/fio", c, Path("/tmp/disposable-test.bin"), 3, 444)
        self.assertIn("--direct=1", args)
        self.assertIn("--allow_file_create=0", args)
        self.assertIn("--ioengine=libaio", args)
        self.assertIn("--output-format=json", args)
        self.assertNotIn("--filename=/dev/nvme0n1", args)

    def test_native_parser_with_directional_percentiles(self):
        c = next(x for x in matrix("quick") if x["study"] == "read_write_mix")
        meta = {**c, "backend":"native", "repetition":0}
        data = {"direct":True, "runtime_s":1.0,
                "read": {"operations":10,"iops":10,"bw_mib_s":2,"mean_us":10,"p50_us":9,"p95_us":17,"p99_us":20,"p999_us":25},
                "write":{"operations":5,"iops":5,"bw_mib_s":1,"mean_us":20,"p50_us":17,"p95_us":22,"p99_us":28,"p999_us":35}}
        row = normalize(data, meta, Path("test.json"))
        self.assertEqual(row["total_iops"],15)
        self.assertAlmostEqual(row["estimated_inflight"],.0002)
        self.assertEqual(row["write_p999_us"],35)

    def test_parser_refuses_zero_io(self):
        meta = {**matrix("quick")[0], "backend":"native", "repetition":0}
        data = {"direct":True,"runtime_s":1,"read":{"operations":0,"iops":0,"bw_mib_s":0,"mean_us":0,"p50_us":0,"p95_us":0,"p99_us":0,"p999_us":0},
                "write":{"operations":0,"iops":0,"bw_mib_s":0,"mean_us":0,"p50_us":0,"p95_us":0,"p99_us":0,"p999_us":0}}
        with self.assertRaises(ValueError): normalize(data, meta, Path("test.json"))

    def test_fio_json_lat_ns_conversion(self):
        c = {**matrix("quick")[0], "backend":"fio", "repetition":0}
        job = {"error":0,"read":{"total_ios":100,"runtime":1000,"iops":100,"bw_bytes":409600,
                                     "lat_ns":{"mean":1000000,"percentile":{"50.000000":600000,"95.000000":1800000,"99.000000":2100000,"99.900000":3000000}}},
                         "write":{"total_ios":0,"runtime":1000,"iops":0,"bw_bytes":0,"lat_ns":{"mean":0,"percentile":{}}}}
        row = normalize({"jobs":[job]}, c, Path("fio.json"))
        self.assertAlmostEqual(row["read_p99_us"],2100.0)
        self.assertAlmostEqual(row["read_mean_us"],1000.0)

    def test_aggregate_is_independent_of_run_count(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            run = root/"raw"/"trial"; run.mkdir(parents=True)
            c = {**matrix("quick")[0],"backend":"native", "repetition":0,"file":"one.json"}
            data = {"direct":True,"runtime_s":1,"read":{"operations":20,"iops":20,"bw_mib_s":1,"mean_us":10,"p50_us":10,"p95_us":20,"p99_us":30,"p999_us":40},
                    "write":{"operations":0,"iops":0,"bw_mib_s":0,"mean_us":0,"p50_us":0,"p95_us":0,"p99_us":0,"p999_us":0}}
            (run/"manifest.json").write_text(json.dumps([c]))
            (run/"one.json").write_text(json.dumps(data))
            result = process(root/"raw", root/"processed")
            self.assertEqual(result["raw_samples"],1)
            self.assertEqual(result["configurations"],1)


if __name__ == "__main__":
    unittest.main()
