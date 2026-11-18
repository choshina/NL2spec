import csv
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "DeepSTL": (123, 86),
    "Archcomp": (12, 12),
    "STDP": (10, 10),
}


def benchmark_uid(dataset, row):
    if dataset == "DeepSTL":
        return row["instance_id"]
    return row.get("uid") or f"{dataset}__{row['instance_id']}"


class RequirementGroupingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping = {}
        for dataset, (expected_instances, expected_requirements) in EXPECTED.items():
            path = ROOT / "benchmarks" / dataset / "instances.jsonl"
            rows = [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            cls.mapping.update(
                (benchmark_uid(dataset, row), row["requirement_id"])
                for row in rows
            )
            assert len(rows) == expected_instances
            assert len({row["requirement_id"] for row in rows}) == expected_requirements

    def test_repair_set_has_145_instances_and_108_requirements(self):
        self.assertEqual(len(self.mapping), 145)
        self.assertEqual(len(set(self.mapping.values())), 108)

    def test_final_mapping_matches_benchmarks(self):
        path = ROOT / "results" / "final_145" / "final_145_ids.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        actual = {row["instance_id"]: row["requirement_id"] for row in rows}
        self.assertEqual(actual, self.mapping)

    def test_per_run_uses_requirement_groups(self):
        path = ROOT / "results" / "final_145" / "per_run.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 870)
        self.assertEqual({row["instance_id"] for row in rows}, set(self.mapping))
        self.assertTrue(
            all(row["requirement"] == self.mapping[row["instance_id"]] for row in rows)
        )

    def test_frozen_specs_use_benchmark_requirement_ids(self):
        specs = list((ROOT / "experiments" / "frozen").glob("*/instances/*/spec.json"))
        self.assertEqual(len(specs), 580)
        for path in specs:
            spec = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(spec["requirement_id"], self.mapping[spec["uid"]], str(path))

if __name__ == "__main__":
    unittest.main()
