from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
# Top-level on purpose: a checker that cannot be imported must stop the run at start,
# never degrade every final verdict to "unknown".
from restless.evaluation.metrics.equivalence import Domain, check_equivalence  # noqa: E402
from restless.evaluation.metrics.minimality import atom_preservation, tree_edit_distance  # noqa: E402
from restless.pipeline.claims import file_lock  # noqa: E402
from restless.common.stl_epoch.ast_nodes import ASTNode, assign_horizon, operator_name, walk_preorder
from restless.common.stl_epoch.formula_ops import bustle_print
from restless.common.stl_epoch.parser import parse_stl


SCHEMA_VERSION = "3.0"
LEGACY_ORIGIN_LABELS = {"curated", "seeded"}


def strip_legacy_origin_labels(value: Any) -> Any:
    """Remove obsolete provenance labels from public result records."""
    if isinstance(value, dict):
        return {
            key: strip_legacy_origin_labels(item)
            for key, item in value.items()
            if not (
                key in {"upstream_method", "fault_origin"}
                and isinstance(item, str)
                and item.lower() in LEGACY_ORIGIN_LABELS
            )
        }
    if isinstance(value, list):
        return [strip_legacy_origin_labels(item) for item in value]
    return value


def _children(node: ASTNode) -> list[tuple[str, ASTNode]]:
    if node.kind in {"not", "always", "eventually"}:
        return [("child", node.child)]
    if node.kind in {"and", "or", "implies", "until"}:
        return [("left", node.left), ("right", node.right)]
    return []


def _node_attributes(node: ASTNode) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": node.kind}
    if node.kind in {"atom", "bool"}:
        result["variable"] = node.var
    if node.kind == "atom":
        result.update(comparator=node.cmp, threshold=float(node.value))
    if node.kind in {"always", "eventually", "until"}:
        result["interval"] = [float(node.a), float(node.b)]
    return result


def stable_ast(formula: str) -> tuple[ASTNode, list[dict[str, Any]]]:
    root = parse_stl(formula)
    assign_horizon(root)
    rows: list[dict[str, Any]] = []
    for index, (node, path) in enumerate(walk_preorder(root)):
        node.name = f"n{index}"
        rows.append({
            "node_id": node.name,
            "path": path,
            "report_path": path.replace(".", "/"),
            "node_type": node.kind,
            "operator": operator_name(node),
            "formula": bustle_print(node),
            "horizon": float(node.horizon or 0.0),
        })
    return root, rows


def _fault_type(before: ASTNode, after: ASTNode) -> str:
    if before.kind != after.kind:
        if {before.kind, after.kind} == {"always", "eventually"}:
            return "flip_temporal"
        if {before.kind, after.kind} == {"and", "or"}:
            return "flip_boolean"
        return f"replace_{before.kind}_with_{after.kind}"
    if before.kind == "atom":
        if before.var != after.var:
            return "replace_signal"
        if before.cmp != after.cmp:
            return "flip_predicate_direction"
        if not math.isclose(float(before.value), float(after.value)):
            return "replace_threshold"
    if before.kind in {"always", "eventually", "until"} and (
        not math.isclose(float(before.a), float(after.a))
        or not math.isclose(float(before.b), float(after.b))
    ):
        return "replace_interval"
    return "replace_subtree"


def infer_fault_metadata(faulty_formula: str, oracle_formula: str) -> dict[str, Any]:
    faulty, faulty_rows = stable_ast(faulty_formula)
    oracle, _ = stable_ast(oracle_formula)
    id_by_path = {row["path"]: row["node_id"] for row in faulty_rows}
    edits: list[dict[str, Any]] = []

    def visit(before: ASTNode, after: ASTNode, path: str) -> None:
        if _node_attributes(before) != _node_attributes(after):
            edits.append({
                "faulty_node": id_by_path[path],
                "faulty_path": path,
                "oracle_path": path,
                "edit": _fault_type(before, after),
                "faulty_subformula": bustle_print(before),
                "oracle_subformula": bustle_print(after),
            })
        before_children = _children(before)
        after_children = _children(after)
        if [name for name, _ in before_children] != [name for name, _ in after_children]:
            return
        for (edge, before_child), (_, after_child) in zip(before_children, after_children):
            visit(before_child, after_child, f"{path}.{edge}")

    visit(faulty, oracle, "root")
    return {
        "fault_types": [edit["edit"] for edit in edits],
        "fault_nodes": [edit["faulty_node"] for edit in edits],
        "num_faults": len(edits),
        "in_scope": all(not edit["edit"].startswith("replace_") or edit["edit"] in {
            "replace_interval", "replace_threshold", "replace_signal"
        } for edit in edits),
        "edits": edits,
        "mapping_basis": "faulty_formula_preorder",
    }


def canonical_formula(formula: str) -> str:
    root = parse_stl(formula)

    def canonical(node: ASTNode) -> str:
        if node.kind == "bool":
            return str(node.var)
        if node.kind == "atom":
            return bustle_print(node)
        if node.kind == "not":
            return f"NOT[{canonical(node.child)}]"
        if node.kind in {"and", "or"}:
            parts: list[str] = []

            def collect(current: ASTNode) -> None:
                if current.kind == node.kind:
                    collect(current.left)
                    collect(current.right)
                else:
                    parts.append(canonical(current))

            collect(node)
            label = node.kind.upper()
            ordered = sorted(parts)
            result = ordered[0]
            for part in ordered[1:]:
                result = f"{label}[{result},{part}]"
            return result
        if node.kind == "implies":
            return f"IMPLIES[{canonical(node.left)},{canonical(node.right)}]"
        if node.kind in {"always", "eventually"}:
            label = "GLOBALLY" if node.kind == "always" else "EVENTUALLY"
            return f"{label} I=[{float(node.a):g},{float(node.b):g}][{canonical(node.child)}]"
        if node.kind == "until":
            return (
                f"UNTIL I=[{float(node.a):g},{float(node.b):g}]"
                f"[{canonical(node.left)},{canonical(node.right)}]"
            )
        raise ValueError(node.kind)

    return canonical(root)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        delimiter = ";" if sample.count(";") > sample.count(",") else ","
        return list(csv.DictReader(handle, delimiter=delimiter))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        try: rows.append(json.loads(line))
        except json.JSONDecodeError: continue
    return rows


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


@dataclass(frozen=True)
class RunSpec:
    benchmark: str
    instance_id: str
    source: str
    method: str
    seed: int
    formula_row: int
    oracle_formula_row: int
    config: dict[str, Any]


def first_equivalent_event(events: Sequence[dict[str, Any]], verdicts: dict[str, str]) -> dict[str, Any] | None:
    """Q*/fitness* from the real evaluation order; unknown verdicts never count as success."""
    for order, event in enumerate(events, start=1):
        if verdicts.get(str(event.get("formula"))) == "equivalent":
            return {"Q": event.get("Q"), "round": event.get("round"), "fitness_evals": order}
    return None


def formula_complexity(formula: str) -> dict[str, int]:
    root = parse_stl(formula)
    temporal = {"always", "eventually", "until"}

    def depth(node: ASTNode, temporal_only: bool) -> int:
        own = (node.kind in temporal) if temporal_only else 1
        return own + max([depth(child, temporal_only) for _, child in _children(node)] or [0])

    nodes = [node for node, _ in walk_preorder(root)]
    return {"ast_nodes": len(nodes), "ast_depth": depth(root, False), "temporal_depth": depth(root, True),
            "temporal_count": sum(node.kind in temporal for node in nodes),
            "atom_count": sum(node.kind in {"atom", "bool"} for node in nodes),
            "signal_count": len({node.var for node in nodes if node.kind in {"atom", "bool"}})}


class RunRecorder:
    """Build paper-facing run records by aggregating existing raw artifacts."""

    def __init__(
        self,
        *,
        output_root: Path,
        record_jsonl: Path,
        spec: RunSpec,
        faulty_formula: str,
        oracle_formula: str,
        fault_metadata_path: Path | None = None,
    ) -> None:
        self.output_root = output_root
        self.record_jsonl = record_jsonl
        self.spec = spec
        self.faulty_formula = faulty_formula
        self.oracle_formula = oracle_formula
        self.started = time.perf_counter()
        digest = hashlib.md5(
            json.dumps(spec.config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:8]
        self.config_hash = hashlib.sha256(
            json.dumps(spec.config, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self.attempt = int(spec.config.get("attempt", 1))
        self.run_id = (f"{spec.source}__{spec.instance_id}__{spec.method}__"
                       f"{self.config_hash[:12]}__a{self.attempt}")
        _, self.ast_rows = stable_ast(faulty_formula)
        self.node_by_report_path = {
            row["report_path"]: row["node_id"] for row in self.ast_rows
        }
        if fault_metadata_path and fault_metadata_path.exists():
            self.fault_metadata = json.loads(fault_metadata_path.read_text())
            self.fault_metadata["source"] = str(fault_metadata_path)
        else:
            self.fault_metadata = infer_fault_metadata(faulty_formula, oracle_formula)
            self.fault_metadata["source"] = "automatic_structural_diff"

    def _write_search_events(self, events: Sequence[dict[str, Any]], verdicts: dict[str, str]) -> None:
        """Online events stay as logged; the offline verdict is an audit column added afterwards."""
        path = self.output_root / "search_events.jsonl"
        lines = []
        for order, event in enumerate(events, start=1):
            lines.append(json.dumps({**event, "evaluation": order, "offline_verdict": verdicts.get(str(event.get("formula")))},
                                    sort_keys=True) + "\n")
        path.write_text("".join(lines))

    def _localization_snapshot(self, round_index: int, round_dir: Path) -> dict[str, Any] | None:
        reports = sorted(round_dir.glob("repair_attempt_*/repair_output/localization_report.csv"))
        if not reports:
            return None
        best_by_node: dict[str, dict[str, Any]] = {}
        attempt_node_ids: list[str] = []
        attempts = 0
        positive_muse = 0
        for report in reports:
            for row in _read_csv(report):
                attempts += 1
                if _number(row.get("muse_score")) > 0.0:
                    positive_muse += 1
                report_path = (row.get("node_path") or "").replace(".", "/")
                node_id = self.node_by_report_path.get(report_path)
                if not node_id:
                    continue
                attempt_node_ids.append(node_id)
                candidate = {
                    "node_id": node_id,
                    "path": report_path,
                    "node_type": row.get("node_type", ""),
                    "evidence": _number(row.get("evidence_score")),
                    "best_operator": row.get("best_operator", ""),
                    "muse": _number(row.get("muse_score")),
                    "edit_score": _number(row.get("best_edit_score")),
                    "report_rank": int(_number(row.get("rank"), attempts)),
                }
                previous = best_by_node.get(node_id)
                key = (candidate["evidence"], candidate["edit_score"], candidate["muse"])
                if previous is None or key > (
                    previous["evidence"], previous["edit_score"], previous["muse"]
                ):
                    best_by_node[node_id] = candidate
        ranking = sorted(
            best_by_node.values(),
            key=lambda row: (-row["evidence"], -row["edit_score"], row["report_rank"], row["node_id"]),
        )
        for rank, row in enumerate(ranking, start=1):
            row["rank"] = rank
        fault_nodes = set(self.fault_metadata.get("fault_nodes", []))
        fault_ranks = {
            row["node_id"]: row["rank"] for row in ranking if row["node_id"] in fault_nodes
        }
        evidence_levels = sorted({row["evidence"] for row in ranking}, reverse=True)
        top_levels = set(evidence_levels[: max(1, int(self.spec.config.get("repair_localization_top_k", 3)))])
        repair_slice = [row["node_id"] for row in ranking if row["evidence"] in top_levels]
        hits = set(repair_slice) & fault_nodes
        localized_attempts = sum(node_id in set(repair_slice) for node_id in attempt_node_ids)
        return {
            "round": round_index,
            "reports": [str(path.relative_to(self.output_root)) for path in reports],
            "ranking": ranking,
            "fault_ranks": fault_ranks,
            "top1_hit": any(rank <= 1 for rank in fault_ranks.values()),
            "top3_hit": any(rank <= 3 for rank in fault_ranks.values()),
            "top5_hit": any(rank <= 5 for rank in fault_ranks.values()),
            "repair_slice": repair_slice,
            "repair_slice_size": len(repair_slice),
            "fault_recall_in_slice": (len(hits) / len(fault_nodes)) if fault_nodes else None,
            "mutation_attempts_reported": attempts,
            "positive_muse_attempts": positive_muse,
            "initial_gate_edit_candidates": localized_attempts,
            "initial_gate_reduction_ratio": (
                1.0 - localized_attempts / len(attempt_node_ids)
                if attempt_node_ids else None
            ),
            "search_space_counter_basis": "localization_report rows and initial top-evidence-level gate",
        }

    def _search_round(
        self,
        round_index: int,
        round_dir: Path,
        selected_formula: str = "",
    ) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        started = sorted(round_dir.glob("repair_attempt_*/search_started.json"))
        for repair_csv in sorted(round_dir.glob("repair_attempt_*/repair_output/*.repair.csv")):
            rows = _read_csv(repair_csv)
            marker = repair_csv.parent.parent / "search_started.json"
            q_at_start = json.loads(marker.read_text()).get("Q") if marker.exists() else None
            for row_index, row in enumerate(rows):
                event = {
                    "round": round_index,
                    "candidate_id": f"r{round_index:02d}_{repair_csv.parent.parent.name}_{row_index:05d}",
                    "Q": q_at_start,
                    "source_file": str(repair_csv.relative_to(self.output_root)),
                    "iteration": int(_number(row.get("iteration"))),
                    "candidate_type": row.get("candidateType", ""),
                    "formula": row.get("serialized", ""),
                    "accuracy": _number(row.get("accuracy")),
                    "fitness": _number(row.get("fitness")),
                    "misclassified": int(_number(row.get("misclassified"))),
                    "formula_size": int(_number(row.get("formulaComplexity"))),
                    "training_consistent": _number(row.get("accuracy")) >= 0.999999,
                    "selected": bool(selected_formula and row.get("serialized", "") == selected_formula),
                }
                events.append(event)
        log_path = round_dir / "search_log.jsonl"
        log_path.write_text("".join(json.dumps(event, sort_keys=True) + "\n" for event in events))
        return {
            "round": round_index,
            "candidate_evaluations": len(events),
            "search_started": len(started),
            "search_completed": sum(1 for path in round_dir.glob("repair_attempt_*/repair_output/*.repair.csv") if _read_csv(path)),
            "training_consistent_candidates": sum(event["training_consistent"] for event in events),
            "sgrl_guided_candidates": sum(
                "sgrl" in str(event["candidate_type"]).lower() for event in events
            ),
            "search_log": str(log_path.relative_to(self.output_root)),
        }

    def _refinement(self) -> dict[str, Any]:
        indexes = sorted(self.output_root.glob("round_*/neighborhood_probe/index.csv"))
        rows = [row for path in indexes for row in _read_csv(path)]
        directions = [row.get("direction", "") for row in rows]
        candidates = {row.get("candidate", "") for row in rows if row.get("candidate")}
        smt_rows = [row for row in rows if row.get("direction") in {"A_SAT_B_VIO", "B_SAT_A_VIO"}]
        grid_rows = [row for row in rows if row.get("direction") == "GRID_REPLAY_VERIFIED"]
        replay_rows = [row for row in rows if row.get("direction") == "REPLAY_VERIFIED"]
        index_verified_traces = sum(
            int(_number(row.get("generated"))) for row in grid_rows + replay_rows
        )
        coverage_files = sorted(
            self.output_root.glob("round_*/neighborhood_probe/mutation_coverage.csv")
        )
        coverage_rows: list[dict[str, Any]] = []
        for path in coverage_files:
            for row in _read_csv(path):
                before = int(_number(row.get("filtered_by_D")))
                after = int(_number(row.get("remaining_mutants")))
                coverage_rows.append({
                    "file": str(path.relative_to(self.output_root)),
                    "status": row.get("status", ""),
                    "candidates_before": before,
                    "candidates_after": after,
                    "candidate_elimination_rate": (
                        1.0 - after / before if before else 0.0
                    ),
                    "new_examples": int(_number(row.get("new_examples"))),
                })
        candidates_before = sum(row["candidates_before"] for row in coverage_rows)
        candidates_after = sum(row["candidates_after"] for row in coverage_rows)
        verified_traces = (
            sum(row["new_examples"] for row in coverage_rows)
            if coverage_rows else index_verified_traces
        )
        return {
            "triggered": bool(indexes),
            "index_files": [str(path.relative_to(self.output_root)) for path in indexes],
            "neighbors_generated": len(candidates),
            "smt_probes": len(smt_rows),
            "smt_success": sum(int(_number(row.get("generated"))) > 0 for row in smt_rows),
            "smt_failed": sum(int(_number(row.get("generated"))) == 0 for row in smt_rows),
            "grid_fallbacks": len(grid_rows),
            "replay_verified_batches": len(replay_rows) + len(grid_rows),
            "discriminating_traces_found": verified_traces,
            "oracle_queries_added": verified_traces,
            "candidates_before": candidates_before,
            "candidates_after": candidates_after,
            "candidate_elimination_rate": (
                1.0 - candidates_after / candidates_before
                if candidates_before else 0.0
            ),
            "candidate_metric_basis": (
                "sum of prediction-equivalent neighborhood survivors before and "
                "after witness checks across probe invocations"
            ),
            "candidate_reduction_by_probe": coverage_rows,
            "directions": dict((direction, directions.count(direction)) for direction in sorted(set(directions))),
        }

    def build_record(
        self,
        summary_rows: Sequence[dict[str, str]],
        *,
        final_formula: str | None = None,
        observed: dict[str, Any] | None = None,
        trace_metrics: dict[str, Any] | None = None,
        complete: bool = False,
    ) -> dict[str, Any]:
        localizations: list[dict[str, Any]] = []
        searches: list[dict[str, Any]] = []
        summary_by_round = {
            int(_number(row.get("round"))): row for row in summary_rows if row.get("round")
        }
        for round_dir in sorted(self.output_root.glob("round_*")):
            try:
                round_index = int(round_dir.name.split("_")[-1])
            except ValueError:
                continue
            snapshot = self._localization_snapshot(round_index, round_dir)
            if snapshot:
                localizations.append(snapshot)
            searches.append(self._search_round(
                round_index,
                round_dir,
                summary_by_round.get(round_index, {}).get("formula_out", ""),
            ))
        localization_by_round = {row["round"]: row for row in localizations}
        search_by_round = {row["round"]: row for row in searches}
        round_records = []
        previous_size = 0
        for round_index, summary in sorted(summary_by_round.items()):
            size = int(_number(summary.get("global_dataset_size")))
            loc = localization_by_round.get(round_index, {})
            search = search_by_round.get(round_index, {})
            round_records.append({
                "round": round_index,
                "traces_requested": int(self.spec.config.get("round_trace_budget", 0)),
                "traces_labelled": max(0, size - previous_size),
                "node_scores": {row["path"]: row["evidence"] for row in loc.get("ranking", [])},
                "gate_nodes": loc.get("repair_slice", []),
                "gate_expansions": int(_number(summary.get("localization_gate_expansions"))),
                "fitness_evals": int(search.get("candidate_evaluations", 0)),
                "time_s": {"tracegen": None, "labelling": None, "search": None, "other": None},
            })
            previous_size = size
        last = dict(summary_rows[-1]) if summary_rows else {}
        repaired = final_formula or last.get("formula_out") or self.faulty_formula
        exact = repaired.strip() == self.oracle_formula.strip()
        try:
            canonical_exact = canonical_formula(repaired) == canonical_formula(self.oracle_formula)
        except Exception:
            canonical_exact = False
        train_accuracy = _number(last.get("new_accuracy") or last.get("old_accuracy"), 0.0)
        if canonical_exact:
            semantic = {
                "result": "equivalent",
                "method": "canonical_exact",
                "horizon": self.spec.config.get("equiv_horizon"),
                "timeout": False,
                "time_s": 0.0,
            }
        else:
            semantic = {"result": "unknown", "method": "not_run",
                        "horizon": self.spec.config.get("equiv_horizon"), "timeout": False, "time_s": 0.0}
            try:
                domain_config = self.spec.config.get("domain_config")
                if domain_config:
                    checked = check_equivalence(repaired, self.oracle_formula, Domain.from_config(domain_config))
                    semantic = {"result": checked.verdict, "verdict": checked.verdict,
                                "method": checked.method, "time_s": checked.time_s,
                                "detail": checked.detail, "timeout": "timeout" in checked.detail.lower()}
            except Exception as exc:
                semantic["detail"] = f"equivalence check failed: {exc}"
        semantic_verdict = semantic.get("verdict", semantic.get("result", "unknown"))
        # "plausible" is a claim about the training traces only, so a changed formula that
        # still misclassifies is not plausible.
        if exact or canonical_exact or semantic_verdict == "equivalent":
            status = "repaired"
        elif train_accuracy >= 0.999999:
            status = "plausible_only"
        else:
            status = "not_repaired"
        generated_traces = max(
            [int(_number(row.get("global_dataset_size"))) for row in summary_rows] or [0]
        )
        candidate_evaluations = sum(row["candidate_evaluations"] for row in searches)
        effective_trace_metrics = trace_metrics or {
            "num_generated": generated_traces,
            "num_oracle_queried": generated_traces,
            "avg_num_signals": 0.0,
            "avg_trace_length": 0.0,
            "num_discriminating": 0,
            "counting_basis": "legacy artifact fallback",
        }
        candidate_pool = sorted({event["formula"] for search in searches
                                 for event in _read_jsonl(self.output_root / search["search_log"])
                                 if event.get("training_consistent") and event.get("formula")})
        equivalence_verdict = semantic.get("verdict", semantic.get("result", "unknown"))
        try:
            ted_initial = tree_edit_distance(self.faulty_formula, repaired)
            ted_oracle = tree_edit_distance(repaired, self.oracle_formula)
            atoms_preserved = atom_preservation(self.faulty_formula, repaired)
        except Exception:
            ted_initial = ted_oracle = None
            atoms_preserved = None
        pool_contains_equivalent = False
        pool_equivalence: dict[str, str] = {}
        domain_config = self.spec.config.get("domain_config")
        # An equivalent candidate matches every oracle label, so only training-consistent
        # candidates can be equivalent and only they need the (expensive) final audit.
        if complete and candidate_pool and domain_config:
            try:
                check_domain = Domain.from_config(domain_config)
                for candidate in candidate_pool:
                    try:
                        verdict = check_equivalence(
                            candidate, self.oracle_formula, check_domain
                        ).verdict
                    except Exception:
                        verdict = "unknown"
                    pool_equivalence[candidate] = verdict
            except Exception as exc:
                pool_equivalence = {candidate: "unknown" for candidate in candidate_pool}
                semantic.setdefault("pool_detail", f"pool audit failed: {exc}")
        pool_contains_equivalent = any(v == "equivalent" for v in pool_equivalence.values())
        ordered_events = [event for search in searches
                          for event in _read_jsonl(self.output_root / search["search_log"])]
        first_success = first_equivalent_event(ordered_events, pool_equivalence)
        if complete:
            self._write_search_events(ordered_events, pool_equivalence)
        q_star = first_success["Q"] if first_success else None
        first_success_round = first_success["round"] if first_success else None
        fitness_evals_star = first_success["fitness_evals"] if first_success else None
        invalid_reason = None
        failures = sorted(self.output_root.glob("round_*/repair_attempt_*/repair_process_failure.txt"))
        search_started = sum(int(row.get("search_started", 0)) for row in searches)
        search_completed = sum(int(row.get("search_completed", 0)) for row in searches)
        # Zero evaluations is only a defect when repair was actually needed: a formula that
        # already fits every trace legitimately never starts BUSTLE and stays a (failed) repair.
        repair_expected = any(
            _number(row.get("old_accuracy"), 1.0) < 0.999999 for row in summary_rows
        )
        if complete and (
            any("Args.a" in path.read_text(errors="replace") for path in failures)
            or (candidate_evaluations == 0 and (failures or repair_expected))
            or (failures and search_completed == 0)
        ):
            invalid_reason = "repair_search_not_executed"
        record = {
            "schema_version": SCHEMA_VERSION,
            "experiment_tag": self.spec.config.get("experiment_tag", ""),
            "phase": self.spec.config.get("phase", ""),
            "cohort": self.spec.config.get("cohort", ""),
            "logical_run_id": f"{self.spec.source}__{self.spec.instance_id}__{self.spec.method}",
            "versions": {key: self.spec.config.get(key, "") for key in ("protocol_hash", "dataset_hash")},
            "run_id": self.run_id,
            "config_hash": self.config_hash, "attempt": self.attempt,
            "method": self.spec.method, "config": self.spec.config,
            "ablation": self.spec.config.get("ablation", {}),
            "instance": {"instance_id": self.spec.instance_id, "source": self.spec.source,
                         "upstream_method": self.spec.config.get("upstream_method", ""),
                         "split": self.spec.config.get("split", ""),
                         "reachable_edit_distance": self.spec.config.get("reachable_edit_distance"),
                         "fault_types": self.fault_metadata.get("fault_types", []),
                         "G_i": self.spec.config.get("G_i", []),
                         "G_i_paths": self.spec.config.get("G_i_paths", []),
                         "injected_paths": self.spec.config.get("injected_paths", []),
                         "complexity": formula_complexity(self.faulty_formula),
                         "n_nodes": len(self.ast_rows),
                         "has_until": any(row["node_type"] == "until" for row in self.ast_rows)},
            "fairness_check": self.spec.config.get("fairness_check", {"passed": False, "compared_fields": []}),
            "rounds": round_records,
            "meta": {
                **asdict(self.spec),
                "faulty_formula": self.faulty_formula,
                "gold_formula": self.oracle_formula,
                **self.fault_metadata,
            },
            "result": {
                "status": status,
                "final_formula": repaired,
                "candidate_pool": candidate_pool,
                "candidate_pool_equivalence": pool_equivalence,
                "equivalence": {"verdict": equivalence_verdict, "method": semantic.get("method"),
                                "time_s": semantic.get("time_s", 0.0)},
                "pool_contains_equivalent": pool_contains_equivalent,
                "plausible": train_accuracy >= 0.999999,
                "q_censored": first_success is None,
                "search": {"started": search_started, "completed": search_completed,
                           "process_failures": len(failures),
                           "state": "not_needed" if search_started == 0 and not repair_expected
                           else "executed" if search_completed else "not_executed"},
                "edits_applied": ted_initial,
                "ted_to_initial": ted_initial,
                "ted_to_oracle": ted_oracle,
                "atoms_preserved": atoms_preserved,
                "heldout_bool_agree": (observed or {}).get("heldout_bool_agree"),
                "heldout_sign_agree": (observed or {}).get("heldout_sign_agree"),
                "repaired_formula": repaired,
                "train_accuracy": train_accuracy,
                "training_consistent": train_accuracy >= 0.999999,
                "exact": exact,
                "canonical_exact": canonical_exact,
                "semantic_equivalence": semantic,
                "observed_trace_set": observed or {},
                "Q": int(effective_trace_metrics["num_oracle_queried"]),
                "Q_requested": int(effective_trace_metrics.get("num_requested", effective_trace_metrics["num_generated"])),
                "Q_star": q_star,
                "first_success_round": first_success_round,
                "fitness_evals_total": candidate_evaluations,
                "fitness_evals_star": fitness_evals_star,
                "wall_time_s": time.perf_counter() - self.started,
                "time_s": dict(effective_trace_metrics.get("time_s", {})),
            },
            "localization": localizations,
            "search": searches,
            "trace_metrics": effective_trace_metrics,
            "refinement": self._refinement(),
            "cost": {
                "candidate_evaluations": candidate_evaluations,
                "oracle_queries": int(effective_trace_metrics["num_oracle_queried"]),
                "generated_traces": int(effective_trace_metrics["num_generated"]),
                "rounds": len(summary_rows),
                "wall_time_s": time.perf_counter() - self.started,
                "timing_note": "component timings unavailable in legacy subprocess artifacts",
                "trace_gen_stats": dict(effective_trace_metrics.get("trace_gen_stats", {})),
            },
            "status": status,
            "complete": complete,
            "invalid_run": invalid_reason is not None,
            "invalid_reason": invalid_reason,
        }
        return strip_legacy_origin_labels(record)

    def checkpoint(
        self,
        summary_rows: Sequence[dict[str, str]],
        *,
        trace_metrics: dict[str, Any] | None = None,
    ) -> None:
        _write_json(
            self.output_root / "run_record.json",
            self.build_record(summary_rows, trace_metrics=trace_metrics),
        )

    def finalize(
        self,
        summary_rows: Sequence[dict[str, str]],
        *,
        final_formula: str,
        observed: dict[str, Any] | None = None,
        trace_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        record = self.build_record(
            summary_rows,
            final_formula=final_formula,
            observed=observed,
            trace_metrics=trace_metrics,
            complete=True,
        )
        _write_json(self.output_root / "run_record.json", record)
        self.record_jsonl.parent.mkdir(parents=True, exist_ok=True)
        # Shared index rewritten by every finishing worker: serialize it and keep the temp name private.
        with file_lock(self.record_jsonl.with_name(self.record_jsonl.name + ".lock")):
            existing: list[dict[str, Any]] = []
            if self.record_jsonl.exists():
                for line in self.record_jsonl.read_text().splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("run_id") != self.run_id:
                        existing.append(item)
            existing.append(record)
            temporary = self.record_jsonl.with_suffix(f"{self.record_jsonl.suffix}.{os.getpid()}.tmp")
            temporary.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in existing))
            temporary.replace(self.record_jsonl)
        return record
