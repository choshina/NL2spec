#!/usr/bin/env python3
"""Cumulative number of instances solved vs. wall-clock time (one curve per method).

    python3 -m restless.evaluation.plots.time_cumulative --runs results/final_145/per_run.csv --out results/final_145

A run counts at its total run time (time_s) if correct == 1; runs that were not correct never enter the curve.
Prints the number solved at fixed time budgets, so every plotted number can be checked.
"""
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--runs", default="results/final_145/per_run.csv")
ap.add_argument("--out", default="results/final_145")
ap.add_argument("--methods", default="restless,llm_ce,llm_direct")
args = ap.parse_args()

plt.rcParams.update({"font.size": 8, "font.family": "serif", "font.serif": ["DejaVu Serif"], "pdf.fonttype": 42})
ST = {"restless": ("ReSTLess", "#1f4e9c", "-"), "llm_ce": ("LLM-CE", "#c8641e", "--"),
      "llm_direct": ("LLM-rewrite", "#666666", ":"), "noloc": ("No localization", "#8a4f9e", "--"),
      "no_ceclass": ("No CE-class", "#2a8a5a", "--"), "no_self_coeff": ("$K{=}1$", "#3a9ca8", "-.")}
d = pd.read_csv(args.runs)
N = d.instance_id.nunique()
tmax = float(d.time_s.max())
fig, ax = plt.subplots(figsize=(3.4, 2.5))
checkpoints = [30, 60, 120, 300, 600, 1800]
print(f"instances: {N}")
print("method".ljust(12) + "".join(f"{t:>7}s" for t in checkpoints) + "   final")
for m in args.methods.split(","):
    label, color, ls = ST[m]
    t = np.sort(d[(d.method == m) & (d.correct == 1)].time_s.to_numpy())
    x = np.concatenate([[t[0] * 0.5], t, [tmax]])
    y = np.concatenate([[0], np.arange(1, len(t) + 1), [len(t)]])
    ax.step(x, y, where="post", color=color, ls=ls, lw=1.4, label=f"{label} ({len(t)})")
    print(m.ljust(12) + "".join(f"{int((t <= c).sum()):>8}" for c in checkpoints) + f"   {len(t)}")
ax.set_xscale("log")
ax.set_xlim(5, tmax * 1.05)
ax.set_xlabel("Run time (s, log scale)")
ax.set_ylabel("Instances repaired (cumulative)")
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
ax.grid(alpha=.3, lw=.4)
ax.set_axisbelow(True)
ax.legend(frameon=False, loc="upper left", fontsize=7)
fig.tight_layout()
for ext in ("png", "pdf"):
    fig.savefig(f"{args.out}/fig_rq1.{ext}", dpi=300, bbox_inches="tight")
