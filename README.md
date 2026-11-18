## Human-in-the-Loop Active Learning of Signal Temporal Logic Specifications

1. **Equivalence query.** Generate traces for the counterexample classes (CE-Class) of the current formula with ATG (STL → SMT → Z3). An oracle labels each trace; a label that disagrees with the formula is a counterexample.
2. **Repair.** A diagnosis-guided evolutionary search edits the formula to agree with all labelled traces. Diagnostics (responsibility and self coefficients, "SGRL") rank the formula's nodes, and only the top-ranked ones (the *gate*) are edited.

```
 initial formula φ0 ──▶ Equivalence query ──traces + labels──▶ Repair ──▶ new hypothesis ─┐
                        (CE-Class, ATG, Z3;                    (SGRL diagnostics, gate,   │
                         MATLAB + Python)                       edits; Java BUSTLE)       │
                              ▲─────────────────────────────────────────────────────────┘
 stop: no counterexample or budget spent ──▶ result, checked against φ* (canonicalization + Z3)
```

The repository holds the implementation, the three benchmarks, the frozen experiment definitions, the stored run results, and the scripts that produce the evaluation tables and figures. ReSTLess needs MATLAB, Z3 and Java, **not a GPU**. The LLM baselines call GPT-5.6 through an API.

There are two reproducibility levels. The reported tables and figures can be
rebuilt from the frozen run records without MATLAB or an API key. Re-running a
repair instance end to end additionally requires MATLAB, Java, and Z3; the LLM
baselines also require API access to the recorded model. The historical API
calls that produced the initial formulas are not replayed: their outputs,
exclusion outcomes, retained instances, and downstream experiment definitions
are frozen in this artifact.

## Quick start

Requires MATLAB, JDK 17, Maven and Z3 4.8.12 (see [Setup](#setup)).

```sh
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt     # numpy, pandas, matplotlib, scipy, z3-solver==4.8.12.0
mvn -q -DskipTests compile          # Java search and evaluator -> target/classes
make test-experiments               # sanity check

# repair one instance (2-3 minutes); writes to .runcheck/, then: rm -rf .runcheck
RESPSTL_RESULTS_ROOT=.runcheck python3 -m restless.pipeline.run_instance \
  experiments/frozen/restless_stdp_test/instances/STDP__stl_005/spec.json --methods restless
```

## Contents

- [Layout](#layout) · [Data](#data) · [Setup](#setup) · [Run](#run-the-experiments) · [Results](#what-a-run-writes) · [Reproduce the paper](#reproduce-the-tables-and-figures) · [Settings](#frozen-settings) · [Troubleshooting](#troubleshooting)

## Layout

| Path | Role |
|---|---|
| `restless/repair/` | `repair_loop.py`: the repair loop (trace generation, oracle labelling, Java search, disambiguation, stopping). `dataset_launcher.py`: builds its command line from an instance config |
| `restless/pipeline/` | `pool_worker.py` (parallel workers), `run_instance.py` (one instance, all methods), `run_record.py` (writes `run_record.json`), `protocol.py` (methods and the fairness rule), `claims.py`, `progress.py` |
| `restless/ceclass/`, `restless/atg/` | CE-Class adapter and obligations; MATLAB session limit and trace cache |
| `restless/baselines/` | LLM-CE and LLM-rewrite (`llm_ce.py`, `llm_direct.py`) |
| `restless/evaluation/` | `validate_runs.py`, `metrics/` (equivalence check, tree edit distance), `tables/` (`restless_summary.py`, `fault_type_table.py`), `plots/` (`make_figures.py`, `time_cumulative.py`) |
| `src/main/java/` | BUSTLE search, SGRL fault localizer (`.../Repair/localization/`), edit mutator, `EvaluateFormulaOnDataset` |
| `stl-trace-generation/` | MATLAB ATG (`GenExTraces/`, `STL_TO_SMT/`), a minimal Breach subset, and the CE-Class code |
| `benchmarks/` | The 145 instances (see [`benchmarks/README.md`](benchmarks/README.md)) |
| `experiments/` | `frozen/` experiment definitions; `llm_ce_config.py` (prompts and model settings of the baselines) |
| `results/` | Stored runs; `results/final_145/` holds the tables and figures of the paper |

| Paper section | Code |
|---|---|
| 4 Equivalence query | `restless/ceclass/`, `stl-trace-generation/`, `restless/repair/repair_loop.py` |
| 5 Diagnostics, gate, search | `src/main/java/.../Repair/localization/`, `restless/repair/repair_loop.py` |
| 7 Evaluation | `restless/pipeline/`, `restless/evaluation/`, `restless/baselines/`, `experiments/frozen/`, `results/` |

## Data

145 instances over 108 source-requirement groups: **DeepSTL** 123 (122 from DeepSTL and 1 from NASA FRET LPC, reported together as DeepSTL), **ARCH-COMP** 12, **STDP** 10. Each has a reference formula φ\*, a signal interface, trace constraints and a GPT-5.6-generated initial formula φ0 that is semantically wrong under the frozen bounded evaluation semantics. Provenance, fields, sampling, selection and the exact scope of the correctness claim are described in [`benchmarks/README.md`](benchmarks/README.md); the instance-to-group mapping is `results/final_145/final_145_ids.csv`.

**Frozen experiments** (`experiments/frozen/`). Each experiment is a *tag*: `expected_runs.csv`, `manifest_summary.json`, and `instances/<id>/` with `spec.json` (methods, budgets, hashes), `config.json` (signals, horizon, `k_gen`, ...), `Incorrect_STL.csv`, `Oracle_STL.csv`.

| Tag | Methods |
|---|---|
| `restless_{archcomp,deepstl,stdp}_test` | `restless`, `noloc`, `no_self_coeff`, `no_ceclass` |
| `restless_{archcomp,deepstl,stdp}_ce145` | `llm_ce` |
| `restless_{archcomp,deepstl,stdp}_rw145` | `llm_direct` |

| Method | Paper name |
|---|---|
| `restless` | ReSTLess |
| `llm_direct` | LLM-rewrite: no labelled traces; an LLM judges the current formula against the requirement and rewrites it until it accepts it, at most 10 iterations |
| `llm_ce` | LLM-CE: replaces only the repair step; each round the LLM gets the requirement, the interface, the current formula and all labelled traces with their verdicts, and its most accurate parseable candidate becomes the next hypothesis |
| `noloc` | without localization (`--repair-localization off`: no diagnostics, no gate) |
| `no_self_coeff` | without self coefficients (K = 1) |
| `no_ceclass` | no CE-class (the query asks only for models of φ and ¬φ) |

## Setup

Paper environment: Ubuntu 22.04, Python 3.12, OpenJDK 17, Z3 4.8.12, licensed MATLAB; two Intel Xeon Silver 4214R CPUs (24 cores, 48 threads, 2.4 GHz) and 94 GB RAM.

```sh
sudo apt update
sudo apt install -y git build-essential python3 python3-venv python3-pip openjdk-17-jdk maven z3
```

- **Java ≥ 14** is required (JDK 11 fails with `invalid target release: 14`). If needed: `export JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 PATH=$JAVA_HOME/bin:$PATH`.
- **MATLAB** must be licensed, with `matlab` on `PATH`.
- **Z3 4.8.12** must be on `PATH` as `z3` (`z3 --version`); ATG calls `z3 -smt2`. Other versions can change the generated traces and so individual runs. Ubuntu 22.04's `apt` gives 4.8.12. Elsewhere, `pip install z3-solver==4.8.12.0` (in `requirements.txt`) and put `.venv/bin` first on `PATH`. On Apple Silicon this builds from source and needs `cmake`; with CMake ≥ 4 use `CMAKE_POLICY_VERSION_MINIMUM=3.5 pip install z3-solver==4.8.12.0`.
- **Python**: the repair pipeline uses only the standard library; `numpy`, `pandas`, `matplotlib`, `scipy` are for the tables and figures.
- **Java build**: `mvn -q -DskipTests compile`. Without `target/classes`, `repair_loop` stops with `Java evaluator not compiled`.

Optional `.env` in the repository root (never commit it):

| Variable | Meaning |
|---|---|
| `JAVA_BIN`, `MATLAB_BIN` | Executables, if not on `PATH` |
| `BUSTLE_ARCH`, `BUSTLE_CLASSPATH` | Java search architecture and classpath; leave `BUSTLE_ARCH` empty on Linux |
| `OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS` | Set to `1` for parallel runs |
| `MATLAB_MAX_CONCURRENT`, `MATLAB_SLOT_DIR` | Limit on simultaneous MATLAB sessions (`pool_worker --matlab-slots` sets it) |
| `OPENAI_API_KEY` | Only for `llm_ce` and `llm_direct`. Both use GPT-5.6 (`gpt-5.6-sol`), configured in `experiments/llm_ce_config.py` (`base_url` empty = default OpenAI endpoint) |

## Run the experiments

From the repository root. Write to a scratch root first (it must be inside the repository):

```sh
RESPSTL_RESULTS_ROOT=.runcheck python3 -m restless.pipeline.run_instance \
  experiments/frozen/restless_stdp_test/instances/STDP__stl_005/spec.json --methods restless
```

Expected: `SUCCESS STDP__stl_005 restless` and `failed runs: 0`. Without `--methods`, all methods of the spec run.

Full experiments (they write into `results/<tag>/` and add `attempt*` directories):

```sh
for d in deepstl archcomp stdp; do
  python3 -m restless.pipeline.pool_worker --tag restless_${d}_test --shard all --workers 4   # restless + ablations
  python3 -m restless.pipeline.pool_worker --tag restless_${d}_ce145 --shard all --workers 4  # LLM-CE (API key)
  python3 -m restless.pipeline.pool_worker --tag restless_${d}_rw145 --shard all --workers 4  # LLM-rewrite (API key)
done
```

Re-running these commands does not overwrite an earlier attempt: it appends a
new `attempt<N>` directory. Use `RESPSTL_RESULTS_ROOT=.runcheck` as above for a
trial run that must not modify the stored result tree.

| Option | Meaning |
|---|---|
| `--workers N` | Worker processes (default 4); each takes one instance at a time and runs all its methods |
| `--matlab-slots N` | Simultaneous MATLAB sessions (default: number of workers) |
| `--methods a,b` | Run only these methods |
| `--shard all` | Run all rows of `expected_runs.csv` (or select one shard letter) |
| `--ttl S` | Seconds after which a claim of another host is taken over (default 900) |

**Resume** by re-running the same command: finished instances are skipped and a dead worker's claim is taken over. **Progress**: `python3 -m restless.pipeline.progress --tag restless_deepstl_test`.

## What a run writes

`run_instance` (1) loads `spec.json`, (2) freezes the initial traces once per instance so every method starts from the same ones, (3) writes `_control/<instance>/fairness.json`, which records each method's effective config and checks they differ only in the allowed fields, and (4) runs each method; a crash is recorded as `runtime_failure` so it stays in the denominator. If the initial trace generation fails, the instance is explicitly recorded as skipped rather than silently counted as a repair failure. In `repair_loop`, a round is: generate class traces and label them → compare with the formula's verdicts → on counterexamples run the Java search → disambiguate if several candidates fit → next hypothesis.

```
results/<tag>/
  _control/<instance>/fairness.json
  rq2/<Dataset>/<method>/<instance>/attempt<N>/   # restless, noloc, no_ceclass, baselines
  rq3/<Dataset>/<method>/<instance>/attempt<N>/   # no_self_coeff
```

The submitted artifact retains the selected attempt's `run_record.json` (config, cost, rounds, result and status). LLM baselines additionally retain `llm_ce_summary.json`, which supplies the reported LLM-call count. Operational trace logs and duplicate attempts are not part of the submitted result set.

The submitted result tree is intentionally pruned to the records needed for
validation and aggregation. Large per-round trace files and execution logs are
not included; the artifact should therefore be described as reproducing the
reported metrics from frozen run records, not as an archive of every raw
intermediate file.

## Reproduce the tables and figures

```sh
OUT=results/final_145; mkdir -p $OUT/_a $OUT/_b

make validate-runs TAG=restless_deepstl_test          # repeat for the other tags

# ReSTLess and ablations (a run that hit the time limit counts as correct if its last verdict was "equivalent")
python3 -m restless.evaluation.tables.restless_summary --count-timeout-repaired \
  --tags restless_deepstl_test:restless,noloc,no_self_coeff,no_ceclass \
         restless_archcomp_test:restless,noloc,no_self_coeff,no_ceclass \
         restless_stdp_test:restless,noloc,no_self_coeff,no_ceclass --out $OUT/_a
# baselines
python3 -m restless.evaluation.tables.restless_summary \
  --tags restless_deepstl_ce145 restless_archcomp_ce145 restless_stdp_ce145 \
         restless_deepstl_rw145 restless_archcomp_rw145 restless_stdp_rw145 --out $OUT/_b

# merge into the per_run.csv used by the figures
python3 - <<'EOF'
import pandas as pd
out = "results/final_145"
a = pd.read_csv(f"{out}/_a/per_run.csv"); b = pd.read_csv(f"{out}/_b/per_run.csv")
a = a[a.method.isin(["restless", "noloc", "no_self_coeff", "no_ceclass"])]
b = b[b.method.isin(["llm_ce", "llm_direct"])]
pd.concat([a, b], ignore_index=True).to_csv(f"{out}/per_run.csv", index=False)
EOF

python3 -m restless.evaluation.tables.fault_type_table $OUT
python3 -m restless.evaluation.plots.make_figures --runs $OUT/per_run.csv --faults $OUT/fault_types.csv --out $OUT
python3 -m restless.evaluation.plots.time_cumulative --runs $OUT/per_run.csv --out $OUT
```

Without `--count-timeout-repaired`, `restless` and the three ablations lose two runs each (`Archcomp__bench_CC2`, `Archcomp__bench_CC4`). `noloc` runs are stored under `results/restless_*_test/`. `_a` and `_b` can be deleted afterwards.

Steps 1--3 reconstruct the stored 870-row table (145 instances × six
methods). For `correct`, `plausible`, `overfit`, `Q_total`, `Q_star`, and
`status`, the reconstructed rows match `results/final_145/per_run.csv`
exactly when sorted by dataset, instance and method.

Correct instances out of 145 (these match the paper):

| Method | Correct | Method | Correct |
|---|---|---|---|
| `restless` | 91 (62.8 %) | `noloc` | 61 (42.1 %) |
| `llm_ce` | 86 (59.3 %) | `no_self_coeff` | 88 (60.7 %) |
| `llm_direct` | 51 (35.2 %) | `no_ceclass` | 90 (62.1 %) |

| Paper item | Output in `results/final_145/` |
|---|---|
| Table 3 (RQ2), Table 4 (ablation) | `per_run.csv`, `summary_stats.csv` (`edits_to_correct` in `per_run.csv`) |
| RQ2 figure (instances with a correct result vs. run time) | `fig_rq1.*` |
| Correct results by fault type | `fig_fault_types.*`, `fault_types.csv` |
| RQ3 figure (ablation outcomes, local vs. structural faults, first-gate size); RQ4 figure (first disagreement, cumulative queries) | `fig_rq2.*`; `fig_rq3.*` |

`make_figures` prints every plotted number (`make_figures_stdout.txt`).

**Metrics.** *correct*: the result agrees with φ\* under the instance's frozen bounded evaluation semantics (signal domains, time grid, horizon and, where applicable, `H_inf=100`), decided by canonicalization and a Z3 check (`restless/evaluation/metrics/equivalence.py`). Atomic predicates follow the monitor semantics, under which `x > c` and `x ≥ c` agree. *plausible*: agrees with every label of its run; *overfit*: plausible but not correct. *Q*: oracle queries used (a query is a labelled trace new to the run, whatever step asked for it); *Q\**: queries after which a correct run first held its final result. *TED*: tree edit distance to φ0. A run that crashes or exceeds the time limit is charged the full budget (64 queries, 1800 s). Confidence intervals bootstrap the 108 source-requirement groups as clusters.

## Frozen settings

Recorded in each `spec.json`.

| Setting | Value |
|---|---|
| Budget | ≤ 10 rounds of ≤ 16 new labelled traces; 64 queries in total; 1800 s |
| Search | 30 generations, population 30 |
| Gate size | `k = max(3, ceil(0.3 × N))`, N = nodes of the current formula; admits nodes of rank ≤ k, ties included |
| Self coefficients | K = 3 (`no_self_coeff`: K = 1); UCB1 parent selection with coefficient 0.35 |
| Multiple candidates | From 20 labelled traces on, all consistent formulas are collected and disambiguated |
| Trace generation | 1 trace per class, `k_gen` 8, MATLAB timeout 300 s |
| LLM baselines | GPT-5.6 (`gpt-5.6-sol`), temperature 0.7, top-p 1.0. LLM-CE: 5 candidates per call, ≤ 3 attempts per round. LLM-rewrite: ≤ 10 iterations |

`python3 -m restless.repair.repair_loop --help` lists all options (`--repair-localization {off,sgrl}`, `--repair-localization-top-k-ratio`, `--repair-localization-self-coefficient-cap`, `--atg-persistent-matlab`, ...).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `invalid target release: 14` | Maven runs with Java 11; set `JAVA_HOME` to a JDK ≥ 14 |
| `Java evaluator not compiled` | Run `mvn -q -DskipTests compile` |
| `GENERATION_FAILURE`, no Java error | Check that `matlab` and `z3` are on `PATH` and the MATLAB licence allows `--matlab-slots`; read `run.log` in the attempt directory |
| `ValueError: … is not in the subpath of …` | `RESPSTL_RESULTS_ROOT` / `--output-root` must be inside the repository |
| Workers idle | All instances are finished or claimed; check `results/<tag>/_claims/` |
| `ModuleNotFoundError: scipy` / `matplotlib` | `pip install -r requirements.txt` into the environment you evaluate with |

## Tests

`make test-experiments` runs the equivalence tests, the run-record test and the SGRL localization tests (which compile and call the Java code). `make validate-runs TAG=<tag>` checks the stored run records of one tag.

## Citation and licensing

See `CITATION.cff`, `CITATIONS.bib`, `LICENSE`, `THIRD_PARTY.md` and `libs/README.md`. This is a composite research artifact, not a uniformly licensed open-source distribution. `src/main/java/`, `libs/`, and `stl-trace-generation/` contain or derive from third-party material (BUSTLE, CE-Class, ATG, Breach and binary dependencies); read `THIRD_PARTY.md` and resolve the recorded redistribution conditions before publishing a complete public archive.
