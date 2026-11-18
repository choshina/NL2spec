#!/usr/bin/env python3
"""Live progress and interim results of a frozen manifest (read-only; works on partial results).

    python3 -m restless.pipeline.progress --tag b4_v1 [--tag b3_v1 ...] [--results-root results]

Per method: finished / expected runs, repaired and equivalent counts among finished runs, median Q, runtime failures,
plus a throughput-based ETA. Interim numbers only: not an analysis, and unfinished runs are simply absent.
"""
from __future__ import annotations

import argparse
import csv
from collections import defaultdict
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]


def latest_records(results: Path) -> dict:
    seen: dict = {}
    for path in results.rglob("run_record.json"):
        if "_stage0" in path.parts:
            continue
        try:
            rec = json.loads(path.read_text())
            key = (rec["instance"]["instance_id"], rec["method"])
        except (json.JSONDecodeError, KeyError, OSError):
            continue
        done = bool(rec.get("complete")) or rec.get("terminal_state") == "runtime_failure"
        old = seen.get(key)
        if old is None or (done and not old[1]) or (done == old[1] and int(rec.get("attempt", 1)) >= int(old[0].get("attempt", 1))):
            seen[key] = (rec, done, path.stat().st_mtime)
    return seen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tag", action="append", required=True)
    parser.add_argument("--results-root", default="results")
    args = parser.parse_args()
    for tag in args.tag:
        expected = list(csv.DictReader((ROOT / "experiments/frozen" / tag / "expected_runs.csv").open(newline="")))
        seen = latest_records(ROOT / args.results_root / tag)
        total, finished = defaultdict(int), defaultdict(list)
        keys = set()
        for row in expected:
            key = (row["instance_id"], row["method"])
            keys.add(key)
            total[row["method"]] += 1
            item = seen.get(key)
            if item and item[1]:
                finished[row["method"]].append(item)
        print(f"\n== {tag}  ({sum(len(v) for v in finished.values())}/{len(expected)} runs finished)")
        print(f"{'method':<18}{'done':>9}{'repaired':>10}{'equiv':>7}{'median Q':>10}{'fail':>6}")
        stamps = []
        for method in sorted(total):
            items = finished[method]
            res = [i[0].get("result") or {} for i in items]
            rep = sum(1 for r in res if r.get("status") == "repaired")
            eq = sum(1 for r in res if (r.get("equivalence") or {}).get("verdict") == "equivalent")
            qs = [r["Q"] for r in res if isinstance(r.get("Q"), (int, float))]
            fail = sum(1 for i in items if i[0].get("terminal_state") == "runtime_failure")
            stamps += [i[2] for i in items]
            print(f"{method:<18}{len(items):>5}/{total[method]:<4}{rep:>10}{eq:>7}{(statistics.median(qs) if qs else float('nan')):>10.1f}{fail:>6}")
        if len(stamps) >= 5:
            span = max(stamps) - min(stamps)
            rate = (len(stamps) - 1) / span * 3600 if span > 0 else 0
            left = len(expected) - len(stamps)
            print(f"throughput {rate:.1f} runs/h on this machine; ETA {left / rate:.1f} h for the {left} runs left" if rate else "throughput: n/a")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
