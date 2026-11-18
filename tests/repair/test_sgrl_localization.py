#!/usr/bin/env python3
# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.


"""Focused integration checks for Separation-Guided Repair Localization."""

from __future__ import annotations

import csv
import io
import os
import platform
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
from restless.common.formula_io import convert_formula_to_bustle
from restless.ceclass.c534_adapter import convert_ast, estimate_lattice_nodes
from restless.common.stl_epoch.parser import parse_stl
from restless.repair import repair_loop as repair_loop

DEFAULT_JAVA = Path(repair_loop.DEFAULT_JAVA)


def _java_executable() -> Path:
    # The production chooser already validates the Java-17 requirement.  A
    # stale JAVA_HOME (notably an Intel Java 11 on Apple Silicon) must not
    # override it inside the integration test.
    return Path(os.environ.get("JAVA_BIN", "").strip() or DEFAULT_JAVA)


class SgrlLocalizationTest(unittest.TestCase):
    def test_arch_wrapper_is_macos_only(self) -> None:
        linux_command: list[str] = []
        with mock.patch.object(repair_loop.platform, "system", return_value="Linux"):
            repair_loop.append_macos_arch_prefix(linux_command, "x86_64")
        self.assertEqual(linux_command, [])

        mac_command: list[str] = []
        with mock.patch.object(repair_loop.platform, "system", return_value="Darwin"):
            repair_loop.append_macos_arch_prefix(mac_command, "x86_64")
        self.assertEqual(mac_command, ["arch", "-x86_64"])

    def test_grid_fallback_contains_inner_f_g_separator(self) -> None:
        args = SimpleNamespace(
            sample_times=list(range(46)),
            signal_names=["x1", "x2"],
            signal_bounds={"x1": (0.0, 100.0), "x2": (0.0, 100.0)},
            active_disambiguation_margin=1.0e-4,
        )
        current = (
            "OR[NOT[EVENTUALLY I=[10,30][EVENTUALLY I=[0,5][x1 > 20]]],"
            "EVENTUALLY I=[30,40][GLOBALLY I=[0,5][x2 < 50]]]"
        )
        target = current.replace(
            "EVENTUALLY I=[0,5][x1 > 20]",
            "GLOBALLY I=[0,5][x1 > 20]",
        )
        candidates = repair_loop.grid_disambiguation_candidates(args, current, target)
        separator_shape = False
        for row in candidates:
            x1 = row[0::2]
            x2 = row[1::2]
            high_x1 = [index for index, value in enumerate(x1) if value > 20.0]
            if len(high_x1) == 1 and 10 <= high_x1[0] <= 35 and all(value > 50.0 for value in x2):
                separator_shape = True
                break
        self.assertTrue(separator_shape)

    def test_disambiguation_replays_blocked_smt_candidates(self) -> None:
        args = SimpleNamespace(
            active_disambiguation_margin=0.0,
            class_generation_margin=0.0,
            active_disambiguation_replay_attempts=3,
            boolean_signal_names=[],
        )
        generated_batches = [
            ([[0.0], [1.0], [2.0]], 8),
            ([[3.0], [4.0], [5.0]], 8),
        ]
        active_rows: list[list[float]] = []

        def build_dataset(_args, _path, rows, _labels):
            active_rows[:] = rows

        def evaluate(_args, formula, _dataset, _output):
            labels_a = {0: 1, 1: 1, 2: 1, 3: 1, 4: -1, 5: -1}
            labels_b = {0: 1, 1: -1, 2: 1, 3: 1, 4: 1, 5: -1}
            table = labels_a if formula == "x > 0" else labels_b
            return [table[int(row[0])] for row in active_rows]

        with tempfile.TemporaryDirectory(prefix="disambiguation-replay-") as tmp_text:
            with mock.patch.object(
                repair_loop,
                "smt_generate_with_budget",
                side_effect=generated_batches,
            ) as generate, mock.patch.object(
                repair_loop,
                "build_eval_dataset",
                side_effect=build_dataset,
            ), mock.patch.object(
                repair_loop,
                "evaluate_formula_labels",
                side_effect=evaluate,
            ):
                records, count, index_rows = repair_loop.generate_active_disambiguation_records(
                    args,
                    "x > 0",
                    "x < 0",
                    "x > 0",
                    "x < 0",
                    Path(tmp_text),
                    trace_budget=2,
                    k_gen_init=8,
                )

        self.assertEqual([call.args[4] for call in generate.call_args_list], [3, 3])
        self.assertEqual(count, 2)
        self.assertEqual([record["trace"] for record in records], [[1.0], [4.0]])
        replay = next(row for row in index_rows if row["direction"] == "REPLAY_VERIFIED")
        self.assertEqual(replay["requested"], "6")
        self.assertEqual(replay["generated"], "2")

    def test_grid_fallback_runs_when_smt_returns_no_rows(self) -> None:
        args = SimpleNamespace(
            active_disambiguation_margin=1.0e-4,
            class_generation_margin=0.0,
            active_disambiguation_replay_attempts=1,
            boolean_signal_names=[],
            sample_times=[0.0, 1.0],
            signal_names=["x"],
            signal_bounds={"x": (-1.0, 1.0)},
        )
        active_rows: list[list[float]] = []

        def build_dataset(_args, _path, rows, _labels):
            active_rows[:] = rows

        def evaluate(_args, formula, _dataset, _output):
            positive = [1 if row[0] > 0.0 else -1 for row in active_rows]
            return positive if formula == "x > 0" else [-label for label in positive]

        with tempfile.TemporaryDirectory(prefix="disambiguation-grid-") as tmp_text:
            with mock.patch.object(
                repair_loop,
                "smt_generate_with_budget",
                side_effect=[([], 8), ([], 8)],
            ), mock.patch.object(
                repair_loop,
                "build_eval_dataset",
                side_effect=build_dataset,
            ), mock.patch.object(
                repair_loop,
                "evaluate_formula_labels",
                side_effect=evaluate,
            ):
                records, count, index_rows = repair_loop.generate_active_disambiguation_records(
                    args,
                    "x > 0",
                    "x < 0",
                    "x > 0",
                    "x < 0",
                    Path(tmp_text),
                    trace_budget=2,
                    k_gen_init=8,
                )

        self.assertEqual(count, 2)
        self.assertEqual({record["class"] for record in records}, {
            "A_SAT_B_VIO", "B_SAT_A_VIO",
        })
        grid = next(row for row in index_rows if row["direction"] == "GRID_REPLAY_VERIFIED")
        self.assertEqual(grid["generated"], "2")

    def test_formula_conversion_preserves_implies_node(self) -> None:
        converted = convert_formula_to_bustle(
            "(G_[0,30](RPM < 3000)) -> (G_[0,4](Speed < 35))",
            signal_aliases={"RPM": "omega", "Speed": "v"},
        )
        self.assertEqual(
            converted,
            "IMPLIES[GLOBALLY I=[0,30][omega < 3000],GLOBALLY I=[0,4][v < 35]]",
        )
        self.assertNotIn("OR[NOT[", converted)

    def test_ceclass_implication_fallback_has_valid_k_hierarchy(self) -> None:
        ast = parse_stl("IMPLIES[x > 0,y > 0]")
        lattice_root, hierarchy, _ = convert_ast(ast, {"x": 0, "y": 1})
        self.assertEqual(estimate_lattice_nodes(lattice_root, hierarchy), 4)

    def run_case(
        self,
        formula: str,
        header: str,
        traces: list[str],
        labels: str,
        times: str,
        top_k: int = 3,
        iterations: int = 1,
        extra_args: tuple[str, ...] = (),
        expect_evidence: bool = True,
    ) -> tuple[list[dict[str, str]], str, dict[str, str]]:
        with tempfile.TemporaryDirectory(prefix="sgrl-test-") as tmp_text:
            tmp = Path(tmp_text)
            dataset = tmp / "dataset"
            output = tmp / "output"
            dataset.mkdir()
            (dataset / "data.csv").write_text(header + "\n" + "\n".join(traces) + "\n")
            (dataset / "labels.csv").write_text(labels + "\n")
            (dataset / "times.csv").write_text(times + "\n")
            metadata = dataset / "trace_metadata.csv"
            with metadata.open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["trace_id", "round_id", "source", "ceclass_id",
                                "predicted_label", "oracle_label"],
                )
                writer.writeheader()
                for index in range(len(traces)):
                    writer.writerow({
                        "trace_id": f"trace_{index + 1:06d}",
                        "round_id": "1",
                        "source": "TEST",
                        "ceclass_id": f"class_{index + 1}",
                        "predicted_label": "",
                        "oracle_label": "",
                    })
            formula_csv = tmp / "formula.csv"
            with formula_csv.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["formula"])
                writer.writeheader()
                writer.writerow({"formula": formula})

            java = _java_executable()
            command = [
                str(java),
                "-cp", "libs/JGEA.jar:libs/moonlight.jar:libs/jblas-1.2.4.jar:target/classes",
                "it.units.malelab.learningstl.Main",
                "seed=701", "dataset=generic", f"dataPath={dataset}", f"output={output}/",
                "mod=supervised", "local=false", "evolver=repair", "fitness=moonlight",
                "moonlightNormalizeValues=false", f"formula={formula_csv}",
                "formulaColumn=formula", "formulaRow=1", "repairMode=bustle",
                f"repairIterations={iterations}", "repairPopulation=2", "repairStopOnPerfect=false",
                "repairLocalization=sgrl", 
                f"repairLocalizationTopK={top_k}",
                f"repairLocalizationReport={output / 'localization_report.csv'}",
                f"repairTraceMetadata={metadata}",
                "threads=1",
                *extra_args,
            ]
            if platform.system() == "Darwin" and platform.machine() == "arm64":
                command = ["arch", "-x86_64", *command]
            completed = subprocess.run(
                command, cwd=ROOT, check=False, capture_output=True, text=True
            )
            self.assertEqual(
                completed.returncode,
                0,
                msg=f"Java repair process failed:\n{completed.stdout}\n{completed.stderr}",
            )
            with (output / "localization_report.csv").open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            reports = {
                name: (output / name).read_text()
                for name in ("robustness_table.csv", "temporal_cells.csv", "evidence_report.csv", "edit_counts.jsonl")
                if (output / name).exists() or name != "edit_counts.jsonl"
            }
            if expect_evidence:
                self.assertGreater(len(reports["robustness_table.csv"].splitlines()), 2)
                self.assertGreater(len(reports["temporal_cells.csv"].splitlines()), 2)
                self.assertGreater(len(reports["evidence_report.csv"].splitlines()), 1)
            return rows, completed.stdout, reports

    def test_wt2_temporal_operator_localization(self) -> None:
        rows, stdout, reports = self.run_case(
            "EVENTUALLY I=[30,630][AND[Mgd >= 21000,Mgd <= 47500]]",
            "Mgd",
            ["0,30000,0", "30000,30000,30000"],
            "-1,1",
            "0,30,630",
        )
        self.assertEqual(rows[0]["node_path"], "root")
        self.assertEqual(rows[0]["best_operator"], "flip_temporal")
        self.assertIn("possible_F_to_G_conflict", rows[0]["symptoms"])
        self.assertIn("trace=trace_000001", stdout)
        self.assertIn("true_class=negative(-1)", stdout)
        self.assertIn("predicted_class=positive(+1)", stdout)
        self.assertIn("suggested=flip_temporal", stdout)
        self.assertEqual(rows[0]["corrected_trace_ids"], "trace_000001")
        self.assertIn("reachability_constraint", reports["evidence_report.csv"])
        self.assertEqual(rows[0]["location_selection_count"], "1")
        self.assertEqual(rows[0]["operator_selection_count"], "1")

    def test_gate_expands_after_stagnation(self) -> None:
        args = (
            "EVENTUALLY I=[30,630][AND[Mgd >= 21000,Mgd <= 47500]]",
            "Mgd",
            ["0,30000,0", "30000,30000,30000"],
            "-1,1",
            "0,30,630",
        )
        _, stdout, _ = self.run_case(
            *args,
            iterations=3,
            extra_args=(
                "repairLocalizationStagnationWindow=1",
                "repairLocalizationExpansionStep=1",
            ),
        )
        self.assertIn("SGRL gate expansion: 1 stagnant generations, +1 ranks", stdout)
        _, stdout, _ = self.run_case(*args, iterations=1)
        self.assertNotIn("SGRL gate expansion", stdout)

    def test_vacuous_guard_localization(self) -> None:
        rows, _, reports = self.run_case(
            "GLOBALLY I=[0,0][IMPLIES[speed <= 10,GLOBALLY I=[0,2][brake]]]",
            "speed,brake",
            ["20,0,20,0,20,0", "5,0,5,0,5,0"],
            "-1,1",
            "0,1,2",
            top_k=10,
        )
        comparator = next(row for row in rows if row["best_operator"] == "flip_direction")
        inner_temporal = next(
            row for row in rows
            if row["node_path"] == "root/child/right"
            and row["best_operator"] == "flip_temporal"
        )
        self.assertLessEqual(int(comparator["rank"]), 3)
        self.assertIn("vacuous_antecedent_false", comparator["symptoms"])
        self.assertGreater(
            float(comparator["best_edit_score"]),
            float(inner_temporal["best_edit_score"]),
        )
        self.assertTrue(any(row["best_operator"] == "sink_temporal_left" for row in rows))
        self.assertIn("implication", reports["evidence_report.csv"])

    def test_until_backward_slice_and_ceclass_evidence(self) -> None:
        rows, _, reports = self.run_case(
            "UNTIL I=[0,2][x > 0,y > 0]",
            "x,y",
            ["1,-1,1,-1,1,-1", "1,-1,1,-1,1,-1"],
            "1,1",
            "0,1,2",
        )
        self.assertTrue(rows)
        self.assertIn("until_constraint", reports["evidence_report.csv"])
        self.assertTrue(any(int(row["ce_class_support"]) >= 1 for row in rows))

    def test_multiple_mandatory_temporal_cells_raise_temporal_weight(self) -> None:
        rows, _, reports = self.run_case(
            "GLOBALLY I=[0,4][x > 0]",
            "x",
            ["-1,1,-1,1,-1", "-2,-2,-2,-2,-2"],
            "1,-1",
            "0,1,2,3,4",
        )
        evidence = list(csv.DictReader(io.StringIO(reports["evidence_report.csv"])))
        trace = [row for row in evidence if row["trace_id"] == "trace_000001"]
        root = [row for row in trace if row["node_path"] == "root"]
        predicate = [row for row in trace if row["node_path"] == "root/child"]
        self.assertEqual(len(root), 1)
        self.assertEqual(len(predicate), 3)
        self.assertAlmostEqual(float(root[0]["weight"]), 0.75, places=9)
        self.assertAlmostEqual(sum(float(row["weight"]) for row in predicate), 0.25, places=9)
        self.assertAlmostEqual(sum(float(row["weight"]) for row in trace), 1.0, places=9)
        self.assertEqual(float(root[0]["site_responsibility"]), 1.0)
        self.assertEqual(float(root[0]["self_coefficient"]), 3.0)

        flip = next(
            row for row in rows
            if row["node_path"] == "root" and row["best_operator"] == "flip_temporal"
        )
        self.assertAlmostEqual(float(flip["muse_score"]), 1.0, places=9)
        self.assertAlmostEqual(float(flip["best_edit_score"]), 2.375, places=9)

    def test_muse_balances_error_directions_and_uses_m_denominator(self) -> None:
        rows, _, _ = self.run_case(
            "x > 0",
            "x",
            ["1", "-1", "2", "-2", "0", "0"],
            "-1,1,1,-1,1,1",
            "0",
        )
        flip = next(row for row in rows if row["best_operator"] == "flip_direction")
        self.assertEqual(flip["corrected_false_satisfactions"], "1")
        self.assertEqual(flip["corrected_false_violations"], "1")
        self.assertEqual(flip["broken_correct_traces"], "2")
        # Repair term is 1; damage is broken/M = 2/2 = 1.
        self.assertAlmostEqual(float(flip["muse_score"]), 0.0, places=9)

    def test_evidence_top_k_filters_nodes_before_edit_probes(self) -> None:
        rows, _, _ = self.run_case(
            "AND[x > 0,y > 0]",
            "x,y",
            ["-2,-1"],
            "1",
            "0",
            top_k=1,
        )
        self.assertTrue(rows)
        self.assertEqual({row["node_path"] for row in rows}, {"root"})

    def test_evidence_gate_admits_top_k_ranks(self) -> None:
        # Rank(v) = 1 + #{candidates with strictly larger evidence}; the gate admits
        # nodes with rank <= K (nodes tied on evidence share a rank). Here the child
        # (evidence 0.75) is rank 1 and the root (0.25) is rank 2.
        args = ("GLOBALLY I=[0,0][x > 0]", "x", ["-1"], "1", "0")
        rows, _, _ = self.run_case(*args, top_k=1)
        self.assertEqual({row["node_path"] for row in rows}, {"root/child"})
        rows, _, _ = self.run_case(*args, top_k=2)
        self.assertEqual({row["node_path"] for row in rows}, {"root", "root/child"})

    # ablation: no self coefficients (K = 1) -----------------------------------

    def test_no_self_coeff_keeps_raw_responsibility(self) -> None:
        case = ("GLOBALLY I=[0,4][x > 0]", "x", ["-1,1,-1,1,-1", "-2,-2,-2,-2,-2"], "1,-1", "0,1,2,3,4")
        _, _, full = self.run_case(*case)
        _, _, k1 = self.run_case(*case, extra_args=("repairLocalizationSelfCoefficientCap=1",))
        full_rows = list(csv.DictReader(io.StringIO(full["evidence_report.csv"])))
        k1_rows = list(csv.DictReader(io.StringIO(k1["evidence_report.csv"])))
        self.assertTrue(k1_rows)
        self.assertTrue(any(float(row["self_coefficient"]) > 1.0 for row in full_rows))
        for row in k1_rows:
            self.assertEqual(float(row["self_coefficient"]), 1.0)
            self.assertAlmostEqual(
                float(row["raw_node_contribution"]), float(row["site_responsibility"]), places=9
            )

    # ---- type-priority trial -------------------------------------------------------------------------------
    @staticmethod
    def _edit_set(rows: list[dict[str, str]]) -> set[tuple[str, str, str, str]]:
        return {(r["node_path"], r["best_operator"], r["muse_score"], r["corrected_mismatches"]) for r in rows}

    @staticmethod
    def _counts(reports: dict[str, str]) -> dict[str, int]:
        return __import__("json").loads(reports["edit_counts.jsonl"].strip().splitlines()[0])

    @staticmethod
    def _jitter_traces(count: int, x1_low: int, x2_low: int) -> str:
        # Constant signals collapse to two breakpoints in the monitor, so every sample differs a little.
        return ",".join(f"{x1_low + i % 2},{x2_low + i % 2}" for i in range(count))

    def _atom_case(self, labels: str, traces: list[str], **kwargs):
        # phi0 has the wrong direction on x2; the atom sits outside every temporal operator, so it also has
        # wrap_globally / wrap_eventually edits that the type-priority rule can skip.
        return self.run_case(
            "AND[x2 < 10,GLOBALLY I=[0,10][x1 > 0]]", "x1,x2", traces, labels,
            ",".join(str(i) for i in range(21)), top_k=10, **kwargs)

    def test_type_priority_skips_other_edits_when_first_batch_fits_d(self) -> None:
        traces = [self._jitter_traces(21, 5, 50), self._jitter_traces(21, 5, 0)]
        rows_on, _, reports_on = self._atom_case("1,-1", traces)
        rows_off, _, reports_off = self._atom_case(
            "1,-1", traces, extra_args=("repairLocalizationTypePriority=false",))
        atom_on = {r["best_operator"] for r in rows_on if r["node_path"] == "root/left"}
        atom_off = {r["best_operator"] for r in rows_off if r["node_path"] == "root/left"}
        self.assertEqual(atom_on, {"flip_direction", "toggle_not"})
        self.assertTrue({"wrap_globally", "wrap_eventually"} & atom_off)
        counts_on, counts_off = self._counts(reports_on), self._counts(reports_off)
        self.assertEqual(counts_off["edits_skipped_by_type"], 0)
        self.assertGreater(counts_on["edits_skipped_by_type"], 0)
        self.assertEqual(counts_on["edits_trialled"] + counts_on["edits_skipped_by_type"], counts_off["edits_trialled"])
        # every other node is untouched
        self.assertEqual(
            {e for e in self._edit_set(rows_on) if e[0] != "root/left"},
            {e for e in self._edit_set(rows_off) if e[0] != "root/left"})

    def test_type_priority_trials_everything_when_first_batch_does_not_fit_d(self) -> None:
        # The third trace repeats the first with the opposite label, so no single edit can fit D.
        traces = [self._jitter_traces(21, 5, 50), self._jitter_traces(21, 5, 0), self._jitter_traces(21, 5, 50)]
        rows_on, _, reports_on = self._atom_case("1,-1,-1", traces)
        rows_off, _, reports_off = self._atom_case(
            "1,-1,-1", traces, extra_args=("repairLocalizationTypePriority=false",))
        self.assertEqual(self._counts(reports_on)["edits_skipped_by_type"], 0)
        self.assertEqual(self._counts(reports_on), self._counts(reports_off))
        self.assertEqual(self._edit_set(rows_on), self._edit_set(rows_off))
        self.assertTrue({"wrap_globally", "wrap_eventually"} & {r["best_operator"] for r in rows_on if r["node_path"] == "root/left"})

    def test_type_priority_is_inert_on_nodes_without_credit(self) -> None:
        # With K = 1 no node earns self credit, so the trial is exhaustive and identical to the disabled run.
        traces = [self._jitter_traces(21, 5, 50), self._jitter_traces(21, 5, 0)]
        cap = ("repairLocalizationSelfCoefficientCap=1",)
        rows_on, _, reports_on = self._atom_case("1,-1", traces, extra_args=cap)
        rows_off, _, reports_off = self._atom_case(
            "1,-1", traces, extra_args=cap + ("repairLocalizationTypePriority=false",))
        self.assertEqual(self._counts(reports_on)["edits_skipped_by_type"], 0)
        self.assertEqual(self._counts(reports_on), self._counts(reports_off))
        self.assertEqual(self._edit_set(rows_on), self._edit_set(rows_off))

    def test_type_priority_working_example(self) -> None:
        # phi0 = G[5,20](not(x1<20) or F[0,10](x2<10)); the intended atom is x2 > 10 (xi1: x2 high, xi2: x2 low).
        phi0 = "GLOBALLY I=[5,20][OR[NOT[x1 < 20],EVENTUALLY I=[0,10][x2 < 10]]]"
        xi = [self._jitter_traces(41, 10, 50), self._jitter_traces(41, 10, 0)]
        times = ",".join(str(i) for i in range(41))
        rows_on, _, reports_on = self.run_case(phi0, "x1,x2", xi, "1,-1", times, top_k=10)
        rows_off, _, reports_off = self.run_case(
            phi0, "x1,x2", xi, "1,-1", times, top_k=10,
            extra_args=("repairLocalizationTypePriority=false",))
        alpha2 = "root/child/right/child"
        alpha1 = "root/child/left/child"
        # alpha2: x2 > 10 (flip_direction) and not(x2 < 10) (toggle_not) are both candidates and both fit D.
        fitting = {r["best_operator"] for r in rows_on
                   if r["node_path"] == alpha2 and r["corrected_mismatches"] == "2"}
        self.assertEqual(fitting, {"flip_direction", "toggle_not"})
        # alpha1 earns credit too, but its first batch does not fit D, so nothing is cut short there.
        alpha1_corrected = {r["corrected_mismatches"] for r in rows_on if r["node_path"] == alpha1}
        self.assertEqual(alpha1_corrected, {"1"})
        # Nothing is skipped on this formula (alpha nodes under G have no other edits) and results are unchanged.
        self.assertEqual(self._counts(reports_on)["edits_skipped_by_type"], 0)
        self.assertEqual(self._edit_set(rows_on), self._edit_set(rows_off))


if __name__ == "__main__":
    unittest.main()
