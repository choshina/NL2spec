"""Fault type of every instance (all three datasets) and correct rate by fault type.

    python3 -m restless.evaluation.tables.fault_type_table results/paper_summary

Reads per_run.csv from a restless_summary output directory, classifies each instance by the structural diff
between its faulty formula and its reference (the same diff the run records use, restless/pipeline/run_record.py),
writes fault_types.csv (one row per instance), and prints the numerical summary used to check the fault-type plot.

Categories (paper Table "fault types"), from the edits of the diff:
  out_of_scope  an edit changes an interval, a threshold or a signal (replace_interval / replace_threshold /
                replace_signal): the search space of ReSTLess cannot express it. Takes priority over the others.
  temporal      only G <-> F flips
  boolean       only and <-> or flips
  predicate     only comparison-direction flips (< <-> >)
  structural    only node replacements of another kind (replace_X_with_Y, replace_subtree)
  mixed         edits of two or more of the four categories above
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
from restless.pipeline.run_record import infer_fault_metadata  # noqa: E402
from restless.common.formula_io import convert_formula_to_bustle, read_formula  # noqa: E402

SOURCE_TAG = {"DeepSTL": "deepstl_v1", "STDP": "stdp_v1", "Archcomp": "archcomp_v1"}
OUT_OF_SCOPE = {"replace_interval", "replace_threshold", "replace_signal"}
CATEGORY = {"flip_temporal": "temporal", "flip_boolean": "boolean", "flip_predicate_direction": "predicate"}
ORDER = ["temporal", "boolean", "predicate", "structural", "mixed", "out_of_scope"]
METHODS = ["llm_direct", "llm_ce", "restless", "no_ceclass", "no_self_coeff", "noloc"]


def classify(edit_types: list[str]) -> str:
    if not edit_types:
        return "none"
    if any(t in OUT_OF_SCOPE for t in edit_types):
        return "out_of_scope"
    kinds = {CATEGORY.get(t, "structural") for t in edit_types}
    return kinds.pop() if len(kinds) == 1 else "mixed"


def main() -> None:
    out = Path(sys.argv[1])
    runs = pd.read_csv(out / "per_run.csv")
    runs = runs[runs.method != "llm"]
    rows = []
    for (dataset, uid), _ in runs.groupby(["dataset", "instance_id"]):
        directory = ROOT / "experiments" / "frozen" / SOURCE_TAG[dataset] / "instances" / uid
        faulty = convert_formula_to_bustle(read_formula(directory / "Incorrect_STL.csv", 1))
        oracle = convert_formula_to_bustle(read_formula(directory / "Oracle_STL.csv", 1))
        meta = infer_fault_metadata(faulty, oracle)
        rows.append({"dataset": dataset, "instance_id": uid, "category": classify(meta["fault_types"]),
                     "edit_types": "+".join(meta["fault_types"]), "num_faults": meta["num_faults"]})
    faults = pd.DataFrame(rows)
    faults.to_csv(out / "fault_types.csv", index=False)

    merged = runs.merge(faults[["dataset", "instance_id", "category"]], on=["dataset", "instance_id"])
    per_instance = merged.groupby(["dataset", "category", "instance_id", "method"], as_index=False).correct.mean()
    table = []
    for dataset in ["DeepSTL", "STDP", "Archcomp", "All"]:
        sub = per_instance if dataset == "All" else per_instance[per_instance.dataset == dataset]
        row = {"dataset": dataset, "metric": "instances"}
        for cat in ORDER:
            row[cat] = sub[sub.category == cat].instance_id.nunique()
        row["total"] = sub.instance_id.nunique()
        table.append(row)
        for method in METHODS:
            part = sub[sub.method == method]
            if part.empty:
                continue
            row = {"dataset": dataset, "metric": method}
            for cat in ORDER:
                values = part[part.category == cat].correct
                row[cat] = round(100 * values.mean(), 1) if len(values) else None
            row["total"] = round(100 * part.correct.mean(), 1)
            table.append(row)
    result = pd.DataFrame(table)
    print(faults.groupby(["dataset", "category"]).size().unstack(fill_value=0))
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
