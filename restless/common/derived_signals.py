"""Derived-signal semantics (single source of truth for materialising derived signals)."""

from __future__ import annotations

from typing import Mapping

# Numeric equality is decided on the exported sample value; the tolerance only
# absorbs decimal printing of an SMT value that was forced to equal the constant.
EQUALS_RELATIVE_TOLERANCE = 1e-9


def evaluate_derived(definition: Mapping[str, object], values: Mapping[str, float]) -> float:
    op = str(definition.get("op", ""))
    if op == "subtract":
        return float(values[str(definition["left"])]) - float(values[str(definition["right"])])
    if op == "linear_difference":
        return float(values[str(definition["left"])]) - float(values[str(definition["right"])])
    if op == "linear_margin_subtract":
        return (float(values[str(definition["left"])])
                - float(values[str(definition["right"])])
                + float(values[str(definition["subtrahend"])]))
    if op == "abs_difference":
        return abs(float(values[str(definition["left"])])
                   - float(values[str(definition["right"])]))
    if op == "equals":
        value = float(values[str(definition["left"])])
        constant = float(definition["value"])
        tolerance = EQUALS_RELATIVE_TOLERANCE * max(1.0, abs(constant))
        return 1.0 if abs(value - constant) <= tolerance else 0.0
    raise ValueError(f"Unsupported derived operation: {op!r}")


def referenced_signals(definition: Mapping[str, object]) -> list[str]:
    if definition.get("op") in {"subtract", "linear_difference"}:
        return [str(definition["left"]), str(definition["right"])]
    if definition.get("op") == "linear_margin_subtract":
        return [str(definition["left"]), str(definition["right"]), str(definition["subtrahend"])]
    if definition.get("op") == "abs_difference":
        return [str(definition["left"]), str(definition["right"])]
    if definition.get("op") == "equals":
        return [str(definition["left"])]
    return []
