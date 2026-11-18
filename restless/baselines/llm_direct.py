#!/usr/bin/env python3
"""LLM-direct baseline ("LLM repair without trace feedback"): a self-check loop with no trace, no SAT/VIO feedback
and no query (Q = 0). The LLM judges whether the current formula expresses the requirement; if INCORRECT it repairs
it and the new formula is judged again, if CORRECT the current formula is the output. The loop stops on CORRECT, on
an unclear verdict or an unparsable repair, or after direct_max_iters iterations (an unchanged repair only stops it if
direct_stop_on_unchanged is set; by default it does not); phi0 is the start. correct =
final formula equivalent to the reference; time = LLM calls + equivalence check.

Writes a schema-current run_record.json so run_instance / restless_summary treat it like every other run.
Called by run_instance.run_method for the method "llm_direct".
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from restless.evaluation.metrics.equivalence import Domain, canonical, check_equivalence  # noqa: E402
from restless.pipeline.run_record import SCHEMA_VERSION, strip_legacy_origin_labels  # noqa: E402
from restless.common.formula_io import convert_formula_to_bustle, read_formula  # noqa: E402
from restless.baselines import llm_ce


def run_llm_direct(spec: dict, seed: int, out: Path) -> bool:
    uid = spec["uid"]
    config = json.loads((ROOT / spec["config"]).read_text())
    aliases = {s["source"]: s["target"] for s in config["signals"] if s["source"] != s["target"]} or None
    phi0 = convert_formula_to_bustle(read_formula(ROOT / config["llm_formula_csv"], int(config.get("formula_row", 1))), aliases)
    reference = convert_formula_to_bustle(
        read_formula(ROOT / config["oracle_formula_csv"], int(config.get("oracle_formula_row", 1))), aliases)
    domain_config = {
        "signals": [{**s, "static": False} for s in config["signals"]],
        "boolean_signals": config.get("boolean_signals", []),
        "derived_signal_definitions": config.get("derived_signal_definitions", {}),
        "time_start": config.get("time_start", 0.0), "time_end": config["time_end"], "time_step": config["time_step"],
    }
    signals = config["signals"]
    times = []
    t = float(config.get("time_start", 0.0))
    while t <= float(config["time_end"]) + 1e-9:
        times.append(t)
        t += float(config["time_step"])
    out.mkdir(parents=True, exist_ok=True)
    cfg = llm_ce.load_config(os.environ.get("LLM_CE_CONFIG") or ROOT / "experiments" / "llm_ce_config.py")
    if cfg.get("direct_max_iters") is None:   # same number of rounds as the other methods of this tag
        cfg = {**cfg, "direct_max_iters": int(spec["params"]["rounds"])}
    names = llm_ce.name_map(uid)   # the spelling phi0 and the reference use for every signal
    derived = llm_ce.derived_signals(uid)   # e.g. y5_minus_y4 = y5 - y4, which the formulas use
    ctx = llm_ce.Context(
        cfg=cfg, requirement=llm_ce.load_requirement(uid, cfg),
        signal_names=[names[s["target"]] for s in signals] + list(derived),
        signal_bounds={names[s["target"]]: (s["min"], s["max"]) for s in signals},
        boolean_signals=[names.get(b, b) for b in config.get("boolean_signals", [])], times=times,
        references=[reference], run_seed=seed, log_path=out / "llm_log.jsonl", aliases=llm_ce.instance_aliases(uid), derived=derived)
    started = time.monotonic()
    try:
        result = llm_ce.llm_direct_loop(ctx, phi0)
    finally:
        llm_ce.write_summary(out / "llm_ce_summary.json", ctx)
    final = result["final"]
    if canonical(final) == canonical(reference):
        verdict, method, detail = "equivalent", "canonical", ""
    else:
        checked = check_equivalence(final, reference, Domain.from_config(domain_config))
        verdict, method, detail = checked.verdict, checked.method, checked.detail
    elapsed = time.monotonic() - started
    correct = verdict == "equivalent"
    record = {
        "run_id": f"{spec['source']}__{uid}__llm_direct__{out.name}", "method": "llm_direct",
        "schema_version": SCHEMA_VERSION, "attempt": int(out.name[len("attempt"):]), "complete": True,
        "invalid_run": False, "status": "repaired" if correct else "not_repaired",
        "instance": {"instance_id": uid, "source": spec["source"], "upstream_method": spec["upstream_method"]},
        "meta": {"instance_id": uid, "source": spec["source"]},
        "experiment_tag": spec.get("experiment_tag", ""),
        "result": {
            "status": "repaired" if correct else "not_repaired", "final_formula": final, "candidate_pool": [final],
            "equivalence": {"verdict": verdict, "method": method, "time_s": elapsed},
            "semantic_equivalence": {"result": verdict, "verdict": verdict, "method": method, "time_s": elapsed,
                                     "detail": detail},
            # No query is made, so nothing can refute phi0 on traces: plausible is defined as correct here.
            "plausible": correct, "Q": 0, "Q_requested": 0, "Q_star": 0 if correct else None,
            "wall_time_s": elapsed, "time_s": {"tracegen": 0.0, "labelling": 0.0, "search": ctx.stats.time_llm, "other": elapsed - ctx.stats.time_llm},
            "fitness_evals_total": 0, "fitness_evals_star": 0 if correct else None,
            "edits_applied": 0, "ted_to_initial": llm_ce.tree_distance(phi0, final), "ted_to_oracle": None,
            "observed_trace_set": {"first_eq": {"queries": 0, "ce_found": False, "q_to_first_ce": None}},
        },
        "trace_metrics": {"num_generated": 0, "num_oracle_queried": 0, "num_oracle_queried_eq": 0,
                          "num_oracle_queried_disambiguation": 0, "time_disambiguation_s": 0.0},
    }
    record = strip_legacy_origin_labels(record)
    (out / "run_record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return True
