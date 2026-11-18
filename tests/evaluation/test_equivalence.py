#!/usr/bin/env python3
"""Tests for the bounded equivalence checker (T2)."""

from __future__ import annotations

import os
import platform
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from restless.evaluation.metrics.equivalence import (  # noqa: E402
    Domain, Evaluator, SignalDomain, canonical, check_equivalence,
)

HAS_Z3 = shutil.which("z3") is not None


def domain(**extra_signals) -> Domain:
    signals = {
        "x": SignalDomain("real", 0.0, 10.0),
        "y": SignalDomain("real", 0.0, 10.0),
        "n": SignalDomain("int", 0, 5),
        "p": SignalDomain("bool", 0, 1),
        "q": SignalDomain("bool", 0, 1),
    }
    signals.update(extra_signals)
    return Domain(signals, {}, 0.0, 5.0, 1.0)


class CanonicalTests(unittest.TestCase):
    def test_negation_pushing_and_duality(self) -> None:
        self.assertEqual(canonical("GLOBALLY I=[0,5][x > 3]"),
                         canonical("NOT[EVENTUALLY I=[0,5][x <= 3]]"))

    def test_implication_flattening_sorting_dedup(self) -> None:
        self.assertEqual(canonical("IMPLIES[p,q]"), canonical("OR[q,NOT[p]]"))
        self.assertEqual(canonical("AND[p,AND[q,p]]"), canonical("AND[q,p]"))

    def test_strictness_merged_only_under_oracle(self) -> None:
        self.assertEqual(canonical("x > 3", "oracle"), canonical("x >= 3", "oracle"))
        self.assertNotEqual(canonical("x > 3", "boolean"), canonical("x >= 3", "boolean"))
        self.assertEqual(canonical("NOT[x > 3]", "boolean"), canonical("x <= 3", "boolean"))

    def test_negated_equality_is_not_complemented(self) -> None:
        self.assertEqual(canonical("NOT[x == 3]"), "NOT[x == 3]")


@unittest.skipUnless(HAS_Z3, "z3 binary not on PATH")
class CheckerTests(unittest.TestCase):
    def check(self, phi, psi, semantics="oracle", dom=None):
        return check_equivalence(phi, psi, dom or domain(), semantics, timeout_s=60)

    def assertVerdict(self, outcome, verdict, method=None):
        self.assertEqual(outcome.verdict, verdict, outcome.detail)
        if method:
            self.assertEqual(outcome.method, method)

    def test_canonical_shortcut(self) -> None:
        self.assertVerdict(self.check("IMPLIES[p,q]", "OR[NOT[p],q]"), "equivalent", "canonical")

    def test_strictness_oracle_vs_boolean(self) -> None:
        self.assertVerdict(self.check("x > 3", "x >= 3", "oracle"), "equivalent")
        outcome = self.check("x > 3", "x >= 3", "boolean")
        self.assertVerdict(outcome, "not_equivalent", "smt")
        self.assertEqual(outcome.counterexample["x"][0], 3.0)

    def test_temporal_flip_not_equivalent(self) -> None:
        self.assertVerdict(self.check("GLOBALLY I=[0,5][x > 3]", "EVENTUALLY I=[0,5][x > 3]"),
                           "not_equivalent", "smt")

    def test_nested_globally_collapses(self) -> None:
        self.assertVerdict(self.check("GLOBALLY I=[0,2][GLOBALLY I=[0,3][p]]",
                                      "GLOBALLY I=[0,5][p]"), "equivalent", "smt")

    def test_point_interval(self) -> None:
        self.assertVerdict(self.check("EVENTUALLY I=[0,0][p]", "p"), "equivalent", "smt")

    def test_until_with_trivial_left(self) -> None:
        self.assertVerdict(self.check("UNTIL I=[0,5][x >= 0,q]", "EVENTUALLY I=[0,5][q]"),
                           "equivalent", "smt")
        self.assertVerdict(self.check("UNTIL I=[0,5][p,q]", "EVENTUALLY I=[0,5][q]"),
                           "not_equivalent", "smt")

    def test_domain_bounds_matter(self) -> None:
        self.assertVerdict(self.check("GLOBALLY I=[0,5][x <= 10]", "GLOBALLY I=[0,5][x <= 20]"),
                           "equivalent", "smt")

    def test_integer_domain(self) -> None:
        self.assertVerdict(self.check("n > 2", "n >= 3", "boolean"), "equivalent", "smt")

    def test_oracle_negated_equality_is_always_true(self) -> None:
        # The quirk that motivated the eq_* Boolean derived signals.
        self.assertVerdict(self.check("NOT[x == 3]", "OR[p,NOT[p]]", "oracle"), "equivalent")
        self.assertVerdict(self.check("NOT[x == 3]", "OR[p,NOT[p]]", "boolean"), "not_equivalent")

    def test_derived_and_static_signals(self) -> None:
        dom = domain(L=SignalDomain("static", 4.5, 4.5),
                     x_minus_y=SignalDomain("real", -10, 10), y_minus_x=SignalDomain("real", -10, 10))
        dom.derived = {"x_minus_y": {"op": "subtract", "left": "x", "right": "y"},
                       "y_minus_x": {"op": "subtract", "left": "y", "right": "x"}}
        self.assertVerdict(self.check("GLOBALLY I=[0,2][x_minus_y >= 0]",
                                      "GLOBALLY I=[0,2][y_minus_x <= 0]", dom=dom), "equivalent", "smt")
        self.assertVerdict(self.check("L >= 4", "OR[p,NOT[p]]", "boolean", dom=dom), "equivalent", "smt")

    def test_equals_derived_signal(self) -> None:
        dom = domain(eq_n_3=SignalDomain("bool", 0, 1))
        dom.derived = {"eq_n_3": {"op": "equals", "left": "n", "value": "3"}}
        self.assertVerdict(self.check("NOT[eq_n_3]", "OR[n < 3,n > 3]", "boolean", dom=dom),
                           "equivalent", "smt")

    def test_unbounded_window_is_clipped_to_trace(self) -> None:
        self.assertVerdict(self.check("GLOBALLY I=[0,inf][p]", "GLOBALLY I=[0,5][p]"), "equivalent", "smt")
        self.assertVerdict(self.check("GLOBALLY I=[0,inf][EVENTUALLY I=[0,2][p]]",
                                      "GLOBALLY I=[0,3][EVENTUALLY I=[0,2][p]]"), "equivalent", "smt")

    def test_unaligned_interval_is_unknown(self) -> None:
        self.assertVerdict(self.check("EVENTUALLY I=[0,2.5][p]", "EVENTUALLY I=[0,2][p]"), "unknown")

    def test_horizon_beyond_time_end_is_unknown(self) -> None:
        self.assertVerdict(self.check("EVENTUALLY I=[0,9][p]", "p"), "unknown")


def _java() -> str | None:
    java = os.environ.get("JAVA_BIN") or shutil.which("java")
    if not os.environ.get("JAVA_BIN") and platform.system() == "Darwin" and platform.machine() == "arm64":
        candidates = sorted(Path.home().glob("Library/Java/JavaVirtualMachines/jdk-17*/Contents/Home/bin/java"))
        if candidates:
            java = str(candidates[-1])
    classes = ROOT / "target" / "classes" / "it" / "units" / "malelab" / "learningstl"
    return java if java and (classes / "EvaluateFormulaOnDataset.class").exists() else None


CROSS_CHECK_FORMULAS = [
    "GLOBALLY I=[0,3][x > 4]",
    "EVENTUALLY I=[1,3][AND[x < 6,p]]",
    "IMPLIES[p,EVENTUALLY I=[0,2][y >= 5]]",
    "NOT[GLOBALLY I=[0,2][OR[q,x <= 2]]]",
    "UNTIL I=[0,4][x >= 3,y > 7]",
    "UNTIL I=[1,3][p,q]",
    # T2 is a bounded-grid checker.  Infinite windows are intentionally
    # covered by the checker tests above, but not advertised as an exact
    # cross-check of Moonlight's continuous finite-signal endpoint policy.
    "AND[x == 5,GLOBALLY I=[0,2][n >= 2]]",
    "NOT[n == 3]",
    "OR[GLOBALLY I=[0,1][n < 2],EVENTUALLY I=[2,4][NOT[p]]]",
]


@unittest.skipUnless(_java(), "compiled Java evaluator not available")
class MoonlightCrossCheckTests(unittest.TestCase):
    """The evaluator must reproduce the oracle verdicts of EvaluateFormulaOnDataset."""

    def test_random_traces_match_java(self) -> None:
        for step in (1.0, 0.5):
            with self.subTest(time_step=step):
                self._compare(step)

    def _compare(self, step: float) -> None:
        from restless.repair.repair_loop import write_data_csv, write_labels_csv

        dom = domain()
        dom.time_end, dom.time_step = 6.0, step
        names = ["x", "y", "n", "p", "q"]
        rng = random.Random(7)
        traces = []
        for _ in range(60):
            trace = {}
            for name in names:
                spec = dom.signals[name]
                if spec.kind == "real":
                    # Integers make boundary cases (x == c) frequent.
                    trace[name] = [float(rng.choice([rng.randint(0, 10), rng.uniform(0, 10)]))
                                   for _ in range(dom.n_steps + 1)]
                else:
                    trace[name] = [float(rng.randint(int(spec.lo), int(spec.hi)))
                                   for _ in range(dom.n_steps + 1)]
            traces.append(trace)
        rows = [[trace[name][k] for k in range(dom.n_steps + 1) for name in names] for trace in traces]
        java = _java()
        scaled = [formula.replace("I=[0,3]", "I=[0,3]") for formula in CROSS_CHECK_FORMULAS]
        with tempfile.TemporaryDirectory(prefix="equiv-xcheck-") as tmp_text:
            tmp = Path(tmp_text)
            write_data_csv(tmp / "data.csv", rows, names)
            write_labels_csv(tmp / "labels.csv", [1] * len(rows))
            write_labels_csv(tmp / "times.csv", dom.times())
            for formula in scaled:
                output = tmp / "eval.csv"
                command = [
                    java, "-cp", "libs/JGEA.jar:libs/moonlight.jar:libs/jblas-1.2.4.jar:target/classes",
                    "it.units.malelab.learningstl.EvaluateFormulaOnDataset",
                    "dataset=xcheck", f"dataPath={tmp}", "strictLengthCheck=false",
                    "shortSignalPolicy=penalty", "normalizeValues=false", "renumberTimes=false",
                    f"formula={formula}", f"output={output}",
                ]
                if platform.system() == "Darwin" and platform.machine() == "arm64":
                    command = ["arch", "-x86_64", *command]
                subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
                lines = [line for line in output.read_text().splitlines()[1:] if not line.startswith("#")]
                java_sat = [line.split(",")[5] == "SAT" for line in lines]
                python_sat = [Evaluator(dom, trace).verdict(formula, "oracle") for trace in traces]
                mismatches = [i for i, (a, b) in enumerate(zip(java_sat, python_sat)) if a != b]
                self.assertEqual(mismatches, [], f"{formula} (dt={step}): Java vs evaluator differ")


class EqualsWitnessTests(unittest.TestCase):
    def test_witness_at_tolerance_edge_replays(self):
        from restless.evaluation.metrics.equivalence import Domain, check_equivalence
        domain = Domain.from_config({
            "signals": [{"name": "x", "min": 1400, "max": 1700}, {"name": "y", "min": 0, "max": 10},
                        {"name": "eq_x", "min": 0, "max": 1}],
            "boolean_signals": ["eq_x"],
            "derived_signal_definitions": {"eq_x": {"op": "equals", "left": "x", "value": "1566"}},
            "time_start": 0, "time_end": 3, "time_step": 1})
        result = check_equivalence("IMPLIES[eq_x,y <= 6]", "GLOBALLY I=[0,3][IMPLIES[eq_x,y <= 6]]", domain, timeout_s=20)
        self.assertEqual(result.verdict, "not_equivalent", result.detail)


if __name__ == "__main__":
    unittest.main()
