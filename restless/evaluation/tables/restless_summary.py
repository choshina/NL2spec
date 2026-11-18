"""RQ1-RQ3 tables of the ReSTLess evaluation from one or more tags.

    python3 -m restless.evaluation.tables.restless_summary --tags restless_deepstl_test --out results/restless_deepstl_test

Per run (run.json): correct (bounded-horizon equivalence with the reference; unknown = fail),
plausible (final formula right on every queried trace), overfit, Q_total, Q_star (censored when
no correct candidate appeared), time (total and tracegen / labelling / search), gate fraction,
edits_to_correct (candidate evaluations until the first correct candidate),
ce_found_first_eq, and q_to_first_ce.

A run that is missing, crashed, timed out or failed to generate traces is a failure: correct =
plausible = 0 and it is charged the full budget (Q_total = Q_max, time = time limit); it stays in
every denominator. What a failed run really consumed before it was killed is kept apart, in the
*_observed columns, and is only used for the timeout analysis.

Per run, Q is split into Q_eq (equivalence-query traces) and Q_disamb (candidate-disambiguation
traces), and the time spent inside candidate disambiguation is reported as time_disamb_s. That
phase includes ATG and evaluator calls, so it overlaps time_tracegen_s / time_labelling_s.
summary_timeouts.csv reports, per method, the timeout rate and how much of the queries and time
of the timed-out runs went to candidate disambiguation (the answer to "is the baseline dragged
down by it?").
Aggregation: one run per (instance, method); average (or take the median of) instances.
95% CI: bootstrap over requirements (10000 resamples, instances of one requirement resampled
together). Paired comparisons against restless: Wilcoxon signed-rank on instance means, Holm-
corrected within each table.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FROZEN = ROOT / "experiments" / "frozen"
RQ1 = ("restless", "llm_direct", "llm_ce")
RQ2 = ("restless", "no_self_coeff", "noloc")
RQ3 = ("restless", "no_ceclass")
BOOTSTRAP = 10000


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def latest_records(results: Path) -> dict[tuple[str, str], dict]:
    """Newest attempt of every (instance, method) under results/<tag>."""
    found: dict[tuple[str, str], tuple[int, dict]] = {}
    for path in results.glob("rq*/*/*/*/attempt*/run_record.json"):
        try:
            record = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        method, uid = path.parts[-4], path.parts[-3]
        attempt = int(path.parts[-2][7:])
        record["_dir"] = path.parent
        if (uid, method) not in found or attempt > found[(uid, method)][0]:
            found[(uid, method)] = (attempt, record)
    return {key: value[1] for key, value in found.items()}


LLM_FIELDS = ("llm_calls",)


def llm_fields(method: str, record: dict | None) -> dict:
    """Cost and parse counters of an LLM-CE run (llm_ce_summary.json, kept even when the run timed out)."""
    if method not in ("llm_ce", "llm_direct"):
        return {key: None for key in LLM_FIELDS}
    path = ((record or {}).get("_dir") or Path("/nonexistent")) / "llm_ce_summary.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {key: None for key in LLM_FIELDS}
    return {key: data.get(key) for key in LLM_FIELDS}


# --count-timeout-repaired: a run cut off by the time limit whose last equivalence check already said
# "equivalent" counts as correct (everything else about the run stays as in a failure).
COUNT_TIMEOUT_REPAIRED = False


def run_metrics(spec: dict, method: str, record: dict | None, budget: dict) -> dict:
    return {**_run_metrics(spec, method, record, budget), **llm_fields(method, record),
            "edit_dist_phi0": ((record or {}).get("result") or {}).get("ted_to_initial")
            if (record or {}).get("complete") and (record or {}).get("status") != "crash" else None}


def _run_metrics(spec: dict, method: str, record: dict | None, budget: dict) -> dict:
    failed_row = {"status": "failure", "correct": 0, "plausible": 0, "overfit": 0, "Q_total": budget["Q_max"],
                  "Q_star": None, "time_s": budget["time_limit_s"], "time_tracegen_s": None,
                  "time_labelling_s": None, "time_search_s": None, "edits_to_correct": None,
                  "ce_found_first_eq": 0, "q_to_first_ce": None,
                  "Q_eq": None, "Q_disamb": None, "time_disamb_s": None,
                  "Q_observed": None, "Q_disamb_observed": None, "time_disamb_observed_s": None}
    if record is None or not record.get("complete") or record.get("status") == "crash":
        reason = "missing" if record is None else (record.get("terminal_reason") or record.get("status") or "incomplete")
        partial = (record or {}).get("partial_metrics") or {}
        if COUNT_TIMEOUT_REPAIRED and ((record or {}).get("result", {}).get("equivalence") or {}).get("verdict") == "equivalent":
            failed_row = {**failed_row, "correct": 1, "plausible": 1}
            reason = f"{reason}; counted correct (equivalent before the time limit)"
        return {**failed_row, "failure_reason": reason,
                "Q_observed": partial.get("Q_total"), "Q_disamb_observed": partial.get("Q_disambiguation"),
                "time_disamb_observed_s": partial.get("time_disambiguation_s")}
    result = record.get("result", {})
    verdict = (result.get("equivalence") or {}).get("verdict")
    correct = int(verdict == "equivalent")
    plausible = int(bool(result.get("plausible")))
    observed = result.get("observed_trace_set") or {}
    first_eq = observed.get("first_eq") or {}
    gate = observed.get("first_gate") or {}
    gate_frac = None
    if method != "noloc" and gate.get("gate") is not None and gate.get("formula_nodes"):
        gate_frac = len(gate["gate"]) / gate["formula_nodes"]   # share of the formula's nodes the first gate keeps
    times = result.get("time_s") or {}
    generation_failed = method != "llm_direct" and result.get("Q", 0) == 0 and not first_eq.get("queries")
    if generation_failed:
        return {**failed_row, "failure_reason": "trace generation failure"}
    trace_metrics = record.get("trace_metrics") or {}
    q_disamb = trace_metrics.get("num_oracle_queried_disambiguation")
    time_disamb = trace_metrics.get("time_disambiguation_s")
    q_total = result.get("Q")
    q_eq = (q_total - q_disamb) if (q_total is not None and q_disamb is not None) else None
    return {
        "status": "ok", "failure_reason": "", "correct": correct, "plausible": plausible,
        "overfit": int(plausible and not correct), "Q_total": result.get("Q"),
        "Q_star": result.get("Q_star") if correct or result.get("Q_star") is not None else None,
        "time_s": result.get("wall_time_s"), "time_tracegen_s": times.get("tracegen"),
        "time_labelling_s": times.get("labelling"), "time_search_s": times.get("search"),
        "gate_frac": gate_frac, "edits_to_correct": result.get("fitness_evals_star"),
        "ce_found_first_eq": int(bool(first_eq.get("ce_found"))), "q_to_first_ce": first_eq.get("q_to_first_ce"),
        "Q_eq": q_eq, "Q_disamb": q_disamb, "time_disamb_s": time_disamb,
        "Q_observed": q_total, "Q_disamb_observed": q_disamb, "time_disamb_observed_s": time_disamb,
    }


def collect(tags: list[str]) -> tuple[list[dict], dict]:
    """tags: "name" or "name:method1,method2" (only those methods of that tag count)."""
    rows, budgets = [], {}
    for tag_spec in tags:
        tag, _, only = tag_spec.partition(":")
        keep = {m for m in only.split(",") if m}
        manifest = json.loads((FROZEN / tag / "manifest_summary.json").read_text())
        budget = manifest["budgets"]
        budgets[tag] = budget
        records = latest_records(ROOT / "results" / tag)
        expected = list(csv.DictReader((FROZEN / tag / "expected_runs.csv").open(newline="")))
        specs = {}
        for run in expected:
            if keep and run["method"] not in keep:
                continue
            spec = specs.setdefault(run["spec"], json.loads((ROOT / run["spec"]).read_text()))
            record = records.get((run["instance_id"], run["method"]))
            metrics = run_metrics(spec, run["method"], record, budget)
            rows.append({"tag": tag, "dataset": spec["source"], "instance_id": run["instance_id"],
                         "requirement": spec.get("requirement_id") or spec["uid"].split("__")[1],
                         "split": spec.get("split", ""), "method": run["method"],
                         "wall_time_s": (record or {}).get("result", {}).get("wall_time_s"), **metrics})
    return rows, budgets


def instance_values(rows: list[dict], metric: str, *, success_only: bool = False) -> dict[str, tuple[str, float]]:
    """instance -> (requirement, value); runs where the metric is n/a are skipped."""
    per = defaultdict(list)
    req = {}
    for row in rows:
        if success_only and not row["correct"]:
            continue
        value = _num(row.get(metric))
        if value is None:
            continue
        per[row["instance_id"]].append(value)
        req[row["instance_id"]] = row["requirement"]
    return {uid: (req[uid], sum(v) / len(v)) for uid, v in per.items()}


def bootstrap_ci(values: dict[str, tuple[str, float]], stat, seed: int = 0) -> tuple[float, float] | tuple[None, None]:
    if not values:
        return None, None
    clusters = defaultdict(list)
    for requirement, value in values.values():
        clusters[requirement].append(value)
    keys = sorted(clusters)
    rng = random.Random(seed)
    draws = []
    for _ in range(BOOTSTRAP):
        sample = [v for k in (rng.choice(keys) for _ in keys) for v in clusters[k]]
        draws.append(stat(sample))
    draws.sort()
    return draws[int(0.025 * BOOTSTRAP)], draws[int(0.975 * BOOTSTRAP) - 1]


def wilcoxon(a: dict, b: dict) -> float | None:
    shared = sorted(set(a) & set(b))
    diffs = [a[u][1] - b[u][1] for u in shared]
    if not any(diffs):
        return None if not diffs else 1.0
    try:
        from scipy.stats import wilcoxon as test  # noqa: PLC0415
    except ImportError as error:
        raise SystemExit("scipy is required for the Wilcoxon test: python3 -m pip install scipy") from error
    return float(test([a[u][1] for u in shared], [b[u][1] for u in shared], zero_method="wilcox").pvalue)


def holm(pvalues: dict) -> dict:
    present = sorted((p, k) for k, p in pvalues.items() if p is not None)
    adjusted, running = {}, 0.0
    for rank, (p, key) in enumerate(present):
        running = max(running, min(1.0, (len(present) - rank) * p))
        adjusted[key] = running
    return {k: adjusted.get(k) for k in pvalues}


def mean(values):
    return sum(values) / len(values) if values else float("nan")


def median(values):
    return statistics.median(values) if values else float("nan")


TABLES = {
    "rq1": (RQ1, None, [("correct", "mean"), ("plausible", "mean"), ("overfit", "mean"), ("Q_total", "median"),
                        ("Q_star", "median_success"), ("time_s", "median")]),
    "rq2": (RQ2, None, [("gate_frac", "mean"), ("edits_to_correct", "median_success"), ("correct", "mean")]),
    "rq3": (RQ3, None, [("ce_found_first_eq", "mean"), ("q_to_first_ce", "median"), ("overfit", "mean"),
                        ("correct", "mean")]),
}


def summarize(rows: list[dict], name: str) -> list[dict]:
    methods, split_by, metrics = TABLES[name]
    out = []
    groups = sorted({(r["dataset"], r[split_by] if split_by else "all") for r in rows})
    for dataset, group in groups:
        scope = [r for r in rows if r["dataset"] == dataset and (not split_by or r[split_by] == group)]
        pvalues, entries = {}, {}
        for method in methods:
            mine = [r for r in scope if r["method"] == method]
            if not mine:
                continue
            entry = {"dataset": dataset, "group": group, "method": method,
                     "n_instances": len({r["instance_id"] for r in mine})}
            for metric, how in metrics:
                values = instance_values(mine, metric, success_only=how == "median_success")
                stat = mean if how == "mean" else median
                point = stat([v for _, v in values.values()])
                low, high = bootstrap_ci(values, stat)
                entry[metric] = point
                entry[f"{metric}_ci"] = f"[{low:.3f}, {high:.3f}]" if low is not None else ""
                if how == "median_success":
                    entry[f"{metric}_n_success"] = len(values)
                if method != "restless":
                    reference = instance_values([r for r in scope if r["method"] == "restless"], metric,
                                                success_only=how == "median_success")
                    pvalues[(method, metric)] = wilcoxon(reference, values)
            entries[method] = entry
        adjusted = holm(pvalues)
        for (method, metric), p in adjusted.items():
            entries[method][f"{metric}_p_holm"] = p
        out.extend(entries.values())
    return out


def is_timeout(row: dict) -> bool:
    return str(row.get("failure_reason", "")).startswith("time limit exceeded")


def timeout_summary(rows: list[dict], budgets: dict) -> list[dict]:
    """Per dataset and method: timeout rate, and where the queries and time of timed-out runs went.
    Shares are pooled (sum of disambiguation / sum of everything) so long runs weigh more."""
    out = []
    groups = sorted({(r["tag"], r["dataset"], r["method"]) for r in rows if r["method"] != "llm"})
    for tag, dataset, method in groups:
        mine = [r for r in rows if (r["tag"], r["dataset"], r["method"]) == (tag, dataset, method)]
        limit = float(budgets[tag]["time_limit_s"])
        timed = [r for r in mine if is_timeout(r)]
        done = [r for r in mine if r["status"] == "ok"]

        def pooled(group, with_time):
            q_all = sum(_num(r.get("Q_observed"), 0.0) for r in group)
            q_dis = sum(_num(r.get("Q_disamb_observed"), 0.0) for r in group)
            t_dis = sum(_num(r.get("time_disamb_observed_s"), 0.0) for r in group)
            t_all = sum((limit if is_timeout(r) else _num(r.get("time_s"), 0.0)) for r in group)
            return (q_dis / q_all if q_all else None, t_dis / t_all if t_all else None) if with_time else (q_all, q_dis)
        q_share_t, t_share_t = pooled(timed, True) if timed else (None, None)
        q_share_d, t_share_d = pooled(done, True) if done else (None, None)
        observed = [r for r in timed if r.get("Q_observed") is not None]
        out.append({
            "dataset": dataset, "method": method, "runs": len(mine), "timeouts": len(timed),
            "timeout_rate": len(timed) / len(mine) if mine else None,
            "timed_out_with_data": len(observed),
            "timed_out_mean_Q_observed": mean([_num(r["Q_observed"]) for r in observed]) if observed else None,
            "timed_out_mean_Q_disamb": mean([_num(r["Q_disamb_observed"], 0.0) for r in observed]) if observed else None,
            "timed_out_disamb_share_of_queries": q_share_t,
            "timed_out_disamb_share_of_time": t_share_t,
            "completed_disamb_share_of_queries": q_share_d,
            "completed_disamb_share_of_time": t_share_d,
        })
    return out


def write_table(rows: list[dict], path: Path) -> None:
    fields = []
    for row in rows:
        fields += [k for k in row if k not in fields]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def public_per_run(rows: list[dict]) -> list[dict]:
    """Keep only identifiers, reported metrics, and inputs used by the paper figures."""
    fields = (
        "dataset", "instance_id", "requirement", "method", "status", "failure_reason",
        "correct", "plausible", "overfit", "Q_total", "Q_star", "time_s", "llm_calls",
        "edit_dist_phi0", "edits_to_correct", "gate_frac", "q_to_first_ce",
    )
    return [{key: row.get(key) for key in fields} for row in rows]


def _fmt(value) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "--"
    return f"{value:.2f}" if isinstance(value, float) else str(value)


def write_tex(rows: list[dict], name: str, path: Path) -> None:
    _, split_by, metrics = TABLES[name]
    head = ["Dataset", *(["Faults"] if split_by else []), "Method", *[m.replace("_", r"\_") for m, _ in metrics]]
    lines = [r"\begin{tabular}{" + "l" * (len(head) - len(metrics)) + "r" * len(metrics) + "}", r"\toprule",
             " & ".join(head) + r" \\", r"\midrule"]
    for row in rows:
        cells = [str(c).replace("_", r"\_") for c in (row["dataset"], *([row["group"]] if split_by else []), row["method"])]
        for metric, how in metrics:
            cell = _fmt(row.get(metric))
            if how == "median_success":
                cell += f" ({row.get(f'{metric}_n_success', 0)})"
            cells.append(cell)
        lines.append(" & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}", ""]
    path.write_text("\n".join(lines))


def export_runs(rows: list[dict], out: Path) -> None:
    for row in rows:
        target = out / "runs" / row["dataset"] / row["method"] / row["instance_id"] / "run.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        row = {**row, "Q_disambig": row.get("Q_disamb"), "time_disambig": row.get("time_disamb_s")}
        target.write_text(json.dumps(row, indent=2, sort_keys=True, default=str) + "\n")


def run_report(rows: list[dict], tags: list[str], budgets: dict, out: Path) -> None:
    lines = ["# RUN_REPORT", ""]
    for tag in tags:
        checks = sorted((ROOT / "results" / tag / "_control").glob("*/fairness.json"))
        results = []
        for path in checks:
            try:
                results.append(bool(json.loads(path.read_text()).get("passed")))
            except (OSError, json.JSONDecodeError):
                results.append(False)
        lines += [f"## {tag}", "", f"- budgets: `{json.dumps(budgets[tag], sort_keys=True)}`",
                  f"- config diff check: {sum(results)}/{len(results)} instance checks passed"
                  + ("" if all(results) else " (FAILED ones exist)"), ""]
        mine = [r for r in rows if r["tag"] == tag and r["method"] != "llm"]
        lines += ["| method | runs | failures | total wall time (h) |", "|---|---|---|---|"]
        for method in sorted({r["method"] for r in mine}):
            runs = [r for r in mine if r["method"] == method]
            failures = [r for r in runs if r["status"] != "ok"]
            hours = sum(_num(r.get("wall_time_s"), 0.0) for r in runs) / 3600
            lines.append(f"| {method} | {len(runs)} | {len(failures)} | {hours:.2f} |")
        reasons = defaultdict(int)
        for r in mine:
            if r["status"] != "ok":
                reasons[r["failure_reason"]] += 1
        reason_lines = [f"- {k}: {v}" for k, v in sorted(reasons.items())] or ["- none"]
        lines += ["", "Failure reasons:", *reason_lines, ""]
    timeouts = timeout_summary([r for r in rows if r["method"] != "llm"], budgets)
    pct = lambda v: "n/a" if v is None else f"{100 * v:.0f}%"
    lines += ["## Timeouts and candidate disambiguation", "",
              "Timeouts are failures charged the full budget and stay in the denominator. The shares below are of "
              "what the run consumed before it was killed.", "",
              "| dataset | method | runs | timeouts | rate | of timed-out runs: disamb. share of queries | "
              "disamb. share of time | (completed runs: queries / time) |", "|---|---|---|---|---|---|---|---|"]
    for row in timeouts:
        lines.append(f"| {row['dataset']} | {row['method']} | {row['runs']} | {row['timeouts']} | "
                     f"{pct(row['timeout_rate'])} | {pct(row['timed_out_disamb_share_of_queries'])} | "
                     f"{pct(row['timed_out_disamb_share_of_time'])} | "
                     f"{pct(row['completed_disamb_share_of_queries'])} / {pct(row['completed_disamb_share_of_time'])} |")
    lines.append("")
    (out / "RUN_REPORT.md").write_text("\n".join(lines))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tags", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--split", default="", help="keep only this split (e.g. test); default: all in the tags")
    parser.add_argument("--count-timeout-repaired", action="store_true",
                        help="count a timed-out run as correct when its last equivalence verdict is 'equivalent'")
    args = parser.parse_args()
    global COUNT_TIMEOUT_REPAIRED
    COUNT_TIMEOUT_REPAIRED = args.count_timeout_repaired
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows, budgets = collect(args.tags)
    if args.split:
        rows = [r for r in rows if r["split"] == args.split]
    write_table(public_per_run(rows), out / "per_run.csv")
    export_runs(rows, out)
    for name in TABLES:
        table = summarize(rows, name)
        write_table(table, out / f"summary_{name}.csv")
        write_tex(table, name, out / f"tab_{name}.tex")
    run_report(rows, [t.partition(":")[0] for t in args.tags], budgets, out)
    write_table(timeout_summary([r for r in rows if r["method"] != "llm"], budgets), out / "summary_timeouts.csv")
    print(f"wrote {out}: per_run.csv, summary_rq1..3.csv, tab_rq1..3.tex, runs/, RUN_REPORT.md ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
