#!/usr/bin/env python3
"""Worker (EC2) driver for ONE instance: freeze initial traces, check fairness, run all methods.

    python -m restless.pipeline.run_instance experiments/instances/<uid>/spec.json

Stages (every stage is idempotent, so an interrupted instance can simply be re-run):
  0a  frozen initial traces: one round of the unchanged trace generator -> trace_cache/<uid>/
  1   sidecars + fairness checks (RQ1/RQ2, RQ2-controlled, RQ3) -- abort the instance on any failure
  2   formal runs: one run per method, benchmark rq2 (shared by RQ1/RQ2/RQ3) or rq3

Oracle formulas are only used for labelling.
"""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
from restless.pipeline.protocol import METHODS, RESTLESS_ALLOWED, apply_method  # noqa: E402
from restless.pipeline.claims import file_lock  # noqa: E402
from restless.pipeline.run_record import SCHEMA_VERSION, strip_legacy_origin_labels  # noqa: E402
from restless.atg.trace_cache import TraceCache  # noqa: E402

RQ3_ONLY = ("no_self_coeff",)   # ablation runs are stored under rq3/, everything else under rq2/
ALL_METHODS = METHODS
RUN_SEED = 0   # fixed RNG seed of every run (repair loop, trace cache, LLM calls); not an experiment dimension


def results_root() -> Path:
    return ROOT / os.environ.get("RESPSTL_RESULTS_ROOT", "results")


def stage0_root() -> Path:
    """A tagged experiment owns its stage 0, so a smoke run can never be reused by a formal run."""
    return results_root() / "_stage0" if os.environ.get("RESPSTL_RESULTS_ROOT") else ROOT


CONTROL_PREFIX = "@@RESPSTL_CONTROL@@ "


def log(message: str) -> None:
    print(f"[run_instance] {message}", flush=True)


def load_spec(path: Path) -> dict:
    spec = json.loads(path.read_text())
    spec["_dir"] = str(path.parent)
    return spec


def base_config(spec: dict) -> dict:
    config = json.loads((ROOT / spec["config"]).read_text())
    config["instance_id"] = spec["uid"]
    config["source"] = spec["source"]
    return config


def _verbose_logging() -> bool:
    return os.environ.get("RESPSTL_VERBOSE", "").strip().lower() in {"1", "true", "yes", "on"}


DISK_LIMIT_MB = int(os.environ.get("RESPSTL_RUN_DISK_LIMIT_MB", "1024"))
DISK_POLL_SECONDS = 15
LAST_FAILURE_REASON = ""


def tree_bytes(root: Path) -> int:
    total = 0
    stack = [str(root)]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def watch_disk(process: subprocess.Popen, root: Path, limit_mb: int, tripped: threading.Event, done: threading.Event) -> None:
    """Kill the run's whole process group once its output directory exceeds the budget."""
    limit = limit_mb * 1024 * 1024
    while not done.wait(DISK_POLL_SECONDS):
        if tree_bytes(root) > limit:
            tripped.set()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
            return


def watch_time(process: subprocess.Popen, limit_s: float, tripped: threading.Event, done: threading.Event) -> None:
    """Kill the run's whole process group once it has run for limit_s seconds."""
    if not done.wait(limit_s):
        tripped.set()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()


def run_loop(config: dict, config_path: Path, label: str, disk_limit_mb: int | None = None,
             time_limit_s: float | None = None) -> bool:
    global LAST_FAILURE_REASON
    LAST_FAILURE_REASON = ""
    # One long-lived MATLAB per run instead of one `matlab -batch` per CE class: same ATG jobs and
    # outputs, but avoids ~16 s of start-up per call and the repeated MathWorks service churn.
    if os.environ.get("RESPSTL_PERSISTENT_MATLAB", "1") != "0":
        repair_args = list(config.get("repair_args", []))
        if "--atg-persistent-matlab" not in repair_args:
            config = {**config, "repair_args": [*repair_args, "--atg-persistent-matlab"]}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n")
    output_root = ROOT / config["output_root"]
    output_root.mkdir(parents=True, exist_ok=True)
    detail_log = output_root / "run.log"
    command = [sys.executable, "-m", "restless.repair.dataset_launcher", "--config", str(config_path)]
    verbose = _verbose_logging()
    tail: deque[str] = deque(maxlen=16)
    started = datetime.now(timezone.utc).isoformat()
    log(f"START {label}; output={output_root.relative_to(ROOT)}; detail={detail_log.relative_to(ROOT)}")
    with detail_log.open("a", encoding="utf-8") as detail:
        detail.write(f"\n===== invocation {started} =====\n")
        detail.write("$ " + " ".join(command) + "\n")
        detail.flush()
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=bool(disk_limit_mb or time_limit_s),
            )
            assert process.stdout is not None
            tripped, timed_out, done = threading.Event(), threading.Event(), threading.Event()
            if disk_limit_mb:
                threading.Thread(target=watch_disk, args=(process, output_root, disk_limit_mb, tripped, done), daemon=True).start()
            if time_limit_s:
                threading.Thread(target=watch_time, args=(process, time_limit_s, timed_out, done), daemon=True).start()
            try:
                for line in process.stdout:
                    detail.write(line)
                    detail.flush()
                    tail.append(line.rstrip("\n"))
                    if verbose:
                        print(line, end="", flush=True)
                    elif line.startswith(CONTROL_PREFIX):
                        print(line[len(CONTROL_PREFIX):], end="", flush=True)
            finally:
                process.stdout.close()
                done.set()
            returncode = process.wait()
            if tripped.is_set() or timed_out.is_set():
                LAST_FAILURE_REASON = (f"disk budget exceeded ({disk_limit_mb} MB)" if tripped.is_set()
                                       else f"time limit exceeded ({time_limit_s:g} s)")
                detail.write(f"\n===== KILLED: {LAST_FAILURE_REASON} =====\n")
                returncode = returncode or -9
        except BaseException:
            traceback.print_exc(file=detail)
            detail.flush()
            log(f"FAILED {label}; launcher exception; detail={detail_log.relative_to(ROOT)}")
            raise
    if returncode == 0:
        log(f"SUCCESS {label}; output={output_root.relative_to(ROOT)}; detail={detail_log.relative_to(ROOT)}")
        return True
    log(f"FAILED {label}; exit={returncode}; detail={detail_log.relative_to(ROOT)}")
    if not verbose and tail:
        log("failure tail (complete output and traceback are in run.log):")
        for line in tail:
            print(f"  {line}", flush=True)
    return False


def complete(output_root: Path) -> bool:
    """Only a schema-current, finished, valid attempt may be skipped on resume."""
    record = output_root / "run_record.json"
    if not record.exists():
        return False
    try:
        data = json.loads(record.read_text())
    except json.JSONDecodeError:
        return False
    if data.get("schema_version") == SCHEMA_VERSION and str(data.get("terminal_reason", "")).startswith("time limit exceeded"):
        # A timeout is a terminal observation (a failure charged the full budget), not a transient
        # fault: re-running it until it happens to finish would hide it. Crashes stay retryable.
        return True
    return bool(data.get("complete")) and not data.get("invalid_run") and data.get("schema_version") == SCHEMA_VERSION


def next_attempt(run_dir: Path) -> tuple[int, bool]:
    """(attempt to use, already complete). Interrupted attempts stay on disk; a new one is opened."""
    existing = sorted(int(path.name[len("attempt"):]) for path in run_dir.glob("attempt[0-9]*"))
    if existing and complete(run_dir / f"attempt{existing[-1]}"):
        return existing[-1], True
    return (existing[-1] + 1 if existing else 1), False


def read_partial_metrics(out: Path, killed_at: float) -> dict | None:
    """Query / time split the run had written before it was killed (phase_metrics.json); a
    disambiguation window still open at the kill is charged up to killed_at."""
    try:
        data = json.loads((out / "phase_metrics.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    open_since = data.get("disambiguation_open_since")
    in_flight = max(0.0, killed_at - float(open_since)) if open_since else 0.0
    data["time_disambiguation_s"] = float(data.get("time_disambiguation_s", 0.0)) + in_flight
    data["disambiguation_in_flight_s"] = in_flight
    return data


def mark_runtime_failure(out: Path, spec: dict, seed: int, method: str, reason: str,
                         partial: dict | None = None) -> None:
    """A crash is a terminal observation: it keeps its denominator and whatever was really consumed."""
    path = out / "run_record.json"
    try:
        record = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        record = {"run_id": f"{spec['source']}__{spec['uid']}__{method}__{out.name}", "method": method,
                  "schema_version": SCHEMA_VERSION, "attempt": int(out.name[len("attempt"):]), "status": "crash",
                  "instance": {"instance_id": spec["uid"], "source": spec["source"], "upstream_method": spec["upstream_method"]},
                  "meta": {"instance_id": spec["uid"], "source": spec["source"]}, "result": {}, "complete": False}
    record.update(terminal_state="runtime_failure", terminal_reason=reason, status="crash")
    if partial is not None:
        record["partial_metrics"] = partial
    record = strip_legacy_origin_labels(record)
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- stage 0


def freeze_initial_traces(spec: dict, seed: int) -> list[str]:
    if spec.get("initial_traces") is False:
        return []  # runs start from an empty D: the first EQ is the method's own
    with file_lock(stage0_root() / "_locks" / f"{spec['uid']}.seed{seed}.lock"):
        return _freeze_initial_traces(spec, seed)


def _freeze_initial_traces(spec: dict, seed: int) -> list[str]:
    uid = spec["uid"]
    cache = TraceCache(stage0_root() / "trace_cache", uid)
    if not cache.load(seed=seed):
        config = base_config(spec)
        out = stage0_root() / "work" / "traces" / uid / f"seed{seed}"
        config["output_root"] = str(out.relative_to(ROOT))
        config["benchmark"] = "stage0_traces"
        config["repair_args"] = [
            "--rounds", "1", "--repair-iterations", "1", "--repair-population", "2",
            "--seed-base", str(seed), "--repair-localization", "off",
            "--no-active-disambiguation",
            "--matlab-timeout-seconds", "300", "--allow-repair-until", "--method", "noloc",
            "--trace-cache-root", str((stage0_root() / "trace_cache").relative_to(ROOT)),
        ]
        if not run_loop(config, stage0_root() / "work" / "runtime_configs" / uid / f"traces_seed{seed}.json", f"{uid} seed{seed} initial traces"):
            raise RuntimeError(f"initial-trace generation failed for seed {seed}")
    entries = cache.load(seed=seed)
    if not entries:
        raise RuntimeError(f"no frozen initial traces for {uid} seed {seed}")
    return sorted(str(entry["hash"]) for entry in entries)


# --------------------------------------------------------------------------- stage 1


def loop_defaults() -> dict:
    from restless.repair import repair_loop as loop
    with mock.patch.object(sys, "argv", ["repair_loop.py"]):
        return vars(loop.parse_args())


def shared_fields(spec: dict, seed: int, initial_ids: list[str], defaults: dict) -> dict:
    config = base_config(spec)
    return {
        "edit_space": "structural", "repair_edit_max_budget": defaults["repair_edit_max_budget"],
        "repair_iterations": spec["params"]["repair_iterations"], "repair_population": spec["params"]["repair_population"],
        "round_trace_budget": config["round_trace_budget"], "rounds": spec["params"]["rounds"],
        "ceclass_k": config["ceclass_k"], "k_gen": config["k_gen"], "ceclass_class_limit": config["ceclass_class_limit"],
        "seed": seed, "initial_trace_ids": initial_ids, "generation_mode": config.get("generation_mode", defaults["generation_mode"]),
        "early_stop": bool(defaults["early_stop"]), "patience": defaults["patience"],
        "repair_localization_top_k": spec["params"]["localization_top_k"],
        "time_step": config["time_step"], "time_end": config["time_end"],
    }


def sidecars_and_fairness(spec: dict, seed: int, initial_ids: list[str], defaults: dict) -> dict[str, Path]:
    d = results_root() / "_control" / spec["uid"]
    d.mkdir(parents=True, exist_ok=True)
    base = shared_fields(spec, seed, initial_ids, defaults)
    methods = spec.get("methods", ALL_METHODS)
    configs = {m: apply_method(base, m) for m in methods}
    for method, cfg in configs.items():
        (d / f"{method}.json").write_text(json.dumps(cfg, indent=2) + "\n")
    result = restless_config_check(spec, seed)
    for name in ("fairness.json", "fairness_rq3.json"):
        (d / name).write_text(json.dumps(result, indent=2, default=str) + "\n")
    return {"rq2": d / "fairness.json", "rq3": d / "fairness_rq3.json"}


# --------------------------------------------------------------------------- stage 2


def run_method(spec: dict, seed: int, method: str, fairness: dict[str, Path]) -> bool:
    uid, source = spec["uid"], spec["source"]
    benchmark = "rq3" if method in RQ3_ONLY else "rq2"
    run_dir = results_root() / benchmark / source / method / uid
    attempt, done = next_attempt(run_dir)
    out = run_dir / f"attempt{attempt}"
    if done:
        log(f"SKIP complete run {out.relative_to(ROOT)}")
        return True
    if method == "llm_direct":   # no loop, no query: phi0 is checked as it is
        from restless.baselines.llm_direct import run_llm_direct  # noqa: PLC0415
        log(f"START {spec['uid']} llm_direct; output={out.relative_to(ROOT)}")
        return run_llm_direct(spec, seed, out)
    config = base_config(spec)
    config["output_root"] = str(out.relative_to(ROOT))
    config["benchmark"] = benchmark
    p = spec["params"]
    config["repair_args"] = loop_args(spec, seed, method, attempt, fairness[benchmark])
    ok = run_loop(config, stage0_root() / "work" / "runtime_configs" / uid / f"seed{seed}_{method}.json", f"{uid} {method}",
                  disk_limit_mb=DISK_LIMIT_MB, time_limit_s=p.get("time_limit_s"))
    return finish_method(ok, out, spec, seed, method)


def loop_args(spec: dict, seed: int, method: str, attempt: int, fairness_path: Path) -> list[str]:
    """The repair-loop command line of one run; every method of a spec is built here."""
    p = spec["params"]
    args = [
        "--rounds", str(p["rounds"]), "--repair-iterations", str(p["repair_iterations"]),
        "--repair-population", str(p["repair_population"]), "--threads", "1", "--seed-base", str(seed),
        "--repair-localization-top-k", str(p["localization_top_k"]),        "--matlab-timeout-seconds", str(p["matlab_timeout"]), "--allow-repair-until", "--method", method,
        "--attempt", str(attempt), "--upstream-method", spec["upstream_method"], "--split", spec.get("split", "test"),
        "--experiment-tag", spec.get("experiment_tag", ""), "--phase", spec.get("phase", ""),
        "--cohort", spec.get("cohort", ""), "--protocol-hash", spec.get("protocol_hash", ""),
        "--dataset-hash", spec.get("dataset_hash", ""),
        "--fairness-check", str(fairness_path.relative_to(ROOT)),
        *(["--replay-traces", str((stage0_root() / "trace_cache").relative_to(ROOT))] if spec.get("initial_traces") is not False else []),
        "--trace-cache-root", str((results_root() / "trace_cache_out").relative_to(ROOT)),
    ]
    if spec.get("total_query_budget") is not None:
        args += ["--total-query-budget", str(spec["total_query_budget"])]
    for key, flag in (("ceclass_traces_per_class", "--ceclass-traces-per-class"),
                      ("localization_top_k_ratio", "--repair-localization-top-k-ratio")):
        if key in p:
            args += [flag, str(p[key])]
    if spec.get("reachable_edit_distance") is not None:
        args += ["--reachable-edit-distance", str(spec["reachable_edit_distance"]),
                 "--g-i", json.dumps(spec["G_i"]), "--g-i-paths", json.dumps(spec["G_i_paths"])]
    return args


# Loop arguments that legitimately differ per run (paths, attempt, bookkeeping), never per method.
RUN_LOCAL_ARGS = {"method", "attempt", "fairness_check", "output_root", "heldout_dir", "trace_cache_root",
                  "replay_traces", "record_jsonl"}


def restless_config_check(spec: dict, seed: int) -> dict:
    """Pre-run config diff: parse every method's real loop arguments through the loop's own
    parse_args + method overrides; two methods may differ only in their declared component."""
    from restless.repair import repair_loop as loop
    effective = {}
    # restless is the reference even when this tag does not run it (the LLM tags); llm_direct runs no loop.
    for method in ["restless", *[m for m in spec["methods"] if m not in ("restless", "llm_direct")]]:
        argv = ["repair_loop.py", *loop_args(spec, seed, method, 0, ROOT / "fairness.json")]
        with mock.patch.object(sys, "argv", argv):
            args = loop.parse_args()
        loop.apply_method_overrides(args)
        effective[method] = {k: v for k, v in vars(args).items() if k not in RUN_LOCAL_ARGS}
    reference = effective["restless"]
    illegal = {}
    for method, values in effective.items():
        allowed = RESTLESS_ALLOWED[method]
        diff = {k: [reference.get(k), values.get(k)] for k in sorted(set(reference) | set(values))
                if k not in allowed and reference.get(k) != values.get(k)}
        if diff:
            illegal[method] = diff
        unchanged = [k for k in allowed if reference.get(k) == values.get(k)]
        if unchanged:
            illegal.setdefault(method, {})["_component_not_changed"] = unchanged
    result = {"passed": not illegal, "reference": "restless",
              "allowed": {m: sorted(v) for m, v in RESTLESS_ALLOWED.items() if m in effective},
              "illegal_differences": illegal}
    if illegal:
        raise ValueError("ReSTLess config diff check failed: " + json.dumps(result, sort_keys=True, default=str))
    return result


def finish_method(ok: bool, out: Path, spec: dict, seed: int, method: str) -> bool:
    uid = spec["uid"]
    if not ok:
        reason = LAST_FAILURE_REASON
        killed_at = time.time()
        partial = read_partial_metrics(out, killed_at)
        if reason:
            for entry in out.iterdir():  # free the space; run.log and the query/time split are the evidence
                if entry.name not in ("run.log", "phase_metrics.json", "llm_ce_summary.json", "llm_log.jsonl"):
                    shutil.rmtree(entry, ignore_errors=True) if entry.is_dir() else entry.unlink()
        if not reason:
            try:  # a licensing failure is raised by the loop with this marker; keep it distinguishable
                tail = "".join((out / "run.log").read_text(errors="replace").splitlines(keepends=True)[-400:])
            except OSError:
                tail = ""
            if "MATLAB_LICENSE_FAILURE" in tail:
                reason = "matlab license failure"
        mark_runtime_failure(out, spec, seed, method, reason or f"see {out.relative_to(ROOT)}/run.log", partial)
        failures = results_root() / "_control" / "failures.jsonl"
        failures.parent.mkdir(parents=True, exist_ok=True)
        with failures.open("a") as handle:
            handle.write(json.dumps({"uid": uid, "method": method}) + "\n")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec")
    parser.add_argument("--stage0-only", action="store_true")
    parser.add_argument("--methods", help="comma list: run only these methods of the spec (fairness sidecars still cover the whole spec)")
    a = parser.parse_args()
    spec = load_spec(ROOT / a.spec if not Path(a.spec).is_absolute() else Path(a.spec))
    log(f"instance {spec['uid']} ({spec['source']}/{spec['upstream_method']}, d={spec.get('reachable_edit_distance')})")
    skipped = stage0_root() / "_skipped" / f"{spec['uid']}.json"
    if skipped.exists():
        log(f"SKIPPED {spec['uid']}: stage 0 failed earlier ({skipped.name}); not retried")
        return 0
    only = [m for m in (a.methods or "").split(",") if m]
    if set(only or spec.get("methods", ALL_METHODS)) == {"llm_direct"}:
        # llm_direct needs no initial traces and no loop config: it only reads phi0 and the requirement.
        failed = 0
        failed += 0 if run_method(spec, RUN_SEED, "llm_direct", {}) else 1
        log(f"done {spec['uid']}; failed runs: {failed}")
        return 1 if failed else 0
    defaults = loop_defaults()
    try:
        freeze_initial_traces(spec, RUN_SEED)
    except RuntimeError as error:
        # No initial-trace data means the instance cannot be evaluated; it is dropped from the manifest, not counted as a failed run.
        skipped.parent.mkdir(parents=True, exist_ok=True)
        skipped.write_text(json.dumps({"instance_id": spec["uid"], "reason": str(error)}) + "\n")
        log(f"SKIPPED {spec['uid']}: {error}")
        return 0
    failed = 0
    ids = freeze_initial_traces(spec, RUN_SEED)
    if not a.stage0_only:
        fairness = sidecars_and_fairness(spec, RUN_SEED, ids, defaults)
        for method in only or spec.get("methods", ALL_METHODS):
            failed += 0 if run_method(spec, RUN_SEED, method, fairness) else 1
    log(f"done {spec['uid']}; failed runs: {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
