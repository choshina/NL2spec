#!/usr/bin/env python3
# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.


"""Configurable CE-class-guided STL repair loop.

The runtime configuration is dataset-driven:
  - data.csv header comes from --signal-names
  - signal value domains come from --signal-bounds
  - the time grid comes from --time-start/--time-end/--time-step
  - Java dataset/grammar selection comes from --dataset-name/--grammar-path

CE-class k controls temporal splitting in the selected CE-class backend.
SMT generation k (--k-gen-init) remains a separate feasibility/switching budget.
"""

from __future__ import annotations

import argparse
import atexit
from contextlib import nullcontext
import collections
import copy
import csv
import hashlib
import itertools
import json
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]

from restless.common.derived_signals import evaluate_derived, referenced_signals


def _default_executable(env_name: str, command: str) -> str:
    configured = os.environ.get(env_name, "").strip()
    if configured:
        return configured
    discovered = shutil.which(command)
    if discovered:
        return discovered
    return command


def _default_java() -> str:
    configured = os.environ.get("JAVA_BIN", "").strip()
    if configured:
        return configured
    # The checked-in Infix2Prefix.class is Java 17 bytecode. macOS commonly
    # exposes an older Java through JAVA_HOME/PATH even when a suitable JDK is
    # installed, so select 17 before consulting those generic defaults.
    if platform.system() == "Darwin":
        local_jdks = sorted(
            Path.home().glob("Library/Java/JavaVirtualMachines/jdk-17*/Contents/Home/bin/java")
        )
        if local_jdks:
            return str(local_jdks[-1])
        try:
            result = subprocess.run(
                ["/usr/libexec/java_home", "-v", "17"],
                check=True,
                capture_output=True,
                text=True,
            )
            candidate = Path(result.stdout.strip()) / "bin" / "java"
            if candidate.is_file():
                return str(candidate)
        except (OSError, subprocess.CalledProcessError):
            pass
    java_home = os.environ.get("JAVA_HOME", "").strip()
    if java_home:
        candidate = Path(java_home) / "bin" / "java"
        if candidate.is_file():
            return str(candidate)
    return _default_executable("JAVA_BIN", "java")


DEFAULT_JAVA = _default_java()


def _default_matlab() -> str:
    configured = os.environ.get("MATLAB_BIN", "").strip()
    if configured:
        return configured
    discovered = shutil.which("matlab")
    if discovered:
        # MATLAB's macOS launcher derives its installation from argv[0].  A
        # Homebrew-style symlink such as /opt/homebrew/bin/matlab can therefore
        # exit immediately even though invoking the same binary through its
        # real /Applications/MATLAB_R*.app path works.  Resolve only the
        # discovered executable; an explicit MATLAB_BIN remains untouched.
        return str(Path(discovered).resolve())
    candidates: list[Path] = []
    if platform.system() == "Darwin":
        candidates.extend(Path("/Applications").glob("MATLAB_R*.app/bin/matlab"))
    elif platform.system() == "Linux":
        candidates.extend(Path("/usr/local/MATLAB").glob("R*/bin/matlab"))
    if candidates:
        return str(sorted(candidates)[-1])
    return "matlab"


DEFAULT_MATLAB = _default_matlab()
DEFAULT_ARCH = os.environ.get(
    "BUSTLE_ARCH",
    "x86_64" if platform.system() == "Darwin" else "",
)
DEFAULT_CLASSPATH = os.environ.get("BUSTLE_CLASSPATH", (
    "libs/JGEA.jar:libs/moonlight.jar:libs/jblas-1.2.4.jar:"
    "target/classes"
))

from restless.ceclass.obligations import derive_obligations, obligations_to_csv
from restless.common.stl_epoch.ast_nodes import ASTNode, walk_preorder as epoch_walk_preorder
from restless.common.stl_epoch.formula_ops import bustle_print
from restless.common.stl_epoch.parser import parse_stl as epoch_parse_stl
from restless.pipeline import RunRecorder
from restless.pipeline.run_record import RunSpec
from restless.atg.matlab_slots import matlab_slot
from restless.atg.trace_cache import TraceCache
from restless.atg.trace_cache import trace_hash as cached_trace_hash

DEFAULT_SIGNAL_NAMES = ["x1", "x2"]
RUN_TIMINGS = {"tracegen": 0.0, "labelling": 0.0, "search": 0.0, "other": 0.0}
# Wall time spent inside the candidate-disambiguation phase. It overlaps RUN_TIMINGS (the
# ATG and evaluator calls made there are also counted under tracegen / labelling), so it is
# reported next to them, never added to them. "open_since" is the start of a window still
# running, so a run killed mid-phase can still be charged for it.
RUN_PHASES = {"disambiguation": 0.0, "disambiguation_open_since": None}


class phase_clock:
    """Context manager timing one candidate-disambiguation window and persisting the totals."""

    def __init__(self, args: argparse.Namespace, name: str = "disambiguation") -> None:
        self.args, self.name = args, name

    def __enter__(self):
        RUN_PHASES[f"{self.name}_open_since"] = time.time()
        write_phase_metrics(self.args)
        return self

    def __exit__(self, *_exc):
        started = RUN_PHASES.get(f"{self.name}_open_since")
        if started is not None:
            RUN_PHASES[self.name] += time.time() - started
        RUN_PHASES[f"{self.name}_open_since"] = None
        write_phase_metrics(self.args)
        return False


def write_phase_metrics(args: argparse.Namespace) -> None:
    """Persist the running query / time split. A timed-out run is SIGKILLed with its process memory,
    so the split has to be on disk before the kill (atomic replace; read back by the launcher)."""
    path = getattr(args, "phase_metrics_path", None)
    metrics = getattr(args, "trace_metric_counters", None)
    if path is None or metrics is None:
        return
    total = len(metrics["oracle_trace_fingerprints"])
    disamb = int(metrics.get("queries_disambiguation", 0))
    payload = {
        "Q_total": total, "Q_disambiguation": disamb, "Q_eq": total - disamb,
        "time_s": dict(RUN_TIMINGS),
        "time_disambiguation_s": RUN_PHASES["disambiguation"],
        "disambiguation_open_since": RUN_PHASES["disambiguation_open_since"],
        "written_at": time.time(),
    }
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps(payload))
        os.replace(temporary, path)
    except OSError:
        pass
# Trace-generation counters/timings reported in run_record.json (trace_metrics.trace_gen_stats).
# MATLAB-side numbers come from trace_gen_stats.csv written by GenerateCaseDatasetFromCsv.m.
TRACE_STATS: dict[str, float] = {
    "matlab_launches": 0, "matlab_jobs": 0, "solves_requested": 0, "solves_performed": 0, "z3_calls": 0, "z3_sat": 0, "z3_unsat": 0, "z3_unknown": 0,
    "matlab_wall_s": 0.0,       # wall time of every `matlab -batch` subprocess
    "matlab_startup_s": 0.0,    # wall time minus time inside GenerateCaseDatasetFromCsv
    "smt_encode_s": 0.0,        # building the SMT2 query (MATLAB side, excludes z3)
    "z3_solve_s": 0.0,          # z3 process time
    "interp_s": 0.0,            # solver trace -> sample grid
    "matlab_csv_write_s": 0.0,  # dataset CSV writing inside MATLAB
    "python_csv_read_s": 0.0,   # reading data.csv back + post-processing in Python
}


# True when the latest MATLAB call saw only `unsat` answers: a retry would fail identically.
LAST_ATG_ALL_UNSAT = False
# Tail of the last failed MATLAB launch's own output, kept so a licensing problem is visible and
# can be told apart from an SMT / generator failure.
LAST_MATLAB_ERROR_TEXT = ""
MATLAB_LICENSE_RETRY_SLEEP_S = 20.0
MATLAB_LICENSE_RETRIES = 5


class MatlabLicenseError(RuntimeError):
    """MATLAB could not obtain a license. Fatal for the run: continuing would silently drop traces."""


def matlab_license_error(text: str) -> bool:
    return bool(re.search(r"licen[sc]", text, re.I)) and bool(
        re.search(r"fail|error|unable|cannot|could not|invalid|expire|denied|not (?:available|activated|licensed)|"
                  r"sign.?in|no valid|checkout", text, re.I))


def matlab_launch_prefix(matlab: str) -> list[str]:
    """MATLAB executable plus the licensing mode of this host (MATLAB_LICENSE_MODE, e.g. onlinelicensing).
    Passed explicitly so every launch uses the mode that was verified, not whatever MATLAB defaults to."""
    mode = os.environ.get("MATLAB_LICENSE_MODE", "").strip()
    return [matlab, "-licmode", mode] if mode else [matlab]
# Kce returned by each round's CE-class refinement (0 = no CE-class this round).
CEKCE_BY_ROUND: dict[str, int] = {}
# First equivalence query of the run (round 1): whether it found a counterexample within B_EQ
# and how many queries it had spent when the first one appeared.
FIRST_EQ: dict[str, object] = {}


def _absorb_matlab_stats(stats_csv: Path, wall_s: float) -> None:
    """Fold one MATLAB call's trace_gen_stats.csv into TRACE_STATS."""
    global LAST_ATG_ALL_UNSAT
    function_s = 0.0
    z3_statuses: list[str] = []
    if stats_csv.exists():
        for line in stats_csv.read_text().splitlines():
            parts = line.split(",")
            if len(parts) < 2:
                continue
            key, status = parts[0], (parts[2] if len(parts) > 2 else "")
            try:
                seconds = float(parts[1])
            except ValueError:
                continue
            if key == "z3":
                z3_statuses.append(status)
                TRACE_STATS["z3_calls"] += 1
                TRACE_STATS["z3_solve_s"] += seconds
                bucket = "z3_sat" if status == "sat" else "z3_unsat" if status == "unsat" else "z3_unknown"
                TRACE_STATS[bucket] += 1
            elif key == "encode":
                TRACE_STATS["smt_encode_s"] += seconds
            elif key == "interp":
                TRACE_STATS["interp_s"] += seconds
            elif key == "csv":
                TRACE_STATS["matlab_csv_write_s"] += seconds
            elif key == "function":
                function_s += seconds
            elif key == "distinct":
                TRACE_STATS["solves_performed"] += int(seconds)
                requested = status.split("|requested=")[1].split("|")[0] if "|requested=" in status else "0"
                TRACE_STATS["solves_requested"] += int(requested)
                print(f"    distinct-trace loop: solves={int(seconds)} requested={requested} "
                      f"reason={status.rsplit('|', 1)[-1]}", flush=True)
    TRACE_STATS["matlab_startup_s"] += max(wall_s - function_s, 0.0)
    LAST_ATG_ALL_UNSAT = bool(z3_statuses) and all(status == "unsat" for status in z3_statuses)


def append_macos_arch_prefix(command: list[str], arch: str) -> None:
    """The `arch -x86_64` wrapper is a macOS/Rosetta feature, not GNU arch."""
    if arch and platform.system() == "Darwin":
        command.extend(["arch", f"-{arch}"])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="CE-class-guided STL repair loop."
    )
    p.add_argument("--rounds", type=int, default=5)
    p.add_argument("--k-gen-init", type=int, default=8,
                   help="SMT trace generation budget: number of signal switchings.")
    p.add_argument("--start-formula-csv", default="dataset/case_formulas.csv")
    p.add_argument("--start-formula-row", type=int, default=1)
    p.add_argument("--oracle-formula-csv", default="dataset/oracle_formulas.csv")
    p.add_argument("--oracle-formula-row", type=int, default=1)
    p.add_argument("--dataset-name", default="generic",
                   help="Dataset identifier forwarded to the Java evaluator/repair backend.")
    p.add_argument("--signal-names", default="x1,x2",
                   help="Comma-separated signal names used in formulas and data.csv, e.g. x1,x2,x3.")
    p.add_argument(
        "--boolean-signal-names",
        default="",
        help=(
            "Comma-separated signal names whose generated trace values must be snapped "
            "to the Boolean domain {0,1}. STL constants are not changed."
        ),
    )
    p.add_argument(
        "--discrete-signal-names",
        default="",
        help=(
            "Comma-separated integer-valued signal names. Domain guards use inclusive "
            "bounds and generated trace values are rounded/clamped to the configured bounds."
        ),
    )
    p.add_argument(
        "--static-signal-names",
        default="",
        help=(
            "Comma-separated time-invariant scenario parameters (e.g. vehicle length). "
            "Their --signal-bounds must be [v,v]; generated traces hold exactly v."
        ),
    )
    p.add_argument(
        "--signal-bounds",
        default="",
        help=(
            "Signal realism/domain bounds. Prefer JSON like "
            "'{\"x1\":[0,2],\"x2\":[-1000,1000]}'; "
            "also accepts 'x1:0:2;x2:-1000:1000'. "
            "When omitted, x1/x2 use --x1/--x2 bounds and other signals use [1e-6,0.999999]."
        ),
    )
    p.add_argument(
        "--derived-signal-definitions",
        default="{}",
        help=(
            "JSON identities enforced by ATG, e.g. "
            "'{\"x_minus_y\":{\"op\":\"subtract\",\"left\":\"x\",\"right\":\"y\"}}'."
        ),
    )
    p.add_argument(
        "--one-hot-groups",
        default="{}",
        help="JSON object mapping a categorical variable to its Boolean predicate signals.",
    )
    p.add_argument("--output-root", default="output/ceclass_repair_loop")
    p.add_argument("--seed-base", type=int, default=22001)
    p.add_argument("--repair-iterations", type=int, default=20)
    p.add_argument("--repair-population", type=int, default=20)
    p.add_argument("--repair-parameter-ga-ratio", type=float, default=0.0,
                   help="Deprecated no-op. Numeric STL constants are preserved.")
    p.add_argument("--repair-parameter-refit", action=argparse.BooleanOptionalAction, default=False,
                   help="Deprecated no-op. Numeric/time parameters are never refit.")
    p.add_argument("--refit-selected-template", action=argparse.BooleanOptionalAction, default=False,
                   help="Deprecated no-op. Selected templates are not numerically refit.")
    p.add_argument("--repair-parameter-budget", type=int, default=50,
                   help="Local parameter optimization budget per BUSTLE structure candidate.")
    p.add_argument("--parameter-refit-min-traces", type=int, default=100,
                   help="Minimum accumulated repair traces required before any GA parameter refit is enabled.")
    p.add_argument("--parameter-phase-top-n", type=int, default=5,
                   help="Top pool candidates used to decide whether the structure template has stabilized.")
    p.add_argument("--parameter-phase-stability-rounds", type=int, default=3,
                   help="Consecutive stable-template rounds required before enabling parameter refit.")
    p.add_argument("--repair-edit-max-budget", type=int, default=1,
                   help="Maximum number of repair-specific AST edits applied to one BUSTLE candidate.")
    p.add_argument("--repair-fuzz-queue-limit", type=int, default=0,
                   help="Maximum BUSTLE fuzz-queue size; 0 keeps every unique valid candidate.")
    p.add_argument(
        "--repair-parent-selection",
        choices=("ucb", "weighted", "tournament"),
        default="ucb",
        help=(
            "Parent-formula scheduler for retained repair candidates. The current "
            "implementation always selects the maximum Formula-UCB; 'weighted' "
            "and 'tournament' remain accepted as compatibility aliases."
        ),
    )
    p.add_argument(
        "--pool-selection-objective",
        default="accuracy",
        help=argparse.SUPPRESS,
    )
    p.add_argument("--active-disambiguation-max-candidates", type=int, default=2,
                   help="Deprecated compatibility flag; active disambiguation now considers every accuracy=1 pair.")
    p.add_argument("--active-disambiguation-trace-budget", type=int, default=2,
                   help="Maximum requested active disambiguation traces per candidate pair.")
    p.add_argument(
        "--active-disambiguation-replay-attempts",
        type=int,
        default=1,
        help=(
            "Distinct SMT candidates generated per requested disambiguation trace. "
            "Candidates are replayed on the configured discrete time grid, and only "
            "genuine phi_a/phi_b separators consume the trace budget. A deterministic "
            "discrete-grid fallback runs when these candidates do not separate."
        ),
    )
    p.add_argument("--active-disambiguation-margin", type=float, default=1.0e-4,
                   help=(
                       "Robustness-separation margin used when generating active "
                       "disambiguation traces. Prevents boundary traces where both "
                       "phi and NOT[phi] replay as SAT."
                   ))
    p.add_argument("--repair-dataset-mode", choices=("misclassified", "all"), default="all",
                   help="'all' uses every labelled trace as D for repair; 'misclassified' is an ablation mode.")
    p.add_argument("--multi-solution-min-traces", type=int, default=20,
                   help="Switch to multi-solution collection once |D| reaches this many repair traces.")
    p.add_argument("--multi-solution-k", type=int, default=2,
                   help="Target number of accuracy=1 candidates before active disambiguation.")
    p.add_argument("--repair-class-focus-mode", choices=("off", "prefer", "only"), default="off",
                   help="Prioritize or restrict one-step edits to predicates from misclassified CE-classes.")
    p.add_argument("--repair-class-focus-top-k", type=int, default=3,
                   help="Number of highest-mismatch CE-classes selected for focused repair.")
    p.add_argument("--repair-attempts-per-round", type=int, default=1,
                   help="Max repeated repair attempts on the current accumulated trace set.")
    p.add_argument("--repair-stop-on-perfect", action=argparse.BooleanOptionalAction, default=True,
                   help="Allow the Java BUSTLE repair loop to stop as soon as a candidate has zero misclassified traces.")
    p.add_argument("--threads", type=int, default=1)
    p.add_argument("--local", choices=["true", "false"], default="false")
    p.add_argument("--disable-repair-until", dest="disable_repair_until", action="store_true", default=True,
                   help="Temporarily forbid UNTIL from repair candidates and repair grammar.")
    p.add_argument("--allow-repair-until", dest="disable_repair_until", action="store_false",
                   help="Allow repair to generate/select UNTIL again.")
    p.add_argument("--repair-mode", default="bustle")
    p.add_argument("--repair-backend", choices=("java", "llm"), default="java",
                   help="Who proposes repair candidates when Mis(phi, D) is not empty: the Java BUSTLE search or "
                        "the LLM-CE baseline (restless/baselines/llm_ce.py). Only method llm_ce uses llm.")
    p.add_argument("--llm-ce-config", default="experiments/llm_ce_config.py",
                   help="Fixed model, sampling and prompt configuration of the LLM-CE baseline.")
    p.add_argument("--repair-localization", choices=("off", "sgrl"), default="sgrl",
                   help="Use Separation-Guided Repair Localization for BUSTLE mutation placement.")
    p.add_argument(
        "--repair-localization-top-k",
        type=int,
        default=3,
        help=(
            "Number of top-ranked nodes by Evidence in the initial edit gate; "
            "ties are retained and the gate widens when no edit has positive MUSE."
        ),
    )
    p.add_argument(
        "--repair-localization-top-k-ratio",
        type=float,
        default=0.2,
        help=(
            "rho: the gate keeps the top max(top-k, ceil(rho * |V_phi|)) ranked nodes, |V_phi| being "
            "the node count of the hypothesis being repaired. 0 keeps the fixed top-k. "
            "Tune on the dev split only."
        ),
    )
    p.add_argument(
        "--type-priority", action=argparse.BooleanOptionalAction, default=True,
        help=(
            "On a gate node with self credit (temporal c > 1 / atom c = K) try the diagnosed-type edits first "
            "(flip_temporal; flip_direction + toggle_not) and skip the node's other edits when one of them "
            "fits D. --no-type-priority restores the previous exhaustive trial."
        ),
    )
    p.add_argument("--repair-localization-stagnation-window", type=int, default=10)
    p.add_argument("--repair-localization-expansion-step", type=int, default=2)
    p.add_argument("--repair-localization-ucb-beta", type=float, default=0.35)
    p.add_argument("--repair-localization-temporal-cell-limit", type=int, default=3)
    p.add_argument("--repair-localization-self-coefficient-cap", type=int, default=3)
    p.add_argument("--repair-localization-evidence-weight", type=float, default=0.5)
    p.add_argument("--repair-localization-muse-weight", type=float, default=2.0)
    p.add_argument("--repair-localization-size-weight", type=float, default=0.05)
    p.add_argument("--repair-localization-broken-lambda", type=float, default=1.0)
    p.add_argument("--repair-localization-reward-weight", type=float, default=2.0)
    p.add_argument("--repair-localization-reward-clip", type=float, default=1.0)
    p.add_argument("--repair-localization-fitness-gain-weight", type=float, default=1.0)
    p.add_argument(
        "--repair-min-complexity-ratio",
        type=float,
        default=0.6,
        help=(
            "Reject candidates smaller than this fraction of the seed formula "
            "only when their replay balanced accuracy does not improve."
        ),
    )
    p.add_argument("--java", default=DEFAULT_JAVA,
                   help="Java executable; defaults to $JAVA_BIN or java on PATH.")
    p.add_argument("--classpath", default=DEFAULT_CLASSPATH)
    p.add_argument("--grammar-path", default="",
                   help="Explicit Java grammar path. Useful for datasets with more than x1/x2.")
    p.add_argument("--matlab", default=DEFAULT_MATLAB,
                   help="MATLAB executable; defaults to $MATLAB_BIN or matlab on PATH.")
    p.add_argument(
        "--no-generation-memo",
        action="store_true",
        help=(
            "Retry trace generation for a class formula that already failed to "
            "produce any trace earlier in this run. Off by default: CE-class "
            "enumeration is deterministic, so an unsatisfiable class otherwise "
            "burns MATLAB calls once per round for the whole run."
        ),
    )
    p.add_argument("--matlab-runtime-retries", type=int, default=2,
                   help="Retries for MATLAB generator runtime failures before giving up on a class.")
    p.add_argument(
        "--atg-early-exit", choices=("off", "unsat", "nonsat"), default="unsat",
        help=(
            "Stop a class's distinct-trace loop at the first non-sat answer. "
            "'unsat' (default) is output-identical to 'off': each blocking clause only "
            "shrinks the model set, so a solve after unsat is unsat. 'nonsat' also "
            "stops on unknown (can differ from 'off'); 'off' runs all requested solves."
        ),
    )
    p.add_argument(
        "--share-round-budget", action="store_true",
        help=(
            "Treat round_trace_budget as a hard cap on SMT solves shared by all CE classes "
            "and the seed fallback: class quota = ceil(remaining / classes left), unused "
            "quota flows to later classes, fallback only gets what is left. Off by default "
            "(original per-class budgets, which can exceed the round budget)."
        ),
    )
    p.add_argument(
        "--max-trace-points", type=int, default=0,
        help=(
            "Coarsen the time grid to at most this many points (dt = span / max). 0 (default) "
            "disables the cap, keeping the configured --time-step. dt is never raised above "
            "1/4 of the narrowest temporal interval. Changes the generated traces."
        ),
    )
    p.add_argument(
        "--retry-unsat-failures", action="store_true",
        help=(
            "Original behaviour: when MATLAB exits non-zero although every SMT answer was "
            "unsat (a deterministic failure), still retry --matlab-runtime-retries times "
            "with a 3s sleep. Off by default: the retries cannot change the outcome."
        ),
    )
    p.add_argument(
        "--atg-persistent-matlab", action="store_true",
        help=(
            "Keep one MATLAB process alive (atg_server.m) and feed it the ATG jobs instead of "
            "starting `matlab -batch` per CE class. Same jobs, same outputs; off by default."
        ),
    )
    p.add_argument("--matlab-timeout-seconds", type=float, default=300.0,
                   help="Per MATLAB ATG call timeout. 0 disables the timeout.")
    p.add_argument(
        "--arch",
        default=DEFAULT_ARCH,
        help=(
            "Optional macOS arch wrapper, e.g. x86_64. Defaults to x86_64 on "
            "macOS and empty on Linux; override with $BUSTLE_ARCH."
        ),
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--generation-mode",
        choices=("ceclass", "obligation"),
        default="ceclass",
        help=(
            "Stage-2 trace generation. 'ceclass' partitions the behaviour space "
            "and therefore depends on what the current formula decides. "
            "'obligation' partitions the syntax tree of the current formula "
            "instead, so it keeps producing demands after accuracy hits 1."
        ),
    )
    p.add_argument(
        "--obligation-boundary-eps",
        type=float,
        default=1e-3,
        help="Half-width of the band a BOUNDARY obligation asks a signal to enter.",
    )
    # CE-class options
    p.add_argument("--ceclass-k", type=int, default=2,
                   help="Temporal split granularity for CE-class (k parameter); fixed schedule only.")
    p.add_argument(
        "--ceclass-schedule",
        choices=("refine", "fixed", "none"),
        default="refine",
        help=(
            "'refine': each equivalence query starts at Kce=1 with up to Kce classes per side "
            "and --ceclass-traces-per-class traces each, returns at the first layer with a "
            "counterexample, else doubles Kce while budget remains, until every temporal "
            "operator is split per sample. "
            "'fixed': one layer at --ceclass-k. 'none': no CE-class lattice, only models of "
            "the trace seed formula and of its negation (Kce = 1)."
        ),
    )
    p.add_argument("--ceclass-max-evals", type=int, default=200)
    p.add_argument("--ceclass-traces-per-class", type=int, default=3)
    p.add_argument("--ceclass-class-limit", type=int, default=5,
                   help="Max number of covered CE-classes to expand per round.")
    p.add_argument(
        "--ceclass-generation-targets",
        choices=("frontier", "class"),
        default="frontier",
        help=(
            "Formula sent to ATG for each CE-class. 'class' keeps the full "
            "CE-class formula; 'frontier' splits disjunctive/frontier branches "
            "such as F(A OR B) into F(A), F(B) targets for more diverse traces."
        ),
    )
    p.add_argument(
        "--ceclass-frontier-limit-per-class",
        type=int,
        default=4,
        help="Maximum frontier generator targets expanded from one CE-class row.",
    )
    p.add_argument(
        "--class-generation-margin",
        type=float,
        default=0.0,
        help=(
            "Strengthen class generator predicates by this margin before ATG. "
            "For example x >= c becomes x >= c+margin, preventing boundary "
            "solutions that replay on the wrong side of current STL."
        ),
    )
    p.add_argument("--independent-traces-per-mode", type=int, default=5)
    p.add_argument("--round-trace-budget", type=int, default=10,
                   help="Fixed total traces per round. 0 = derive from ceclass budget.")
    p.add_argument("--no-independent", action="store_true",
                   help="Disable independent fallback trace generation.")
    p.add_argument(
        "--no-class-seed-fallback",
        action="store_true",
        help=(
            "Disable the per-side fallback that generates SAT/VIOLATION traces "
            "from the trace seed formula when a CE-class side yields nothing. "
            "Without it a round can end up single-labelled."
        ),
    )
    p.add_argument("--oracle-balance-traces", type=int, default=0)
    p.add_argument("--no-oracle-balance-generation", action="store_true")
    p.add_argument("--anchor-per-class", type=int, default=1)
    p.add_argument("--error-representatives-per-class", type=int, default=1)
    p.add_argument("--balance-repair-labels", action="store_true")
    p.add_argument(
        "--trace-seed-formula",
        choices=("initial", "current", "uncertainty"),
        default="current",
        help=(
            "Formula used to guide CE-class/independent trace generation. "
            "'initial' keeps generating traces from the original erroneous STL; "
            "'current' follows the selected repaired formula each round; "
            "'uncertainty' uses the pool candidate whose robustness is closest to "
            "the decision boundary on accumulated traces."
        ),
    )
    # Stop condition
    p.add_argument("--patience", type=int, default=2,
                   help="Early-stop patience when --early-stop is enabled; otherwise recorded only.")
    p.add_argument("--early-stop", action="store_true",
                   help="Enable patience-based early stopping. Default is fixed-round mode: run exactly --rounds rounds.")
    # Time grid default: 0 to 300 step 5
    p.add_argument("--time-start", type=float, default=0.0)
    p.add_argument("--time-end", type=float, default=300.0)
    p.add_argument("--time-step", type=float, default=5.0,
                   help="Sampling interval for the generated time grid (default 5).")
    p.add_argument("--x1-min", type=float, default=1e-6)
    p.add_argument("--x1-max", type=float, default=0.999999)
    p.add_argument("--x2-min", type=float, default=1e-6)
    p.add_argument("--x2-max", type=float, default=0.999999)
    # Paper-facing run metadata. These flags do not change repair behaviour.
    p.add_argument("--benchmark", default="unspecified")
    p.add_argument("--instance-id", default="")
    p.add_argument("--source", default="unspecified")
    p.add_argument("--upstream-method", default="unspecified")
    p.add_argument("--split", choices=("dev", "test", "appendix", "unspecified"), default="unspecified")
    p.add_argument("--method", default="restless")
    p.add_argument("--attempt", type=int, default=1)
    p.add_argument(
        "--keep-intermediate-artifacts",
        action="store_true",
        default=os.environ.get("RESPSTL_KEEP_INTERMEDIATE_ARTIFACTS", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "Keep evaluator input datasets and MATLAB/SMT work directories after a "
            "successful run. By default completed formal runs are compacted; failed "
            "or interrupted runs always retain their intermediates for resumption."
        ),
    )
    p.add_argument("--reachable-edit-distance", default="")
    p.add_argument("--g-i", default="[]", help="Frozen T3 G_i as a JSON list of index paths.")
    p.add_argument("--g-i-paths", default="[]", help="Frozen T3 G_i as a JSON list of AST paths.")
    p.add_argument("--fairness-check", default="", help="T8 fairness-check JSON produced before this run.")
    p.add_argument("--record-jsonl", default="output/runs.jsonl")
    p.add_argument("--trace-cache-root", default="trace_cache")
    p.add_argument("--total-query-budget", type=int, default=-1,
                   help="Hard cap on unique online oracle-labelled traces per run; -1 = rounds x round budget only, 0 = no query.")
    p.add_argument("--experiment-tag", default="")
    p.add_argument("--phase", default="", help="formal or smoke; recorded so smoke output cannot enter formal tables")
    p.add_argument("--cohort", default="", help="confirmed_fault / initial_unknown / ... from the frozen dataset audit")
    p.add_argument("--protocol-hash", default="")
    p.add_argument("--dataset-hash", default="")
    p.add_argument("--replay-traces", default="",
                   help="T9 cache root to preload for this instance/seed; no cached trace is re-queried.")
    p.add_argument("--fault-metadata", default="")
    p.add_argument("--equiv-horizon", type=float, default=None)
    return p.parse_args()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

CONTROL_PREFIX = "@@RESPSTL_CONTROL@@ "


def control_progress(message: str) -> None:
    """Emit one intentionally concise line for the outer worker control log.

    The full stdout/stderr stream is always retained by run_instance.py.  These
    tagged records are the only successful-run lines mirrored to its quiet
    terminal/control stream.
    """
    print(CONTROL_PREFIX + message, flush=True)


def run(
    cmd: list[str],
    cwd: Path,
    dry_run: bool = False,
    capture: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str] | None:
    print("$", " ".join(_shell_quote(c) for c in cmd), flush=True)
    if not dry_run:
        command_text = " ".join(str(part) for part in cmd)
        category = ("tracegen" if any("matlab" in Path(str(part)).name.lower() for part in cmd[:3])
                    else "labelling" if "EvaluateFormulaOnDataset" in command_text
                    else "search" if "it.units.malelab.learningstl.Main" in command_text
                    else "other")
        started = time.perf_counter()
        try:
            is_matlab = any("matlab" in Path(str(part)).name.lower() for part in cmd[:3])
            with (matlab_slot() if is_matlab else nullcontext()):
                completed = subprocess.run(
                    cmd,
                    cwd=cwd,
                    check=True,
                    text=True,
                    capture_output=capture,
                    timeout=timeout,
                )
        except subprocess.CalledProcessError as exc:
            if capture:
                if exc.stdout:
                    sys.stdout.write(exc.stdout)
                    if not exc.stdout.endswith("\n"):
                        sys.stdout.write("\n")
                if exc.stderr:
                    sys.stderr.write(exc.stderr)
                    if not exc.stderr.endswith("\n"):
                        sys.stderr.write("\n")
            raise
        finally:
            RUN_TIMINGS[category] += time.perf_counter() - started
        if capture:
            if completed.stdout:
                sys.stdout.write(completed.stdout)
                if not completed.stdout.endswith("\n"):
                    sys.stdout.write("\n")
            if completed.stderr:
                sys.stderr.write(completed.stderr)
                if not completed.stderr.endswith("\n"):
                    sys.stderr.write("\n")
        return completed
    return None


def _shell_quote(text: str) -> str:
    if not text or any(ch.isspace() or ch in "\"'" for ch in text):
        return "'" + text.replace("'", "'\"'\"'") + "'"
    return text


def _format_process_failure(exc: subprocess.CalledProcessError) -> str:
    """Render a subprocess failure without assuming captured output exists."""
    command = exc.cmd if isinstance(exc.cmd, str) else " ".join(_shell_quote(str(c)) for c in exc.cmd)
    parts = [f"exit_code={exc.returncode}", f"command={command}"]
    if exc.stdout:
        parts.append("stdout:\n" + str(exc.stdout).rstrip())
    if exc.stderr:
        parts.append("stderr:\n" + str(exc.stderr).rstrip())
    return "\n\n".join(parts) + "\n"


def _matlab_string(path_like: object) -> str:
    return str(path_like).replace("\\", "/").replace("'", "''")


def resolve_path(path_like: object) -> Path:
    path = Path(path_like)
    if path.is_absolute():
        return path
    return ROOT / path


def format_number(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.10f}".rstrip("0").rstrip(".")


def parse_signal_names(text: str) -> list[str]:
    names = [part.strip() for part in str(text).split(",") if part.strip()]
    if not names:
        raise ValueError("--signal-names must contain at least one signal")
    if len(set(names)) != len(names):
        raise ValueError(f"--signal-names contains duplicates: {names}")
    return names


def parse_boolean_signal_names(text: str, signal_names: Sequence[str]) -> list[str]:
    names = [part.strip() for part in str(text or "").split(",") if part.strip()]
    if len(set(names)) != len(names):
        raise ValueError(f"--boolean-signal-names contains duplicates: {names}")
    unknown = sorted(set(names) - set(signal_names))
    if unknown:
        raise ValueError(
            f"--boolean-signal-names has unknown signals {unknown}; "
            f"signals={list(signal_names)}"
        )
    return names


def parse_one_hot_groups(text: str, signal_names: Sequence[str]) -> dict[str, list[str]]:
    raw = json.loads(str(text or "{}"))
    if not isinstance(raw, dict):
        raise ValueError("--one-hot-groups must be a JSON object")
    known = set(signal_names)
    groups: dict[str, list[str]] = {}
    for group, values in raw.items():
        if not isinstance(values, list):
            raise ValueError(f"one-hot group {group!r} must be a list")
        names = list(dict.fromkeys(str(value) for value in values))
        unknown = sorted(set(names) - known)
        if unknown:
            raise ValueError(f"one-hot group {group!r} has unknown signals {unknown}")
        if len(names) >= 2:
            groups[str(group)] = names
    return groups


def _default_signal_bound(name: str, args: argparse.Namespace) -> tuple[float, float]:
    if name == "x1":
        return float(args.x1_min), float(args.x1_max)
    if name == "x2":
        return float(args.x2_min), float(args.x2_max)
    return 1e-6, 0.999999


def parse_signal_bounds(
    text: str,
    signal_names: Sequence[str],
    args: argparse.Namespace,
) -> dict[str, tuple[float, float]]:
    bounds: dict[str, tuple[float, float]] = {
        name: _default_signal_bound(name, args)
        for name in signal_names
    }
    spec = str(text or "").strip()
    if not spec:
        return bounds

    parsed: dict[str, object]
    if spec.startswith("{"):
        raw = json.loads(spec)
        if not isinstance(raw, dict):
            raise ValueError("--signal-bounds JSON must be an object")
        parsed = raw
    else:
        parsed = {}
        for chunk in re.split(r"\s*;\s*", spec):
            if not chunk:
                continue
            if "=" in chunk:
                name, value_text = chunk.split("=", 1)
                pieces = re.split(r"[:,\s]+", value_text.strip().strip("[]()"))
            else:
                pieces = chunk.split(":")
                if len(pieces) != 3:
                    raise ValueError(
                        "Text --signal-bounds entries must look like "
                        "x1:0:2;x2:-1000:1000"
                    )
                name, pieces = pieces[0], pieces[1:]
            parsed[name.strip()] = [float(p) for p in pieces if str(p).strip()]

    for name, value in parsed.items():
        if name not in signal_names:
            raise ValueError(f"--signal-bounds has unknown signal {name!r}; signals={list(signal_names)}")
        if isinstance(value, dict):
            lo = float(value.get("min", value.get("lo")))
            hi = float(value.get("max", value.get("hi")))
        elif isinstance(value, (list, tuple)) and len(value) == 2:
            lo = float(value[0])
            hi = float(value[1])
        else:
            raise ValueError(f"Invalid bounds for {name!r}: {value!r}")
        static = name in set(getattr(args, "static_signal_names", []) or [])
        if (lo != hi) if static else (lo >= hi):
            expected = "min == max" if static else "min < max"
            raise ValueError(f"Invalid bounds for {name!r}: [{lo}, {hi}], expected {expected}")
        bounds[name] = (lo, hi)
    return bounds


def parse_derived_signal_definitions(
    text: str,
    signal_names: Sequence[str],
) -> dict[str, dict[str, str]]:
    raw = json.loads(str(text or "{}"))
    if not isinstance(raw, dict):
        raise ValueError("--derived-signal-definitions must be a JSON object")
    signal_set = set(signal_names)
    definitions: dict[str, dict[str, str]] = {}
    for derived, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"Invalid derived definition for {derived!r}: {value!r}")
        definition = {str(key): str(item).strip() for key, item in value.items()}
        if definition.get("op") not in {"subtract", "linear_difference", "linear_margin_subtract",
                                        "abs_difference",
                                        "equals"}:
            raise ValueError(
                f"Unsupported derived operation for {derived!r}: {definition.get('op')!r}"
            )
        names = {str(derived), *referenced_signals(definition)}
        unknown = sorted(names - signal_set)
        if unknown:
            raise ValueError(
                f"Derived definition {derived!r} references unknown signals {unknown}; "
                f"signals={list(signal_names)}"
            )
        definitions[str(derived)] = definition
    return definitions


def matlab_cellstr(values: Sequence[str]) -> str:
    return "{" + ",".join(f"'{_matlab_string(value)}'" for value in values) + "}"


def matlab_derived_signal_definitions(definitions: dict[str, dict[str, str]]) -> str:
    values: list[str] = []
    for derived, definition in sorted(definitions.items()):
        values.extend([derived, json.dumps(definition, separators=(",", ":"))])
    return matlab_cellstr(values)


def make_sample_times(start: float, end: float, step: float) -> list[float]:
    count = int(round((end - start) / step))
    return [round(start + i * step, 10) for i in range(count + 1)]


_INTERVAL_RE = re.compile(r"I=\[\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\]")


def apply_trace_point_cap(args: argparse.Namespace, formula_texts: Sequence[str]) -> dict[str, object]:
    """Coarsen the time grid when it exceeds ``--max-trace-points`` (0 = never).

    dt becomes (time_end - time_start) / max_points, but never more than a quarter of the
    narrowest temporal interval in the formulae. Mutates ``args.time_step`` so MATLAB, the
    monitor and the Java repair all see the same grid. Returns the audit record that
    run_record.json stores under trace_metrics.time_grid.
    """
    original_step = float(args.time_step)
    original_points = int(round((args.time_end - args.time_start) / original_step)) + 1
    info: dict[str, object] = {
        "max_trace_points": int(args.max_trace_points), "original_time_step": original_step,
        "actual_time_step": original_step, "original_points": original_points,
        "actual_points": original_points, "truncated": False, "limited_by_interval": False,
    }
    cap = int(args.max_trace_points)
    if cap <= 0 or original_points <= cap:
        return info
    new_step = (args.time_end - args.time_start) / cap
    widths = [
        float(hi) - float(lo)
        for text in formula_texts for lo, hi in _INTERVAL_RE.findall(text)
        if float(hi) - float(lo) > 0
    ]
    if widths and new_step > min(widths) / 4.0:
        allowed = max(min(widths) / 4.0, original_step)
        print(
            f"warning: dt={format_number(new_step)} needed for --max-trace-points={cap} exceeds "
            f"1/4 of the narrowest temporal interval ({format_number(min(widths))}); "
            f"using dt={format_number(allowed)} instead.",
            flush=True,
        )
        new_step = allowed
        info["limited_by_interval"] = True
    if new_step > original_step:
        args.time_step = new_step
        info.update(
            actual_time_step=new_step,
            actual_points=int(round((args.time_end - args.time_start) / new_step)) + 1,
            truncated=True,
        )
    return info


def effective_round_trace_budget(args: argparse.Namespace) -> int:
    if args.round_trace_budget > 0:
        return args.round_trace_budget
    if args.ceclass_class_limit > 0:
        return max(0, 2 * args.ceclass_class_limit * max(args.ceclass_traces_per_class, 0))
    raise ValueError(
        "When CE-class is enabled and --ceclass-class-limit is 0 (unlimited), "
        "you must provide an explicit --round-trace-budget."
    )


def allocate_trace_budget(total_budget: int, item_count: int) -> list[int]:
    if total_budget <= 0 or item_count <= 0:
        return [0] * max(item_count, 0)
    base = total_budget // item_count
    remainder = total_budget % item_count
    return [base + (1 if i < remainder else 0) for i in range(item_count)]


def allocate_ceclass_side_budgets(
    total_budget: int,
    sat_class_count: int,
    vio_class_count: int,
) -> tuple[list[int], list[int]]:
    """Split CE-class trace budget by SAT/VIO side before class-level spread."""
    if total_budget <= 0:
        return [0] * max(sat_class_count, 0), [0] * max(vio_class_count, 0)
    if sat_class_count <= 0 and vio_class_count <= 0:
        return [], []
    if sat_class_count <= 0:
        return [], allocate_trace_budget(total_budget, vio_class_count)
    if vio_class_count <= 0:
        return allocate_trace_budget(total_budget, sat_class_count), []

    sat_budget = total_budget // 2 + (total_budget % 2)
    vio_budget = total_budget // 2
    return (
        allocate_trace_budget(sat_budget, sat_class_count),
        allocate_trace_budget(vio_budget, vio_class_count),
    )


def fit_record_budget(
    records: list[dict[str, object]],
    target_budget: int,
) -> tuple[list[dict[str, object]], str]:
    if target_budget <= 0:
        return [], "disabled"
    if not records:
        return [], "empty"
    if len(records) == target_budget:
        return records, "exact"
    if len(records) > target_budget:
        return records[:target_budget], f"trimmed:{len(records)}->{target_budget}"
    adjusted = list(records)
    source_count = len(records)
    i = 0
    while len(adjusted) < target_budget:
        adjusted.append(copy.deepcopy(records[i % source_count]))
        i += 1
    return adjusted, f"padded:{source_count}->{target_budget}"


def matlab_vector(values: Sequence[float]) -> str:
    return "[" + " ".join(format_number(v) for v in values) + "]"


def matlab_sample_times(values: Sequence[float]) -> str:
    if len(values) < 3:
        return matlab_vector(values)
    first = float(values[0])
    step = float(values[1]) - first
    if step <= 0:
        return matlab_vector(values)
    tol = max(1e-9, abs(step) * 1e-7)
    for i, value in enumerate(values):
        expected = first + step * i
        if abs(float(value) - expected) > tol:
            return matlab_vector(values)
    return f"{format_number(first)}:{format_number(step)}:{format_number(float(values[-1]))}"


def matlab_domain_bounds(args: argparse.Namespace) -> str:
    parts: list[str] = []
    for signal_name in args.signal_names:
        lo, hi = args.signal_bounds[signal_name]
        parts.append(
            f"'{_matlab_string(signal_name)}',"
            f"[{format_number(lo)} {format_number(hi)}]"
        )
    return "{" + ",".join(parts) + "}"


def snap_to_time_grid(value: float, sample_times: Sequence[float]) -> float:
    if not sample_times:
        return value
    return min(sample_times, key=lambda t: abs(t - value))


# ---------------------------------------------------------------------------
# CSV I/O
# ---------------------------------------------------------------------------

def read_formula_from_csv(path: Path, row_number: int, column: str = "formula") -> str:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if row_number < 1 or row_number > len(rows):
        raise ValueError(f"row {row_number} out of range for {path}")
    value = (rows[row_number - 1].get(column) or "").strip()
    if not value:
        raise ValueError(f"empty formula at row {row_number} in {path}")
    return value


def write_single_formula_csv(path: Path, formula: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["formula"])
        w.writerow([formula])


def read_data_rows(path: Path) -> list[list[float]]:
    rows: list[list[float]] = []
    with path.open(newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            rows.append([float(v) for v in row if v.strip()])
    return rows


def write_data_csv(path: Path, rows: list[list[float]], header: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def snap_domain_trace_rows(args: argparse.Namespace, rows: list[list[float]]) -> list[list[float]]:
    """Force selected signal columns in flattened traces onto configured discrete domains.

    Trace rows are flattened as [s1@t0, s2@t0, ..., s1@t1, s2@t1, ...].
    Snapping is a post-generation data-domain constraint; it does not rewrite the
    STL formula or any numeric constants.
    """
    boolean_indexes = set(getattr(args, "boolean_signal_indexes", []) or [])
    discrete_indexes = set(getattr(args, "discrete_signal_indexes", []) or [])
    static_indexes = set(getattr(args, "static_signal_indexes", []) or [])
    if not rows or (not boolean_indexes and not discrete_indexes and not static_indexes):
        return rows
    stride = len(args.signal_names)
    for row in rows:
        for signal_index in boolean_indexes:
            for column in range(signal_index, len(row), stride):
                value = float(row[column])
                row[column] = 1.0 if abs(value - 1.0) <= abs(value - 0.0) else 0.0
        for signal_index in discrete_indexes:
            signal_name = args.signal_names[signal_index]
            lo, hi = args.signal_bounds[signal_name]
            for column in range(signal_index, len(row), stride):
                rounded = round(float(row[column]))
                row[column] = float(min(max(rounded, int(round(lo))), int(round(hi))))
        for signal_index in static_indexes:
            value = float(args.signal_bounds[args.signal_names[signal_index]][0])
            for column in range(signal_index, len(row), stride):
                row[column] = value
    return rows


def materialize_derived_trace_rows(
    args: argparse.Namespace,
    rows: list[list[float]],
) -> list[list[float]]:
    """Recompute derived columns after sampling/snapping, then verify them.

    ATG receives the same identities as hard SMT constraints.  Recomputing is
    still necessary after interpolation and integer-domain snapping so the CSV
    consumed by MoonLight remains algebraically exact up to floating error.
    """
    definitions = getattr(args, "derived_signal_definitions", {}) or {}
    if not rows or not definitions:
        return rows
    stride = len(args.signal_names)
    indexes = {name: index for index, name in enumerate(args.signal_names)}
    for row_index, row in enumerate(rows):
        if len(row) % stride != 0:
            raise ValueError(
                f"Trace row {row_index} has {len(row)} values, not a multiple of {stride} signals"
            )
        for derived, definition in definitions.items():
            derived_index = indexes[derived]
            for offset in range(0, len(row), stride):
                sample = {name: float(row[offset + index]) for name, index in indexes.items()}
                row[offset + derived_index] = evaluate_derived(definition, sample)
    validate_derived_trace_rows(args, rows)
    return rows


def validate_derived_trace_rows(
    args: argparse.Namespace,
    rows: Sequence[Sequence[float]],
    tolerance: float = 1e-8,
) -> None:
    definitions = getattr(args, "derived_signal_definitions", {}) or {}
    if not definitions:
        return
    stride = len(args.signal_names)
    indexes = {name: index for index, name in enumerate(args.signal_names)}
    for row_index, row in enumerate(rows):
        for derived, definition in definitions.items():
            for sample_index, offset in enumerate(range(0, len(row), stride)):
                actual = float(row[offset + indexes[derived]])
                sample = {name: float(row[offset + index]) for name, index in indexes.items()}
                expected = evaluate_derived(definition, sample)
                scale = max(1.0, abs(actual), abs(expected))
                if abs(actual - expected) > tolerance * scale:
                    raise ValueError(
                        "Derived trace identity failed: "
                        f"row={row_index} sample={sample_index} {derived}={actual:g}, "
                        f"expected {definition['op']}={expected:g}"
                    )


def write_labels_csv(path: Path, labels: Sequence[int | float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(",".join(format_number(v) for v in labels) + "\n")


def read_repair_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not path.exists():
        return rows
    with path.open(newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header: list[str] | None = None
        for row in reader:
            if not row:
                continue
            if row[0] == "iteration":
                header = row
                continue
            if header is not None:
                rows.append(dict(zip(header, row)))
    return rows


REPAIR_BULK_DEBUG_FILES = ("robustness_table.csv", "evidence_report.csv", "temporal_cells.csv")


def prune_repair_bulk(output_dir: Path) -> None:
    """Drop per-candidate debug dumps as soon as the search returns.

    They grow with candidate size and trace count (hundreds of MB per round on
    large instances) and nothing downstream reads them; the selected formula
    (*.repair.csv) and localization_report.csv are kept. Set
    RESPSTL_KEEP_REPAIR_DEBUG=1 to keep them.
    """
    if os.environ.get("RESPSTL_KEEP_REPAIR_DEBUG") == "1":
        return
    for name in REPAIR_BULK_DEBUG_FILES:
        try:
            (output_dir / name).unlink()
        except FileNotFoundError:
            pass


def repair_csv_has_usable_candidate(path: Path) -> bool:
    for row in read_repair_rows(path):
        formula = (row.get("serialized") or "").strip().strip('"')
        if (
            formula
            and "NaN" not in formula
            and "Infinity" not in formula
            and "null" not in formula
        ):
            return True
    return False


def canonicalize_candidate_formula_for_loop(args: argparse.Namespace, formula: str) -> str:
    """Preserve repair candidates before they are inserted into the repair pool."""
    del args
    return formula.strip().strip('"')


def repair_csv_candidate_formula_scores(
    args: argparse.Namespace,
    repair_csv: Path,
) -> list[tuple[str, float]]:
    rows = read_repair_rows(repair_csv)
    formulas: list[tuple[str, float]] = []
    seen: set[str] = set()
    for row in sorted(rows, key=lambda r: float(r.get("fitness") or "inf")):
        raw = (row.get("serialized") or "").strip().strip('"')
        if (
            not raw
            or "NaN" in raw
            or "Infinity" in raw
            or "null" in raw
        ):
            continue
        formula = canonicalize_candidate_formula_for_loop(args, raw)
        if not formula or formula in seen:
            continue
        seen.add(formula)
        try:
            fitness = float(row.get("fitness") or "inf")
        except ValueError:
            fitness = float("inf")
        formulas.append((formula, fitness))
    return formulas


def repair_csv_candidate_formula_scores_with_accuracy(
    args: argparse.Namespace,
    repair_csv: Path,
) -> list[tuple[str, float, float | None]]:
    rows = read_repair_rows(repair_csv)
    formulas: list[tuple[str, float, float | None]] = []
    seen: set[str] = set()
    for row in rows:
        raw = (row.get("serialized") or "").strip().strip('"')
        if (
            not raw
            or "NaN" in raw
            or "Infinity" in raw
            or "null" in raw
        ):
            continue
        formula = canonicalize_candidate_formula_for_loop(args, raw)
        if not formula or formula in seen:
            continue
        seen.add(formula)
        try:
            fitness = float(row.get("fitness") or "inf")
        except ValueError:
            fitness = float("inf")
        try:
            accuracy = float(row.get("accuracy") or "nan")
        except ValueError:
            accuracy = float("nan")
        formulas.append((formula, fitness, accuracy if math.isfinite(accuracy) else None))
    return sorted(
        formulas,
        key=lambda item: (
            not (item[2] is not None and item[2] >= 0.999999),
            item[1],
        ),
    )


def labels_from_eval_csv(path: Path) -> list[int]:
    labels: list[int] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trace = (row.get("trace") or "").strip()
            if not trace or trace.startswith("#"):
                continue
            status = (row.get("status") or "").strip()
            if status not in {"SAT", "VIO"}:
                continue
            labels.append(1 if status == "SAT" else -1)
    return labels


def robustness_from_eval_csv(path: Path) -> list[float]:
    values: list[float] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trace = (row.get("trace") or "").strip()
            if not trace or trace.startswith("#"):
                continue
            raw = (row.get("robustness") or "").strip()
            if not raw:
                continue
            try:
                values.append(float(raw))
            except ValueError:
                values.append(float("nan"))
    return values


def compute_label_categories(
    predicted: Sequence[int],
    oracle: Sequence[int],
) -> dict[str, int]:
    counts = {"sat_sat": 0, "sat_vio": 0, "vio_sat": 0, "vio_vio": 0}
    for pred, gold in zip(predicted, oracle):
        if pred == 1 and gold == 1:
            counts["sat_sat"] += 1
        elif pred == 1 and gold == -1:
            counts["sat_vio"] += 1
        elif pred == -1 and gold == 1:
            counts["vio_sat"] += 1
        elif pred == -1 and gold == -1:
            counts["vio_vio"] += 1
    return counts


def balanced_accuracy(predicted: Sequence[int], oracle: Sequence[int]) -> float:
    if not predicted or not oracle:
        return float("-inf")
    labels = sorted(set(int(v) for v in oracle if int(v) in {-1, 1}))
    if not labels:
        return float("-inf")
    recalls: list[float] = []
    for label in labels:
        total = sum(1 for gold in oracle if int(gold) == label)
        if total == 0:
            continue
        correct = sum(
            1 for pred, gold in zip(predicted, oracle)
            if int(gold) == label and int(pred) == label
        )
        recalls.append(correct / total)
    return sum(recalls) / len(recalls) if recalls else float("-inf")


def candidate_boundary_contrast_scores(
    robustness_by_formula: dict[str, Sequence[float]],
    eps: float = 1.0e-4,
    big: float = 1.0e-2,
) -> dict[str, tuple[float, float]]:
    """Score candidates that are near-boundary where the rest of the pool is far.

    Equivalent to:
        A = abs(R)
        near = A < EPS
        score[i] = sum_t near[i,t] * mean_j!=i(A[j,t] > BIG)

    Returns (score, near_zero_ratio) for each formula.
    """
    formulas = list(robustness_by_formula.keys())
    if not formulas:
        return {}
    trace_count = max((len(robustness_by_formula.get(f, ())) for f in formulas), default=0)
    if trace_count <= 0:
        return {formula: (float("-inf"), 0.0) for formula in formulas}
    abs_values: dict[str, list[float]] = {}
    for formula in formulas:
        values: list[float] = []
        for rho in robustness_by_formula.get(formula, ()):
            try:
                value = abs(float(rho))
            except (TypeError, ValueError):
                value = float("inf")
            if math.isnan(value) or not math.isfinite(value):
                value = float("inf")
            values.append(value)
        if len(values) < trace_count:
            values.extend([float("inf")] * (trace_count - len(values)))
        abs_values[formula] = values[:trace_count]

    out: dict[str, tuple[float, float]] = {}
    for formula in formulas:
        vals = abs_values[formula]
        near_flags = [value < eps for value in vals]
        near_zero_ratio = sum(1 for flag in near_flags if flag) / trace_count
        score = 0.0
        for trace_index, is_near in enumerate(near_flags):
            if not is_near:
                continue
            other_count = 0
            far_count = 0
            for other in formulas:
                if other == formula:
                    continue
                other_count += 1
                if abs_values[other][trace_index] > big:
                    far_count += 1
            far_ratio = (far_count / other_count) if other_count > 0 else 0.0
            score += far_ratio
        out[formula] = (score, near_zero_ratio)
    return out


@dataclass
class EvaluationMetrics:
    predicted: list[int]
    robustness: list[float]
    accuracy: float
    balanced_accuracy: float


def formula_atom_direction_signature(formula: str) -> set[tuple[str, str]]:
    """Return signal/comparator atom directions, ignoring numeric thresholds."""
    atoms: set[tuple[str, str]] = set()
    try:
        for node, _ in epoch_walk_preorder(epoch_parse_stl(formula)):
            if getattr(node, "kind", "") == "atom":
                var = str(getattr(node, "var", ""))
                cmp_op = str(getattr(node, "cmp", ""))
                if var and cmp_op:
                    atoms.add((var, cmp_op))
            elif getattr(node, "kind", "") == "bool":
                var = str(getattr(node, "var", ""))
                if var:
                    atoms.add((var, "bool"))
    except Exception:
        for var, cmp_op in re.findall(r"\b([A-Za-z_]\w*)\s*(<=|>=|==|=|<|>)", formula):
            atoms.add((var, "==" if cmp_op == "=" else cmp_op))
    return atoms


def anchor_atom_coverage_score(formula: str, anchor_atoms: set[tuple[str, str]]) -> tuple[int, float]:
    if not anchor_atoms:
        return 0, 1.0
    candidate_atoms = formula_atom_direction_signature(formula)
    covered = len(anchor_atoms & candidate_atoms)
    return covered, covered / float(len(anchor_atoms))


def _node_structure_signature(node: object) -> str:
    kind = getattr(node, "kind", "")
    if kind == "atom":
        return f"atom:{getattr(node, 'var', '')}:{getattr(node, 'cmp', '')}:#"
    if kind == "bool":
        return f"bool:{getattr(node, 'var', '')}"
    if kind in {"always", "eventually", "until"}:
        return f"{kind}:#:#"
    return str(kind)


def formula_structure_signature(formula: str) -> str:
    try:
        return "|".join(
            _node_structure_signature(node)
            for node, _ in epoch_walk_preorder(epoch_parse_stl(formula))
        )
    except Exception:
        return re.sub(
            r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?",
            "#",
            formula,
        )


def _strictness_direction(comparator: str) -> str | None:
    if comparator in {"<", "<="}:
        return "less"
    if comparator in {">", ">="}:
        return "greater"
    return None


def remove_prediction_neutral_strictness_edits(
    args: argparse.Namespace,
    candidate_formula: str,
    reference_formula: str,
    baseline_predictions: Sequence[int],
    rows: list[list[float]],
    labels: Sequence[int],
    out_dir: Path,
) -> tuple[str, list[int], list[str]]:
    """Restore prior strictness when doing so leaves every prediction unchanged."""
    try:
        reference_root = epoch_parse_stl(reference_formula)
        candidate_root = epoch_parse_stl(candidate_formula)
    except Exception:
        return candidate_formula, list(baseline_predictions), []

    reference_comparators: dict[tuple[str, float, str], set[str]] = {}
    for node, _path in epoch_walk_preorder(reference_root):
        if getattr(node, "kind", "") != "atom":
            continue
        comparator = str(getattr(node, "cmp", ""))
        direction = _strictness_direction(comparator)
        if direction is None:
            continue
        key = (
            str(getattr(node, "var", "")),
            round(float(getattr(node, "value", 0.0)), 12),
            direction,
        )
        reference_comparators.setdefault(key, set()).add(comparator)

    accepted_predictions = list(baseline_predictions)
    reverted: list[str] = []
    probe_index = 0
    for node, path in epoch_walk_preorder(candidate_root):
        if getattr(node, "kind", "") != "atom":
            continue
        comparator = str(getattr(node, "cmp", ""))
        direction = _strictness_direction(comparator)
        if direction is None:
            continue
        key = (
            str(getattr(node, "var", "")),
            round(float(getattr(node, "value", 0.0)), 12),
            direction,
        )
        prior_choices = reference_comparators.get(key, set())
        if len(prior_choices) != 1:
            continue
        prior_comparator = next(iter(prior_choices))
        if prior_comparator == comparator:
            continue

        node.cmp = prior_comparator
        probe_formula = bustle_print(candidate_root)
        try:
            probe_predictions, _probe_accuracy = evaluate_round_formula(
                args,
                probe_formula,
                rows,
                labels,
                out_dir,
                f"strictness_revert_{probe_index:02d}",
            )
        except subprocess.CalledProcessError:
            node.cmp = comparator
            probe_index += 1
            continue
        probe_index += 1
        if tuple(probe_predictions) == tuple(accepted_predictions):
            reverted.append(
                f"{path}:{key[0]} {comparator} {key[1]:g} -> "
                f"{key[0]} {prior_comparator} {key[1]:g}"
            )
            accepted_predictions = probe_predictions
        else:
            node.cmp = comparator

    if not reverted:
        return candidate_formula, accepted_predictions, []
    return bustle_print(candidate_root), accepted_predictions, reverted


def structure_phase_status(
    pool_eval_rows: list[dict[str, str]],
    pool_predictions: dict[str, tuple[int, ...]],
    top_n: int,
) -> tuple[bool, str, int]:
    ranked_rows = sorted(
        [
            row for row in pool_eval_rows
            if row.get("balanced_accuracy") not in {"", None}
        ],
        key=lambda row: (
            float(row.get("balanced_accuracy") or "-inf"),
            float(row.get("accuracy") or "-inf"),
            -int(row.get("formula_size") or "0"),
            -int(row.get("first_round") or "0"),
        ),
        reverse=True,
    )
    top_rows = ranked_rows[:max(1, top_n)]
    if not top_rows:
        return False, "no_ranked_candidates", 0
    signatures = {
        formula_structure_signature(row["formula"])
        for row in top_rows
    }
    if len(signatures) == 1:
        prediction_vectors = {
            pool_predictions[row["formula"]]
            for row in top_rows
            if row["formula"] in pool_predictions
        }
        if len(top_rows) > 1 and len(prediction_vectors) == 1:
            return True, "top_candidates_single_structure_same_predictions", len(top_rows)
        return True, "top_candidates_single_structure", len(top_rows)
    return False, f"top_structures={len(signatures)}", len(top_rows)


def formula_size(formula: str) -> int:
    return len(
        re.findall(
            r"\b(?:AND|OR|NOT|GLOBALLY|EVENTUALLY|UNTIL)\b|"
            r"[A-Za-z_]\w*\s*(?:<=|>=|==|=|<|>)",
            formula,
        )
    )


def formula_contains_until(formula: str) -> bool:
    try:
        return any(
            getattr(node, "kind", "") == "until"
            for node, _ in epoch_walk_preorder(epoch_parse_stl(formula))
        )
    except Exception:
        return bool(
            re.search(r"\bUNTIL\b", formula)
            or re.search(r"\bU\s*\[", formula)
            or re.search(r"\buntil(?:_|\b)", formula, flags=re.IGNORECASE)
        )


def format_category_counts(counts: dict[str, int]) -> str:
    return (
        f"SAT/SAT={counts.get('sat_sat', 0)};"
        f"SAT/VIO={counts.get('sat_vio', 0)};"
        f"VIO/SAT={counts.get('vio_sat', 0)};"
        f"VIO/VIO={counts.get('vio_vio', 0)}"
    )


def prefix_and(parts: Sequence[str]) -> str:
    items = [part for part in parts if part]
    if not items:
        return "TRUE"
    result = items[0]
    for item in items[1:]:
        result = f"AND[{result},{item}]"
    return result


def prefix_or(parts: Sequence[str]) -> str:
    items = [part for part in parts if part]
    if not items:
        return "FALSE"
    result = items[0]
    for item in items[1:]:
        result = f"OR[{result},{item}]"
    return result


def repair_grammar_template(args: argparse.Namespace) -> Path:
    multi_signal = len(args.signal_names) > 1
    stem = "grammar_multisignal_seeded" if multi_signal else "grammar_seeded"
    path = ROOT / "grammars" / f"{stem}.bnf"
    if path.exists():
        return path
    raise FileNotFoundError(f"No repair grammar template found for {stem}")


def write_dynamic_repair_grammar(args: argparse.Namespace, output_root: Path) -> str:
    template = repair_grammar_template(args)
    text = template.read_text()
    variable_rule = "<num_var> ::= " + " | ".join(args.signal_names)
    if re.search(r"^<num_var>\s*::=.*$", text, flags=re.MULTILINE):
        text = re.sub(r"^<num_var>\s*::=.*$", variable_rule, text, flags=re.MULTILINE)
    else:
        text = text.rstrip() + "\n" + variable_rule + "\n"
    bool_names = list(getattr(args, "boolean_signal_names", []) or [])
    if bool_names:
        bool_rule = "<bool_var> ::= " + " | ".join(bool_names)
        if re.search(r"^<bool_var>\s*::=.*$", text, flags=re.MULTILINE):
            text = re.sub(r"^<bool_var>\s*::=.*$", bool_rule, text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + "\n" + bool_rule + "\n"
    else:
        text = re.sub(r"\s*\|\s*<bool_prop>", "", text)
        text = re.sub(r"^<bool_prop>\s*::=.*\n?", "", text, flags=re.MULTILINE)
        text = re.sub(r"^<bool_var>\s*::=.*\n?", "", text, flags=re.MULTILINE)
    grammar_dir = output_root / "_generated_grammar"
    grammar_dir.mkdir(parents=True, exist_ok=True)
    signal_slug = "_".join(args.signal_names)
    if len(signal_slug) > 120:
        digest = hashlib.sha256(signal_slug.encode("utf-8")).hexdigest()[:16]
        signal_slug = signal_slug[:80].rstrip("_") + "_" + digest
    grammar_name = "grammar_" + signal_slug + ".bnf"
    grammar_path = grammar_dir / grammar_name
    grammar_path.write_text(text)
    return str(grammar_path)


# ---------------------------------------------------------------------------
# MATLAB trace generation
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Oracle labelling and replay with RTAMT
# ---------------------------------------------------------------------------

def evaluate_formula_labels(
    args: argparse.Namespace,
    formula: str,
    dataset_dir: Path,
    output_csv: Path,
) -> list[int]:
    run_evaluator(args, formula, dataset_dir, output_csv, capture=False)
    if args.dry_run:
        return []
    return labels_from_eval_csv(output_csv)


def _require_compiled_evaluator() -> None:
    """Fail loudly if target/classes lacks the Java evaluator (run `mvn compile`)."""
    cls = ROOT / "target" / "classes" / "it" / "units" / "malelab" / "learningstl" / "EvaluateFormulaOnDataset.class"
    if not cls.exists():
        raise RuntimeError(
            f"Java evaluator not compiled ({cls} missing). Run `mvn compile` with JDK>=14 "
            "before starting experiments; refusing to continue (would be recorded as GENERATION_FAILURE)."
        )


def run_evaluator(
    args: argparse.Namespace,
    formula: str,
    dataset_dir: Path,
    output_csv: Path,
    capture: bool = False,
) -> subprocess.CompletedProcess[str] | None:
    cmd = []
    append_macos_arch_prefix(cmd, args.arch)
    cmd.extend([
        args.java,
        "-cp", args.classpath,
        "it.units.malelab.learningstl.EvaluateFormulaOnDataset",
        f"dataset={args.dataset_name}",
        f"dataPath={dataset_dir}",
        "strictLengthCheck=false",
        "shortSignalPolicy=penalty",
        "normalizeValues=false",
        "renumberTimes=false",
        f"formula={formula}",
        f"output={output_csv}",
    ])
    if args.dry_run:
        print("$", " ".join(_shell_quote(c) for c in cmd), flush=True)
        return None
    _require_compiled_evaluator()
    if capture:
        return run(cmd, ROOT, dry_run=False, capture=True)
    run(cmd, ROOT, dry_run=False)
    return None


# ---------------------------------------------------------------------------
# BUSTLE repair runner
# ---------------------------------------------------------------------------

LLM_CE_STATE: dict[str, object] = {}


def run_llm_repair_round(
    args: argparse.Namespace,
    round_index: int,
    attempt_index: int,
    parent_formula: str,
    rows: list[list[float]],
    labels: Sequence[int],
    round_dir: Path,
    oracle_formula: str,
    instance_id: str,
) -> Path:
    """LLM-CE: the repair step of one round. Writes the same repair_output/*.repair.csv the Java search writes,
    so pool evaluation, disambiguation and hypothesis selection are the shared code path."""
    from restless.baselines import llm_ce
    output_dir = round_dir / f"repair_attempt_{attempt_index:02d}" / "repair_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir.parent / "search_started.json").write_text(json.dumps({
        "round": round_index, "attempt": attempt_index, "Q": query_count(args), "backend": "llm",
    }) + "\n")
    out_root = resolve_path(args.output_root)
    ctx = LLM_CE_STATE.get("ctx")
    if ctx is None:
        cfg = llm_ce.load_config(os.environ.get("LLM_CE_CONFIG") or resolve_path(args.llm_ce_config))   # env: tests only
        names = llm_ce.name_map(instance_id)   # the spelling the instance's formulas use for every signal
        ctx = llm_ce.Context(
            cfg=cfg, requirement=llm_ce.load_requirement(instance_id, cfg),
            signal_names=list(dict.fromkeys([names.get(n, n) for n in args.signal_names]
                                            + list(llm_ce.derived_signals(instance_id)))),
            signal_bounds={names.get(k, k): v for k, v in dict(args.signal_bounds).items()},
            boolean_signals=[names.get(n, n) for n in args.boolean_signal_names],
            times=[float(t) for t in args.sample_times], references=[oracle_formula],
            run_seed=int(args.seed_base), log_path=out_root / "llm_log.jsonl",
            aliases=llm_ce.instance_aliases(instance_id), derived=llm_ce.derived_signals(instance_id))
        LLM_CE_STATE["ctx"] = ctx
    monitor_dir = round_dir / f"llm_ce_monitor_{attempt_index:02d}"
    build_eval_dataset(args, monitor_dir / "dataset", rows, labels)
    cache: dict[str, list[int]] = {}

    def monitor(formulas: Sequence[str]) -> dict[str, list[int]]:
        todo = [f for f in dict.fromkeys(formulas) if f not in cache]
        for index, formula in enumerate(todo):
            csv_path = monitor_dir / f"m_{len(cache) + index:04d}.csv"
            try:
                cache[formula] = evaluate_formula_labels(args, formula, monitor_dir / "dataset", csv_path)
            except subprocess.CalledProcessError:
                continue   # the evaluator rejects it: not a usable candidate
        return {f: cache[f] for f in formulas if f in cache}

    started = time.time()
    try:
        items, _fits = llm_ce.llm_ce_repair(ctx, parent_formula, rows, labels, monitor, round_index)
    finally:
        RUN_TIMINGS["search"] += time.time() - started   # repair time, like the Java search of the other methods
        llm_ce.write_summary(out_root / "llm_ce_summary.json", ctx)
    llm_ce.write_repair_csv(output_dir / "llm.repair.csv", items, len(labels), attempt_index)
    return output_dir / "llm.repair.csv"


def run_repair_round(
    args: argparse.Namespace,
    round_index: int,
    attempt_index: int,
    formula_csv: Path,
    dataset_dir: Path,
    round_dir: Path,
) -> Path:
    output_dir = round_dir / f"repair_attempt_{attempt_index:02d}" / "repair_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    # Written before Java starts: the dataset is frozen for this attempt, so this is the
    # Q of every candidate the attempt evaluates, and proof the search was launched.
    (output_dir.parent / "search_started.json").write_text(json.dumps({
        "round": round_index, "attempt": attempt_index, "Q": query_count(args),
    }) + "\n")
    seed = args.seed_base + (round_index - 1) * 100 + attempt_index
    cmd: list[str] = []
    append_macos_arch_prefix(cmd, args.arch)
    cmd.extend([
        args.java, "-cp", args.classpath,
        "it.units.malelab.learningstl.Main",
        f"seed={seed}",
        f"dataset={args.dataset_name}",
        f"dataPath={dataset_dir}",
        f"output=./{output_dir.relative_to(ROOT)}/",
        "mod=supervised",
        f"local={args.local}",
        "evolver=repair",
        "fitness=moonlight",
        "moonlightNormalizeValues=false",
        f"formula={formula_csv}",
        "formulaColumn=formula",
        "formulaRow=1",
        f"repairMode={args.repair_mode}",
        f"repairLocalization={args.repair_localization}",
        f"repairLocalizationTopK={max(1, args.repair_localization_top_k)}",
        f"repairLocalizationTopKRatio={max(0.0, args.repair_localization_top_k_ratio)}",
        f"repairLocalizationTypePriority={str(args.type_priority).lower()}",
        f"repairLocalizationStagnationWindow={max(1, args.repair_localization_stagnation_window)}",
        f"repairLocalizationExpansionStep={max(1, args.repair_localization_expansion_step)}",
        f"repairLocalizationUcbBeta={max(0.0, args.repair_localization_ucb_beta)}",
        f"repairLocalizationTemporalCellLimit={max(1, args.repair_localization_self_coefficient_cap)}",
        f"repairLocalizationEvidenceWeight={max(0.0, args.repair_localization_evidence_weight)}",
        f"repairLocalizationMuseWeight={max(0.0, args.repair_localization_muse_weight)}",
        f"repairLocalizationSizeWeight={max(0.0, args.repair_localization_size_weight)}",
        f"repairLocalizationBrokenLambda={max(0.0, args.repair_localization_broken_lambda)}",
        f"repairLocalizationRewardWeight={max(0.0, args.repair_localization_reward_weight)}",
        f"repairLocalizationRewardClip={max(0.0, args.repair_localization_reward_clip)}",
        f"repairLocalizationFitnessGainWeight={max(0.0, args.repair_localization_fitness_gain_weight)}",
        f"repairLocalizationReport={output_dir / 'localization_report.csv'}",
        f"repairTraceMetadata={dataset_dir / 'trace_metadata.csv'}",
        f"repairIterations={args.repair_iterations}",
        f"repairPopulation={args.repair_population}",
        f"repairParameterGaRatio={args.repair_parameter_ga_ratio}",
        f"repairRefitParameters={str(args.repair_parameter_refit).lower()}",
        f"repairStopOnPerfect={str(args.repair_stop_on_perfect).lower()}",
        f"repairMultiSolutionMinTraces={max(0, args.multi_solution_min_traces)}",
        f"repairParameterBudget={max(1, args.repair_parameter_budget)}",
        f"repairTimeStep={args.time_step}",
        f"repairEditMaxBudget={max(1, args.repair_edit_max_budget)}",
        f"repairParentSelection={args.repair_parent_selection}",
        f"repairMinComplexityRatio={args.repair_min_complexity_ratio}",
        f"iterations={args.repair_iterations}",
        f"population={args.repair_population}",
        f"threads={args.threads}",
    ])
    grammar_path = args.grammar_path or getattr(args, "generated_grammar_path", "")
    if grammar_path:
        cmd.append(f"grammarPath={grammar_path}")
    if args.disable_repair_until:
        cmd.extend([
            "grammarVariant=no_until",
            "disableRepairUntil=true",
        ])
    repair_csv = output_dir / f"{seed}.repair.csv"

    def _run_repair(local_value: str) -> None:
        local_cmd = [
            f"local={local_value}" if item.startswith("local=") else item
            for item in cmd
        ]
        run(local_cmd, ROOT, dry_run=args.dry_run)

    if args.local == "true":
        try:
            _run_repair("true")
        except subprocess.CalledProcessError:
            print(
                "    local=true repair failed; retrying this attempt with local=false.",
                flush=True,
            )
            _run_repair("false")
        else:
            if not args.dry_run and not repair_csv_has_usable_candidate(repair_csv):
                print(
                    "    local=true repair produced no usable candidates; retrying this attempt with local=false.",
                    flush=True,
                )
                _run_repair("false")
    else:
        _run_repair(args.local)
    prune_repair_bulk(output_dir)
    return repair_csv


@dataclass
class StructuralClass:
    formula_node: object
    parser: object
    simplified_id: str
    class_type: str
    formula_dialect: str = "bustle"


def negate_prefix_formula(formula: str) -> str:
    return f"NOT[{formula}]"


def _strip_outer_parens(text: str) -> str:
    result = text.strip()
    while result.startswith("(") and result.endswith(")"):
        depth = 0
        close = -1
        for i, ch in enumerate(result):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close != len(result) - 1:
            break
        result = result[1:-1].strip()
    return result


def _split_top_level_infix(text: str, operator: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    token = f" {operator} "
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in ("(", "["):
            depth += 1
        elif ch in (")", "]"):
            depth -= 1
        elif depth == 0 and text.startswith(token, i):
            parts.append(text[start:i].strip())
            i += len(token)
            start = i
            continue
        i += 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


_BREACH_TEMPORAL_RE = re.compile(r"^(?P<op>ev_|alw_)\[(?P<interval>[^\]]+)\]\s*(?P<child>.+)$")
_BREACH_ATOM_RE = re.compile(
    r"^\(?\s*(?P<var>[A-Za-z_]\w*\[t\])\s*(?P<op><=|>=|==|=|<|>)\s*"
    r"(?P<value>-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)\s*\)?$"
)


def negate_breach_atom(text: str) -> str:
    expr = _strip_outer_parens(text)
    match = _BREACH_ATOM_RE.fullmatch(expr)
    if not match:
        return f"(not {expr})"
    op = match.group("op")
    if op == "=":
        op = "=="
    var = match.group("var")
    value = match.group("value")
    if op == "==":
        return f"(({var} < {value}) or ({var} > {value}))"
    flipped = {
        "<": ">=",
        "<=": ">",
        ">": "<=",
        ">=": "<",
    }.get(op)
    if flipped is None:
        return f"(not {expr})"
    return f"({var} {flipped} {value})"


def breach_to_nnf(text: str, neg: bool = False) -> str:
    expr = _strip_outer_parens(text)
    if expr.startswith("not "):
        return breach_to_nnf(expr[4:].strip(), not neg)
    temporal = _BREACH_TEMPORAL_RE.match(expr)
    if temporal:
        op = temporal.group("op")
        interval = temporal.group("interval")
        child = temporal.group("child").strip()
        if neg:
            flipped = "alw_" if op == "ev_" else "ev_"
            return f"({flipped}[{interval}] {breach_to_nnf(child, True)})"
        return f"({op}[{interval}] {breach_to_nnf(child, False)})"
    and_parts = _split_top_level_infix(expr, "and")
    if len(and_parts) > 1:
        mapped = [breach_to_nnf(part, neg) for part in and_parts]
        joiner = " or " if neg else " and "
        return "(" + joiner.join(mapped) + ")"
    or_parts = _split_top_level_infix(expr, "or")
    if len(or_parts) > 1:
        mapped = [breach_to_nnf(part, neg) for part in or_parts]
        joiner = " and " if neg else " or "
        return "(" + joiner.join(mapped) + ")"
    if neg:
        return negate_breach_atom(expr)
    atom = _BREACH_ATOM_RE.fullmatch(expr)
    if atom:
        op = atom.group("op")
        if op in {"=", "=="}:
            var = atom.group("var")
            value = atom.group("value")
            return f"(({var} >= {value}) and ({var} <= {value}))"
    return expr


def _negate_numeric_atom(var: str, cmp_op: str, value: float) -> str:
    if cmp_op in {"=", "=="}:
        value_text = format_number(float(value))
        return f"OR[{var} < {value_text},{var} > {value_text}]"
    replacement = {
        "<": ">=",
        "<=": ">",
        ">": "<=",
        ">=": "<",
    }.get(cmp_op)
    if replacement is None:
        return f"NOT[{var} {cmp_op} {format_number(float(value))}]"
    return f"{var} {replacement} {format_number(float(value))}"


def _eval_numeric_bool_atom(sample: float, cmp_op: str, value: float) -> bool:
    if cmp_op == "<":
        return sample < value
    if cmp_op == "<=":
        return sample <= value
    if cmp_op == ">":
        return sample > value
    if cmp_op == ">=":
        return sample >= value
    if cmp_op in {"=", "=="}:
        return abs(sample - value) <= 1e-12
    if cmp_op == "!=":
        return abs(sample - value) > 1e-12
    return False


def _boolean_domain_atom(var: str) -> str:
    return f"OR[AND[{var} >= 0,{var} <= 0],AND[{var} >= 1,{var} <= 1]]"


def _boolean_exact_atom(var: str, value: float) -> str:
    value_text = format_number(float(value))
    return f"AND[{var} >= {value_text},{var} <= {value_text}]"


def _boolean_numeric_atom_constraint(var: str, cmp_op: str, value: float, negated: bool) -> str:
    sat0 = _eval_numeric_bool_atom(0.0, cmp_op, value)
    sat1 = _eval_numeric_bool_atom(1.0, cmp_op, value)
    if negated:
        sat0 = not sat0
        sat1 = not sat1
    if sat0 and sat1:
        return _boolean_domain_atom(var)
    if sat0:
        return _boolean_exact_atom(var, 0.0)
    if sat1:
        return _boolean_exact_atom(var, 1.0)
    return f"AND[{var} >= 1,{var} <= 0]"


def _bool_atoms_as_numeric_constraints(formula: str, boolean_signal_names: Sequence[str]) -> str:
    bool_names = set(boolean_signal_names)

    def serialize(node: ASTNode, negated: bool = False) -> str:
        if node.kind == "bool":
            if node.var in bool_names:
                return _boolean_exact_atom(str(node.var), 0.0 if negated else 1.0)
            return f"NOT[{node.var}]" if negated else str(node.var)
        if node.kind == "atom":
            if node.var in bool_names:
                return _boolean_numeric_atom_constraint(
                    str(node.var),
                    str(node.cmp),
                    float(node.value),
                    negated,
                )
            if negated:
                return _negate_numeric_atom(str(node.var), str(node.cmp), float(node.value))
            if str(node.cmp) in {"=", "=="}:
                return _boolean_exact_atom(str(node.var), float(node.value))
            return bustle_print(node)
        if node.kind == "not":
            return serialize(node.child, not negated)
        if node.kind in {"and", "or"}:
            op = node.kind
            if negated:
                op = "or" if op == "and" else "and"
            rendered = [serialize(node.left, negated), serialize(node.right, negated)]
            prefix = "AND" if op == "and" else "OR"
            return f"{prefix}[{rendered[0]},{rendered[1]}]"
        if node.kind == "implies":
            rendered = f"IMPLIES[{serialize(node.left, False)},{serialize(node.right, False)}]"
            return f"NOT[{rendered}]" if negated else rendered
        if node.kind in {"always", "eventually"}:
            kind = node.kind
            child_negated = negated
            if negated:
                kind = "eventually" if kind == "always" else "always"
            op = "GLOBALLY" if kind == "always" else "EVENTUALLY"
            return (
                f"{op} I=[{format_number(float(node.a))},{format_number(float(node.b))}]"
                f"[{serialize(node.child, child_negated)}]"
            )
        if node.kind == "until":
            rendered = (
                f"UNTIL I=[{format_number(float(node.a))},{format_number(float(node.b))}]"
                f"[{serialize(node.left, False)},{serialize(node.right, False)}]"
            )
            return f"NOT[{rendered}]" if negated else rendered
        return bustle_print(node)

    try:
        return serialize(epoch_parse_stl(formula), False)
    except Exception:
        return formula


def prepare_generation_formula(
    formula: str,
    formula_dialect: str,
    boolean_signal_names: Sequence[str] = (),
    one_hot_groups: dict[str, list[str]] | None = None,
    time_start: float = 0.0,
    time_end: float = 0.0,
) -> str:
    if formula_dialect == "breach":
        prepared = breach_to_nnf(formula)
    else:
        prepared = _bool_atoms_as_numeric_constraints(formula, boolean_signal_names)
    group_constraints: list[str] = []
    for names in (one_hot_groups or {}).values():
        at_least_one = prefix_or([_boolean_exact_atom(name, 1.0) for name in names])
        pairwise = [
            f"NOT[AND[{_boolean_exact_atom(left, 1.0)},{_boolean_exact_atom(right, 1.0)}]]"
            for index, left in enumerate(names)
            for right in names[index + 1:]
        ]
        exact_one = prefix_and([at_least_one, *pairwise])
        group_constraints.append(
            f"GLOBALLY I=[{format_number(time_start)},{format_number(time_end)}][{exact_one}]"
        )
    return prefix_and([prepared, *group_constraints]) if group_constraints else prepared


def is_degenerate_ceclass_formula(formula: str) -> bool:
    text = formula.strip().lower()
    compact = text.replace(" ", "")
    return (
        not text
        or text in {"true", "false"}
        or compact in {"inf<0", "inf>0", "0<inf", "0>inf", "-inf<0", "-inf>0", "0<-inf", "0>-inf"}
        or ("inf" in compact and "[t]" not in compact and "x" not in compact)
    )


def _same_formula_node(left: ASTNode, right: ASTNode) -> bool:
    try:
        return bustle_print(left) == bustle_print(right)
    except Exception:
        return False


def _frontier_ast_targets(node: ASTNode, *, limit: int) -> list[ASTNode]:
    if limit <= 0:
        limit = 1

    if node.kind == "or":
        out: list[ASTNode] = []
        for child in (node.left, node.right):
            for target in _frontier_ast_targets(child, limit=limit):
                out.append(copy.deepcopy(target))
                if len(out) >= limit:
                    return out
        return out or [copy.deepcopy(node)]

    if node.kind == "and":
        left_targets = _frontier_ast_targets(node.left, limit=limit)
        right_targets = _frontier_ast_targets(node.right, limit=limit)
        if (
            len(left_targets) == 1
            and len(right_targets) == 1
            and _same_formula_node(left_targets[0], node.left)
            and _same_formula_node(right_targets[0], node.right)
        ):
            return [copy.deepcopy(node)]
        out: list[ASTNode] = []
        for left in left_targets:
            for right in right_targets:
                out.append(
                    ASTNode(
                        kind="and",
                        text="",
                        left=copy.deepcopy(left),
                        right=copy.deepcopy(right),
                    )
                )
                if len(out) >= limit:
                    return out
        return out or [copy.deepcopy(node)]

    if node.kind in {"eventually", "always"}:
        child_targets = _frontier_ast_targets(node.child, limit=limit)
        if len(child_targets) == 1 and _same_formula_node(child_targets[0], node.child):
            return [copy.deepcopy(node)]
        return [
            ASTNode(
                kind=node.kind,
                text="",
                a=float(node.a),
                b=float(node.b),
                child=copy.deepcopy(child),
            )
            for child in child_targets[:limit]
        ]

    return [copy.deepcopy(node)]


def ceclass_generation_targets(formula: str, args: argparse.Namespace) -> list[str]:
    if getattr(args, "ceclass_generation_targets", "frontier") == "class":
        return [formula]
    try:
        root = epoch_parse_stl(formula)
        targets = _frontier_ast_targets(
            root,
            limit=max(1, int(getattr(args, "ceclass_frontier_limit_per_class", 4))),
        )
    except Exception:
        return [formula]

    out: list[str] = []
    seen: set[str] = set()
    for target in targets:
        text = bustle_print(target)
        if is_degenerate_ceclass_formula(text) or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out or [formula]


def harden_class_generation_formula(formula: str, args: argparse.Namespace) -> str:
    margin = max(0.0, float(getattr(args, "class_generation_margin", 0.0)))
    if margin <= 0.0:
        return formula
    try:
        root = epoch_parse_stl(formula)
    except Exception:
        return formula

    def visit(node: ASTNode) -> None:
        if node.kind == "atom":
            if str(node.var) in set(getattr(args, "boolean_signal_names", [])):
                return
            if node.cmp in {">", ">="}:
                node.value = float(node.value) + margin
            elif node.cmp in {"<", "<="}:
                node.value = float(node.value) - margin
            return
        if node.kind == "not":
            visit(node.child)
        elif node.kind in {"always", "eventually"}:
            visit(node.child)
        elif node.kind in {"and", "or", "implies", "until"}:
            visit(node.left)
            visit(node.right)

    visit(root)
    return bustle_print(root)


def enumerate_ceclass_classes(
    args: argparse.Namespace,
    formula_str: str,
    temporal_split: int,
    class_type: str,
    class_limit: int,
    out_dir: Path,
) -> list[StructuralClass]:
    mode = "positive" if class_type == "SAT_CLASS" else "negative"
    out_dir.mkdir(parents=True, exist_ok=True)
    formula_csv = out_dir / f"{class_type.lower()}_input_formula.csv"
    # No NNF normalization. Pushing negations inward rewrites NOT[AND[a, NOT[b]]]
    # into OR[NOT[a], b] and then dissolves it further, so the implication - and
    # with it the guard that says when the consequent is even on the hook -
    # never reaches CE-class. The formula goes in exactly as written.
    write_single_formula_csv(formula_csv, formula_str)
    (out_dir / f"{class_type.lower()}_input_formula_original.txt").write_text(formula_str + "\n")
    output_stem = class_type.lower()
    classes_csv = out_dir / f"{output_stem}_all_structural_classes.csv"
    run(
        [
            sys.executable,
            "-m", "restless.ceclass.c534_adapter",
            "--formula-csv", str(formula_csv),
            "--formula-row", "1",
            "--mode", mode,
            "--k", str(temporal_split),
            "--time-step", str(args.time_step),
            "--signals", ",".join(args.signal_names),
            "--class-limit", str(class_limit),
            "--output-csv", str(classes_csv),
            "--summary-csv", str(out_dir / f"{output_stem}_inputs_summary.csv"),
        ],
        ROOT,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        return []
    if not classes_csv.exists():
        print(f"  CEclass C534 produced no class CSV: {classes_csv}", flush=True)
        return []

    classes: list[StructuralClass] = []
    with classes_csv.open(newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bustle_formula = (row.get("formula_bustle_concrete") or "").strip()
            formula = (
                bustle_formula
                or row.get("formula_breach_concrete")
                or row.get("formula_breach")
                or row.get("formula")
                or ""
            ).strip()
            class_id = (row.get("class_id") or f"{class_type}_{len(classes)}").strip()
            if is_degenerate_ceclass_formula(formula):
                continue
            formula_dialect = row.get("formula_dialect") or ("bustle" if bustle_formula else "breach")
            targets = ceclass_generation_targets(formula, args)
            for target_index, target_formula in enumerate(targets):
                target_id = class_id if len(targets) == 1 else f"{class_id}::frontier_{target_index:02d}"
                classes.append(
                    StructuralClass(
                        formula_node=target_formula,
                        parser=None,
                        simplified_id=target_id,
                        class_type=class_type,
                        formula_dialect=formula_dialect,
                    )
                )
                if class_limit > 0 and len(classes) >= class_limit:
                    break
            if class_limit > 0 and len(classes) >= class_limit:
                break
    print(
        f"  CEclass C534 {class_type}: selected {len(classes)} "
        f"{args.ceclass_generation_targets} targets from {classes_csv}",
        flush=True,
    )
    return classes


def choose_uniform_theta(
    formula_node: object,
    parser: object,
    sample_times: Sequence[float],
) -> list[dict[str, float]]:
    if parser is None or isinstance(formula_node, str):
        return [{}]
    param_names = list(dict.fromkeys(formula_node.get_param_names()))
    if not param_names:
        return [{}]
    groups: dict[str, list[tuple[int, str]]] = {}
    for name in param_names:
        if "____t" in name:
            base, suffix = name.split("____t", 1)
            try:
                order = int(suffix)
            except ValueError:
                order = 2
        else:
            base = name
            order = 2
        groups.setdefault(base, []).append((order, name))

    equal_split: dict[str, float] = {}
    sample_grid: dict[str, float] = {}
    for entries in groups.values():
        entries.sort()
        max_order = max(order for order, _ in entries)
        first_name = entries[0][1]
        t_start, t_end = parser.interval_dict.get(first_name, parser.interval_dict.get(entries[0][1], (0.0, 1.0)))
        for order, name in entries:
            equal_ratio = (order - 1) / max(max_order, 1)
            sample_ratio = order / (max_order + 1)
            equal_value = float(t_start) + equal_ratio * (float(t_end) - float(t_start))
            sample_value = float(t_start) + sample_ratio * (float(t_end) - float(t_start))
            equal_split[name] = round(snap_to_time_grid(equal_value, sample_times), 10)
            sample_grid[name] = round(snap_to_time_grid(sample_value, sample_times), 10)

    theta_list = [equal_split]
    if sample_grid != equal_split:
        theta_list.append(sample_grid)
    return theta_list


def instantiate_structural_class(structural_class: StructuralClass, params: dict[str, float]) -> str:
    if structural_class.parser is None or isinstance(structural_class.formula_node, str):
        return str(structural_class.formula_node)
    raise TypeError("Unsupported in-memory CE-class representation.")


class MatlabServer:
    """One long-lived MATLAB process (``atg_server.m``) that runs ATG job scripts.

    Replaces one ``matlab -batch`` start-up per CE class by a single start-up per
    process. Jobs are the unchanged run_atg.m scripts; only their failure exit
    changes (``error`` instead of ``exit(1)``). Same exceptions as ``run()``.
    """

    READY_TIMEOUT_S = 300.0

    def __init__(self, matlab: str, cwd: Path) -> None:
        self.matlab = matlab
        self.cwd = cwd
        self.process: subprocess.Popen | None = None
        self.job_dir: Path | None = None
        self.job_counter = 0

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self) -> None:
        self.stop()
        self.job_dir = Path(tempfile.mkdtemp(prefix="atg_server_"))
        genex = ROOT / "stl-trace-generation" / "GenExTraces"
        command = f"cd('{_matlab_string(genex)}'); atg_server('{_matlab_string(self.job_dir)}')"
        started = time.perf_counter()
        log_path = self.job_dir / "server.log"
        with log_path.open("wb") as server_log:
            self.process = subprocess.Popen(
                [*matlab_launch_prefix(self.matlab), "-batch", command], cwd=self.cwd,
                stdout=server_log, stderr=subprocess.STDOUT,
            )
        deadline = started + self.READY_TIMEOUT_S
        while not (self.job_dir / "ready.flag").exists():
            if self.process.poll() is not None or time.perf_counter() > deadline:
                global LAST_MATLAB_ERROR_TEXT
                try:
                    LAST_MATLAB_ERROR_TEXT = log_path.read_text(errors="replace")[-4000:]
                except OSError:
                    LAST_MATLAB_ERROR_TEXT = ""
                if LAST_MATLAB_ERROR_TEXT.strip():
                    print("    MATLAB output before it stopped:\n" + "\n".join(
                        "      " + line for line in LAST_MATLAB_ERROR_TEXT.strip().splitlines()[-12:]), flush=True)
                self.stop()
                raise subprocess.CalledProcessError(1, [self.matlab, "-batch", "atg_server"])
            time.sleep(0.05)
        TRACE_STATS["matlab_launches"] += 1
        TRACE_STATS["matlab_startup_s"] += time.perf_counter() - started
        print(f"    persistent MATLAB server ready in {time.perf_counter() - started:.1f}s", flush=True)

    def stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            if self.job_dir is not None:
                (self.job_dir / "quit").write_text("")
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process = None
        if self.job_dir is not None:
            shutil.rmtree(self.job_dir, ignore_errors=True)
            self.job_dir = None

    def run_job(self, runner: Path, timeout: float | None) -> None:
        if not self.alive():
            self.start()  # first use, or the previous MATLAB died/was killed
        assert self.job_dir is not None and self.process is not None
        self.job_counter += 1
        stem = f"job_{self.job_counter:06d}"
        staging = self.job_dir / f"{stem}.staging"
        staging.write_text(str(runner))
        staging.rename(self.job_dir / f"{stem}.run")  # atomic hand-over
        done = self.job_dir / f"{stem}.done"
        deadline = None if timeout is None else time.perf_counter() + timeout
        while not done.exists():
            if self.process.poll() is not None:
                self.stop()
                raise subprocess.CalledProcessError(1, [self.matlab, "atg_server", str(runner)])
            if deadline is not None and time.perf_counter() > deadline:
                self.stop()  # a hung job poisons the session; the next job restarts it
                raise subprocess.TimeoutExpired([self.matlab, "atg_server", str(runner)], timeout)
            time.sleep(0.02)
        output = (self.job_dir / f"{stem}.out").read_text(errors="replace") if (
            self.job_dir / f"{stem}.out").exists() else ""
        status = done.read_text().strip()
        if output:
            sys.stdout.write(output if output.endswith("\n") else output + "\n")
        if status != "0":
            raise subprocess.CalledProcessError(1, [self.matlab, "atg_server", str(runner)])


_MATLAB_SERVER: MatlabServer | None = None


def matlab_server(args: argparse.Namespace) -> MatlabServer:
    global _MATLAB_SERVER
    if _MATLAB_SERVER is None:
        _MATLAB_SERVER = MatlabServer(args.matlab, ROOT)
        atexit.register(_MATLAB_SERVER.stop)
    return _MATLAB_SERVER


def matlab_inclusive_signal_names(args: argparse.Namespace) -> list[str]:
    """Signals whose ATG domain guard is inclusive: discrete and static ones.

    MATLAB only uses this list to choose ``>=/<=`` guards; integer rounding is
    applied in Python to discrete signals only, static ones are pinned instead.
    """
    names = list(getattr(args, "discrete_signal_names", []) or [])
    names += [name for name in getattr(args, "static_signal_names", []) or [] if name not in names]
    return names


def run_matlab_sat_generation(
    args: argparse.Namespace,
    formula_str: str,
    out_dir: Path,
    trace_count: int,
    k_gen: int,
    formula_dialect: str = "bustle",
) -> list[list[float]]:
    formula_csv = out_dir / "formula.csv"
    work_dir = out_dir / "work"
    export_dir = out_dir / "dataset"
    prepared_formula = prepare_generation_formula(
        formula_str,
        formula_dialect,
        getattr(args, "boolean_signal_names", []),
        getattr(args, "one_hot_groups", {}),
        float(args.time_start),
        float(args.time_end),
    )
    write_single_formula_csv(formula_csv, prepared_formula)
    # STL_Parse_Table invokes `java Infix2Prefix` through PATH rather than the
    # javaBin option used by the later MoonLight evaluator. Put the selected
    # JDK first so both stages use the same compatible Java runtime.
    java_dir = Path(args.java).resolve().parent
    matlab_path_setup = (
        "setenv('PATH',['"
        + _matlab_string(java_dir)
        + "' pathsep getenv('PATH') pathsep '/usr/local/bin']); "
    )
    persistent_matlab = bool(getattr(args, "atg_persistent_matlab", False))
    matlab_cmd = (
        "try; " + matlab_path_setup
        + f"cd('{_matlab_string(ROOT / 'stl-trace-generation' / 'GenExTraces')}'); "
        "GenerateCaseDatasetFromCsv("
        f"'datasetName','{_matlab_string(args.dataset_name)}',"
        f"'formulaCsv','{_matlab_string(formula_csv)}',"
        f"'formulaDialect','{formula_dialect}',"
        "'row',1,"
        f"'oracleFormulaCsv','{_matlab_string(resolve_path(args.oracle_formula_csv))}',"
        f"'oracleRow',{args.oracle_formula_row},"
        f"'outDir','{_matlab_string(work_dir)}',"
        f"'exportDir','{_matlab_string(export_dir)}',"
        "'requireMoonlightCaseConsistency',false,"
        "'allowFallback',false,"
        f"'signalNames',{matlab_cellstr(args.signal_names)},"
        f"'booleanSignalNames',{matlab_cellstr(getattr(args, 'boolean_signal_names', []))},"
        f"'discreteSignalNames',{matlab_cellstr(matlab_inclusive_signal_names(args))},"
        f"'derivedSignalDefinitions',{matlab_derived_signal_definitions(getattr(args, 'derived_signal_definitions', {}))},"
        f"'domainBounds',{matlab_domain_bounds(args)},"
        f"'sampleTimes',{matlab_sample_times(args.sample_times)},"
        f"'javaBin','{_matlab_string(args.java)}',"
        f"'satCount',{trace_count},"
        "'vioCount',0,"
        "'satCandidateExtra',0,"
        "'vioCandidateExtra',0,"
        "'skipEvaluation',true,"
        f"'earlyExit','{getattr(args, 'atg_early_exit', 'off')}',"
        f"'k',{k_gen});"
        " catch ME; disp(getReport(ME,'extended','hyperlinks','off')); "
        + ("error('ATG_JOB_FAILED'); end;" if persistent_matlab else "exit(1); end;")
    )
    timeout = None if args.matlab_timeout_seconds <= 0 else args.matlab_timeout_seconds
    # Configurations with many long derived-signal definitions. Passing
    # the whole MATLAB program through `-batch` can exceed MATLAB's command
    # argument parser even when the OS accepts it. Execute a short runner file.
    matlab_runner = out_dir / "run_atg.m"
    matlab_runner.parent.mkdir(parents=True, exist_ok=True)
    matlab_runner.write_text(matlab_cmd + "\n", encoding="utf-8")
    matlab_entry = f"run('{_matlab_string(matlab_runner)}')"
    launch_started = time.perf_counter()
    try:
        try:
            if persistent_matlab and not args.dry_run:
                print(f"$ [persistent MATLAB] run {matlab_runner}", flush=True)
                tracegen_started = time.perf_counter()
                try:
                    with matlab_slot():
                        matlab_server(args).run_job(matlab_runner, timeout)
                finally:
                    RUN_TIMINGS["tracegen"] += time.perf_counter() - tracegen_started
            else:
                run(
                    [*matlab_launch_prefix(args.matlab), "-batch", matlab_entry],
                    ROOT,
                    dry_run=args.dry_run,
                    capture=True,
                    timeout=timeout,
                )
        finally:
            if not args.dry_run:
                if not persistent_matlab:
                    TRACE_STATS["matlab_launches"] += 1
                TRACE_STATS["matlab_jobs"] += 1
                wall_s = time.perf_counter() - launch_started
                TRACE_STATS["matlab_wall_s"] += wall_s
                _absorb_matlab_stats(work_dir / "trace_gen_stats.csv", wall_s)
    except subprocess.TimeoutExpired:
        if not (export_dir / "data.csv").exists():
            raise
        message = (
            f"    MATLAB ATG timed out after {args.matlab_timeout_seconds:g}s, "
            f"but data.csv exists; using partial traces from {export_dir}"
        )
        print(message, flush=True)
        control_progress("TIMEOUT " + message.strip())
    except subprocess.CalledProcessError:
        if not (export_dir / "data.csv").exists():
            raise
        print(
            f"    MATLAB ATG returned non-zero, but data.csv exists; using {export_dir}",
            flush=True,
        )
    if args.dry_run or not (export_dir / "data.csv").exists():
        return []
    post_started = time.perf_counter()
    rows = read_data_rows(export_dir / "data.csv")
    snap_domain_trace_rows(args, rows)
    materialize_derived_trace_rows(args, rows)
    if (
        getattr(args, "boolean_signal_names", [])
        or getattr(args, "discrete_signal_names", [])
        or getattr(args, "static_signal_names", [])
        or getattr(args, "derived_signal_definitions", {})
    ):
        write_data_csv(export_dir / "data.csv", rows, list(args.signal_names))
        work_data = work_dir / "data.csv"
        if work_data.exists():
            write_data_csv(work_data, rows, list(args.signal_names))
    TRACE_STATS["python_csv_read_s"] += time.perf_counter() - post_started
    return rows[:trace_count]


# (generation formula, target label, dialect) -> (skip when max_k <= this,
# highest k_gen actually attempted). Reset per process, never persisted.
GENERATION_MEMO: dict[tuple[str, int, str], tuple[int, int]] = {}


def smt_generate_with_budget(
    args: argparse.Namespace,
    formula_str: str,
    target_label: int,
    base_dir: Path,
    trace_count: int,
    k_gen_init: int,
    formula_dialect: str = "bustle",
    max_k: int = 16,
    solve_cap: int | None = None,
) -> tuple[list[list[float]], int]:
    """Generate traces for one formula, climbing the k_gen ladder until a trace appears.

    ``solve_cap`` (shared round budget only) is a hard cap on SMT solves across all
    k attempts of this call; ``None`` keeps the original uncapped behaviour.
    """
    solves_at_start = TRACE_STATS["solves_performed"]
    if target_label != 1 and formula_dialect != "bustle":
        raise ValueError("target_label=-1 is only supported for BUSTLE formulas")
    generation_formula = formula_str if target_label == 1 else negate_prefix_formula(formula_str)
    k_gen = k_gen_init
    max_k = max(max_k, k_gen_init)
    # CE-class enumeration is deterministic, so an unsatisfiable class comes
    # back every round. Without this memo the same hopeless SMT problem is
    # re-solved once per round for the whole run.
    memo_key = (generation_formula, target_label, formula_dialect)
    memo = GENERATION_MEMO.get(memo_key)
    if not args.no_generation_memo and memo is not None and memo[0] >= max_k:
        print(
            f"    skipping generation: this formula already produced no trace "
            f"at k_gen={memo[1]} earlier in this run.",
            flush=True,
        )
        return [], k_gen_init
    while k_gen <= max_k:
        attempt_count = trace_count
        if solve_cap is not None:
            left = solve_cap - int(TRACE_STATS["solves_performed"] - solves_at_start)
            if left <= 0:
                print("    round solve budget exhausted; not starting another solve.", flush=True)
                return [], k_gen
            attempt_count = min(trace_count, left)
        attempt_dir = base_dir / f"k_{k_gen:02d}"
        rows: list[list[float]] = []
        runtime_failed = False
        license_failures = 0
        runtime_attempt = -1
        while True:
            runtime_attempt += 1
            if runtime_attempt > max(0, args.matlab_runtime_retries) + license_failures:
                break
            try:
                rows = run_matlab_sat_generation(
                    args,
                    generation_formula,
                    attempt_dir,
                    attempt_count,
                    k_gen,
                    formula_dialect=formula_dialect,
                )
                runtime_failed = False
                break
            except subprocess.TimeoutExpired:
                runtime_failed = True
                message = (
                    f"    MATLAB generator timeout at k_gen={k_gen} "
                    f"(attempt {runtime_attempt + 1}/{args.matlab_runtime_retries + 1})."
                )
                print(message, flush=True)
                control_progress("TIMEOUT " + message.strip())
            except subprocess.CalledProcessError as exc:
                runtime_failed = True
                message = (
                    f"    MATLAB generator runtime failure at k_gen={k_gen} "
                    f"(exit={exc.returncode}, attempt "
                    f"{runtime_attempt + 1}/{args.matlab_runtime_retries + 1})."
                )
                print(message, flush=True)
                control_progress("ERROR " + message.strip())
                if matlab_license_error(LAST_MATLAB_ERROR_TEXT):
                    license_failures += 1
                    note = (f"    MATLAB LICENSE ERROR ({license_failures}/{MATLAB_LICENSE_RETRIES}); "
                            f"waiting {MATLAB_LICENSE_RETRY_SLEEP_S * license_failures:g}s before retrying.")
                    print(note, flush=True)
                    control_progress("LICENSE " + note.strip())
                    if license_failures >= MATLAB_LICENSE_RETRIES:
                        raise MatlabLicenseError(
                            "MATLAB_LICENSE_FAILURE: could not obtain a MATLAB license after "
                            f"{license_failures} attempts: {LAST_MATLAB_ERROR_TEXT.strip()[-300:]}")
                    time.sleep(MATLAB_LICENSE_RETRY_SLEEP_S * license_failures)
                    continue
                if LAST_ATG_ALL_UNSAT and not getattr(args, "retry_unsat_failures", False):
                    print("    every solve was unsat, so a retry would fail identically; "
                          "not retrying.", flush=True)
                    break
            if runtime_attempt < args.matlab_runtime_retries:
                time.sleep(3.0)
        if runtime_failed:
            print("    MATLAB runtime retries exhausted; not treating this as SMT UNSAT.", flush=True)
            # A MATLAB error (as opposed to an empty-but-clean result) never
            # advances the k ladder, so a repeat call would fail identically.
            GENERATION_MEMO[memo_key] = (max_k, k_gen)
            return [], k_gen
        if rows:
            metrics = getattr(args, "trace_metric_counters", None)
            if metrics is not None:
                metrics["num_generated"] += len(rows)
            return rows, k_gen
        k_gen += 1
    GENERATION_MEMO[memo_key] = (max_k, max_k)
    return [], k_gen


def harden_active_disambiguation_formula(formula: str, args: argparse.Namespace) -> str:
    margin = max(
        0.0,
        float(getattr(args, "active_disambiguation_margin", 0.0)),
        float(getattr(args, "class_generation_margin", 0.0)),
    )
    if margin <= 0.0:
        return formula
    boolean_names = set(getattr(args, "boolean_signal_names", []) or [])

    def atom_text(var: str, cmp_op: str, value: float, negated: bool) -> str:
        value_text = format_number(float(value))
        if var in boolean_names:
            if negated:
                return _boolean_numeric_atom_constraint(var, cmp_op, value, True)
            return _boolean_numeric_atom_constraint(var, cmp_op, value, False)
        if cmp_op in {"=", "=="}:
            if negated:
                lo = format_number(float(value) - margin)
                hi = format_number(float(value) + margin)
                return f"OR[{var} < {lo},{var} > {hi}]"
            return _boolean_exact_atom(var, float(value))
        if not negated:
            if cmp_op in {">", ">="}:
                return f"{var} {cmp_op} {format_number(float(value) + margin)}"
            if cmp_op in {"<", "<="}:
                return f"{var} {cmp_op} {format_number(float(value) - margin)}"
            return f"{var} {cmp_op} {value_text}"
        if cmp_op == ">":
            return f"{var} <= {format_number(float(value) - margin)}"
        if cmp_op == ">=":
            return f"{var} < {format_number(float(value) - margin)}"
        if cmp_op == "<":
            return f"{var} >= {format_number(float(value) + margin)}"
        if cmp_op == "<=":
            return f"{var} > {format_number(float(value) + margin)}"
        return _negate_numeric_atom(var, cmp_op, value)

    def serialize(node: ASTNode, negated: bool = False) -> str:
        if node.kind == "bool":
            if node.var in boolean_names:
                return _boolean_exact_atom(str(node.var), 0.0 if negated else 1.0)
            return f"NOT[{node.var}]" if negated else str(node.var)
        if node.kind == "atom":
            return atom_text(str(node.var), str(node.cmp), float(node.value), negated)
        if node.kind == "not":
            return serialize(node.child, not negated)
        if node.kind in {"and", "or"}:
            op = node.kind
            if negated:
                op = "or" if op == "and" else "and"
            left = serialize(node.left, negated)
            right = serialize(node.right, negated)
            return f"{'AND' if op == 'and' else 'OR'}[{left},{right}]"
        if node.kind == "implies":
            rendered = f"IMPLIES[{serialize(node.left, False)},{serialize(node.right, False)}]"
            return f"NOT[{rendered}]" if negated else rendered
        if node.kind in {"always", "eventually"}:
            kind = node.kind
            if negated:
                kind = "eventually" if kind == "always" else "always"
            op = "GLOBALLY" if kind == "always" else "EVENTUALLY"
            return (
                f"{op} I=[{format_number(float(node.a))},{format_number(float(node.b))}]"
                f"[{serialize(node.child, negated)}]"
            )
        if node.kind == "until":
            if negated:
                return f"NOT[{bustle_print(node)}]"
            return bustle_print(node)
        return bustle_print(node)

    try:
        return serialize(epoch_parse_stl(formula), False)
    except Exception:
        return formula


def disambiguation_formula(phi_sat: str, phi_vio: str, args: argparse.Namespace) -> str:
    """Formula for traces classified SAT by phi_sat and VIO by phi_vio."""
    return harden_active_disambiguation_formula(f"AND[{phi_sat},NOT[{phi_vio}]]", args)


def grid_disambiguation_candidates(
    args: argparse.Namespace,
    formula_a: str,
    formula_b: str,
    limit: int = 512,
) -> list[list[float]]:
    """Construct deterministic threshold-crossing traces on the replay grid.

    The continuous SMT encoding can satisfy a temporal formula with a switch
    just outside the sampled horizon.  Constant traces and one-sample pulses
    exercise F/G and scope differences directly under the evaluator's actual
    discrete semantics.
    """
    if limit <= 0 or not args.sample_times:
        return []
    atom = re.compile(
        r"\b([A-Za-z_]\w*)\s*(?:<=|>=|==|=|<|>)\s*"
        r"(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    )
    thresholds: dict[str, list[float]] = collections.defaultdict(list)
    for name, value in atom.findall(f"{formula_a} {formula_b}"):
        if name in args.signal_names:
            thresholds[name].append(float(value))

    levels: list[tuple[float, float]] = []
    margin = max(float(getattr(args, "active_disambiguation_margin", 1.0e-4)), 1.0e-6)
    for name in args.signal_names:
        lo, hi = args.signal_bounds[name]
        pad = max(margin, (float(hi) - float(lo)) * 1.0e-6)
        values = thresholds.get(name, [])
        if values:
            low = max(float(lo), min(values) - pad)
            high = min(float(hi), max(values) + pad)
        else:
            low = float(lo) + 0.25 * (float(hi) - float(lo))
            high = float(lo) + 0.75 * (float(hi) - float(lo))
        if low == high:
            low, high = float(lo), float(hi)
        levels.append((low, high))

    baselines = list(itertools.product((0, 1), repeat=len(args.signal_names)))
    if len(baselines) > 32:
        baselines = baselines[:32]
    candidates: list[list[float]] = []
    seen: set[tuple[float, ...]] = set()

    def add(selectors: list[list[int]]) -> None:
        if len(candidates) >= limit:
            return
        row = [
            levels[signal_index][selectors[signal_index][time_index]]
            for time_index in range(len(args.sample_times))
            for signal_index in range(len(args.signal_names))
        ]
        key = tuple(row)
        if key not in seen:
            seen.add(key)
            candidates.append(row)

    for baseline in baselines:
        selectors = [[bit] * len(args.sample_times) for bit in baseline]
        add(selectors)
        for signal_index in range(len(args.signal_names)):
            for time_index in range(len(args.sample_times)):
                pulse = [list(values) for values in selectors]
                pulse[signal_index][time_index] = 1 - baseline[signal_index]
                add(pulse)
                if len(candidates) >= limit:
                    return candidates
    return candidates


def generate_active_disambiguation_records(
    args: argparse.Namespace,
    formula_a: str,
    formula_b: str,
    current_formula: str,
    oracle_formula: str,
    round_dir: Path,
    trace_budget: int,
    k_gen_init: int,
) -> tuple[list[dict[str, object]], int, list[dict[str, str]]]:
    """Generate traces that distinguish two currently tied pool candidates.

    This realizes the rho(phi_a,w) * rho(phi_b,w) < 0 idea using the existing
    SAT generator: first request phi_a AND NOT phi_b, then the reverse.  Labels
    are still replayed through current_formula and oracle_formula before the
    traces enter D_global.
    """
    if trace_budget <= 0:
        return [], 0, []
    specs = [
        ("A_SAT_B_VIO", disambiguation_formula(formula_a, formula_b, args)),
        ("B_SAT_A_VIO", disambiguation_formula(formula_b, formula_a, args)),
    ]
    budgets = allocate_trace_budget(trace_budget, len(specs))
    rows: list[list[float]] = []
    tags: list[str] = []
    requested_by_tag: dict[str, int] = {}
    index_rows: list[dict[str, str]] = []
    base_dir = round_dir / "disambiguation_generation"
    for idx, ((tag, formula), count) in enumerate(zip(specs, budgets)):
        if count <= 0:
            continue
        # The continuous SMT encoding can produce a model whose decisive
        # switch lies just beyond the final discrete sample.  Generate a
        # small blocked batch and let the replay check below choose genuine
        # separators instead of abandoning the mutation after the first
        # spurious model.
        replay_attempts = max(
            1,
            int(getattr(args, "active_disambiguation_replay_attempts", 1)),
        )
        candidate_count = count * replay_attempts
        generated, final_k = smt_generate_with_budget(
            args,
            formula,
            1,
            base_dir / f"{idx:02d}_{tag.lower()}",
            candidate_count,
            k_gen_init,
            formula_dialect="bustle",
        )
        requested_by_tag[tag] = count
        index_rows.append({
            "direction": tag,
            "requested": str(count),
            "generated": str(len(generated)),
            "k_gen": str(final_k),
            "formula": formula,
        })
        print(
            f"  active disambiguation {tag}: {len(generated)} replay candidates "
            f"for {count} requested traces at k_gen={final_k}",
            flush=True,
        )
        rows.extend(generated)
        tags.extend([tag] * len(generated))
    eval_dir = round_dir / "disambiguation_eval"
    labels_a: list[int] = []
    labels_b: list[int] = []
    if rows:
        build_eval_dataset(args, eval_dir, rows, [1] * len(rows))
        labels_a = evaluate_formula_labels(
            args,
            formula_a,
            eval_dir,
            round_dir / "disambiguation_phi_a_eval.csv",
        )
        labels_b = evaluate_formula_labels(
            args,
            formula_b,
            eval_dir,
            round_dir / "disambiguation_phi_b_eval.csv",
        )
    verified_rows: list[list[float]] = []
    verified_tags: list[str] = []
    accepted_by_tag = {tag: 0 for tag in requested_by_tag}
    for row, tag, label_a, label_b in zip(rows, tags, labels_a, labels_b):
        separates = (
            tag == "A_SAT_B_VIO" and int(label_a) == 1 and int(label_b) == -1
        ) or (
            tag == "B_SAT_A_VIO" and int(label_a) == -1 and int(label_b) == 1
        )
        if (
            separates
            and accepted_by_tag.get(tag, 0) < requested_by_tag.get(tag, 0)
        ):
            verified_rows.append(row)
            verified_tags.append(tag)
            accepted_by_tag[tag] = accepted_by_tag.get(tag, 0) + 1
    if sum(accepted_by_tag.values()) < sum(requested_by_tag.values()):
        fallback_rows = grid_disambiguation_candidates(args, formula_a, formula_b)
        if fallback_rows:
            metrics = getattr(args, "trace_metric_counters", None)
            if metrics is not None:
                metrics["num_generated"] += len(fallback_rows)
            fallback_dir = round_dir / "disambiguation_grid_eval"
            build_eval_dataset(args, fallback_dir, fallback_rows, [1] * len(fallback_rows))
            fallback_a = evaluate_formula_labels(
                args,
                formula_a,
                fallback_dir,
                round_dir / "disambiguation_grid_phi_a_eval.csv",
            )
            fallback_b = evaluate_formula_labels(
                args,
                formula_b,
                fallback_dir,
                round_dir / "disambiguation_grid_phi_b_eval.csv",
            )
            added = 0
            for row, label_a, label_b in zip(fallback_rows, fallback_a, fallback_b):
                tag = ""
                if int(label_a) == 1 and int(label_b) == -1:
                    tag = "A_SAT_B_VIO"
                elif int(label_a) == -1 and int(label_b) == 1:
                    tag = "B_SAT_A_VIO"
                if tag and accepted_by_tag.get(tag, 0) < requested_by_tag.get(tag, 0):
                    verified_rows.append(row)
                    verified_tags.append(tag)
                    accepted_by_tag[tag] = accepted_by_tag.get(tag, 0) + 1
                    added += 1
                if sum(accepted_by_tag.values()) >= sum(requested_by_tag.values()):
                    break
            index_rows.append({
                "direction": "GRID_REPLAY_VERIFIED",
                "requested": str(len(fallback_rows)),
                "generated": str(added),
                "k_gen": "",
                "formula": "discrete threshold and pulse traces replayed on sample_times",
            })
    if len(verified_rows) != len(rows):
        index_rows.append({
            "direction": "REPLAY_VERIFIED",
            "requested": str(len(rows)),
            "generated": str(len(verified_rows)),
            "k_gen": "",
            "formula": "kept only traces whose replay labels separate phi_a and phi_b",
        })
    if not verified_rows:
        return [], 0, index_rows

    allowed = reserve_query_indices(args, verified_rows)
    if not allowed:
        return [], 0, index_rows
    rows = [verified_rows[index] for index in allowed]
    tags = [verified_tags[index] for index in allowed]
    build_eval_dataset(args, eval_dir, rows, [1] * len(rows))
    pred_labels = evaluate_formula_labels(
        args,
        current_formula,
        eval_dir,
        round_dir / "disambiguation_current_eval.csv",
    )
    true_labels = evaluate_formula_labels(
        args,
        oracle_formula,
        eval_dir,
        round_dir / "disambiguation_oracle_eval.csv",
    )
    record_oracle_queries(args, rows, "active_disambiguation")
    metrics = getattr(args, "trace_metric_counters", None)
    if metrics is not None:
        metrics["num_discriminating"] += len(rows)
    records = [
        make_trace_record(row, tag, "ACTIVE_DISAMBIGUATION", pred, truth)
        for row, tag, pred, truth in zip(rows, tags, pred_labels, true_labels)
    ]
    return records, len(rows), index_rows


def filter_formulas_consistent_with_records(
    args: argparse.Namespace,
    formulas: Sequence[str],
    records: Sequence[dict[str, object]],
    out_dir: Path,
) -> list[str]:
    """Keep formulas whose labels match the oracle on all given records."""
    if not formulas or not records:
        return list(formulas)
    rows = [record["trace"] for record in records]
    labels = [int(record["true_label"]) for record in records]
    dataset_dir = out_dir / "candidate_filter_dataset"
    build_eval_dataset(args, dataset_dir, rows, labels)
    kept: list[str] = []
    eval_rows: list[dict[str, str]] = []
    for candidate_index, formula in enumerate(formulas):
        try:
            pred = evaluate_formula_labels(
                args,
                formula,
                dataset_dir,
                out_dir / f"candidate_{candidate_index:04d}_eval.csv",
            )
        except subprocess.CalledProcessError:
            pred = []
        correct = sum(1 for y_hat, y in zip(pred, labels) if int(y_hat) == int(y))
        consistent = len(pred) == len(labels) and correct == len(labels)
        if consistent:
            kept.append(formula)
        eval_rows.append({
            "candidate_index": str(candidate_index),
            "consistent": str(consistent).lower(),
            "correct": str(correct),
            "total": str(len(labels)),
            "predicted": ",".join(str(int(v)) for v in pred),
            "oracle": ",".join(str(int(v)) for v in labels),
            "formula": formula,
        })
    write_dict_csv(
        out_dir / "candidate_filter.csv",
        eval_rows,
        ["candidate_index", "consistent", "correct", "total", "predicted", "oracle", "formula"],
    )
    return kept


def build_eval_dataset(
    args: argparse.Namespace,
    dataset_dir: Path,
    rows: list[list[float]],
    labels: Sequence[int],
) -> None:
    dataset_dir.mkdir(parents=True, exist_ok=True)
    snap_domain_trace_rows(args, rows)
    write_data_csv(dataset_dir / "data.csv", rows, list(args.signal_names))
    write_labels_csv(dataset_dir / "labels.csv", labels)
    write_labels_csv(dataset_dir / "oracle_label.csv", labels)
    write_labels_csv(dataset_dir / "case_label.csv", labels)
    write_labels_csv(dataset_dir / "time.csv", args.sample_times)
    write_labels_csv(dataset_dir / "times.csv", args.sample_times)


def write_trace_metadata(path: Path, records: Sequence[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "trace_id", "round_id", "source", "ceclass_id", "kce",
            "predicted_label", "oracle_label",
        ])
        writer.writeheader()
        for index, record in enumerate(records):
            writer.writerow({
                "trace_id": f"trace_{index + 1:06d}",
                "round_id": record.get("round_id", ""),
                "source": record.get("class_type", ""),
                "ceclass_id": record.get("class", ""),
                "kce": record.get("kce", ""),
                "predicted_label": record.get("pred_label", ""),
                "oracle_label": record.get("true_label", ""),
            })


def ensure_eval_dataset(
    args: argparse.Namespace,
    dataset_dir: Path,
    rows: list[list[float]],
    labels: Sequence[int],
) -> None:
    required = (
        dataset_dir / "data.csv",
        dataset_dir / "labels.csv",
        dataset_dir / "oracle_label.csv",
        dataset_dir / "case_label.csv",
        dataset_dir / "time.csv",
        dataset_dir / "times.csv",
    )
    if all(path.exists() for path in required):
        return
    build_eval_dataset(args, dataset_dir, rows, labels)


def make_trace_record(
    trace: list[float], class_formula: str, class_type: str, pred_label: int, true_label: int,
) -> dict[str, object]:
    return {
        "trace": trace,
        "class": class_formula,
        "class_type": class_type,
        "pred_label": pred_label,
        "true_label": true_label,
        "is_error": pred_label != true_label,
    }


def replay_record_labels(
    args: argparse.Namespace,
    records: list[dict[str, object]],
    current_formula: str,
    oracle_formula: str,
    out_dir: Path,
    stem: str,
) -> list[dict[str, object]]:
    """Recompute both current-formula and oracle labels through the same evaluator."""
    allowed = reserve_query_indices(args, [record["trace"] for record in records])
    records = [records[index] for index in allowed]
    if not records:
        return records
    rows = [record["trace"] for record in records]
    dataset_dir = out_dir / f"{stem}_dataset"
    build_eval_dataset(args, dataset_dir, rows, [1] * len(rows))
    pred = evaluate_formula_labels(
        args,
        current_formula,
        dataset_dir,
        out_dir / f"{stem}_current_eval.csv",
    )
    truth = evaluate_formula_labels(
        args,
        oracle_formula,
        dataset_dir,
        out_dir / f"{stem}_oracle_eval.csv",
    )
    record_oracle_queries(args, rows, [str(record.get("class_type", "unknown")) for record in records])
    for record, pred_label, true_label in zip(records, pred, truth):
        record["pred_label"] = int(pred_label)
        record["true_label"] = int(true_label)
        record["is_error"] = int(pred_label) != int(true_label)
    write_labels_csv(dataset_dir / "current_pred_label.csv", pred)
    write_labels_csv(dataset_dir / "oracle_replay_label.csv", truth)
    write_labels_csv(dataset_dir / "labels.csv", truth)
    write_labels_csv(dataset_dir / "oracle_label.csv", truth)
    write_labels_csv(dataset_dir / "case_label.csv", pred)
    return records


def group_by_class(records: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for record in records:
        groups.setdefault(str(record["class"]), []).append(record)
    return groups


def evaluate_round_formula(
    args: argparse.Namespace,
    formula_str: str,
    rows: list[list[float]],
    labels: Sequence[int],
    out_dir: Path,
    stem: str,
) -> tuple[list[int], float]:
    dataset_dir = out_dir / f"{stem}_dataset"
    build_eval_dataset(args, dataset_dir, rows, labels)
    eval_csv = out_dir / f"{stem}.csv"
    predicted = evaluate_formula_labels(args, formula_str, dataset_dir, eval_csv)
    accuracy = (
        sum(1 for pred, gold in zip(predicted, labels) if pred == gold) / len(labels)
        if labels else 0.0
    )
    return predicted, accuracy


def prune_pool_eval_artifacts(out_dir: Path, stem: str) -> None:
    """Delete one candidate's evaluation dataset and eval csv (RESPSTL_KEEP_REPAIR_DEBUG=1 keeps them)."""
    if os.environ.get("RESPSTL_KEEP_REPAIR_DEBUG") == "1":
        return
    shutil.rmtree(out_dir / f"{stem}_dataset", ignore_errors=True)  # legacy per-candidate layout
    try:
        (out_dir / f"{stem}.csv").unlink()
    except FileNotFoundError:
        pass


def evaluate_round_metrics(
    args: argparse.Namespace,
    formula_str: str,
    rows: list[list[float]],
    labels: Sequence[int],
    out_dir: Path,
    stem: str,
) -> EvaluationMetrics:
    dataset_dir = out_dir / f"{stem}_dataset"
    build_eval_dataset(args, dataset_dir, rows, labels)
    eval_csv = out_dir / f"{stem}.csv"
    return evaluate_metrics_from_dataset(args, formula_str, dataset_dir, labels, eval_csv)


def evaluate_metrics_from_dataset(
    args: argparse.Namespace,
    formula_str: str,
    dataset_dir: Path,
    labels: Sequence[int],
    eval_csv: Path,
) -> EvaluationMetrics:
    """Evaluate one formula against an already materialized dataset."""
    predicted = evaluate_formula_labels(args, formula_str, dataset_dir, eval_csv)
    return _metrics_from_eval(predicted, eval_csv, labels)


def _metrics_from_eval(predicted: list[int], eval_csv: Path, labels: Sequence[int]) -> EvaluationMetrics:
    robustness = robustness_from_eval_csv(eval_csv)
    accuracy = (
        sum(1 for pred, gold in zip(predicted, labels) if pred == gold) / len(labels)
        if labels else 0.0
    )
    return EvaluationMetrics(
        predicted=predicted,
        robustness=robustness,
        accuracy=accuracy,
        balanced_accuracy=balanced_accuracy(predicted, labels),
    )


def metrics_from_eval_csv(eval_csv: Path, labels: Sequence[int]) -> EvaluationMetrics:
    """Metrics of a candidate already evaluated by run_evaluator_batch. A missing file is the batch
    evaluator's way of saying this formula failed, i.e. what a failed single call raises."""
    if not eval_csv.exists():
        raise subprocess.CalledProcessError(1, ["EvaluateFormulaOnDataset", "batch"])
    return _metrics_from_eval(labels_from_eval_csv(eval_csv), eval_csv, labels)


# One JVM start per candidate dominated the cost of scoring a large search pool (thousands of candidates
# per round); a batch scores many formulas on one dataset with one JVM and writes the very same CSVs.
POOL_EVAL_BATCH = 400


def run_evaluator_batch(args: argparse.Namespace, jobs: Sequence[tuple[str, Path]], dataset_dir: Path) -> None:
    """Evaluate every (formula, output csv) of jobs on dataset_dir. Same evaluator flags as run_evaluator."""
    if args.dry_run or not jobs:
        return
    for start in range(0, len(jobs), POOL_EVAL_BATCH):
        batch_file = dataset_dir.parent / f"pool_batch_{start:05d}.tsv"
        batch_file.write_text("".join(f"{path}\t{formula}\n" for formula, path in jobs[start:start + POOL_EVAL_BATCH]),
                              encoding="utf-8")
        cmd: list[str] = []
        append_macos_arch_prefix(cmd, args.arch)
        cmd.extend([
            args.java, "-cp", args.classpath, "it.units.malelab.learningstl.EvaluateFormulaOnDataset",
            f"dataset={args.dataset_name}", f"dataPath={dataset_dir}",
            "strictLengthCheck=false", "shortSignalPolicy=penalty",
            "normalizeValues=false", "renumberTimes=false",
            f"batchFile={batch_file}",
        ])
        try:
            run(cmd, ROOT, dry_run=False, capture=True)
        finally:
            batch_file.unlink(missing_ok=True)


EVALUATOR_INPUT_FILES = {
    "data.csv",
    "labels.csv",
    "oracle_label.csv",
    "case_label.csv",
    "time.csv",
    "times.csv",
}


COMPACT_KEEP_FILES = {
    "run_record.json",
    "llm_ce_summary.json",  # LLM call count used by the paper metrics
}


def _tree_usage(path: Path) -> tuple[int, int]:
    files = [item for item in path.rglob("*") if item.is_file()]
    return len(files), sum(item.stat().st_size for item in files)


def compact_completed_run(output_root: Path) -> dict[str, object]:
    """Remove only reproducible bulk artifacts after a run is durably complete.

    Paper-facing summaries, run records, search/localization/profile CSVs,
    evaluator outputs, formulas, failure records and run.log are preserved.
    External heldout/ and trace_cache/ roots are outside output_root and are
    therefore never touched.
    """
    record_path = output_root / "run_record.json"
    if not record_path.is_file():
        return {"compacted": False, "reason": "missing_run_record"}
    try:
        complete = bool(json.loads(record_path.read_text()).get("complete"))
    except json.JSONDecodeError:
        return {"compacted": False, "reason": "invalid_run_record"}
    if not complete:
        return {"compacted": False, "reason": "incomplete_run"}
    resolved_root = output_root.resolve()
    if resolved_root == ROOT.resolve() or not resolved_root.is_dir():
        raise ValueError(f"refusing to compact unsafe output root: {resolved_root}")

    removed_files = 0
    removed_bytes = 0
    removed_dirs = 0
    # Allow-list: everything the analysis, resume logic and progress tools read
    # lives in these files (run_record.json carries localization, results, cost).
    for entry in sorted(output_root.iterdir()):
        if entry.name in COMPACT_KEEP_FILES:
            continue
        if entry.is_dir() and not entry.is_symlink():
            count, size = _tree_usage(entry)
            shutil.rmtree(entry)
            removed_dirs += 1
        else:
            count, size = 1, entry.stat().st_size
            entry.unlink()
        removed_files += count
        removed_bytes += size

    manifest = {
        "compacted": True,
        "policy": "completed_run_allowlist_v2",
        "files_removed": removed_files,
        "directories_removed": removed_dirs,
        "bytes_removed": removed_bytes,
        "preserved": sorted(COMPACT_KEEP_FILES),
    }
    return manifest


# ---------------------------------------------------------------------------
# Summary writing
# ---------------------------------------------------------------------------

def write_round_summary(path: Path, records: list[dict[str, str]]) -> None:
    if not records:
        return
    fieldnames: list[str] = []
    for record in records:
        if str(record.get("round", "")) in CEKCE_BY_ROUND:
            record.setdefault("ceclass_kce", str(CEKCE_BY_ROUND[str(record["round"])]))
        for key in record.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(records)


def _trace_fingerprint(row: Sequence[float]) -> str:
    return hashlib.sha256(
        ",".join(f"{float(value):.17g}" for value in row).encode()
    ).hexdigest()


def query_count(args: argparse.Namespace) -> int:
    metrics = getattr(args, "trace_metric_counters", None)
    return len(metrics["oracle_trace_fingerprints"]) if metrics else 0


def remaining_query_budget(args: argparse.Namespace) -> int | None:
    total = int(getattr(args, "total_query_budget", -1))
    return None if total < 0 else max(0, total - query_count(args))


def reserve_query_indices(args: argparse.Namespace, rows: Sequence[Sequence[float]]) -> list[int]:
    """Indices of the rows the budget still allows; already-labelled traces cost nothing."""
    remaining = remaining_query_budget(args)
    if remaining is None:
        return list(range(len(rows)))
    seen = getattr(args, "trace_metric_counters", {}).get("oracle_trace_fingerprints", set())
    kept, fresh = [], set()
    for index, row in enumerate(rows):
        fingerprint = _trace_fingerprint(row)
        if fingerprint not in seen and fingerprint not in fresh:
            if len(fresh) >= remaining:
                continue
            fresh.add(fingerprint)
        kept.append(index)
    return kept


def _trace_shape_metrics(args: argparse.Namespace, row: Sequence[float]) -> dict[str, object]:
    """Complexity over base signals only; derived columns are functions of them, never counted."""
    names = list(args.signal_names)
    derived = set(getattr(args, "derived_signal_definitions", None) or {})
    width = max(1, len(names))
    n_samples = len(row) // width
    changed, switches = 0, 0
    for offset, name in enumerate(names):
        if name in derived:
            continue
        values = row[offset::width]
        flips = sum(1 for left, right in zip(values, values[1:]) if left != right)
        switches += flips
        changed += bool(flips)
    step = float(getattr(args, "time_step", 0) or 0)
    return {"n_samples": n_samples, "duration": (n_samples - 1) * step if step else None,
            "n_signals": len([n for n in names if n not in derived]), "n_changed_signals": changed, "switches": switches}


def record_oracle_queries(
    args: argparse.Namespace,
    rows: Sequence[Sequence[float]],
    generators: Sequence[str] | str = "unknown",
    *,
    cache_hit: bool = False,
) -> None:
    """Count unique traces labelled by the oracle, not repeated monitor replay."""
    metrics = getattr(args, "trace_metric_counters", None)
    if metrics is None:
        return
    signal_count = max(1, len(args.signal_names))
    path = getattr(args, "query_events_path", None)
    for index, row in enumerate(rows):
        fingerprint = _trace_fingerprint(row)
        if fingerprint in metrics["oracle_trace_fingerprints"]:
            continue
        metrics["oracle_trace_fingerprints"].add(fingerprint)
        metrics["oracle_trace_lengths"].append(len(row) / signal_count)
        kind = generators if isinstance(generators, str) else generators[index]
        if str(kind).lower() == "active_disambiguation":
            metrics["queries_disambiguation"] = metrics.get("queries_disambiguation", 0) + 1
        if path is None:
            continue
        generator = generators if isinstance(generators, str) else generators[index]
        content = json.dumps({"signals": list(args.signal_names), "time_step": getattr(args, "time_step", None),
                              "values": [f"{float(v):.17g}" for v in row]}, separators=(",", ":"))
        event = {"event": len(metrics["oracle_trace_fingerprints"]), "round": getattr(args, "current_round", 0),
                 "trace_hash": hashlib.sha256(content.encode()).hexdigest(), "generator": str(generator).lower(),
                 "Q_after": len(metrics["oracle_trace_fingerprints"]), "cache_hit": cache_hit,
                 **_trace_shape_metrics(args, row)}
        with path.open("a") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
    write_phase_metrics(args)


def trace_metrics_snapshot(args: argparse.Namespace) -> dict[str, object]:
    metrics = args.trace_metric_counters
    lengths = metrics["oracle_trace_lengths"]
    return {
        "num_generated": int(metrics["num_generated"]),
        "num_oracle_queried": len(metrics["oracle_trace_fingerprints"]),
        "avg_num_signals": float(len(args.signal_names)) if lengths else 0.0,
        "avg_trace_length": (sum(lengths) / len(lengths)) if lengths else 0.0,
        "num_discriminating": int(metrics["num_discriminating"]),
        "num_oracle_queried_disambiguation": int(metrics.get("queries_disambiguation", 0)),
        "num_oracle_queried_eq": len(metrics["oracle_trace_fingerprints"]) - int(metrics.get("queries_disambiguation", 0)),
        "time_disambiguation_s": RUN_PHASES["disambiguation"],
        "time_s": dict(RUN_TIMINGS),
        "trace_gen_stats": dict(TRACE_STATS),
        "counting_basis": (
            "generated candidates; unique oracle-labelled trace contents; "
            "repeated monitor replay excluded"
        ),
    }


def write_dict_csv(path: Path, records: list[dict[str, str]], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(records)


def write_trace_record_summary(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        fieldnames = ["trace_index", "class_type", "class", "pred_label", "true_label", "category", "is_error"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i, record in enumerate(records):
            pred = int(record["pred_label"])
            truth = int(record["true_label"])
            if pred == 1 and truth == 1:
                category = "SAT/SAT"
            elif pred == 1 and truth == -1:
                category = "SAT/VIO"
            elif pred == -1 and truth == 1:
                category = "VIO/SAT"
            else:
                category = "VIO/VIO"
            w.writerow({
                "trace_index": i,
                "class_type": str(record["class_type"]),
                "class": str(record["class"]),
                "pred_label": pred,
                "true_label": truth,
                "category": category,
                "is_error": bool(record["is_error"]),
            })


def write_traces_index(path: Path, records: Sequence[dict[str, object]], n_signals: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ["hash", "generator", "length", "changed_signals", "switches", "oracle_label"]
    seen: set[str] = set()
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for record in records:
            trace = list(record["trace"]); digest = cached_trace_hash(trace)
            if digest in seen: continue
            seen.add(digest); steps = len(trace) // max(1, n_signals); changed = switches = 0
            for signal in range(max(1, n_signals)):
                values = [trace[t * n_signals + signal] for t in range(steps)]
                count = sum(left != right for left, right in zip(values, values[1:])); switches += count
                changed += int(count > 0)
            writer.writerow({"hash": digest, "generator": str(record.get("class_type", "")).lower(),
                             "length": steps, "changed_signals": changed, "switches": switches,
                             "oracle_label": record.get("true_label", "")})


def write_misclassified_class_focus(
    path: Path,
    records: list[dict[str, object]],
    top_k: int,
) -> None:
    grouped: dict[tuple[str, str], list[tuple[int, dict[str, object]]]] = {}
    for trace_index, record in enumerate(records):
        if not bool(record["is_error"]):
            continue
        key = (str(record["class_type"]), str(record["class"]))
        grouped.setdefault(key, []).append((trace_index, record))
    ranked = sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0][0], item[0][1]))
    selected_count = len(ranked) if top_k <= 0 else min(top_k, len(ranked))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        fieldnames = [
            "rank", "selected", "class_type", "class_formula",
            "misclassified_count", "trace_indices", "categories",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for rank, ((class_type, class_formula), members) in enumerate(ranked, start=1):
            categories = sorted({
                ("SAT" if int(record["pred_label"]) == 1 else "VIO")
                + "/"
                + ("SAT" if int(record["true_label"]) == 1 else "VIO")
                for _, record in members
            })
            writer.writerow({
                "rank": rank,
                "selected": rank <= selected_count,
                "class_type": class_type,
                "class_formula": class_formula,
                "misclassified_count": len(members),
                "trace_indices": ",".join(str(index) for index, _ in members),
                "categories": ",".join(categories),
            })


def select_repair_records(
    args: argparse.Namespace,
    records: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    """Choose which newly labelled traces enter the repair dataset D."""
    if args.repair_dataset_mode == "all":
        return list(records)
    selected: list[dict[str, object]] = []
    for record in records:
        if bool(record.get("is_error", False)):
            selected.append(record)
            continue
        # Active disambiguation traces are direct questions to the oracle about
        # tied candidates, so they refine D even when current_formula agrees
        # with the oracle on that trace.
        if str(record.get("class_type", "")) == "ACTIVE_DISAMBIGUATION":
            selected.append(record)
    return selected


def obligation_is_covered(
    args: argparse.Namespace,
    check_formula: str,
    eval_dir: Path,
    tag: str,
) -> bool:
    """Does any trace already in D witness this obligation?"""
    if not check_formula:
        return True
    try:
        labels = evaluate_formula_labels(
            args, check_formula, eval_dir, eval_dir / f"{tag}_check.csv",
        )
    except subprocess.CalledProcessError:
        # An obligation we cannot even replay is not evidence of coverage.
        return False
    return any(int(label) == 1 for label in labels)


def generate_obligation_records(
    args: argparse.Namespace,
    current_formula: str,
    oracle_formula: str,
    global_records: Sequence[dict[str, object]],
    round_dir: Path,
    trace_budget: int,
    k_gen_init: int,
    covered_keys: set[str],
    uncoverable_keys: dict[str, str],
) -> tuple[list[list[float]], list[tuple[str, str]], list[dict[str, str]]]:
    """Generate one trace per uncovered syntax-tree obligation of the current phi.

    Nothing here consults what phi decides about any trace, which is the whole
    point: the obligation table is a function of phi's shape alone, so it keeps
    producing demands after phi already fits D perfectly.
    """
    obligations = derive_obligations(
        current_formula,
        boundary_eps=args.obligation_boundary_eps,
        time_end=args.time_end,
    )
    obligation_dir = round_dir / "obligations"
    obligation_dir.mkdir(parents=True, exist_ok=True)
    obligations_to_csv(obligations, obligation_dir / "table.csv")
    print(f"  obligations: {len(obligations)} derived from the current syntax tree", flush=True)

    eval_dir = obligation_dir / "coverage_eval"
    if global_records:
        build_eval_dataset(
            args,
            eval_dir,
            [record["trace"] for record in global_records],
            [int(record["true_label"]) for record in global_records],
        )

    rows: list[list[float]] = []
    meta: list[tuple[str, str]] = []
    index_rows: list[dict[str, str]] = []
    covered_now = 0
    for index, obligation in enumerate(obligations):
        if len(rows) >= trace_budget:
            index_rows.append({
                "kind": obligation.kind, "path": obligation.path,
                "status": "budget_exhausted", "generated": "0", "k_gen": "",
                "witness_formula": obligation.witness_formula,
            })
            continue
        if obligation.key in uncoverable_keys:
            index_rows.append({
                "kind": obligation.kind, "path": obligation.path,
                "status": f"uncoverable:{uncoverable_keys[obligation.key]}",
                "generated": "0", "k_gen": "",
                "witness_formula": obligation.witness_formula,
            })
            continue
        # D only grows, so an obligation once covered stays covered.
        if obligation.key in covered_keys or (
            global_records
            and obligation_is_covered(args, obligation.check_formula, eval_dir, f"ob_{index:03d}")
        ):
            covered_keys.add(obligation.key)
            covered_now += 1
            index_rows.append({
                "kind": obligation.kind, "path": obligation.path,
                "status": "covered", "generated": "0", "k_gen": "",
                "witness_formula": obligation.witness_formula,
            })
            continue

        # One k, not the whole ladder: the ladder plus the doubling retry below
        # would spend ~19 MATLAB calls proving a single obligation unmeetable.
        generated, final_k = smt_generate_with_budget(
            args,
            obligation.witness_formula,
            1,
            obligation_dir / f"gen_{index:03d}",
            1,
            k_gen_init,
            max_k=k_gen_init,
        )
        status = "generated"
        if not generated:
            # UNSAT here means one of two very different things. Retry once with
            # twice the switching budget before believing the obligation cannot
            # be met, otherwise "the horizon was too coarse" gets recorded as
            # "converged".
            retry_k = max(k_gen_init * 2, k_gen_init + 1)
            print(
                f"  obligation {index} unmet at k_gen={final_k}; retrying at k_gen={retry_k} "
                "before calling it uncoverable.",
                flush=True,
            )
            generated, final_k = smt_generate_with_budget(
                args,
                obligation.witness_formula,
                1,
                obligation_dir / f"gen_{index:03d}_retry",
                1,
                retry_k,
                max_k=retry_k,
            )
            if not generated:
                uncoverable_keys[obligation.key] = f"unsat_at_k_gen_{retry_k}"
                status = "uncoverable"
        if generated:
            rows.extend(generated)
            meta.extend([(obligation.witness_formula, "OBLIGATION")] * len(generated))
        index_rows.append({
            "kind": obligation.kind, "path": obligation.path,
            "status": status, "generated": str(len(generated)), "k_gen": str(final_k),
            "witness_formula": obligation.witness_formula,
        })
        print(
            f"  obligation {index} {obligation.kind} {obligation.path}: "
            f"{len(generated)} traces at k_gen={final_k} ({status})",
            flush=True,
        )

    print(
        f"  obligations: {covered_now} already covered, {len(rows)} new traces, "
        f"{len(uncoverable_keys)} marked uncoverable so far",
        flush=True,
    )
    return rows, meta, index_rows


def obligations_all_settled(
    args: argparse.Namespace,
    current_formula: str,
    global_records: Sequence[dict[str, object]],
    round_dir: Path,
    covered_keys: set[str],
    uncoverable_keys: dict[str, str],
) -> tuple[bool, int, int]:
    """Is every obligation of the current phi covered or provably uncoverable?

    This is the convergence statement for obligation mode: an exhausted table,
    not a round counter that happened to run out.
    """
    obligations = derive_obligations(
        current_formula,
        boundary_eps=args.obligation_boundary_eps,
        time_end=args.time_end,
    )
    eval_dir = round_dir / "obligations" / "settled_eval"
    if global_records:
        build_eval_dataset(
            args,
            eval_dir,
            [record["trace"] for record in global_records],
            [int(record["true_label"]) for record in global_records],
        )
    open_count = 0
    for index, obligation in enumerate(obligations):
        if obligation.key in covered_keys or obligation.key in uncoverable_keys:
            continue
        if global_records and obligation_is_covered(
            args, obligation.check_formula, eval_dir, f"settled_{index:03d}"
        ):
            covered_keys.add(obligation.key)
            continue
        open_count += 1
    return open_count == 0, len(obligations), open_count


def apply_method_overrides(args: argparse.Namespace) -> None:
    """Map a formal method name onto the low-level switches it is allowed to change."""
    method = str(args.method).lower()
    method_modes = {
        "restless": "sgrl", "no_ceclass": "sgrl", "noloc": "off", "no_self_coeff": "sgrl", "llm_ce": "sgrl",
    }
    if method == "llm_ce":
        # Only the repair component differs from restless: same EQ, oracle, budget, disambiguation.
        args.repair_backend = "llm"
    if method in method_modes:
        args.repair_localization = method_modes[method]
    if method == "no_ceclass":
        # EQ fixed at Kce = 1 without the class lattice: only models of phi and NOT phi.
        args.ceclass_schedule = "none"
    if method == "no_self_coeff":
        args.repair_localization_self_coefficient_cap = 1


def ablation_flags(args: argparse.Namespace) -> dict[str, object]:
    """RQ3 component switches as they actually reach the repair pipeline."""
    sgrl = args.repair_localization == "sgrl"
    return {
        "responsibility_enabled": sgrl,
        # Without responsibility no backward slice runs, so K is inactive.
        "self_coefficient_enabled": sgrl and int(args.repair_localization_self_coefficient_cap) > 1,
        "gate_enabled": sgrl,
        "distinguishing_probe_enabled": True,
        "localization_score_mode": "responsibility" if sgrl else args.repair_localization,
        "gate_nodes": "top_k_levels",
    }


# ---------------------------------------------------------------------------
# CE-class refinement (equivalence query)
# ---------------------------------------------------------------------------

def ceclass_kce_max(args: argparse.Namespace, formula: str) -> int:
    """Finest split: every segment one sample long. Only outermost temporal operators are split
    (the adapter keeps nested ones whole), each capped at b-a+1 samples, so refinement is done
    once the widest outermost one is."""
    try:
        root = epoch_parse_stl(formula)
    except Exception:
        return max(1, args.ceclass_k)
    step = float(args.time_step) or 1.0
    samples: list[int] = []

    def visit(node) -> None:
        if node.kind in ("always", "eventually"):
            if not math.isinf(float(node.b)):  # unbounded intervals are never split
                samples.append(int(round((float(node.b) - float(node.a)) / step)) + 1)
            return
        for child in (node.child, node.left, node.right):
            if child is not None:
                visit(child)

    visit(root)
    return max(samples, default=1)


def ceclass_splits(class_dir: Path, class_type: str) -> dict[str, int]:
    """Segments per temporal operator (K_v) the adapter actually used, after its lattice-size guard."""
    summary = class_dir / f"{class_type.lower()}_inputs_summary.csv"
    try:
        with summary.open(newline="") as handle:
            row = next(csv.DictReader(handle))
        return {key: int(value) for key, value in json.loads(row.get("splits") or "{}").items()}
    except (OSError, StopIteration, ValueError):
        return {}


def ceclass_layers(kce_max: int) -> list[int]:
    """Doubling schedule 1, 2, 4, ...: equal-split boundaries of layer K stay boundaries of
    layer 2K, so segment j of layer 2K lies in segment j//2 of layer K. The last layer is
    the first power of two >= kce_max, where every operator is at its per-sample cap."""
    layers = [1]
    while layers[-1] < kce_max:
        layers.append(layers[-1] * 2)
    return layers


def take_unseen_traces(
    rows: Sequence[list[float]], seen: set[str], limit: int | None = None,
) -> list[list[float]]:
    """Drop traces already in D (or already taken this round) and record the kept ones."""
    kept: list[list[float]] = []
    for row in rows:
        if limit is not None and len(kept) >= limit:
            break
        key = _trace_fingerprint(row)
        if key in seen:
            continue
        seen.add(key)
        kept.append(row)
    return kept


def generate_ceclass_layer(
    args: argparse.Namespace,
    classes: Sequence[tuple[StructuralClass, int]],
    gen_dir: Path,
    k_gen: int,
    budget: int,
    seen: set[str],
) -> tuple[list[list[float]], list[tuple[str, str]]]:
    """Up to the per-class count of new traces for each class of one Kce layer, capped by budget."""
    rows_out: list[list[float]] = []
    meta_out: list[tuple[str, str]] = []
    for class_index, (structural_class, target_trace_count) in enumerate(classes):
        target_trace_count = min(target_trace_count, budget - len(rows_out))
        if target_trace_count <= 0:
            continue
        theta_list = choose_uniform_theta(
            structural_class.formula_node,
            structural_class.parser,
            args.sample_times,
        )
        theta_budgets = allocate_trace_budget(target_trace_count, len(theta_list))
        for theta_index, (theta, theta_trace_count) in enumerate(zip(theta_list, theta_budgets)):
            if theta_trace_count <= 0:
                continue
            psi_concrete = instantiate_structural_class(structural_class, theta)
            generation_formula = harden_class_generation_formula(psi_concrete, args)
            # VIOLATION_CLASS formula is already structurally negated by CEclass;
            # generate traces that SATISFY it (target_label=1).
            rows, final_k = smt_generate_with_budget(
                args,
                generation_formula,
                1,
                gen_dir / f"{structural_class.class_type.lower()}_{class_index:02d}_{theta_index:02d}",
                theta_trace_count,
                k_gen,
                formula_dialect=structural_class.formula_dialect,
            )
            rows = take_unseen_traces(rows, seen, theta_trace_count)
            print(
                f"  {structural_class.class_type} class {class_index} theta {theta_index}: "
                f"{len(rows)} new traces at k_gen={final_k}",
                flush=True,
            )
            rows_out.extend(rows)
            meta_out.extend([(generation_formula, structural_class.class_type)] * len(rows))
    return rows_out, meta_out


def label_ceclass_records(
    args: argparse.Namespace,
    rows: list[list[float]],
    meta: Sequence[tuple[str, str]],
    current_formula: str,
    oracle_formula: str,
    eval_dir: Path,
) -> list[dict[str, object]]:
    """Replay current and oracle formula on CE-class traces (one oracle query per trace)."""
    if not rows:
        return []
    build_eval_dataset(args, eval_dir, rows, [1] * len(rows))
    # CE-class type is generation metadata only. Use an RTAMT replay
    # for mismatch grouping, summaries, repair entry, and stopping.
    write_labels_csv(
        eval_dir / "source_pred_label.csv",
        [1 if class_type == "SAT_CLASS" else -1 for _, class_type in meta],
    )
    try:
        pred_labels = evaluate_formula_labels(
            args, current_formula, eval_dir, eval_dir / "class_current_monitor_eval.csv",
        )
    except subprocess.CalledProcessError:
        print(
            "  warning: class monitor replay failed; discarding this class pool "
            "instead of using CE-class source labels.",
            flush=True,
        )
        return []
    write_labels_csv(eval_dir / "current_pred_label.csv", pred_labels)
    true_labels = evaluate_formula_labels(
        args, oracle_formula, eval_dir, eval_dir / "class_oracle_eval.csv"
    )
    return [
        make_trace_record(row, class_formula, class_type, pred, truth)
        for row, pred, truth, (class_formula, class_type) in zip(rows, pred_labels, true_labels, meta)
    ]


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    apply_method_overrides(args)
    random.seed(args.seed_base)
    args.trace_metric_counters = {
        "num_generated": 0,
        "num_discriminating": 0,
        "queries_disambiguation": 0,
        "oracle_trace_fingerprints": set(),
        "oracle_trace_lengths": [],
    }
    requested_numeric_repair = (
        bool(args.repair_parameter_refit)
        or bool(args.refit_selected_template)
        or float(args.repair_parameter_ga_ratio) != 0.0
    )
    args.repair_parameter_ga_ratio = 0.0
    args.repair_parameter_refit = False
    args.refit_selected_template = False
    if requested_numeric_repair:
        print(
            "Numeric repair disabled: preserving all STL numeric constants exactly.",
            flush=True,
        )
    args.signal_names = parse_signal_names(args.signal_names)
    args.boolean_signal_names = parse_boolean_signal_names(
        args.boolean_signal_names,
        args.signal_names,
    )
    args.discrete_signal_names = parse_boolean_signal_names(
        args.discrete_signal_names,
        args.signal_names,
    )
    args.static_signal_names = parse_boolean_signal_names(
        args.static_signal_names,
        args.signal_names,
    )
    overlap = set(args.static_signal_names) & (
        set(args.boolean_signal_names) | set(args.discrete_signal_names)
    )
    if overlap:
        raise ValueError(f"Static signals cannot also be Boolean/discrete: {sorted(overlap)}")
    args.static_signal_indexes = [
        args.signal_names.index(name)
        for name in args.static_signal_names
    ]
    args.boolean_signal_indexes = [
        args.signal_names.index(name)
        for name in args.boolean_signal_names
    ]
    args.discrete_signal_indexes = [
        args.signal_names.index(name)
        for name in args.discrete_signal_names
    ]
    args.signal_bounds = parse_signal_bounds(args.signal_bounds, args.signal_names, args)
    args.derived_signal_definitions = parse_derived_signal_definitions(
        args.derived_signal_definitions,
        args.signal_names,
    )
    args.one_hot_groups = parse_one_hot_groups(args.one_hot_groups, args.signal_names)
    args.time_grid_info = apply_trace_point_cap(args, [
        read_formula_from_csv(resolve_path(args.start_formula_csv), args.start_formula_row),
        read_formula_from_csv(resolve_path(args.oracle_formula_csv), args.oracle_formula_row),
    ])
    TRACE_STATS["time_grid"] = args.time_grid_info
    args.sample_times = make_sample_times(args.time_start, args.time_end, args.time_step)
    args.effective_round_trace_budget = effective_round_trace_budget(args)

    n_pts = len(args.sample_times)
    print(
        f"Time grid: [{format_number(args.time_start)}, {format_number(args.time_end)}] "
        f"dt={format_number(args.time_step)} points={n_pts} "
        f"signals={args.signal_names}",
        flush=True,
    )
    print(
        "Signal bounds: "
        + ", ".join(
            f"{name}=[{format_number(lo)},{format_number(hi)}]"
            for name, (lo, hi) in args.signal_bounds.items()
        ),
        flush=True,
    )
    if args.boolean_signal_names:
        print(
            "Boolean trace signals: "
            + ",".join(args.boolean_signal_names)
            + " (generated values snapped to {0,1})",
            flush=True,
        )
    if args.static_signal_names:
        print(
            "Static trace signals: "
            + ",".join(args.static_signal_names)
            + " (time-invariant scenario parameters; generated values pinned to [v,v])",
            flush=True,
        )
    if args.discrete_signal_names:
        print(
            "Discrete trace signals: "
            + ",".join(args.discrete_signal_names)
            + " (inclusive bounds; generated values rounded/clamped to integers)",
            flush=True,
        )
    if args.derived_signal_definitions:
        print(
            "Derived signal identities: "
            + ", ".join(
                (f"{name}={definition['left']}-{definition['right']}"
                 if definition["op"] == "subtract" else f"{name}:{definition['op']}")
                for name, definition in sorted(args.derived_signal_definitions.items())
            )
            + " (hard-constrained in ATG)",
            flush=True,
        )
    print(
        f"Round trace budget: {args.effective_round_trace_budget} "
        "(CE-class=C534)",
        flush=True,
    )
    print("Repair backend: Java BUSTLE repair + Moonlight replay", flush=True)

    output_root = resolve_path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    if args.grammar_path:
        args.generated_grammar_path = ""
        print(f"Repair grammar: {args.grammar_path}", flush=True)
    else:
        args.generated_grammar_path = write_dynamic_repair_grammar(args, output_root)
        print(f"Repair grammar: {args.generated_grammar_path}", flush=True)

    current_formula = canonicalize_candidate_formula_for_loop(
        args,
        read_formula_from_csv(resolve_path(args.start_formula_csv), args.start_formula_row),
    )
    oracle_formula = canonicalize_candidate_formula_for_loop(
        args,
        read_formula_from_csv(resolve_path(args.oracle_formula_csv), args.oracle_formula_row),
    )
    run_config = {
        "edit_space": "structural",
        "repair_edit_max_budget": args.repair_edit_max_budget,
        "rounds": args.rounds,
        "ceclass_k": args.ceclass_k,
        "ceclass_schedule": args.ceclass_schedule,
        "k_gen_init": args.k_gen_init,
        "k_gen": args.k_gen_init,
        "ceclass_class_limit": args.ceclass_class_limit,
        "round_trace_budget": args.effective_round_trace_budget,
        "repair_iterations": args.repair_iterations,
        "repair_population": args.repair_population,
        "repair_localization": args.repair_localization,
        "repair_localization_top_k": args.repair_localization_top_k,
        "repair_localization_top_k_ratio": args.repair_localization_top_k_ratio,
        "type_priority": args.type_priority,
        "repair_localization_self_coefficient_cap": args.repair_localization_self_coefficient_cap,
        "ablation": ablation_flags(args),
        "repair_dataset_mode": args.repair_dataset_mode,
        "generation_mode": args.generation_mode,
        "time_start": args.time_start,
        "time_end": args.time_end,
        "time_step": args.time_step,
        "seed": args.seed_base,
        "initial_trace_ids": [],
        "attempt": args.attempt,
        "keep_intermediate_artifacts": args.keep_intermediate_artifacts,
        "upstream_method": args.upstream_method,
        "split": args.split,
        "reachable_edit_distance": args.reachable_edit_distance or None,
        "G_i": json.loads(args.g_i),
        "G_i_paths": json.loads(args.g_i_paths),
        "early_stop": args.early_stop,
        "patience": args.patience,
        "signals": args.signal_names,
        "signal_bounds": args.signal_bounds,
        "derived_signal_definitions": args.derived_signal_definitions,
        "equiv_horizon": args.equiv_horizon,
        "boolean_signals": args.boolean_signal_names,
        "discrete_signals": args.discrete_signal_names,
        "static_signals": args.static_signal_names,
        "domain_config": {
            "signals": [
                {"source": name, "min": args.signal_bounds[name][0], "max": args.signal_bounds[name][1],
                 "discrete": name in args.discrete_signal_names, "static": name in args.static_signal_names}
                for name in args.signal_names
            ],
            "boolean_signals": args.boolean_signal_names,
            "derived_signal_definitions": args.derived_signal_definitions,
            "time_start": args.time_start, "time_end": args.time_end, "time_step": args.time_step,
        },
    }
    if args.fairness_check:
        run_config["fairness_check"] = json.loads(resolve_path(args.fairness_check).read_text())
        if not run_config["fairness_check"].get("passed", False):
            raise ValueError("T8 fairness check did not pass; refusing to start the run")
    elif str(args.benchmark).lower().startswith(("rq1", "rq2", "rq3")):
        raise ValueError("Formal RQ1/RQ2/RQ3 runs require --fairness-check from experiments.protocol")
    instance_id = args.instance_id or (
        f"{args.dataset_name}_row{args.start_formula_row}"
    )
    replay_entries = []
    if args.replay_traces and not args.dry_run:
        replay_entries = TraceCache(resolve_path(args.replay_traces), instance_id).load(seed=args.seed_base)
        run_config["initial_trace_ids"] = [str(entry["hash"]) for entry in replay_entries]
    run_config.update(experiment_tag=args.experiment_tag, phase=args.phase, cohort=args.cohort,
                      protocol_hash=args.protocol_hash, dataset_hash=args.dataset_hash,
                      total_query_budget=args.total_query_budget, oracle_balance_traces=args.oracle_balance_traces)
    args.query_events_path = output_root / "query_events.jsonl"
    args.phase_metrics_path = output_root / "phase_metrics.json"
    args.current_round = 0
    args.query_events_path.write_text("")
    fault_metadata_path = resolve_path(args.fault_metadata) if args.fault_metadata else None
    recorder = RunRecorder(
        output_root=output_root,
        record_jsonl=resolve_path(args.record_jsonl),
        spec=RunSpec(
            benchmark=args.benchmark,
            instance_id=instance_id,
            source=args.source,
            method=args.method,
            seed=args.seed_base,
            formula_row=args.start_formula_row,
            oracle_formula_row=args.oracle_formula_row,
            config=run_config,
        ),
        faulty_formula=current_formula,
        oracle_formula=oracle_formula,
        fault_metadata_path=fault_metadata_path,
    )
    anchor_formula = current_formula
    anchor_size = formula_size(anchor_formula)
    anchor_atoms = formula_atom_direction_signature(anchor_formula)
    repair_pool: dict[str, dict[str, object]] = {
        anchor_formula: {
            "formula": anchor_formula,
            "first_round": 0,
            "last_selected_round": -1,
            "selected_count": 0,
            "anchor_size": anchor_size,
        }
    }
    parameter_phase_active = False
    structure_fixed_rounds = 0
    structure_fixed_reason = "not_checked"
    trace_seed_uncertain_formula = current_formula
    summary_rows: list[dict[str, str]] = []

    reported_summary_count = 0

    def checkpoint_run() -> None:
        nonlocal reported_summary_count
        recorder.checkpoint(
            summary_rows,
            trace_metrics=trace_metrics_snapshot(args),
        )
        if len(summary_rows) <= reported_summary_count:
            return
        row = summary_rows[-1]
        parts = [
            f"round={row.get('round', '?')}",
            f"traces={row.get('dataset_size', '')}",
            f"global_traces={row.get('global_dataset_size', '')}",
            f"budget={args.effective_round_trace_budget}",
        ]
        old_accuracy = row.get("old_accuracy", "")
        new_accuracy = row.get("new_accuracy", "")
        if old_accuracy:
            parts.append(f"accuracy_old={old_accuracy}")
        if new_accuracy:
            parts.append(f"accuracy_new={new_accuracy}")
        if row.get("search_budget_bump", ""):
            parts.append(f"budget_bump={row['search_budget_bump']}")
        if row.get("solution_mode", ""):
            parts.append(f"status={row['solution_mode']}")
        control_progress(" ".join(part for part in parts if not part.endswith("=")))
        formula_in = row.get("formula_in", "")
        formula_out = row.get("formula_out", "")
        if formula_out and formula_out != formula_in:
            control_progress(f"round={row.get('round', '?')} candidate: {formula_in} -> {formula_out}")
        reported_summary_count = len(summary_rows)
    global_records: list[dict[str, object]] = []
    repair_global_records: list[dict[str, object]] = []
    if replay_entries:
        replay_entries = [replay_entries[index] for index in
                          reserve_query_indices(args, [entry["trace"] for entry in replay_entries])]
    if replay_entries:
        replay_rows = [entry["trace"] for entry in replay_entries]
        # A cache hit saves computation, not query cost: every run pays for its initial labels.
        record_oracle_queries(args, replay_rows, [str(entry.get("generator", "initial")) for entry in replay_entries],
                              cache_hit=True)
        replay_dir = output_root / "initial_trace_replay"
        build_eval_dataset(args, replay_dir, replay_rows, [1] * len(replay_rows))
        replay_predictions = evaluate_formula_labels(
            args, current_formula, replay_dir, replay_dir / "current_eval.csv"
        )
        global_records = [
            make_trace_record(row, "CACHE", "CACHE_REPLAY", prediction, int(entry["oracle_label"]))
            for row, prediction, entry in zip(replay_rows, replay_predictions, replay_entries)
        ]
        repair_global_records = select_repair_records(args, global_records)
        print(f"Replayed {len(global_records)} cached initial traces without Oracle calls.", flush=True)
    search_budget_bump = 0
    no_new_error_rounds = 0
    no_new_error_class_rounds = 0
    seen_error_classes: set[str] = set()
    active_disambiguation_pair: tuple[str, str] | None = None
    # D only grows, so coverage is monotone and worth remembering across rounds.
    # Uncoverable is remembered too, with the reason, so an obligation that the
    # generator cannot express is not retried every round nor mistaken for
    # convergence.
    obligation_covered_keys: set[str] = set()
    obligation_uncoverable_keys: dict[str, str] = {}

    evidence_round_done = False
    for round_index in range(1, args.rounds + 1):
        args.current_round = round_index
        evidence_only = False
        if remaining_query_budget(args) == 0:
            # A matched budget can be smaller than the initial evidence. Then one repair round still runs on the
            # labelled traces already paid for, with no new generation or queries, and the run ends.
            if evidence_round_done or not global_records:
                control_progress(f"round={round_index} STOP query budget exhausted Q={query_count(args)}")
                break
            evidence_only = evidence_round_done = True
            args.effective_round_trace_budget = 0
            control_progress(f"round={round_index} EVIDENCE-ONLY repair on Q={query_count(args)} labelled traces")
        round_dir = output_root / f"round_{round_index:02d}"
        round_dir.mkdir(parents=True, exist_ok=True)
        write_single_formula_csv(round_dir / "current_formula.csv", current_formula)

        print(f"\n=== Round {round_index} ===", flush=True)
        print(f"φ_err: {current_formula}", flush=True)
        control_progress(f"round={round_index} START candidate={current_formula}")
        if args.trace_seed_formula == "initial":
            trace_seed_formula = anchor_formula
        elif args.trace_seed_formula == "uncertainty":
            trace_seed_formula = trace_seed_uncertain_formula
        else:
            trace_seed_formula = current_formula
        write_single_formula_csv(round_dir / "trace_seed_formula.csv", trace_seed_formula)
        print(
            f"trace seed ({args.trace_seed_formula}): {trace_seed_formula}",
            flush=True,
        )

        dataset_records: list[dict[str, object]] = []
        active_disambiguation_trace_count = 0
        active_disambiguation_pair_text = ""
        next_active_disambiguation_pair_text = ""
        if active_disambiguation_pair is not None:
            phi_a, phi_b = active_disambiguation_pair
            active_disambiguation_pair_text = f"A={phi_a} || B={phi_b}"
            with phase_clock(args):
                active_records, active_count, active_index_rows = generate_active_disambiguation_records(
                    args,
                    phi_a,
                    phi_b,
                    current_formula,
                    oracle_formula,
                    round_dir,
                    min(max(0, args.active_disambiguation_trace_budget), args.effective_round_trace_budget),
                    args.k_gen_init + search_budget_bump,
                )
            active_disambiguation_trace_count = active_count
            if active_index_rows:
                write_dict_csv(
                    round_dir / "disambiguation_generation" / "index.csv",
                    active_index_rows,
                    ["direction", "requested", "generated", "k_gen", "formula"],
                )
            if active_records:
                dataset_records.extend(active_records)
                print(
                    f"  active disambiguation added {len(active_records)} replayed traces",
                    flush=True,
                )
            active_disambiguation_pair = None
        ceclass_dir = round_dir / "ceclass"
        obligation_mode = args.generation_mode == "obligation"
        skip_ceclass_for_until = obligation_mode or formula_contains_until(trace_seed_formula)
        if skip_ceclass_for_until and not obligation_mode:
            print(
                "  trace seed contains UNTIL; skipping CE-class-C534 and using "
                "direct independent ATG for this round",
                flush=True,
            )
        use_independent = skip_ceclass_for_until

        class_trace_rows: list[list[float]] = []
        class_meta: list[tuple[str, str]] = []
        remaining_budget = args.effective_round_trace_budget
        if remaining_query_budget(args) is not None:
            remaining_budget = min(remaining_budget, remaining_query_budget(args))
        seen_traces = {_trace_fingerprint(record["trace"]) for record in [*global_records, *dataset_records]}
        k_gen_round = args.k_gen_init + search_budget_bump
        round_kce = 0
        ce_found = False
        layer_log: list[dict[str, object]] = []
        if not skip_ceclass_for_until and args.ceclass_schedule != "none":
            refine = args.ceclass_schedule == "refine"
            kce_max = ceclass_kce_max(args, trace_seed_formula) if refine else max(1, args.ceclass_k)
            layers = ceclass_layers(kce_max) if refine else (kce_max,)
            for kce in layers:
                if remaining_budget <= 0:
                    print("  CE-class query budget exhausted", flush=True)
                    break
                round_kce = kce
                layer_dir = ceclass_dir / f"kce_{kce:02d}" if refine else ceclass_dir
                class_limit = kce if refine else args.ceclass_class_limit
                classes_pos = enumerate_ceclass_classes(
                    args, trace_seed_formula, kce, "SAT_CLASS", class_limit, layer_dir / "sat",
                )
                classes_neg = enumerate_ceclass_classes(
                    args, trace_seed_formula, kce, "VIOLATION_CLASS", class_limit, layer_dir / "violation",
                )
                print(
                    f"  structural classes: SAT={len(classes_pos)} VIOLATION={len(classes_neg)} "
                    f"(Kce={kce}/{kce_max}, class_limit={class_limit}, budget left={remaining_budget})",
                    flush=True,
                )
                if refine and not args.dry_run:
                    # The adapter halves k when the lattice is too large. Stop as soon as a
                    # layer raises no operator's K_v: it would only re-ask the same classes.
                    layer_splits = ceclass_splits(layer_dir / "sat", "SAT_CLASS")
                    for key, value in ceclass_splits(layer_dir / "violation", "VIOLATION_CLASS").items():
                        layer_splits[key] = max(value, layer_splits.get(key, 0))
                    if layer_log and not any(
                        value > layer_log[-1]["splits"].get(key, 0) for key, value in layer_splits.items()
                    ):
                        print(f"  Kce={kce}: no K_v increased ({layer_splits}); refinement saturated", flush=True)
                        break
                else:
                    layer_splits = {}
                if refine:
                    per_class = max(0, args.ceclass_traces_per_class)
                    sat_budgets = [per_class] * len(classes_pos)
                    vio_budgets = [per_class] * len(classes_neg)
                else:
                    sat_budgets, vio_budgets = allocate_ceclass_side_budgets(
                        remaining_budget, len(classes_pos), len(classes_neg),
                    )
                layer_rows, layer_meta = generate_ceclass_layer(
                    args,
                    list(zip(classes_pos, sat_budgets)) + list(zip(classes_neg, vio_budgets)),
                    round_dir / "class_generation" / (f"kce_{kce:02d}" if refine else ""),
                    k_gen_round,
                    remaining_budget,
                    seen_traces,
                )
                remaining_budget -= len(layer_rows)
                layer_records = label_ceclass_records(
                    args, layer_rows, layer_meta, current_formula, oracle_formula,
                    round_dir / "class_eval" / (f"kce_{kce:02d}" if refine else ""),
                )
                for record in layer_records:
                    record["kce"] = kce
                dataset_records.extend(layer_records)
                class_trace_rows.extend(record["trace"] for record in layer_records)
                class_meta.extend((record["class"], record["class_type"]) for record in layer_records)
                layer_ce = sum(1 for record in layer_records if record["is_error"])
                layer_log.append({"kce": kce, "splits": layer_splits, "classes_sat": len(classes_pos), "classes_violation": len(classes_neg),
                                  "traces": len(layer_records), "counterexamples": layer_ce})
                print(f"  Kce={kce}: {len(layer_records)} new traces, {layer_ce} counterexamples", flush=True)
                if layer_ce:
                    ce_found = True
                    break

        if obligation_mode:
            obligation_rows, obligation_meta, obligation_index_rows = generate_obligation_records(
                args,
                current_formula,
                oracle_formula,
                global_records,
                round_dir,
                args.effective_round_trace_budget,
                k_gen_round,
                obligation_covered_keys,
                obligation_uncoverable_keys,
            )
            if obligation_index_rows:
                write_dict_csv(
                    round_dir / "obligations" / "index.csv",
                    obligation_index_rows,
                    ["kind", "path", "status", "generated", "k_gen", "witness_formula"],
                )
            obligation_records = label_ceclass_records(
                args, obligation_rows, obligation_meta, current_formula, oracle_formula,
                round_dir / "class_eval" / "obligations",
            )
            dataset_records.extend(obligation_records)
            class_trace_rows.extend(record["trace"] for record in obligation_records)
            class_meta.extend((record["class"], record["class_type"]) for record in obligation_records)

        # Per-side fallback. A CE-class side can under-deliver either because the
        # lattice produced no class for it or because its classes are
        # unsatisfiable, and then the round is lopsided or single-labelled, which
        # teaches the repair nothing. Top the side up straight from the trace
        # seed formula: SAT traces satisfy it, VIOLATION traces satisfy its
        # negation. The oracle formula is never used for generation here.
        # Not needed once a layer has returned a counterexample.
        if not skip_ceclass_for_until and not args.no_class_seed_fallback and not ce_found:
            total_budget = args.effective_round_trace_budget
            side_fallback_budgets = {
                "SAT_CLASS": total_budget // 2 + (total_budget % 2),
                "VIOLATION_CLASS": total_budget // 2,
            }
            produced_per_side = collections.Counter(
                meta_type for _, meta_type in class_meta
            )
            fallback_rows: list[list[float]] = []
            fallback_meta: list[tuple[str, str]] = []
            fallback_sides = (("SAT_CLASS", 1), ("VIOLATION_CLASS", -1))
            for side_index, (class_type, target_label) in enumerate(fallback_sides):
                side_budget = min(
                    side_fallback_budgets[class_type] - produced_per_side[class_type],
                    -(-remaining_budget // (len(fallback_sides) - side_index)),
                )
                if side_budget <= 0:
                    continue
                print(
                    f"  {class_type}: CE-class delivered "
                    f"{produced_per_side[class_type]}/{side_fallback_budgets[class_type]} "
                    f"traces for this side; topping up from the trace seed formula "
                    f"(target_label={target_label})",
                    flush=True,
                )
                rows, final_k = smt_generate_with_budget(
                    args,
                    trace_seed_formula,
                    target_label,
                    round_dir / "class_generation" / f"{class_type.lower()}_seed_fallback",
                    side_budget,
                    k_gen_round,
                )
                rows = take_unseen_traces(rows, seen_traces, side_budget)
                remaining_budget -= len(rows)
                print(
                    f"  {class_type} seed fallback: {len(rows)} traces at k_gen={final_k}",
                    flush=True,
                )
                fallback_rows.extend(rows)
                fallback_meta.extend([(trace_seed_formula, class_type)] * len(rows))
            fallback_records = label_ceclass_records(
                args, fallback_rows, fallback_meta, current_formula, oracle_formula,
                round_dir / "class_eval" / "seed_fallback",
            )
            for record in fallback_records:
                record["kce"] = round_kce
            dataset_records.extend(fallback_records)
            class_trace_rows.extend(record["trace"] for record in fallback_records)

        if args.ceclass_schedule == "none" and not skip_ceclass_for_until:
            round_kce = 1
        CEKCE_BY_ROUND[str(round_index)] = round_kce
        if not FIRST_EQ:
            eq_records = [record for record in dataset_records if record.get("class_type") != "ACTIVE_DISAMBIGUATION"]
            first_ce = next((index for index, record in enumerate(eq_records, start=1) if record["is_error"]), None)
            FIRST_EQ.update({
                "round": round_index,
                "queries": len(eq_records),
                "ce_found": first_ce is not None,
                "q_to_first_ce": first_ce,
                "kce": round_kce,
                "schedule": args.ceclass_schedule,
            })
        if layer_log:
            ceclass_dir.mkdir(parents=True, exist_ok=True)
            (ceclass_dir / "refinement.json").write_text(json.dumps(
                {"schedule": args.ceclass_schedule, "returned_kce": round_kce,
                 "counterexample_found": ce_found, "layers": layer_log}, indent=2) + "\n")

        independent_rows: list[list[float]] = []
        independent_formula_tags: list[str] = []
        if (not class_trace_rows) and (not args.no_independent) and (not use_independent):
            print(
                "  no CE-class traces generated; enabling independent fallback for this round",
                flush=True,
            )
            use_independent = True
        if use_independent:
            independent_specs = [
                (trace_seed_formula, 1, "INDEPENDENT_ERR_SAT"),
                (trace_seed_formula, -1, "INDEPENDENT_ERR_VIO"),
            ]
            independent_budgets = allocate_trace_budget(
                args.effective_round_trace_budget,
                len(independent_specs),
            )
            for (formula_str, target_label, tag), target_trace_count in zip(
                independent_specs, independent_budgets,
            ):
                if target_trace_count <= 0:
                    continue
                rows, final_k = smt_generate_with_budget(
                    args,
                    formula_str,
                    target_label,
                    round_dir / "independent_generation" / tag.lower(),
                    target_trace_count,
                    args.k_gen_init + search_budget_bump,
                )
                print(f"  independent {tag}: {len(rows)} traces at k_gen={final_k}", flush=True)
                independent_rows.extend(rows)
                independent_formula_tags.extend([tag] * len(rows))

        independent_pred: list[int] = []
        independent_true: list[int] = []
        if independent_rows:
            independent_eval_dir = round_dir / "independent_eval"
            build_eval_dataset(args, independent_eval_dir, independent_rows, [1] * len(independent_rows))
            independent_pred = evaluate_formula_labels(
                args, current_formula, independent_eval_dir,
                round_dir / "independent_pred_eval.csv",
            )
            independent_true = evaluate_formula_labels(
                args, oracle_formula, independent_eval_dir,
                round_dir / "independent_oracle_eval.csv",
            )
            for row, pred, truth, tag in zip(
                independent_rows, independent_pred, independent_true, independent_formula_tags,
            ):
                dataset_records.append(make_trace_record(row, tag, "INDEPENDENT", pred, truth))

        if not dataset_records and not evidence_only:
            if args.dry_run:
                print("  [dry-run] No traces (MATLAB not executed); skipping repair.", flush=True)
                continue
            search_budget_bump += 1
            summary_rows.append({
                "round": str(round_index),
                "formula_in": current_formula,
                "formula_out": current_formula,
                "error_group_count": "0",
                "new_error_class_count": "0",
                "dataset_size": "0",
                "global_dataset_size": str(len(global_records)),
                "repair_dataset_size": str(len(repair_global_records)),
                "repair_dataset_mode": args.repair_dataset_mode,
                "solution_mode": "generation_failure",
                "old_accuracy": "",
                "old_balanced_accuracy": "",
                "old_mcc": "",
                "new_accuracy": "",
                "new_balanced_accuracy": "",
                "new_mcc": "",
                "round_source_accuracy": "",
                "round_categories": "GENERATION_FAILURE",
                "independent_consistent": "false",
                "no_new_error_rounds": str(no_new_error_rounds),
                "no_new_error_class_rounds": str(no_new_error_class_rounds),
                "search_budget_bump": str(search_budget_bump),
                "predicted_labels_new": "",
                "oracle_labels": "",
            })
            write_round_summary(output_root / "loop_summary.csv", summary_rows)
            checkpoint_run()
            print(
                "  No traces were generated in this round; recorded generation failure "
                "and increasing search budget.",
                flush=True,
            )
            continue

        round_budget = args.effective_round_trace_budget
        if remaining_query_budget(args) is not None:
            round_budget = min(round_budget, remaining_query_budget(args))
        dataset_records, budget_status = fit_record_budget(dataset_records, round_budget)
        print(
            f"  round trace budget adjustment: {budget_status}; "
            f"final_trace_count={len(dataset_records)}",
            flush=True,
        )

        if not args.no_oracle_balance_generation and args.oracle_balance_traces > 0:
            available_truth = {int(record["true_label"]) for record in [*global_records, *dataset_records]}
            missing_truth = [label for label in (1, -1) if label not in available_truth]
            oracle_balance_rows: list[list[float]] = []
            oracle_balance_tags: list[str] = []
            for target_truth in missing_truth:
                tag = "ORACLE_BALANCE_SAT" if target_truth == 1 else "ORACLE_BALANCE_VIO"
                rows, final_k = smt_generate_with_budget(
                    args,
                    oracle_formula,
                    target_truth,
                    round_dir / "oracle_balance_generation" / tag.lower(),
                    args.oracle_balance_traces,
                    args.k_gen_init + search_budget_bump,
                )
                print(f"  oracle balance {tag}: {len(rows)} traces at k_gen={final_k}", flush=True)
                oracle_balance_rows.extend(rows)
                oracle_balance_tags.extend([tag] * len(rows))
            if oracle_balance_rows:
                oracle_balance_eval_dir = round_dir / "oracle_balance_eval"
                build_eval_dataset(args, oracle_balance_eval_dir, oracle_balance_rows, [1] * len(oracle_balance_rows))
                oracle_balance_pred = evaluate_formula_labels(
                    args, current_formula, oracle_balance_eval_dir,
                    round_dir / "oracle_balance_pred_eval.csv",
                )
                oracle_balance_true = evaluate_formula_labels(
                    args, oracle_formula, oracle_balance_eval_dir,
                    round_dir / "oracle_balance_oracle_eval.csv",
                )
                record_oracle_queries(args, oracle_balance_rows, "oracle_balance")
                for row, pred, truth, tag in zip(
                    oracle_balance_rows, oracle_balance_pred, oracle_balance_true, oracle_balance_tags,
                ):
                    dataset_records.append(make_trace_record(row, tag, "ORACLE_BALANCE", pred, truth))

        dataset_records = replay_record_labels(
            args,
            dataset_records,
            current_formula,
            oracle_formula,
            round_dir,
            "round_replay_labels",
        )
        if not args.dry_run:
            cache = TraceCache(resolve_path(args.trace_cache_root), instance_id)
            for record in dataset_records:
                cache.put(
                    record["trace"], generator=str(record.get("class_type", "unknown")).lower(),
                    seed=args.seed_base, oracle_label=int(record["true_label"]), round_id=round_index,
                )

        write_trace_record_summary(round_dir / "round_trace_records.csv", dataset_records)
        misclassified_focus_file = round_dir / "misclassified_class_focus.csv"
        write_misclassified_class_focus(
            misclassified_focus_file,
            dataset_records,
            args.repair_class_focus_top_k,
        )
        round_repair_records = select_repair_records(args, dataset_records)
        repair_global_records.extend(round_repair_records)
        for record in dataset_records:
            record.setdefault("round_id", round_index)
        global_records.extend(dataset_records)
        round_pred_current = [int(record["pred_label"]) for record in dataset_records]
        round_truth = [int(record["true_label"]) for record in dataset_records]
        global_rows = [record["trace"] for record in global_records]
        global_truth = [int(record["true_label"]) for record in global_records]
        _, round_accuracy_source = evaluate_round_formula(
            args, current_formula, global_rows, global_truth,
            round_dir, "global_eval_current_all_traces",
        )
        round_category_counts = compute_label_categories(round_pred_current, round_truth)
        category_summary = format_category_counts(round_category_counts)
        print(
            f"  round class-guided categories: {category_summary}; "
            f"all_trace_accuracy={round_accuracy_source:.4f}",
            flush=True,
        )

        error_groups = group_by_class([r for r in dataset_records if bool(r["is_error"])])
        current_error_class_keys = set(error_groups.keys())
        new_error_class_keys = sorted(current_error_class_keys - seen_error_classes)
        if new_error_class_keys:
            seen_error_classes.update(new_error_class_keys)
            no_new_error_class_rounds = 0
        else:
            no_new_error_class_rounds += 1
        if not error_groups:
            no_new_error_rounds += 1
            global_pred_old, global_accuracy_old = evaluate_round_formula(
                args, current_formula, global_rows, global_truth,
                round_dir, "global_eval_old_no_error",
            )
            global_ba_old = balanced_accuracy(global_pred_old, global_truth)
            if global_accuracy_old >= 0.999999 and active_disambiguation_trace_count <= 0:
                # In obligation mode the table is the convergence criterion: an
                # open obligation is a trace nobody has generated yet, so the
                # round must not be treated as settled.
                if args.generation_mode == "obligation":
                    settled, total_obligations, open_obligations = obligations_all_settled(
                        args,
                        current_formula,
                        global_records,
                        round_dir,
                        obligation_covered_keys,
                        obligation_uncoverable_keys,
                    )
                    print(
                        f"  obligation table: {total_obligations} total, "
                        f"{open_obligations} still open, "
                        f"{len(obligation_uncoverable_keys)} uncoverable",
                        flush=True,
                    )
                    if not settled:
                        print(
                            "  obligations not exhausted; continuing instead of stopping.",
                            flush=True,
                        )
                summary_rows.append({
                    "round": str(round_index),
                    "formula_in": current_formula,
                    "formula_out": current_formula,
                    "error_group_count": "0",
                    "new_error_class_count": "0",
                    "dataset_size": str(len(dataset_records)),
                    "global_dataset_size": str(len(global_records)),
                    "repair_dataset_size": str(len(repair_global_records)),
                    "repair_dataset_mode": args.repair_dataset_mode,
                    "solution_mode": "validation",
                    "old_accuracy": f"{global_accuracy_old:.10f}",
                    "old_balanced_accuracy": f"{global_ba_old:.10f}",
                    "new_accuracy": "",
                    "new_balanced_accuracy": "",
                    "round_source_accuracy": f"{round_accuracy_source:.10f}",
                    "round_categories": category_summary,
                    "independent_consistent": "true",
                    "no_new_error_rounds": str(no_new_error_rounds),
                    "no_new_error_class_rounds": str(no_new_error_class_rounds),
                    "search_budget_bump": str(search_budget_bump),
                    "predicted_labels_new": ",".join(str(v) for v in round_pred_current),
                    "oracle_labels": ",".join(str(v) for v in round_truth),
                })
                write_round_summary(output_root / "loop_summary.csv", summary_rows)
                checkpoint_run()
                if args.ceclass_schedule in ("refine", "none"):
                    # The equivalence query spent its budget (or ran out of new traces) without a
                    # counterexample: EQ passes, the hypothesis is returned.
                    print("  EQ found no counterexample; returning the current hypothesis.", flush=True)
                    break
                if (
                    args.early_stop
                    and no_new_error_class_rounds >= args.patience
                ):
                    print(
                        f"  No new misclassified classes for {no_new_error_class_rounds} rounds; stopping.",
                        flush=True,
                    )
                    break
                print(
                    f"  No new misclassified classes ({no_new_error_class_rounds}/{args.patience}); continuing.",
                    flush=True,
                )
                continue
            if active_disambiguation_trace_count > 0:
                print(
                    "  Current round has no current-formula mismatches, but active "
                    "disambiguation traces were added; re-evaluating the pool.",
                    flush=True,
                )
            else:
                print(
                    "  Current round has no mismatches, but D_global is not 100% accurate; "
                    "entering repair on accumulated traces.",
                    flush=True,
                )
        else:
            no_new_error_rounds = 0

        repair_rows = [record["trace"] for record in repair_global_records]
        repair_labels = [int(record["true_label"]) for record in repair_global_records]
        repair_dataset_dir = round_dir / "repair_dataset"
        build_eval_dataset(args, repair_dataset_dir, repair_rows, repair_labels)
        write_trace_metadata(repair_dataset_dir / "trace_metadata.csv", repair_global_records)

        if not repair_rows:
            global_pred_old = []
            global_accuracy_old = 0.0
            global_ba_old = balanced_accuracy(global_pred_old, repair_labels)
            search_budget_bump += 1
            summary_rows.append({
                "round": str(round_index),
                "formula_in": current_formula,
                "formula_out": current_formula,
                "error_group_count": str(len(error_groups)),
                "new_error_class_count": str(len(new_error_class_keys)),
                "dataset_size": str(len(dataset_records)),
                "global_dataset_size": str(len(global_records)),
                "repair_dataset_size": str(len(repair_rows)),
                "repair_dataset_mode": args.repair_dataset_mode,
                "solution_mode": "empty_D",
                "old_accuracy": f"{global_accuracy_old:.10f}",
                "old_balanced_accuracy": f"{global_ba_old:.10f}",
                "new_accuracy": "",
                "new_balanced_accuracy": "",
                "round_source_accuracy": f"{round_accuracy_source:.10f}",
                "round_categories": category_summary,
                "independent_consistent": "false",
                "no_new_error_rounds": str(no_new_error_rounds),
                "no_new_error_class_rounds": str(no_new_error_class_rounds),
                "search_budget_bump": str(search_budget_bump),
                "predicted_labels_new": ",".join(str(v) for v in round_pred_current),
                "oracle_labels": ",".join(str(v) for v in round_truth),
            })
            write_round_summary(output_root / "loop_summary.csv", summary_rows)
            checkpoint_run()
            if args.early_stop and no_new_error_class_rounds >= args.patience:
                print(
                    f"  No new misclassified classes for {no_new_error_class_rounds} rounds; stopping.",
                    flush=True,
                )
                break
            print(
                "  Repair skipped: repair dataset D is empty; increasing search budget.",
                flush=True,
            )
            continue

        enough_traces_for_refit = len(repair_rows) >= max(0, args.parameter_refit_min_traces)
        round_parameter_refit_enabled = bool(parameter_phase_active and enough_traces_for_refit)
        round_repair_phase = "parameter" if round_parameter_refit_enabled else "structure"
        fixed_budget_mode = len(repair_rows) >= max(0, args.multi_solution_min_traces)
        early_stop_on_accuracy1 = not fixed_budget_mode
        multi_solution_mode = fixed_budget_mode
        round_solution_mode = "fixed_budget" if fixed_budget_mode else "accuracy1_early_stop"
        print(
            f"  repair phase: {round_repair_phase} "
            f"(solution_mode={round_solution_mode}, "
            f"structure_fixed_rounds={structure_fixed_rounds}, "
            f"repair_traces={len(repair_rows)}/{args.parameter_refit_min_traces}, "
            f"reason={structure_fixed_reason})",
            flush=True,
        )

        target_new_candidates = max(1, args.repair_population)
        # One CE-class round performs exactly one Java repair call.  Older
        # versions split this budget across an anchor attempt plus extra
        # corpus-parent attempts, which produced repair_attempt_00/01/... in
        # the same round.  Keep the cross-round Pool for evaluation/history,
        # but do not launch additional repair processes here.
        anchor_budget = target_new_candidates
        corpus_budget = 0
        corpus_parents: list[str] = []

        generated_formulas: list[str] = []
        round_accuracy1_formulas: list[str] = []
        generated_seen: set[str] = set()
        existing_pool_formulas: set[str] = set(repair_pool.keys())
        attempt_index = 0
        single_solution_found = False

        def run_one_step_parent(parent_formula: str, budget: int, tag: str) -> None:
            nonlocal attempt_index, single_solution_found
            if budget <= 0 or (single_solution_found and early_stop_on_accuracy1):
                return
            local_args = copy.copy(args)
            local_args.repair_iterations = max(1, args.repair_iterations)
            if fixed_budget_mode:
                # Once D has enough traces, every selected parent gets the full
                # configured GA budget; accuracy=1 candidates are collected, not used
                # to stop the search immediately.
                local_args.repair_population = max(2, args.repair_population)
            else:
                # Early rounds are cheap: search until the first accuracy=1 candidate
                # is found, then move that formula into the next trace round.
                local_args.repair_population = max(2, budget)
            local_args.repair_stop_on_perfect = bool(args.repair_stop_on_perfect and early_stop_on_accuracy1)
            local_args.repair_parameter_refit = bool(args.repair_parameter_refit and round_parameter_refit_enabled)
            attempt_dir = round_dir / f"repair_attempt_{attempt_index:02d}"
            attempt_formula_csv = attempt_dir / "input_formula.csv"
            write_single_formula_csv(attempt_formula_csv, parent_formula)
            ensure_eval_dataset(local_args, repair_dataset_dir, repair_rows, repair_labels)
            try:
                if args.repair_backend == "llm":
                    repair_csv = run_llm_repair_round(
                        local_args, round_index, attempt_index, parent_formula, repair_rows, repair_labels,
                        round_dir, oracle_formula, instance_id)
                else:
                    repair_csv = run_repair_round(
                        local_args,
                        round_index,
                        attempt_index,
                        attempt_formula_csv,
                        repair_dataset_dir,
                        round_dir,
                    )
            except subprocess.CalledProcessError as exc:
                failure_path = attempt_dir / "repair_process_failure.txt"
                failure_path.parent.mkdir(parents=True, exist_ok=True)
                failure_path.write_text(
                    "Repair process failed before producing a usable candidate.\n\n"
                    + _format_process_failure(exc)
                )
                control_progress(
                    f"ERROR round={round_index} repair_attempt={attempt_index} "
                    f"exit={exc.returncode} detail={failure_path}"
                )
                print(f"    {tag} parent repair failed; skipping", flush=True)
                attempt_index += 1
                return
            kept = 0
            kept_accuracy_one = 0
            for formula, _unused_score, candidate_accuracy in repair_csv_candidate_formula_scores_with_accuracy(local_args, repair_csv):
                if (
                    formula == parent_formula
                    or formula in existing_pool_formulas
                    or formula in generated_seen
                ):
                    continue
                accuracy_is_perfect = candidate_accuracy is not None and candidate_accuracy >= 0.999999
                if accuracy_is_perfect:
                    generated_seen.add(formula)
                    generated_formulas.append(formula)
                    round_accuracy1_formulas.append(formula)
                    kept_accuracy_one += 1
                    if early_stop_on_accuracy1:
                        single_solution_found = True
                        break
                    continue
                generated_seen.add(formula)
                generated_formulas.append(formula)
                kept += 1
            print(
                f"    {tag}: requested={budget}, kept={kept}, "
                f"kept_accuracy1={kept_accuracy_one}, parent_size={formula_size(parent_formula)}",
                flush=True,
            )
            attempt_index += 1

        # Repair the formula selected by the previous round.  The immutable
        # anchor remains in the cross-round pool for comparison, but using it
        # as the Java repair seed would discard all accumulated repairs.
        run_one_step_parent(current_formula, anchor_budget, "current")

        added_to_pool = 0
        for formula in generated_formulas:
            repair_pool.setdefault(
                formula,
                {
                    "formula": formula,
                    "first_round": round_index,
                    "last_selected_round": round_index - 1,
                    "selected_count": 0,
                    "anchor_size": anchor_size,
                },
            )
            added_to_pool += 1

        print(
            f"    repair pool: added={added_to_pool}, "
            f"pool_size={len(repair_pool)}, anchor_budget={anchor_budget}, "
            f"corpus_budget={corpus_budget}, corpus_parents={len(corpus_parents)}",
            flush=True,
        )

        global_pred_old, global_accuracy_old = evaluate_round_formula(
            args, current_formula, repair_rows, repair_labels,
            round_dir, "global_eval_old",
        )
        global_ba_old = balanced_accuracy(global_pred_old, repair_labels)

        pool_eval_rows: list[dict[str, str]] = []
        pool_predictions: dict[str, tuple[int, ...]] = {}
        pool_robustness_by_formula: dict[str, list[float]] = {}
        best_pool_formula: str | None = None
        best_pool_key: tuple[float, ...] | None = None
        best_uncertainty_formula: str | None = None
        # One dataset for the whole pool: every candidate is scored on the same traces, so
        # build it once and only swap the formula (was: one identical copy per candidate).
        pool_eval_dir = round_dir / "repair_pool_eval"
        pool_dataset_dir = pool_eval_dir / "shared_dataset"
        build_eval_dataset(args, pool_dataset_dir, repair_rows, repair_labels)
        pool_formulas = list(repair_pool.keys())
        batched_csv: dict[str, Path] = {}
        # RESPSTL_POOL_BATCH=0 switches back to one evaluator JVM per candidate (results are identical).
        batch_jobs = [(formula, pool_eval_dir / f"pool_{index:04d}.csv") for index, formula in enumerate(pool_formulas)
                      if "\t" not in formula and "\n" not in formula] if os.environ.get("RESPSTL_POOL_BATCH", "1") != "0" else []
        try:
            run_evaluator_batch(args, batch_jobs, pool_dataset_dir)
            batched_csv = {formula: path for formula, path in batch_jobs} if not args.dry_run else {}
        except subprocess.CalledProcessError:
            batched_csv = {}  # the batch itself failed: score every candidate one by one, as before
        for pool_index, formula in enumerate(pool_formulas):
            try:
                if formula in batched_csv:
                    pool_metrics = metrics_from_eval_csv(batched_csv[formula], repair_labels)
                else:
                    pool_metrics = evaluate_metrics_from_dataset(
                        args,
                        formula,
                        pool_dataset_dir,
                        repair_labels,
                        pool_eval_dir / f"pool_{pool_index:04d}.csv",
                    )
            except subprocess.CalledProcessError:
                pool_accuracy = float("-inf")
                pool_ba = float("-inf")
            else:
                pool_accuracy = pool_metrics.accuracy
                pool_ba = pool_metrics.balanced_accuracy
                pool_predictions[formula] = tuple(pool_metrics.predicted)
                if args.trace_seed_formula == "uncertainty":
                    pool_robustness_by_formula[formula] = list(pool_metrics.robustness)
            finally:
                # Metrics are in memory; every candidate would otherwise keep a
                # full copy of the round's traces (GBs per round on large pools).
                prune_pool_eval_artifacts(round_dir / "repair_pool_eval", f"pool_{pool_index:04d}")
            atom_coverage_count, atom_coverage_ratio = anchor_atom_coverage_score(formula, anchor_atoms)
            pool_entry = repair_pool.setdefault(formula, {})
            frozen_finalist = bool(pool_accuracy >= 0.999999)
            pool_entry["frozen_finalist"] = frozen_finalist
            first_round = int(repair_pool.get(formula, {}).get("first_round", 0))
            last_selected_round = int(repair_pool.get(formula, {}).get("last_selected_round", first_round - 1))
            selected_count = int(repair_pool.get(formula, {}).get("selected_count", 0))
            key = (
                pool_accuracy,
                atom_coverage_ratio,
                atom_coverage_count,
                -formula_size(formula),
                -first_round,
            )
            pool_eval_rows.append({
                "pool_index": str(pool_index),
                "first_round": str(first_round),
                "last_selected_round": str(last_selected_round),
                "selected_count": str(selected_count),
                "accuracy": "" if pool_accuracy == float("-inf") else f"{pool_accuracy:.10f}",
                "balanced_accuracy": "" if pool_ba == float("-inf") else f"{pool_ba:.10f}",
                "anchor_atom_coverage": str(atom_coverage_count),
                "anchor_atom_coverage_ratio": f"{atom_coverage_ratio:.10f}",
                "frozen_finalist": str(frozen_finalist).lower(),
                "formula_size": str(formula_size(formula)),
                "selected": "false",
                "formula": formula,
            })
            if best_pool_key is None or key > best_pool_key:
                best_pool_key = key
                best_pool_formula = formula
        if os.environ.get("RESPSTL_KEEP_REPAIR_DEBUG") != "1":
            shutil.rmtree(pool_dataset_dir, ignore_errors=True)
        if args.trace_seed_formula == "uncertainty":
            contrast_scores = candidate_boundary_contrast_scores(pool_robustness_by_formula)
            best_uncertainty_key: tuple[float, ...] | None = None
            for formula, (contrast_score, contrast_near_zero_ratio) in contrast_scores.items():
                row = next((item for item in pool_eval_rows if item.get("formula") == formula), None)
                try:
                    pool_acc_for_uncertainty = float(row.get("accuracy") or "-inf") if row is not None else float("-inf")
                    first_round_for_uncertainty = float(row.get("first_round") or "0") if row is not None else 0.0
                except ValueError:
                    pool_acc_for_uncertainty = float("-inf")
                    first_round_for_uncertainty = 0.0
                uncertainty_key = (
                    contrast_score,
                    contrast_near_zero_ratio,
                    -pool_acc_for_uncertainty,
                    -formula_size(formula),
                    -first_round_for_uncertainty,
                )
                if best_uncertainty_key is None or uncertainty_key > best_uncertainty_key:
                    best_uncertainty_key = uncertainty_key
                    best_uncertainty_formula = formula

        selection_deferred_for_disambiguation = False
        perfect_rows: list[dict[str, str]] = []
        for row in pool_eval_rows:
            try:
                row_accuracy = float(row.get("accuracy") or "nan")
            except ValueError:
                row_accuracy = float("nan")
            if row_accuracy >= 0.999999:
                perfect_rows.append(row)

        perfect_by_formula = {row.get("formula", ""): row for row in perfect_rows}
        perfect_formulas: list[str] = []
        perfect_seen: set[str] = set()
        if early_stop_on_accuracy1 and round_accuracy1_formulas:
            # Before the trace threshold, do exactly the cheap transition:
            # first accuracy=1 repair result becomes the next trace seed formula.
            perfect_formulas = [round_accuracy1_formulas[0]]
        else:
            # In fixed-budget mode, collect every accuracy=1 candidate found in the
            # current repair pass first, then add historical accuracy=1 pool members.
            for formula in round_accuracy1_formulas:
                if formula in perfect_by_formula and formula not in perfect_seen:
                    perfect_formulas.append(formula)
                    perfect_seen.add(formula)
            for row in perfect_rows:
                formula = row.get("formula", "")
                if formula and formula not in perfect_seen:
                    perfect_formulas.append(formula)
                    perfect_seen.add(formula)

        if fixed_budget_mode and len(perfect_formulas) > 1:
            per_pair_disambiguation_budget = max(0, args.active_disambiguation_trace_budget)
            disambiguation_step = 0
            print(
                f"  active disambiguation starts now: {len(perfect_formulas)} accuracy=1 candidates",
                flush=True,
            )
            while len(perfect_formulas) > 1 and per_pair_disambiguation_budget > 0:
                shrank_this_pass = False
                tried_pair_count = 0
                current_perfect = list(perfect_formulas)
                for i in range(len(current_perfect)):
                    if shrank_this_pass or len(perfect_formulas) <= 1:
                        break
                    for j in range(i + 1, len(current_perfect)):
                        if shrank_this_pass or len(perfect_formulas) <= 1:
                            break
                        phi_a, phi_b = current_perfect[i], current_perfect[j]
                        if phi_a not in perfect_formulas or phi_b not in perfect_formulas:
                            continue
                        tried_pair_count += 1
                        pair_text = f"A={phi_a} || B={phi_b}"
                        next_active_disambiguation_pair_text = pair_text
                        if active_disambiguation_pair_text:
                            active_disambiguation_pair_text += f" ; immediate_{disambiguation_step}: {pair_text}"
                        else:
                            active_disambiguation_pair_text = f"immediate_{disambiguation_step}: {pair_text}"
                        disambiguation_dir = round_dir / f"immediate_disambiguation_{disambiguation_step:02d}"
                        with phase_clock(args):
                            active_records_now, active_count_now, active_index_rows = generate_active_disambiguation_records(
                                args,
                                phi_a,
                                phi_b,
                                current_formula,
                                oracle_formula,
                                disambiguation_dir,
                                per_pair_disambiguation_budget,
                                args.k_gen_init + search_budget_bump,
                            )
                        if active_index_rows:
                            write_dict_csv(
                                disambiguation_dir / "disambiguation_generation" / "index.csv",
                                active_index_rows,
                                ["direction", "requested", "generated", "k_gen", "formula"],
                            )
                        disambiguation_step += 1
                        if not active_records_now:
                            print(
                                "  active disambiguation pair produced no replay-verified separating trace; "
                                "trying the next accuracy=1 pair.",
                                flush=True,
                            )
                            continue

                        active_disambiguation_trace_count += active_count_now
                        for record in active_records_now:
                            record.setdefault("round_id", round_index)
                        dataset_records.extend(active_records_now)
                        global_records.extend(active_records_now)
                        active_repair_records = select_repair_records(args, active_records_now)
                        repair_global_records.extend(active_repair_records)
                        repair_rows.extend(record["trace"] for record in active_repair_records)
                        repair_labels.extend(int(record["true_label"]) for record in active_repair_records)
                        build_eval_dataset(args, repair_dataset_dir, repair_rows, repair_labels)
                        write_trace_metadata(
                            repair_dataset_dir / "trace_metadata.csv",
                            repair_global_records,
                        )

                        with phase_clock(args):
                            survivors = filter_formulas_consistent_with_records(
                                args,
                                perfect_formulas,
                                active_records_now,
                                disambiguation_dir / "candidate_filter",
                            )
                        if survivors and len(survivors) < len(perfect_formulas):
                            perfect_formulas = survivors
                            shrank_this_pass = True
                            print(
                                f"  active disambiguation kept {len(perfect_formulas)} candidates",
                                flush=True,
                            )
                        else:
                            print(
                                "  active disambiguation trace did not shrink the accuracy=1 set; "
                                "trying the next accuracy=1 pair.",
                                flush=True,
                            )
                if not shrank_this_pass:
                    print(
                        f"  active disambiguation tried {tried_pair_count} accuracy=1 pairs but could not isolate one formula.",
                        flush=True,
                    )
                    break

            round_pred_current = [int(record["pred_label"]) for record in dataset_records]
            round_truth = [int(record["true_label"]) for record in dataset_records]
            global_rows = [record["trace"] for record in global_records]
            global_truth = [int(record["true_label"]) for record in global_records]
            round_category_counts = compute_label_categories(round_pred_current, round_truth)
            category_summary = format_category_counts(round_category_counts)
            write_trace_record_summary(round_dir / "round_trace_records.csv", dataset_records)
            global_pred_old, global_accuracy_old = evaluate_round_formula(
                args, current_formula, repair_rows, repair_labels,
                round_dir, "global_eval_old_after_disambiguation",
            )
            global_ba_old = balanced_accuracy(global_pred_old, repair_labels)

        if perfect_formulas:
            best_pool_formula = perfect_formulas[0]
            active_disambiguation_pair = None

        phi_new = best_pool_formula
        if best_uncertainty_formula is not None:
            trace_seed_uncertain_formula = best_uncertainty_formula
        refit_selected_formula = ""
        if (
            phi_new is not None
            and args.refit_selected_template
            and args.repair_parameter_refit
            and round_parameter_refit_enabled
        ):
            refit_args = copy.copy(args)
            refit_args.repair_mode = "baseline"
            refit_args.repair_iterations = 1
            refit_args.repair_population = 1
            refit_args.repair_parameter_refit = True
            refit_attempt_dir = round_dir / f"repair_attempt_{attempt_index:02d}"
            refit_formula_csv = refit_attempt_dir / "input_formula.csv"
            write_single_formula_csv(refit_formula_csv, phi_new)
            ensure_eval_dataset(refit_args, repair_dataset_dir, repair_rows, repair_labels)
            try:
                refit_csv = run_repair_round(
                    refit_args,
                    round_index,
                    attempt_index,
                    refit_formula_csv,
                    repair_dataset_dir,
                    round_dir,
                )
                refit_candidates = repair_csv_candidate_formula_scores(refit_args, refit_csv)
            except subprocess.CalledProcessError as exc:
                failure_path = refit_attempt_dir / "repair_process_failure.txt"
                failure_path.parent.mkdir(parents=True, exist_ok=True)
                failure_path.write_text(
                    "Selected-template parameter refit failed.\n\n"
                    + _format_process_failure(exc)
                )
                control_progress(
                    f"ERROR round={round_index} refit_attempt={attempt_index} "
                    f"exit={exc.returncode} detail={failure_path}"
                )
                refit_candidates = []
            if refit_candidates:
                refit_selected_formula, _ = refit_candidates[0]
                if refit_selected_formula:
                    phi_new = refit_selected_formula
                    entry = repair_pool.setdefault(
                        phi_new,
                        {
                            "formula": phi_new,
                            "first_round": round_index,
                            "last_selected_round": round_index - 1,
                            "selected_count": 0,
                            "anchor_size": anchor_size,
                        },
                    )
            attempt_index += 1
        round_pred_new: list[int] = []
        global_accuracy_new = float("-inf")
        global_ba_new = float("-inf")
        strictness_reverts: list[str] = []
        selected_formula_before_minimality = phi_new or ""
        if phi_new is not None:
            round_pred_new, global_accuracy_new = evaluate_round_formula(
                args, phi_new, repair_rows, repair_labels,
                round_dir, "global_eval_new",
            )
            phi_new, round_pred_new, strictness_reverts = remove_prediction_neutral_strictness_edits(
                args,
                phi_new,
                current_formula,
                round_pred_new,
                repair_rows,
                repair_labels,
                round_dir / "selected_formula_minimality",
            )
            if strictness_reverts:
                global_accuracy_new = (
                    sum(1 for pred, gold in zip(round_pred_new, repair_labels) if pred == gold)
                    / len(repair_labels)
                    if repair_labels else 0.0
                )
                print(
                    "  removed prediction-neutral strictness edit(s): "
                    + "; ".join(strictness_reverts),
                    flush=True,
                )
                entry = repair_pool.setdefault(
                    phi_new,
                    {
                        "formula": phi_new,
                        "first_round": round_index,
                        "last_selected_round": round_index - 1,
                        "selected_count": 0,
                        "anchor_size": anchor_size,
                    },
                )
                pool_predictions[phi_new] = tuple(round_pred_new)
                atom_coverage_count, atom_coverage_ratio = anchor_atom_coverage_score(phi_new, anchor_atoms)
                pool_eval_rows.append({
                    "pool_index": str(len(pool_eval_rows)),
                    "first_round": str(entry.get("first_round", round_index)),
                    "last_selected_round": str(entry.get("last_selected_round", round_index - 1)),
                    "selected_count": str(entry.get("selected_count", 0)),
                    "accuracy": f"{global_accuracy_new:.10f}",
                    "balanced_accuracy": f"{balanced_accuracy(round_pred_new, repair_labels):.10f}",
                    "anchor_atom_coverage": str(atom_coverage_count),
                    "anchor_atom_coverage_ratio": f"{atom_coverage_ratio:.10f}",
                    "frozen_finalist": str(global_accuracy_new >= 0.999999).lower(),
                    "formula_size": str(formula_size(phi_new)),
                    "selected": "false",
                    "formula": phi_new,
                })
            global_ba_new = balanced_accuracy(round_pred_new, repair_labels)
            for row in pool_eval_rows:
                if row["formula"] == phi_new:
                    row["selected"] = "true"

        fixed_this_round, fixed_reason, fixed_top_count = structure_phase_status(
            pool_eval_rows,
            pool_predictions,
            args.parameter_phase_top_n,
        )
        if fixed_this_round:
            structure_fixed_rounds += 1
            structure_fixed_reason = fixed_reason
        else:
            structure_fixed_rounds = 0
            structure_fixed_reason = fixed_reason
        if (
            not parameter_phase_active
            and structure_fixed_rounds >= max(1, args.parameter_phase_stability_rounds)
        ):
            parameter_phase_active = True
            structure_fixed_reason = f"{structure_fixed_reason};parameter_phase_enabled"

        write_dict_csv(
            round_dir / "repair_pool_summary.csv",
            pool_eval_rows,
            [
                "pool_index",
                "first_round",
                "last_selected_round",
                "selected_count",
                "accuracy",
                "balanced_accuracy",
                "anchor_atom_coverage",
                "anchor_atom_coverage_ratio",
                "frozen_finalist",
                "formula_size",
                "selected",
                "formula",
            ],
        )
        summary_rows.append({
            "round": str(round_index),
            "formula_in": current_formula,
            "formula_out": phi_new or current_formula,
            "selected_formula_before_minimality": selected_formula_before_minimality,
            "neutral_strictness_reverts": "; ".join(strictness_reverts),
            "trace_seed_mode": args.trace_seed_formula,
            "trace_seed_formula": trace_seed_formula,
            "active_disambiguation_traces": str(active_disambiguation_trace_count),
            "active_disambiguation_pair": active_disambiguation_pair_text,
            "next_active_disambiguation_pair": next_active_disambiguation_pair_text,
            "selection_deferred_for_disambiguation": str(selection_deferred_for_disambiguation).lower(),
            "error_group_count": str(len(error_groups)),
            "new_error_class_count": str(len(new_error_class_keys)),
            "dataset_size": str(len(dataset_records)),
            "global_dataset_size": str(len(global_records)),
            "repair_dataset_size": str(len(repair_rows)),
            "repair_dataset_mode": args.repair_dataset_mode,
            "solution_mode": round_solution_mode,
            "multi_solution_min_traces": str(args.multi_solution_min_traces),
            "multi_solution_k": str(args.multi_solution_k),
            "old_accuracy": f"{global_accuracy_old:.10f}",
            "old_balanced_accuracy": f"{global_ba_old:.10f}",
            "new_accuracy": "" if phi_new is None else f"{global_accuracy_new:.10f}",
            "new_balanced_accuracy": "" if phi_new is None else f"{global_ba_new:.10f}",
            "refit_selected_template": str(bool(args.refit_selected_template and args.repair_parameter_refit and round_parameter_refit_enabled)).lower(),
            "parameter_refit_enabled": str(round_parameter_refit_enabled).lower(),
            "parameter_refit_min_traces": str(args.parameter_refit_min_traces),
            "refit_selected_formula": refit_selected_formula,
            "repair_phase": round_repair_phase,
            "parameter_phase_active_next": str(parameter_phase_active).lower(),
            "structure_fixed_rounds": str(structure_fixed_rounds),
            "structure_fixed_reason": structure_fixed_reason,
            "structure_fixed_top_count": str(fixed_top_count),
            "round_source_accuracy": f"{round_accuracy_source:.10f}",
            "round_categories": category_summary,
            "independent_consistent": "false",
            "no_new_error_rounds": str(no_new_error_rounds),
            "no_new_error_class_rounds": str(no_new_error_class_rounds),
            "search_budget_bump": str(search_budget_bump),
            "predicted_labels_new": ",".join(str(v) for v in round_pred_new),
            "oracle_labels": ",".join(str(v) for v in repair_labels),
        })
        write_round_summary(output_root / "loop_summary.csv", summary_rows)
        checkpoint_run()

        if args.early_stop and no_new_error_class_rounds >= args.patience:
            print(
                f"  No new misclassified classes for {no_new_error_class_rounds} rounds; stopping.",
                flush=True,
            )
            break

        if phi_new is None:
            search_budget_bump += 1
            print("  No selected repair candidate; increasing search budget and continuing.", flush=True)
            continue

        if global_accuracy_new < global_accuracy_old - 1e-12:
            search_budget_bump += 1
            print("  No accuracy improvement; increasing search budget and continuing.", flush=True)
            continue

        current_formula = phi_new

    write_round_summary(output_root / "loop_summary.csv", summary_rows)
    final_formula = current_formula
    if summary_rows:
        last_summary = summary_rows[-1]
        old_score = float(last_summary.get("old_accuracy") or "-inf")
        new_score = float(last_summary.get("new_accuracy") or "-inf")
        if new_score >= old_score - 1.0e-12 and last_summary.get("formula_out"):
            final_formula = last_summary["formula_out"]

    observed: dict[str, object] = {
        "name": "accumulated_generated_traces",
        "trace_count": len(global_records),
        "boolean_agreement": None,
        "robustness_sign_agreement": None,
        "robustness_mae": None,
    }
    if global_records and not args.dry_run:
        observed_rows = [record["trace"] for record in global_records]
        observed_labels = [int(record["true_label"]) for record in global_records]
        try:
            repaired_metrics = evaluate_round_metrics(
                args,
                final_formula,
                observed_rows,
                observed_labels,
                output_root / "final_evaluation",
                "repaired",
            )
            oracle_metrics = evaluate_round_metrics(
                args,
                oracle_formula,
                observed_rows,
                observed_labels,
                output_root / "final_evaluation",
                "oracle",
            )
            count = min(len(repaired_metrics.predicted), len(oracle_metrics.predicted))
            if count:
                observed["boolean_agreement"] = sum(
                    repaired_metrics.predicted[i] == oracle_metrics.predicted[i]
                    for i in range(count)
                ) / count
            robust_pairs = [
                (left, right)
                for left, right in zip(repaired_metrics.robustness, oracle_metrics.robustness)
                if math.isfinite(left) and math.isfinite(right)
            ]
            if robust_pairs:
                observed["robustness_sign_agreement"] = sum(
                    (left >= 0.0) == (right >= 0.0) for left, right in robust_pairs
                ) / len(robust_pairs)
                observed["robustness_mae"] = sum(
                    abs(left - right) for left, right in robust_pairs
                ) / len(robust_pairs)
        except (subprocess.CalledProcessError, OSError, ValueError) as exc:
            observed["evaluation_error"] = str(exc)
    observed["first_eq"] = dict(FIRST_EQ)
    first_gate_files = sorted(output_root.glob("round_*/repair_attempt_*/repair_output/first_gate.json"))
    if first_gate_files:
        try:
            observed["first_gate"] = {"source": str(first_gate_files[0].relative_to(output_root)),
                                      **json.loads(first_gate_files[0].read_text())}
        except (OSError, ValueError):
            observed["first_gate"] = None
    else:
        observed["first_gate"] = None
    gate_steps = []
    for expansion_file in sorted(output_root.glob("round_*/repair_attempt_*/repair_output/gate_expansions.jsonl")):
        try:
            gate_steps.extend({"source": str(expansion_file.relative_to(output_root)), **json.loads(line)}
                              for line in expansion_file.read_text().splitlines() if line.strip())
        except (OSError, ValueError):
            pass
    observed["gate_expansions"] = gate_steps
    edits_trialled = edits_skipped = 0
    for counts_file in sorted(output_root.glob("round_*/repair_attempt_*/repair_output/edit_counts.jsonl")):
        try:
            for line in counts_file.read_text().splitlines():
                if line.strip():
                    counts = json.loads(line)
                    edits_trialled += int(counts.get("edits_trialled", 0))
                    edits_skipped += int(counts.get("edits_skipped_by_type", 0))
        except (OSError, ValueError):
            pass
    observed["edits_trialled_total"] = edits_trialled
    observed["edits_skipped_by_type"] = edits_skipped
    run_record = recorder.finalize(
        summary_rows,
        final_formula=final_formula,
        observed=observed,
        trace_metrics=trace_metrics_snapshot(args),
    )
    write_traces_index(output_root / "traces_index.csv", global_records, len(args.signal_names))
    if not args.keep_intermediate_artifacts and not str(args.benchmark).startswith("stage0_"):
        try:
            artifact_manifest = compact_completed_run(output_root)
            control_progress(
                "ARTIFACTS compacted "
                f"files={artifact_manifest.get('files_removed', 0)} "
                f"bytes={artifact_manifest.get('bytes_removed', 0)}"
            )
        except (OSError, ValueError) as exc:
            # Compaction failure must not turn a valid completed experiment into
            # an algorithmic failure. The unpruned artifacts remain available.
            control_progress(f"WARNING artifact compaction failed: {exc}")
    print(f"\nSummary: {output_root / 'loop_summary.csv'}", flush=True)
    print(f"Run record: {output_root / 'run_record.json'}", flush=True)
    print(f"Run index: {recorder.record_jsonl} ({run_record['run_id']})", flush=True)
    control_progress(
        f"COMPLETE result={output_root / 'run_record.json'} summary={output_root / 'loop_summary.csv'}"
    )


if __name__ == "__main__":
    main()
