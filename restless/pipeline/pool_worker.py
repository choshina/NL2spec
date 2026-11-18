#!/usr/bin/env python3
"""Run one shard of a frozen manifest with several workers in the same project directory.

    python3 -m restless.pipeline.pool_worker --tag respstl_v1 --shard a --workers 4

Each worker takes one instance at a time through an atomic claim, runs its methods one after another
(its own MATLAB), and then takes the next unit. Re-running the command resumes: finished units are skipped, and units
whose worker died are taken over after their heartbeat goes stale.
"""
from __future__ import annotations

import argparse
import csv
from collections import OrderedDict
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
from restless.pipeline.claims import Claims  # noqa: E402


def units(tag: str, shard: str, methods: set | None = None) -> "OrderedDict[str, dict]":
    """instance -> its methods, in manifest order."""
    rows = list(csv.DictReader((ROOT / "experiments" / "frozen" / tag / "expected_runs.csv").open(newline="")))
    grouped: "OrderedDict[str, dict]" = OrderedDict()
    for row in rows:
        if shard != "all" and row["shard"] != shard:
            continue
        if methods and row["method"] not in methods:
            continue
        key = row["instance_id"]
        unit = grouped.setdefault(key, {"spec": row["spec"], "instance": row["instance_id"], "methods": []})
        unit["methods"].append(row["method"])
    # Interleave so the first units handed to concurrent workers belong to different instances.
    by_instance: "OrderedDict[str, list]" = OrderedDict()
    for key, unit in grouped.items():
        by_instance.setdefault(unit["instance"], []).append(key)
    ordered: "OrderedDict[str, dict]" = OrderedDict()
    pending = [list(v) for v in by_instance.values()]
    while any(pending):
        for queue in pending:
            if queue:
                key = queue.pop(0)
                ordered[key] = grouped[key]
    return ordered


def run_unit(unit: dict, results_root: str, env_extra: dict, runner: list[str]) -> bool:
    env = {**os.environ, "RESPSTL_RESULTS_ROOT": results_root, **env_extra}
    command = [*runner, unit["spec"], "--methods", ",".join(unit["methods"])]
    return subprocess.run(command, cwd=ROOT, env=env).returncode == 0


def worker_loop(args: argparse.Namespace, worker_id: int) -> int:
    results_root = f"results/{args.tag}"
    claims = Claims(ROOT / results_root / "_claims", ttl_s=args.ttl)
    runner = args.runner.split() if args.runner else [sys.executable, "-m", "restless.pipeline.run_instance"]
    failed = 0
    for key, unit in units(args.tag, args.shard, args.method_set).items():
        if not claims.try_claim(key):
            continue
        print(f"[worker {worker_id}] claimed {key} ({len(unit['methods'])} runs)", flush=True)
        with claims.hold(key):
            ok = run_unit(unit, results_root, {"MATLAB_MAX_CONCURRENT": str(args.matlab_slots)}, runner)
        claims.finish(key, ok=ok)
        failed += 0 if ok else 1
        print(f"[worker {worker_id}] {'finished' if ok else 'FAILED'} {key}", flush=True)
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", required=True)
    parser.add_argument("--shard", default="a", help="shard letter from the manifest, or `all`")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--matlab-slots", type=int, default=0, help="simultaneous MATLAB sessions on this host; default = workers")
    parser.add_argument("--ttl", type=float, default=900.0, help="seconds of silence before a claim on another host is taken over")
    parser.add_argument("--methods", default="", help="comma-separated methods to run (default: all)")
    parser.add_argument("--runner", default="", help="test hook: replacement for run_instance.py")
    parser.add_argument("--worker-id", type=int, default=-1, help="internal")
    args = parser.parse_args()
    args.matlab_slots = args.matlab_slots or args.workers
    args.method_set = {x for x in args.methods.split(",") if x}
    if args.worker_id >= 0:
        return 1 if worker_loop(args, args.worker_id) else 0
    manifest = ROOT / "experiments" / "frozen" / args.tag / "expected_runs.csv"
    if not manifest.exists():
        sys.exit(f"pool_worker: no manifest for tag {args.tag}")
    total = len(units(args.tag, args.shard, args.method_set))
    print(f"pool_worker: tag={args.tag} shard={args.shard} units={total} workers={args.workers} matlab_slots={args.matlab_slots}", flush=True)
    children = []
    for worker_id in range(args.workers):
        command = [sys.executable, "-m", "restless.pipeline.pool_worker", *sys.argv[1:], "--worker-id", str(worker_id)]
        children.append(subprocess.Popen(command, cwd=ROOT))
        time.sleep(1.0)  # lets the first claims land on different units
    codes = [child.wait() for child in children]
    print(f"pool_worker: finished; workers with failures: {sum(code != 0 for code in codes)}", flush=True)
    return 1 if any(codes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
