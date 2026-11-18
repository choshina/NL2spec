#!/usr/bin/env python3

from __future__ import annotations

import argparse
import unittest

from restless.repair.repair_loop import (
    materialize_derived_trace_rows,
    matlab_derived_signal_definitions,
    parse_derived_signal_definitions,
    validate_derived_trace_rows,
)
from restless.repair.dataset_launcher import SignalSpec, infer_derived_signal_definitions


class DerivedSignalConstraintTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = {
            "x_minus_y": {"op": "subtract", "left": "x", "right": "y"}
        }

    def test_infers_adapter_difference_identity(self) -> None:
        specs = [
            SignalSpec("x", "x", 0.0, 10.0),
            SignalSpec("y", "y", -2.0, 2.0),
            SignalSpec("x_minus_y", "x_minus_y", -2.0, 12.0),
        ]
        actual = infer_derived_signal_definitions(
            specs,
            ["GLOBALLY I=[0,5][x_minus_y > 3]"],
        )
        self.assertEqual(actual, self.definition)

    def test_parses_and_serializes_definition(self) -> None:
        parsed = parse_derived_signal_definitions(
            '{"x_minus_y":{"op":"subtract","left":"x","right":"y"}}',
            ["x", "y", "x_minus_y"],
        )
        self.assertEqual(parsed, self.definition)
        self.assertEqual(
            matlab_derived_signal_definitions(parsed),
            "{'x_minus_y','{\"op\":\"subtract\",\"left\":\"x\",\"right\":\"y\"}'}",
        )

    def test_materializes_absolute_difference(self) -> None:
        args = argparse.Namespace(
            signal_names=["x", "y", "abs_xy"],
            derived_signal_definitions={
                "abs_xy": {"op": "abs_difference", "left": "x", "right": "y"}
            },
        )
        rows = [[-2.0, 3.0, 999.0, 7.0, 1.0, 999.0]]
        materialize_derived_trace_rows(args, rows)
        self.assertEqual(rows, [[-2.0, 3.0, 5.0, 7.0, 1.0, 6.0]])
        validate_derived_trace_rows(args, rows)

    def test_refuses_independent_difference_without_base_signals(self) -> None:
        specs = [SignalSpec("x_minus_y", "x_minus_y", -20.0, 20.0)]
        with self.assertRaisesRegex(ValueError, "requires configured base signals"):
            infer_derived_signal_definitions(specs, ["x_minus_y > 3"])

    def test_materializes_time_major_trace_columns(self) -> None:
        args = argparse.Namespace(
            signal_names=["x", "y", "x_minus_y"],
            derived_signal_definitions=self.definition,
        )
        rows = [[5.0, 2.0, 999.0, -1.0, 4.0, 999.0]]
        materialize_derived_trace_rows(args, rows)
        self.assertEqual(rows, [[5.0, 2.0, 3.0, -1.0, 4.0, -5.0]])
        validate_derived_trace_rows(args, rows)


if __name__ == "__main__":
    unittest.main()
