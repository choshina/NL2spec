# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.

"""Shared helpers for the dataset/LLM_generated_STL.csv wrapper scripts.

Extracted from the formula wrapper scripts; keeps CSV reading, STL syntax
conversion, and formula metadata logic before routing to the generic repair
loop.
"""

from __future__ import annotations

import csv
from pathlib import Path

from restless.common.stl_epoch.ast_nodes import assign_horizon, walk_preorder
from restless.common.stl_epoch.formula_ops import bustle_print
from restless.common.stl_epoch.parser import parse_stl

try:
    from restless.ceclass.c534_adapter import adapt_archcomp_formula
except Exception:  # pragma: no cover - keeps formula_io usable without C534 deps.
    def adapt_archcomp_formula(formula: str, signal_aliases: dict[str, str] | None = None) -> str:
        return formula

_MATH_REPLACEMENTS = {
    "¬": " not ",
    "∨": " or ",
    "∧": " and ",
    "→": " -> ",
    "□": " G",
    "◇": " F",
    "≤": "<=",
    "≥": ">=",
    "−": "-",
}


def read_formula(path: Path, row_index: int) -> str:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if row_index < 1 or row_index > len(rows):
        raise IndexError(f"row {row_index} outside formula CSV range 1..{len(rows)}: {path}")
    row = rows[row_index - 1]
    if "formula" not in row:
        raise KeyError(f'column "formula" not found in {path}')
    # The benchmark CSVs are sometimes written without quoting formulas that
    # contain interval commas. DictReader stores those extra fields under the
    # None key; joining them reconstructs the original formula losslessly.
    pieces = [row["formula"] or ""]
    pieces.extend(row.get(None) or [])
    formula = ",".join(pieces).strip().strip('"')
    if not formula:
        raise ValueError(f"empty formula at row {row_index}: {path}")
    return formula


def convert_formula_to_bustle(
    formula: str,
    signal_aliases: dict[str, str] | None = None,
) -> str:
    """Convert supported NL/CSV STL syntax to BUSTLE prefix STL.

    This is syntax conversion only. It does not normalize signal values, time
    stamps, robustness, or data columns.
    """
    text = adapt_archcomp_formula(formula.strip(), signal_aliases=signal_aliases)
    for source, target in _MATH_REPLACEMENTS.items():
        text = text.replace(source, target)
    # Keep implication as an explicit AST node.  Expanding A -> B into
    # OR[NOT[A],B] loses the antecedent/consequent roles needed by SGRL.
    return bustle_print(parse_stl(text))


def formula_metadata(formula: str) -> tuple[float, list[str]]:
    ast = parse_stl(formula)
    horizon = float(assign_horizon(ast))
    signals = sorted({
        str(node.var)
        for node, _path in walk_preorder(ast)
        if node.kind in {"atom", "bool"}
    })
    return horizon, signals


def write_formula_csv(path: Path, formula: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["formula"])
        writer.writeheader()
        writer.writerow({"formula": formula})
