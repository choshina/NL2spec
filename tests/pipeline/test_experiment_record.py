# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from restless.pipeline.run_record import (
    RunRecorder,
    RunSpec,
    canonical_formula,
    infer_fault_metadata,
    stable_ast,
)


FAULTY = "AND[EVENTUALLY I=[0,2][x > 1],GLOBALLY I=[0,3][y < 2]]"
ORACLE = "OR[GLOBALLY I=[0,2][x > 1],GLOBALLY I=[0,3][y < 2]]"


class ExperimentRecordTests(unittest.TestCase):
    def test_stable_ids_are_faulty_preorder_ids(self) -> None:
        _root, rows = stable_ast(FAULTY)
        self.assertEqual([row["node_id"] for row in rows], [f"n{i}" for i in range(5)])
        self.assertEqual(rows[2]["path"], "root.left.child")

    def test_tree_diff_finds_both_operator_faults(self) -> None:
        metadata = infer_fault_metadata(FAULTY, ORACLE)
        self.assertEqual(metadata["fault_nodes"], ["n0", "n1"])
        self.assertEqual(metadata["fault_types"], ["flip_boolean", "flip_temporal"])

    def test_canonicalization_handles_commutativity(self) -> None:
        left = "AND[x > 1,y < 2]"
        right = "AND[y < 2,x > 1]"
        self.assertEqual(canonical_formula(left), canonical_formula(right))

    def test_finalize_replaces_same_run_in_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            index = Path(directory) / "runs.jsonl"
            spec = RunSpec("B", "i", "src", "ours", 7, 1, 1, {})
            recorder = RunRecorder(
                output_root=root,
                record_jsonl=index,
                spec=spec,
                faulty_formula=FAULTY,
                oracle_formula=ORACLE,
            )
            coverage = root / "round_01" / "neighborhood_probe" / "mutation_coverage.csv"
            coverage.parent.mkdir(parents=True)
            coverage.write_text(
                "status,total_mutants,filtered_by_D,killed_mutants,"
                "domain_equivalent_mutants,unknown_or_timeout_mutants,"
                "remaining_mutants,mutation_coverage,new_examples\n"
                "incomplete,20,18,15,0,0,3,0.8333333333,2\n"
            )
            summary = [{
                "round": "1", "formula_out": ORACLE,
                "old_accuracy": "0.5", "new_accuracy": "1.0",
                "global_dataset_size": "4",
            }]
            trace_metrics = {
                "num_generated": 37,
                "num_oracle_queried": 24,
                "avg_num_signals": 1.8,
                "avg_trace_length": 42.3,
                "num_discriminating": 9,
            }
            recorder.finalize(summary, final_formula=ORACLE, trace_metrics=trace_metrics)
            recorder.finalize(summary, final_formula=ORACLE, trace_metrics=trace_metrics)
            records = [json.loads(line) for line in index.read_text().splitlines()]
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]["result"]["canonical_exact"])
            self.assertEqual(records[0]["status"], "repaired")
            self.assertEqual(records[0]["trace_metrics"]["num_discriminating"], 9)
            self.assertEqual(records[0]["refinement"]["candidates_before"], 18)
            self.assertEqual(records[0]["refinement"]["candidates_after"], 3)
            self.assertAlmostEqual(
                records[0]["refinement"]["candidate_elimination_rate"],
                1.0 - 3.0 / 18.0,
            )


    def run_search(self, rows: list[str], final: str, root: Path, old_accuracy: str = "1.0") -> dict:
        from types import SimpleNamespace
        from unittest import mock
        from restless.evaluation.metrics import equivalence as equivalence
        spec = RunSpec("B", "i", "src", "respstl", 7, 1, 1, {"domain_config": {"signals": []}})
        recorder = RunRecorder(output_root=root, record_jsonl=root.parent / "runs.jsonl", spec=spec,
                               faulty_formula=FAULTY, oracle_formula=ORACLE)
        repair_csv = root / "round_01" / "repair_attempt_00" / "repair_output" / "1.repair.csv"
        repair_csv.parent.mkdir(parents=True)
        repair_csv.write_text("iteration,candidateType,serialized,accuracy\n" + "".join(
            f'0,sgrl,"{formula}",{accuracy}\n' for formula, accuracy in rows))
        verdict = lambda candidate, oracle, domain: SimpleNamespace(
            verdict="equivalent" if candidate == ORACLE else "not_equivalent")
        summary = [{"round": "1", "formula_out": final, "old_accuracy": old_accuracy, "new_accuracy": "1.0", "global_dataset_size": "4"}]
        with mock.patch.object(equivalence.Domain, "from_config", return_value=None), \
                mock.patch.object(equivalence, "check_equivalence", side_effect=verdict):
            recorder.finalize(summary, final_formula=final)
        return json.loads((root / "run_record.json").read_text())

    def test_fitness_evals_star_counts_to_first_equivalent_candidate(self) -> None:
        wrong = "AND[x > 1,y < 2]"
        with tempfile.TemporaryDirectory() as directory:
            record = self.run_search([(FAULTY, 0.5), (wrong, 1.0), (ORACLE, 1.0)], wrong, Path(directory) / "run")
        self.assertEqual(record["result"]["fitness_evals_total"], 3)
        self.assertEqual(record["result"]["fitness_evals_star"], 3)
        self.assertFalse(record["invalid_run"])

    def test_fitness_evals_star_is_null_without_success(self) -> None:
        wrong = "AND[x > 1,y < 2]"
        with tempfile.TemporaryDirectory() as directory:
            record = self.run_search([(wrong, 1.0)], wrong, Path(directory) / "run")
        self.assertIsNone(record["result"]["fitness_evals_star"])

    def test_no_search_marks_invalid_run_when_repair_was_needed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = self.run_search([], FAULTY, Path(directory) / "run", old_accuracy="0.5")
        self.assertTrue(record["invalid_run"])
        self.assertEqual(record["invalid_reason"], "repair_search_not_executed")

    def test_no_search_is_valid_when_formula_already_fits_all_traces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            record = self.run_search([], FAULTY, Path(directory) / "run", old_accuracy="1.0")
        self.assertFalse(record["invalid_run"])


if __name__ == "__main__":
    unittest.main()
