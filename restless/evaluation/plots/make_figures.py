#!/usr/bin/env python3
"""Reproduce the evaluation figures from the raw run data.

Usage:
    python make_figures.py --runs final_145/per_run.csv --faults fault_types.csv --out .

Inputs
    per_run.csv      one row per (instance, method); columns used:
                     instance_id, method, correct, overfit, Q_star, Q_total,
                     edit_dist_phi0, edits_to_correct, gate_frac, q_to_first_ce
    fault_types.csv  instance_id, category, edit_types

Outputs (PDF + PNG): fig_rq2, fig_rq3, fig_fault_types (fig_rq1 is written by time_cumulative.py)
The script also prints every number that is drawn, so the figures can be checked.
"""
import argparse, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

ap = argparse.ArgumentParser()
ap.add_argument("--runs", default="final_145/per_run.csv")
ap.add_argument("--faults", default="fault_types.csv")
ap.add_argument("--out", default=".")
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)

plt.rcParams.update({"font.size": 8, "font.family": "serif", "font.serif": ["DejaVu Serif"],
                     "pdf.fonttype": 42, "mathtext.fontset": "dejavuserif"})

d = pd.read_csv(args.runs)
f = pd.read_csv(args.faults)
d = d.merge(f[["instance_id", "category", "edit_types"]], on="instance_id", how="left")
# an instance has "local faults only" if every fault is a flip (G/F, and/or, comparison),
# i.e. a local edit in the sense of the paper; otherwise it has a structural fault
d["local"] = d.edit_types.apply(lambda t: all(e.startswith("flip_") for e in t.split("+")))
N = d.instance_id.nunique()
print(f"instances: {N}; rows: {len(d)}; methods: {sorted(d.method.unique())}")

# method key -> (label, colour, line style)
ST = {"restless":      ("ReSTLess",        "#1f4e9c", "-"),
      "llm_ce":        ("LLM-CE",          "#c8641e", "--"),
      "llm_direct":    ("LLM-rewrite",     "#666666", ":"),
      "no_ceclass":    ("No CE-class",     "#2a8a5a", "--"),
      "noloc":         ("No localization", "#8a4f9e", "--"),
      "no_self_coeff": ("$K{=}1$",         "#3a9ca8", "-.")}
CATS   = [("temporal", "Temporal"), ("boolean", "Boolean"), ("predicate", "Predicate"),
          ("structural", "Structural"), ("mixed", "Mixed"), ("out_of_scope", "Out of scope")]
CATS_S = [("temporal", "Temp."), ("boolean", "Bool."), ("predicate", "Pred."),
          ("structural", "Struct."), ("mixed", "Mixed"), ("out_of_scope", "Out of\nscope")]


def clean(a, axis="y"):
    for s in ("top", "right"):
        a.spines[s].set_visible(False)
    a.grid(axis=axis, alpha=.3, lw=.4)
    a.set_axisbelow(True)


def title(a, t):
    a.set_title(t, fontsize=8, loc="left")


def save(fig, name):
    for ext, kw in (("pdf", {}), ("png", {"dpi": 150})):
        fig.savefig(os.path.join(args.out, f"{name}.{ext}"), bbox_inches="tight", **kw)
    plt.close(fig)


def qcurve(a, methods):
    """% of all instances that are correct and reached the result within x queries.
    A correct run without Q_star (the two ARCH-COMP runs that hit the time limit)
    is placed at its Q_total."""
    xs = np.arange(0, 65)
    for m in methods:
        lab, col, ls = ST[m]
        r = d[(d.method == m) & (d.correct == 1)]
        q = r.Q_star.fillna(r.Q_total).values
        ys = [100 * (q <= x).sum() / N for x in xs]
        a.step(xs, ys, where="post", label=lab, color=col, ls=ls, lw=1.4)
        print(f"  curve {m:14s} at 0/8/16/32/64 queries: "
              + " ".join(f"{ys[x]:.1f}" for x in (0, 8, 16, 32, 64)))
    a.set_xlabel("Cumulative oracle queries"); a.set_ylabel("Correct (%)")
    a.set_xlim(0, 64); a.set_ylim(0, 70); a.set_xticks([0, 16, 32, 48, 64])
    clean(a, "both"); a.legend(frameon=False, loc="lower right", fontsize=7)


def catbars(a, methods, cats):
    """Correct rate per fault category (out_of_scope is drawn as its own, last category)."""
    cats = [(k, l) for k, l in cats if (d.category == k).any()]   # a category without instances is not drawn
    w = .8 / len(methods)
    for j, m in enumerate(methods):
        lab, col, _ = ST[m]
        ys = [100 * d[(d.method == m) & (d.category == k)].correct.mean() for k, _ in cats]
        xs = np.arange(len(cats)) + (j - (len(methods) - 1) / 2) * w
        a.bar(xs, ys, w * .92, color=col, label=lab)
        for x, y in zip(xs, ys):
            a.text(x, y + 1.5, f"{y:.0f}", ha="center", fontsize=6)
        print(f"  by type {m:14s}: " + " ".join(f"{k}={y:.1f}" for (k, _), y in zip(cats, ys)))
    ns = [(d[d.method == "restless"].category == k).sum() for k, _ in cats]
    a.set_xticks(range(len(cats)))
    a.set_xticklabels([f"{l}\n({n})" for (_, l), n in zip(cats, ns)], fontsize=7)
    a.set_ylabel("Correct (%)"); a.set_ylim(0, 108); clean(a)


# ------------------------------------------------------------------ RQ2
print("fig_rq2")
fig, ax = plt.subplots(1, 3, figsize=(6.6, 2.1), gridspec_kw={"width_ratios": [.8, 1.15, 1]})
a = ax[0]
ms = [("noloc", "No\nlocal."), ("no_self_coeff", "$K{=}1$"), ("restless", "ReSTLess")]
for i, (m, lab) in enumerate(ms):
    r = d[d.method == m]
    c = 100 * r.correct.mean(); o = 100 * r.overfit.mean(); fl = 100 - c - o
    a.bar(i, c, .62, color="#1f4e9c", label="Correct" if i == 0 else None)
    a.bar(i, o, .62, bottom=c, color="#9db7e0", label="Overfit" if i == 0 else None)
    a.bar(i, fl, .62, bottom=c + o, color="#d9d9d9", label="Not plausible" if i == 0 else None)
    for y, v, col in [(c / 2, c, "white"), (c + o / 2, o, "black"), (c + o + fl / 2, fl, "black")]:
        a.text(i, y, f"{v:.0f}", ha="center", va="center", color=col, fontsize=7)
    print(f"  outcomes {m:14s}: correct={c:.1f} overfit={o:.1f} not plausible={fl:.1f}")
a.set_xticks(range(3)); a.set_xticklabels([l for _, l in ms], fontsize=7)
a.set_ylim(0, 100); a.set_ylabel("Instances (%)"); clean(a)
H = a.get_legend_handles_labels(); title(a, "(a) Outcomes")

a = ax[1]; ms2 = ["noloc", "no_self_coeff", "restless"]; w = .26
groups = [(True, "Local faults\nonly"), (False, "With a\nstructural fault")]
for j, m in enumerate(ms2):
    lab, col, _ = ST[m]
    ys = [100 * d[(d.method == m) & (d.local == g)].correct.mean() for g, _ in groups]
    xs = np.arange(2) + (j - 1) * w
    a.bar(xs, ys, w * .92, color=col, label=lab)
    for x, y in zip(xs, ys):
        a.text(x, y + 1.5, f"{y:.0f}", ha="center", fontsize=6.5)
    print(f"  by fault kind {m:14s}: local={ys[0]:.1f} structural={ys[1]:.1f}")
ns = [int((d[d.method == "restless"].local == g).sum()) for g, _ in groups]
a.set_xticks(range(2)); a.set_xticklabels([f"{l} ({n})" for (_, l), n in zip(groups, ns)], fontsize=7)
a.set_ylabel("Correct (%)"); a.set_ylim(0, 125); a.set_yticks([0, 25, 50, 75, 100]); clean(a)
a.legend(frameon=False, fontsize=6.5, ncol=3, loc="upper center", columnspacing=.8, handlelength=1); title(a, "(b) By kind of fault")

a = ax[2]
for m in ["no_self_coeff", "restless"]:
    lab, col, ls = ST[m]
    e = np.sort(d[d.method == m].gate_frac.dropna().values)
    a.step(e, 100 * np.arange(1, len(e) + 1) / len(e), where="post", label=lab, color=col, ls=ls, lw=1.4)
    print(f"  gate  {m:14s} (n={len(e)}): mean={e.mean():.3f} whole formula: {100*(e>=.999).mean():.1f}%")
a.set_xlabel("First gate (fraction of nodes)"); a.set_ylabel("Instances (%)")
a.set_xlim(0, 1); a.set_ylim(0, 100); clean(a, "both")
a.legend(frameon=False, fontsize=7, loc="upper left"); title(a, "(c) Gate size")
fig.tight_layout(pad=.4, w_pad=1.2)
fig.legend(*H, frameon=False, fontsize=7, ncol=3, loc="lower left", bbox_to_anchor=(0.0, 1.0),
           columnspacing=1.2, handlelength=1.2)
save(fig, "fig_rq2")

# ------------------------------------------------------------------ RQ3
print("fig_rq3")
fig, ax = plt.subplots(1, 2, figsize=(4.5, 2.1), gridspec_kw={"width_ratios": [.85, 1]})
a = ax[0]; xs = np.arange(0, 17)
for m in ["no_ceclass", "restless"]:
    lab, col, ls = ST[m]
    q = d[d.method == m].q_to_first_ce.dropna().values     # NaN = no disagreement found
    ys = [100 * (q <= x).sum() / N for x in xs]
    a.step(xs, ys, where="post", label=lab, color=col, ls=ls, lw=1.4)
    print(f"  discovery {m:11s} after 1/2/8/16 queries: " + " ".join(f"{ys[x]:.1f}" for x in (1, 2, 8, 16)))
a.set_xlabel("Queries of the first\nequivalence query"); a.set_ylabel("Disagreement found (%)")
a.set_xlim(0, 16); a.set_xticks([0, 4, 8, 12, 16]); a.set_ylim(0, 100); clean(a, "both")
a.legend(frameon=False, fontsize=7, loc="lower right"); title(a, "(a) Discovery")
qcurve(ax[1], ["no_ceclass", "restless"]); title(ax[1], "(b) Repair")
fig.tight_layout(pad=.4, w_pad=1.2); save(fig, "fig_rq3")

# ------------------------------------------------------------------ fault types
print("fig_fault_types")
fig, a = plt.subplots(figsize=(6.6, 1.9))
catbars(a, ["llm_direct", "llm_ce", "restless"], CATS)
a.legend(frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(.5, 1.2), fontsize=7)
fig.tight_layout(pad=.4, w_pad=1.2); save(fig, "fig_fault_types")
print("done ->", os.path.abspath(args.out))
