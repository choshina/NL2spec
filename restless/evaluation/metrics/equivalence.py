"""Bounded semantic equivalence of two STL formulas (task T2).

Two formulas are compared on one instance's execution domain: the configured
signal ranges (Boolean, integer, static, continuous), the exact derived-signal
identities, and the sampling grid ``time_start:time_step:time_end``.

Two semantics are supported:

``oracle``
    The verdict the repair pipeline actually uses: Moonlight robustness at the
    first sample, SAT iff robustness >= 0 (``EvaluateFormulaOnDataset``).
    Comparators follow ``CompareSign`` (``x>c`` and ``x>=c`` both have
    robustness ``x-c``), Boolean atoms have robustness +/-1 (true iff value >=
    1), negation is ``-rho``.  This is the main metric.
``boolean``
    Textbook Boolean STL on the same grid (strict comparators are strict).

Pipeline: (1) canonical-form comparison; (2) bounded SMT search with the
``z3`` binary for a trace on which the verdicts differ; every SMT
counterexample is replayed through the independent Python evaluator below and
must reproduce the disagreement, otherwise the verdict is ``unknown``.

Verdicts: ``equivalent``, ``not_equivalent``, ``unknown``.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[3]
from restless.common.derived_signals import EQUALS_RELATIVE_TOLERANCE, evaluate_derived  # noqa: E402
from restless.common.stl_epoch.ast_nodes import ASTNode  # noqa: E402
from restless.common.stl_epoch.parser import parse_stl  # noqa: E402

SEMANTICS = ("oracle", "boolean")
VERDICTS = ("equivalent", "not_equivalent", "unknown")
GRID_TOLERANCE = 1e-6


class UnsupportedFormula(ValueError):
    """The formula cannot be decided on this domain (reported as ``unknown``)."""


# ---------------------------------------------------------------------------
# Domain


@dataclass
class SignalDomain:
    kind: str  # "real", "int", "bool", "static"
    lo: float
    hi: float


@dataclass
class Domain:
    signals: dict[str, SignalDomain]
    derived: dict[str, dict[str, str]]
    time_start: float
    time_end: float
    time_step: float

    @property
    def n_steps(self) -> int:
        steps = (self.time_end - self.time_start) / self.time_step
        if abs(steps - round(steps)) > GRID_TOLERANCE:
            raise UnsupportedFormula("time_end is not on the time_step grid")
        return int(round(steps))

    def times(self) -> list[float]:
        return [self.time_start + k * self.time_step for k in range(self.n_steps + 1)]

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "Domain":
        """Build from a batch ``_batch_input/config.json`` (or the same keys)."""
        booleans = set(config.get("boolean_signals", []) or [])
        derived = {str(k): {str(a): str(b) for a, b in v.items()}
                   for k, v in (config.get("derived_signal_definitions") or {}).items()}
        signals: dict[str, SignalDomain] = {}
        for entry in config["signals"]:
            name = str(entry.get("source") or entry.get("name"))
            lo, hi = float(entry["min"]), float(entry["max"])
            if entry.get("static"):
                kind = "static"
            elif name in booleans:
                kind = "bool"
            elif entry.get("discrete"):
                kind = "int"
            else:
                kind = "real"
            signals[name] = SignalDomain(kind, lo, hi)
        return cls(signals, derived, float(config.get("time_start", 0.0)),
                   float(config["time_end"]), float(config["time_step"]))


# ---------------------------------------------------------------------------
# Canonical form

_COMPLEMENT_BOOLEAN = {">": "<=", ">=": "<", "<": ">=", "<=": ">"}
# Under the oracle, x>c and x>=c have identical robustness; negation flips sign.
_ORACLE_NORMAL = {">": ">=", ">=": ">=", "<": "<=", "<=": "<="}
_COMPLEMENT_ORACLE = {">=": "<=", "<=": ">="}


def _fmt(value: float) -> str:
    if math.isinf(value):
        return "inf"
    return f"{value:.12g}"


def canonical(formula: str | ASTNode, semantics: str = "oracle") -> str:
    """Canonical string; equal strings imply equivalence under ``semantics``.

    Rewrites (all robustness-preserving, hence sound for both semantics):
    implication -> NOT a OR b; negation pushed to atoms (De Morgan, G/F
    duality); AND/OR flattened, sorted, deduplicated.  Comparator
    complements and the merge of strict/non-strict comparators depend on the
    semantics.  NOT[x==c], NOT[b] and NOT[UNTIL] stay negated.
    """
    if semantics not in SEMANTICS:
        raise ValueError(f"unknown semantics {semantics!r}")
    root = parse_stl(formula) if isinstance(formula, str) else formula
    return _canon(root, False, semantics)


def _canon(node: ASTNode, negated: bool, semantics: str) -> str:
    kind = node.kind
    if kind == "not":
        return _canon(node.child, not negated, semantics)
    if kind == "bool":
        return f"NOT[{node.var}]" if negated else str(node.var)
    if kind == "atom":
        cmp = node.cmp
        if cmp in ("=", "=="):
            text = f"{node.var} == {_fmt(float(node.value))}"
            return f"NOT[{text}]" if negated else text
        if semantics == "oracle":
            cmp = _ORACLE_NORMAL[cmp]
            if negated:
                cmp = _COMPLEMENT_ORACLE[cmp]
        elif negated:
            cmp = _COMPLEMENT_BOOLEAN[cmp]
        return f"{node.var} {cmp} {_fmt(float(node.value))}"
    if kind == "implies":
        # a -> b  ==  NOT a OR b  (robustness max(-a, b))
        parts = [_canon(node.left, not negated, semantics), _canon(node.right, negated, semantics)]
        return _junction("AND" if negated else "OR", parts)
    if kind in ("and", "or"):
        label = {"and": "AND", "or": "OR"}[kind]
        if negated:
            label = "OR" if label == "AND" else "AND"
        parts = [_canon(node.left, negated, semantics), _canon(node.right, negated, semantics)]
        return _junction(label, parts)
    if kind in ("always", "eventually"):
        label = "GLOBALLY" if kind == "always" else "EVENTUALLY"
        if negated:
            label = "EVENTUALLY" if label == "GLOBALLY" else "GLOBALLY"
        child = _canon(node.child, negated, semantics)
        return f"{label} I=[{_fmt(float(node.a))},{_fmt(float(node.b))}][{child}]"
    if kind == "until":
        text = (f"UNTIL I=[{_fmt(float(node.a))},{_fmt(float(node.b))}]"
                f"[{_canon(node.left, False, semantics)},{_canon(node.right, False, semantics)}]")
        return f"NOT[{text}]" if negated else text
    raise UnsupportedFormula(f"unknown node kind {kind!r}")


def _junction(label: str, parts: Iterable[str]) -> str:
    flat: set[str] = set()
    prefix = f"{label}("
    for part in parts:
        if part.startswith(prefix) and part.endswith(")") and _balanced_top(part[len(prefix):-1]):
            flat.update(_split_top(part[len(prefix):-1]))
        else:
            flat.add(part)
    ordered = sorted(flat)
    return ordered[0] if len(ordered) == 1 else f"{label}({';'.join(ordered)})"


def _split_top(text: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        elif char == ";" and depth == 0:
            parts.append(text[start:index])
            start = index + 1
    parts.append(text[start:])
    return parts


def _balanced_top(text: str) -> bool:
    depth = 0
    for char in text:
        depth += char in "(["
        depth -= char in ")]"
        if depth < 0:
            return False
    return depth == 0


# ---------------------------------------------------------------------------
# Grid geometry shared by the evaluator and the SMT encoder


def _finite_horizon(node: ASTNode) -> float:
    """Horizon counting unbounded windows as 0 (Java ``getNecessaryLength``)."""
    if node.kind in ("atom", "bool"):
        return 0.0
    if node.kind == "not":
        return _finite_horizon(node.child)
    if node.kind in ("and", "or", "implies"):
        return max(_finite_horizon(node.left), _finite_horizon(node.right))
    b = float(node.b)
    own = 0.0 if math.isinf(b) else b
    if node.kind in ("always", "eventually"):
        return own + _finite_horizon(node.child)
    return own + max(_finite_horizon(node.left), _finite_horizon(node.right))


def _steps(value: float, dt: float) -> int:
    ratio = value / dt
    if abs(ratio - round(ratio)) > GRID_TOLERANCE:
        raise UnsupportedFormula(f"interval bound {value:g} is not a multiple of time_step {dt:g}")
    return int(round(ratio))


def _last_defined(node: ASTNode, n_steps: int, dt: float) -> int:
    return n_steps - int(math.ceil(_finite_horizon(node) / dt - GRID_TOLERANCE))


def _window(node: ASTNode, index: int, n_steps: int, dt: float,
            children: list[ASTNode]) -> list[int]:
    """Sample indices of the temporal window at ``index`` (clipped like Moonlight)."""
    start = index + _steps(float(node.a), dt)
    b = float(node.b)
    if math.isinf(b):
        end = min(_last_defined(child, n_steps, dt) for child in children)
    else:
        end = index + _steps(b, dt)
        limit = min(_last_defined(child, n_steps, dt) for child in children)
        if end > limit:
            raise UnsupportedFormula("time_end does not cover the formula horizon")
    return list(range(start, end + 1))


# ---------------------------------------------------------------------------
# Independent evaluator (Moonlight robustness on the sample grid)


class Evaluator:
    """Robustness / Boolean verdicts of formulas on one sampled trace.

    ``trace`` maps every base signal to its samples; derived signals are
    recomputed from their definitions exactly as the pipeline does.
    """

    def __init__(self, domain: Domain, trace: dict[str, list[float]]):
        self.domain = domain
        self.n_steps = domain.n_steps
        self.dt = domain.time_step
        self.values = {name: [float(v) for v in samples] for name, samples in trace.items()}
        for name, definition in domain.derived.items():
            if name in self.values:
                continue
            self.values[name] = [
                evaluate_derived(definition, {key: self.values[key][k] for key in self.values})
                for k in range(self.n_steps + 1)
            ]

    def robustness(self, formula: str | ASTNode, index: int = 0) -> float:
        root = parse_stl(formula) if isinstance(formula, str) else formula
        return self._rho(root, index, {})

    def verdict(self, formula: str | ASTNode, semantics: str = "oracle") -> bool:
        root = parse_stl(formula) if isinstance(formula, str) else formula
        if semantics == "oracle":
            return self._rho(root, 0, {}) >= 0.0
        return self._truth(root, 0, {})

    def _sample(self, var: str, index: int) -> float:
        try:
            return self.values[var][index]
        except KeyError as exc:
            raise UnsupportedFormula(f"signal {var!r} is not in the domain") from exc

    def _rho(self, node: ASTNode, i: int, memo: dict) -> float:
        key = (id(node), i)
        if key in memo:
            return memo[key]
        kind = node.kind
        if kind == "bool":
            value = 1.0 if self._sample(node.var, i) >= 1.0 else -1.0
        elif kind == "atom":
            x, c = self._sample(node.var, i), float(node.value)
            if node.cmp in (">", ">="):
                value = x - c
            elif node.cmp in ("<", "<="):
                value = c - x
            else:
                value = -abs(x - c)
        elif kind == "not":
            value = -self._rho(node.child, i, memo)
        elif kind == "and":
            value = min(self._rho(node.left, i, memo), self._rho(node.right, i, memo))
        elif kind == "or":
            value = max(self._rho(node.left, i, memo), self._rho(node.right, i, memo))
        elif kind == "implies":
            value = max(-self._rho(node.left, i, memo), self._rho(node.right, i, memo))
        elif kind in ("always", "eventually"):
            window = _window(node, i, self.n_steps, self.dt, [node.child])
            values = [self._rho(node.child, j, memo) for j in window]
            if not values:
                raise UnsupportedFormula("empty temporal window")
            value = min(values) if kind == "always" else max(values)
        elif kind == "until":
            window = _window(node, i, self.n_steps, self.dt, [node.left, node.right])
            if not window:
                raise UnsupportedFormula("empty until window")
            # Match the exact Moonlight version bundled with this artifact.
            # TemporalMonitorUntil computes unbounded-until first, then
            # conjoins it with F_[a,b](right), rather than restricting the
            # unbounded witness itself to [a,b].
            prefix, unbounded = math.inf, -math.inf
            limit = min(_last_defined(node.left, self.n_steps, self.dt),
                        _last_defined(node.right, self.n_steps, self.dt))
            for j in range(i, limit + 1):
                unbounded = max(unbounded, min(prefix, self._rho(node.right, j, memo)))
                prefix = min(prefix, self._rho(node.left, j, memo))
            value = min(unbounded, max(self._rho(node.right, j, memo) for j in window))
        else:
            raise UnsupportedFormula(f"unknown node kind {kind!r}")
        memo[key] = value
        return value

    def _truth(self, node: ASTNode, i: int, memo: dict) -> bool:
        key = (id(node), i)
        if key in memo:
            return memo[key]
        kind = node.kind
        if kind == "bool":
            value = self._sample(node.var, i) >= 1.0
        elif kind == "atom":
            x, c = self._sample(node.var, i), float(node.value)
            value = {">": x > c, ">=": x >= c, "<": x < c, "<=": x <= c}.get(node.cmp, x == c)
        elif kind == "not":
            value = not self._truth(node.child, i, memo)
        elif kind == "and":
            value = self._truth(node.left, i, memo) and self._truth(node.right, i, memo)
        elif kind == "or":
            value = self._truth(node.left, i, memo) or self._truth(node.right, i, memo)
        elif kind == "implies":
            value = (not self._truth(node.left, i, memo)) or self._truth(node.right, i, memo)
        elif kind in ("always", "eventually"):
            window = _window(node, i, self.n_steps, self.dt, [node.child])
            values = (self._truth(node.child, j, memo) for j in window)
            value = all(values) if kind == "always" else any(values)
        elif kind == "until":
            window = _window(node, i, self.n_steps, self.dt, [node.left, node.right])
            limit = min(_last_defined(node.left, self.n_steps, self.dt),
                        _last_defined(node.right, self.n_steps, self.dt))
            unbounded = any(self._truth(node.right, j, memo)
                            and all(self._truth(node.left, k, memo) for k in range(i, j))
                            for j in range(i, limit + 1))
            value = unbounded and any(self._truth(node.right, j, memo) for j in window)
        else:
            raise UnsupportedFormula(f"unknown node kind {kind!r}")
        memo[key] = value
        return value


# ---------------------------------------------------------------------------
# SMT encoding


def _num(value: float) -> str:
    frac = Fraction(str(float(value))).limit_denominator(10**12)
    text = str(abs(frac.numerator)) if frac.denominator == 1 else f"(/ {abs(frac.numerator)} {frac.denominator})"
    if frac.denominator == 1:
        text = f"{text}.0"
    return f"(- {text})" if frac < 0 else text


class _Encoder:
    def __init__(self, domain: Domain, semantics: str):
        self.domain = domain
        self.semantics = semantics
        self.n_steps = domain.n_steps
        self.dt = domain.time_step
        self.lines: list[str] = []
        self.declared: set[str] = set()
        self.memo: dict[tuple, Any] = {}
        self.base_vars: dict[tuple[str, int], str] = {}
        self.counter = 0

    # -- signals -----------------------------------------------------------
    def signal(self, name: str, index: int) -> str:
        if name in self.domain.derived:
            return self._derived(name, index)
        spec = self.domain.signals.get(name)
        if spec is None:
            raise UnsupportedFormula(f"signal {name!r} is not in the domain")
        if spec.kind == "static":
            return _num(spec.lo)
        var = f"s_{len(self.base_vars)}"
        key = (name, index)
        if key in self.base_vars:
            return self._as_real(name, self.base_vars[key])
        self.base_vars[key] = var
        if spec.kind in ("int", "bool"):
            lo, hi = (0, 1) if spec.kind == "bool" else (math.ceil(spec.lo), math.floor(spec.hi))
            self.lines.append(f"(declare-const {var} Int)")
            self.lines.append(f"(assert (and (>= {var} {lo}) (<= {var} {hi})))")
        else:
            self.lines.append(f"(declare-const {var} Real)")
            self.lines.append(f"(assert (and (>= {var} {_num(spec.lo)}) (<= {var} {_num(spec.hi)})))")
        return self._as_real(name, var)

    def _as_real(self, name: str, var: str) -> str:
        spec = self.domain.signals[name]
        return f"(to_real {var})" if spec.kind in ("int", "bool") else var

    def _derived(self, name: str, index: int) -> str:
        key = ("derived", name, index)
        if key in self.memo:
            return self.memo[key]
        d = self.domain.derived[name]
        op = d["op"]
        sig = lambda key_name: self.signal(d[key_name], index)  # noqa: E731
        if op in ("subtract", "linear_difference"):
            expr = f"(- {sig('left')} {sig('right')})"
        elif op == "linear_margin_subtract":
            expr = f"(+ (- {sig('left')} {sig('right')}) {sig('subtrahend')})"
        elif op == "abs_difference":
            raw = f"(- {sig('left')} {sig('right')})"
            expr = f"(ite (>= {raw} 0.0) {raw} (- {raw}))"
        elif op == "equals":
            constant = float(d["value"])
            tol = EQUALS_RELATIVE_TOLERANCE * max(1.0, abs(constant))
            x = sig("left")
            expr = (f"(ite (and (>= {x} {_num(constant - tol)}) (<= {x} {_num(constant + tol)}))"
                    f" 1.0 0.0)")
        else:
            raise UnsupportedFormula(f"derived operation {op!r} is not supported")
        var = self._fresh("d", "Real")
        self.lines.append(f"(assert (= {var} {expr}))")
        self.memo[key] = var
        return var

    def _fresh(self, prefix: str, sort: str) -> str:
        self.counter += 1
        name = f"{prefix}_{self.counter}"
        self.lines.append(f"(declare-const {name} {sort})")
        return name

    def _define(self, expr: str) -> str:
        var = self._fresh("b", "Bool")
        self.lines.append(f"(assert (= {var} {expr}))")
        return var

    # -- formulas ----------------------------------------------------------
    def verdict(self, node: ASTNode) -> str:
        if self.semantics == "oracle":
            return self.signs(node, 0)[0]
        return self.truth(node, 0)

    def signs(self, node: ASTNode, i: int) -> tuple[str, str]:
        """(rho >= 0, rho <= 0) as SMT Booleans — exact for min/max/negation."""
        key = ("sign", id(node), i)
        if key in self.memo:
            return self.memo[key]
        kind = node.kind
        if kind == "bool":
            x = self.signal(node.var, i)
            ge = self._define(f"(>= {x} 1.0)")
            result = (ge, f"(not {ge})")
        elif kind == "atom":
            x, c = self.signal(node.var, i), _num(float(node.value))
            if node.cmp in (">", ">="):
                result = (self._define(f"(>= {x} {c})"), self._define(f"(<= {x} {c})"))
            elif node.cmp in ("<", "<="):
                result = (self._define(f"(<= {x} {c})"), self._define(f"(>= {x} {c})"))
            else:
                result = (self._define(f"(= {x} {c})"), "true")
        elif kind == "not":
            ge, le = self.signs(node.child, i)
            result = (le, ge)
        elif kind in ("and", "or", "implies"):
            lg, ll = self.signs(node.left, i)
            rg, rl = self.signs(node.right, i)
            if kind == "implies":
                lg, ll = ll, lg
                kind = "or"
            if kind == "and":
                result = (self._define(f"(and {lg} {rg})"), self._define(f"(or {ll} {rl})"))
            else:
                result = (self._define(f"(or {lg} {rg})"), self._define(f"(and {ll} {rl})"))
        elif kind in ("always", "eventually"):
            window = _window(node, i, self.n_steps, self.dt, [node.child])
            pairs = [self.signs(node.child, j) for j in window]
            ges, les = " ".join(p[0] for p in pairs), " ".join(p[1] for p in pairs)
            if kind == "always":
                result = (self._define(f"(and true {ges})"), self._define(f"(or false {les})"))
            else:
                result = (self._define(f"(or false {ges})"), self._define(f"(and true {les})"))
        elif kind == "until":
            window = _window(node, i, self.n_steps, self.dt, [node.left, node.right])
            ge_terms, le_terms, prefix_ge, prefix_le = [], [], "true", "false"
            limit = min(_last_defined(node.left, self.n_steps, self.dt),
                        _last_defined(node.right, self.n_steps, self.dt))
            for j in range(i, limit + 1):
                rg, rl = self.signs(node.right, j)
                ge_terms.append(f"(and {rg} {prefix_ge})")
                le_terms.append(f"(or {rl} {prefix_le})")
                lg, ll = self.signs(node.left, j)
                prefix_ge = self._define(f"(and {prefix_ge} {lg})")
                prefix_le = self._define(f"(or {prefix_le} {ll})")
            window_pairs = [self.signs(node.right, j) for j in window]
            until_ge = self._define(f"(or false {' '.join(ge_terms)})")
            until_le = self._define(f"(and true {' '.join(le_terms)})")
            future_ge = self._define(f"(or false {' '.join(pair[0] for pair in window_pairs)})")
            future_le = self._define(f"(and true {' '.join(pair[1] for pair in window_pairs)})")
            result = (self._define(f"(and {until_ge} {future_ge})"),
                      self._define(f"(or {until_le} {future_le})"))
        else:
            raise UnsupportedFormula(f"unknown node kind {kind!r}")
        self.memo[key] = result
        return result

    def truth(self, node: ASTNode, i: int) -> str:
        key = ("truth", id(node), i)
        if key in self.memo:
            return self.memo[key]
        kind = node.kind
        if kind == "bool":
            result = self._define(f"(>= {self.signal(node.var, i)} 1.0)")
        elif kind == "atom":
            x, c = self.signal(node.var, i), _num(float(node.value))
            op = {">": ">", ">=": ">=", "<": "<", "<=": "<="}.get(node.cmp, "=")
            result = self._define(f"({op} {x} {c})")
        elif kind == "not":
            result = f"(not {self.truth(node.child, i)})"
        elif kind in ("and", "or"):
            result = self._define(f"({kind} {self.truth(node.left, i)} {self.truth(node.right, i)})")
        elif kind == "implies":
            result = self._define(f"(=> {self.truth(node.left, i)} {self.truth(node.right, i)})")
        elif kind in ("always", "eventually"):
            window = _window(node, i, self.n_steps, self.dt, [node.child])
            terms = " ".join(self.truth(node.child, j) for j in window)
            result = self._define(f"(and true {terms})" if kind == "always" else f"(or false {terms})")
        elif kind == "until":
            window = _window(node, i, self.n_steps, self.dt, [node.left, node.right])
            terms, prefix = [], "true"
            limit = min(_last_defined(node.left, self.n_steps, self.dt),
                        _last_defined(node.right, self.n_steps, self.dt))
            for j in range(i, limit + 1):
                terms.append(f"(and {self.truth(node.right, j)} {prefix})")
                prefix = self._define(f"(and {prefix} {self.truth(node.left, j)})")
            unbounded = self._define(f"(or false {' '.join(terms)})")
            future = self._define(f"(or false {' '.join(self.truth(node.right, j) for j in window)})")
            result = self._define(f"(and {unbounded} {future})")
        else:
            raise UnsupportedFormula(f"unknown node kind {kind!r}")
        self.memo[key] = result
        return result


# ---------------------------------------------------------------------------
# Public API


@dataclass
class EquivalenceResult:
    verdict: str
    method: str  # canonical | smt | none
    semantics: str
    time_s: float
    detail: str = ""
    counterexample: dict[str, list[float]] | None = field(default=None, repr=False)

    def to_dict(self, with_counterexample: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not with_counterexample:
            data.pop("counterexample")
        return data


def check_equivalence(
    phi: str,
    psi: str,
    domain: Domain,
    semantics: str = "oracle",
    timeout_s: float = 120.0,
    z3_bin: str | None = None,
) -> EquivalenceResult:
    if semantics not in SEMANTICS:
        raise ValueError(f"unknown semantics {semantics!r}")
    started = time.monotonic()

    def result(verdict: str, method: str, detail: str = "", cex=None) -> EquivalenceResult:
        return EquivalenceResult(verdict, method, semantics, time.monotonic() - started, detail, cex)

    try:
        phi_ast, psi_ast = parse_stl(phi), parse_stl(psi)
        if canonical(phi_ast, semantics) == canonical(psi_ast, semantics):
            return result("equivalent", "canonical")
        encoder = _Encoder(domain, semantics)
        left, right = encoder.verdict(phi_ast), encoder.verdict(psi_ast)
    except UnsupportedFormula as exc:
        return result("unknown", "none", str(exc))
    z3 = z3_bin or shutil.which("z3")
    if z3 is None:
        return result("unknown", "none", "z3 binary not found")
    variables = list(encoder.base_vars.items())
    script = "\n".join([
        f"(set-option :timeout {int(timeout_s * 1000)})",
        *encoder.lines,
        f"(assert (xor {left} {right}))",
        "(check-sat)",
        *(["(get-value (" + " ".join(var for _, var in variables) + "))"] if variables else []),
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".smt2", delete=False) as handle:
        handle.write(script)
        path = handle.name
    try:
        completed = subprocess.run(
            [z3, "-smt2", path], capture_output=True, text=True, timeout=timeout_s + 30,
        )
        output = completed.stdout.strip()
    except subprocess.TimeoutExpired:
        return result("unknown", "smt", "z3 process timeout")
    finally:
        Path(path).unlink(missing_ok=True)
    status = output.splitlines()[0].strip() if output else ""
    if status == "unsat":
        return result("equivalent", "smt")
    if status != "sat":
        return result("unknown", "smt", f"z3: {status or completed.stderr.strip()[:200]}")

    trace = _model_trace(output, variables, domain)
    try:
        evaluator = Evaluator(domain, trace)
        differ = evaluator.verdict(phi_ast, semantics) != evaluator.verdict(psi_ast, semantics)
    except UnsupportedFormula as exc:
        return result("unknown", "smt", f"counterexample replay failed: {exc}")
    if not differ:
        return result("unknown", "smt", "SMT counterexample not reproduced by the evaluator")
    return result("not_equivalent", "smt", cex=trace)


def _model_trace(output: str, variables: list[tuple[tuple[str, int], str]],
                 domain: Domain) -> dict[str, list[float]]:
    values = _parse_model_values(output)
    trace: dict[str, list[float]] = {}
    n = domain.n_steps + 1
    for name, spec in domain.signals.items():
        if name in domain.derived:
            continue
        default = spec.lo if spec.kind == "static" else min(max(0.0, spec.lo), spec.hi)
        trace[name] = [default] * n
    for (name, index), var in variables:
        trace[name][index] = values.get(var, trace[name][index])
    _snap_equals_witnesses(trace, domain)
    return trace


def _snap_equals_witnesses(trace: dict[str, list[float]], domain: Domain) -> None:
    """Z3 likes the very edge of the equals tolerance, where float replay can land outside it.

    A sample the encoding counted as equal is moved onto the constant, which keeps the model's
    truth value and makes the counterexample replayable by the evaluator.
    """
    for definition in domain.derived.values():
        if definition.get("op") != "equals" or definition.get("left") not in trace:
            continue
        constant = float(definition["value"])
        tolerance = EQUALS_RELATIVE_TOLERANCE * max(1.0, abs(constant))
        samples = trace[definition["left"]]
        for index, value in enumerate(samples):
            if constant - tolerance * (1 + 1e-6) <= value <= constant + tolerance * (1 + 1e-6):
                samples[index] = constant


def _parse_model_values(output: str) -> dict[str, float]:
    tokens = output.replace("(", " ( ").replace(")", " ) ").split()[1:]  # drop "sat"

    def parse(position: int):
        token = tokens[position]
        if token != "(":
            return token, position + 1
        items, position = [], position + 1
        while tokens[position] != ")":
            item, position = parse(position)
            items.append(item)
        return items, position + 1

    def number(term) -> float:
        if isinstance(term, str):
            return float(Fraction(term))
        head, *args = term
        if head == "-" and len(args) == 1:
            return -number(args[0])
        if head == "/":
            return number(args[0]) / number(args[1])
        if head == "to_real":
            return number(args[0])
        raise ValueError(f"cannot parse model value {term!r}")

    values: dict[str, float] = {}
    if not tokens:
        return values
    pairs, _ = parse(0)
    for var, term in pairs:
        values[var] = number(term)
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="Instance config.json (batch _batch_input).")
    parser.add_argument("--phi", required=True)
    parser.add_argument("--psi", required=True)
    parser.add_argument("--semantics", choices=SEMANTICS, default="oracle")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    domain = Domain.from_config(json.loads(Path(args.config).read_text(encoding="utf-8")))
    outcome = check_equivalence(args.phi, args.psi, domain, args.semantics, args.timeout)
    print(json.dumps(outcome.to_dict(), indent=2))


if __name__ == "__main__":
    main()
