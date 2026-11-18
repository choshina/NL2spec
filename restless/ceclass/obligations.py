#!/usr/bin/env python3
# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.


"""Syntax-tree coverage obligations: a generation mode parallel to CE-class.

CE-class partitions the *behaviour* space, so it has to know which behaviours
the current formula calls right and which it calls wrong. Once the formula
agrees with every trace in D there is nothing left to partition, and generation
dries up exactly when the search needs it most.

This mode partitions the *syntax tree* instead. Whatever phi looks like, its
subformulas are there to be covered, and what the formula decides about a trace
never enters into it - so "accuracy=1 means no more counterexamples" does not
arise. Repairing phi rewrites the tree, the tree yields a different obligation
table, the new obligations ask for traces nobody has asked for before, and the
loop keeps moving. An obligation table that is fully covered (or provably
uncoverable) is a real convergence statement: enumerable, and checkable.

Obligations are carried as STL formulas rather than raw SMT terms:

  witness_formula  handed to the existing SMT trace generator
  check_formula    replayed on existing traces to test coverage

Both go through machinery this repo already has, which is why REACHED is
mechanised as "every ancestor guard holds" - the definition given for it - and
not as monitor instrumentation. Moonlight does not report which nodes it
touched, and teaching it to would mean changing evaluation itself.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
from restless.common.stl_epoch.ast_nodes import ASTNode  # noqa: E402
from restless.common.stl_epoch.formula_ops import bustle_print, format_number  # noqa: E402
from restless.common.stl_epoch.parser import parse_stl  # noqa: E402

REACHED = "REACHED"
TOGGLED = "TOGGLED"
BOUNDARY = "BOUNDARY"
IMPLICATION = "IMPLICATION"

DEFAULT_BOUNDARY_EPS = 1e-3


@dataclass
class Obligation:
    """One coverage requirement drawn from one node of phi's syntax tree."""

    kind: str
    path: str
    subformula: str
    witness_formula: str
    check_formula: str
    guards: list[str] = field(default_factory=list)
    window: tuple[float, float] = (0.0, 0.0)
    trivial: bool = False

    @property
    def key(self) -> str:
        """Identity across rounds: same node, same kind, same demand."""
        return f"{self.kind}@{self.path}:{self.check_formula}"

    def as_row(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "path": self.path,
            "subformula": self.subformula,
            "guards": " AND ".join(self.guards),
            "window": f"[{format_number(self.window[0])},{format_number(self.window[1])}]",
            "trivial": "true" if self.trivial else "false",
            "witness_formula": self.witness_formula,
            "check_formula": self.check_formula,
        }


def _negate(node: ASTNode) -> ASTNode:
    """Wrap in NOT, cancelling a NOT that is already there."""
    if node.kind == "not":
        return node.child
    return ASTNode(kind="not", child=node, text=f"(not {node.text})")


def implication_parts(node: ASTNode) -> tuple[ASTNode, ASTNode] | None:
    """Read this node as `antecedent -> consequent`, whatever the spelling.

    BUSTLE has no implication node, so a -> b arrives written any of several
    ways depending on which rewrite last touched it. Recognising only one form
    means losing the guard on every requirement that happens to be spelled
    another way - and the guard is the whole reason to care that a subformula is
    a consequent at all.

    Returns None for nodes that are not implications. The consequent is returned
    as a node, so `NOT[AND[a, b]]` yields (a, NOT[b]) rather than a bare b.
    """
    if node.kind == "implies":
        return node.left, node.right
    if node.kind == "or":
        left, right = node.left, node.right
        # OR[NOT[a], b] is a -> b; prefer this reading when both sides are
        # negated, so the left operand stays the antecedent as written.
        if left is not None and left.kind == "not":
            return left.child, right
        # OR[a, NOT[b]] is b -> a.
        if right is not None and right.kind == "not":
            return right.child, left
        return None
    if node.kind == "not":
        inner = node.child
        # NOT[AND[a, b]] is a -> NOT[b].
        if inner is not None and inner.kind == "and":
            return inner.left, _negate(inner.right)
        return None
    return None


def _eventually(inner: str, window: tuple[float, float]) -> str:
    low, high = window
    return f"EVENTUALLY I=[{format_number(low)},{format_number(high)}][{inner}]"


def _boundary_band(node: ASTNode, eps: float) -> str:
    value = float(node.value)
    lower = format_number(value - eps)
    upper = format_number(value + eps)
    return f"AND[{node.var} >= {lower},{node.var} <= {upper}]"


def derive_obligations(
    formula: str,
    *,
    boundary_eps: float = DEFAULT_BOUNDARY_EPS,
    time_end: float | None = None,
    include_trivial: bool = False,
) -> list[Obligation]:
    """Walk phi's syntax tree and emit one obligation set per subformula.

    Post-order, root included: children are covered before the node that
    combines them, which is the order in which a repair has to be able to see
    them separated.
    """
    root = parse_stl(formula) if isinstance(formula, str) else formula
    out: list[Obligation] = []

    def visit(
        node: ASTNode,
        path: str,
        guards: list[ASTNode],
        window: tuple[float, float],
        pending_guard: ASTNode | None = None,
    ) -> None:
        low, high = window
        if time_end is not None:
            high = min(high, float(time_end))
            low = min(low, high)
        span = (low, high)

        # Children first (post-order), carrying the guards and window they are
        # actually evaluated under. Which child is the consequent depends on how
        # the implication is spelled, so it is read off implication_parts rather
        # than assumed to be the right operand.
        parts = implication_parts(node)
        consequent_side = ""
        if parts is not None and node.kind in ("implies", "or"):
            consequent_side = "right" if parts[1] is node.right else "left"

        if node.kind == "not":
            inner = node.child
            # NOT[AND[a, b]] is a -> NOT[b]: the guard has to cross the NOT to
            # reach b, which no other case needs.
            carry = inner.left if (parts is not None and inner is not None and inner.kind == "and") else None
            visit(inner, f"{path}.child", guards, window, pending_guard=carry)
        elif node.kind in ("and", "or", "implies", "until"):
            left_guards = list(guards)
            right_guards = list(guards)
            if consequent_side == "right" and parts is not None:
                right_guards.append(parts[0])
            elif consequent_side == "left" and parts is not None:
                left_guards.append(parts[0])
            if pending_guard is not None and node.kind == "and":
                right_guards.append(pending_guard)
            visit(node.left, f"{path}.left", left_guards, window)
            visit(node.right, f"{path}.right", right_guards, window)
        elif node.kind in ("always", "eventually"):
            child_window = (low + float(node.a), high + float(node.b))
            visit(node.child, f"{path}.child", guards, child_window)

        subformula = bustle_print(node)
        guard_texts = [bustle_print(g) for g in guards]
        guard_conjunction = ""
        for text in guard_texts:
            guard_conjunction = text if not guard_conjunction else f"AND[{guard_conjunction},{text}]"

        # A: the node is actually evaluated, i.e. nothing on the path to it
        # short-circuits it away. With no guards above it that is true of every
        # trace, so the obligation carries no demand and is marked trivial.
        reached_inner = guard_conjunction or "TRUE"
        out.append(Obligation(
            kind=REACHED,
            path=path,
            subformula=subformula,
            witness_formula=_eventually(reached_inner, span) if guard_conjunction else subformula,
            check_formula=_eventually(reached_inner, span) if guard_conjunction else "",
            guards=guard_texts,
            window=span,
            trivial=not guard_conjunction,
        ))

        # B: the node's truth value is not pinned - it holds somewhere in its
        # window and fails somewhere else. A trace where a subformula never
        # changes cannot tell two repairs of it apart.
        toggled = (
            f"AND[{_eventually(subformula, span)},"
            f"{_eventually(f'NOT[{subformula}]', span)}]"
        )
        out.append(Obligation(
            kind=TOGGLED,
            path=path,
            subformula=subformula,
            witness_formula=toggled,
            check_formula=toggled,
            guards=guard_texts,
            window=span,
        ))

        # D: an implication is only informative on a trace that actually puts it
        # under stress - antecedent firing, consequent failing. Without such a
        # trace a -> b is vacuously true and every repair of b looks equally
        # good. This is the obligation the scope/nesting failures need.
        if parts is not None:
            antecedent, consequent = parts
            stressed = f"AND[{bustle_print(antecedent)},{bustle_print(_negate(consequent))}]"
            witness = _eventually(stressed, span)
            out.append(Obligation(
                kind=IMPLICATION,
                path=path,
                subformula=subformula,
                witness_formula=witness,
                check_formula=witness,
                guards=guard_texts,
                window=span,
            ))

        # C: comparisons get probed at the threshold, where < and <= and the
        # off-by-a-hair thresholds part company.
        if node.kind == "atom":
            band = _eventually(_boundary_band(node, boundary_eps), span)
            out.append(Obligation(
                kind=BOUNDARY,
                path=path,
                subformula=subformula,
                witness_formula=band,
                check_formula=band,
                guards=guard_texts,
                window=span,
            ))

    horizon_end = float(time_end) if time_end is not None else 0.0
    visit(root, "root", [], (0.0, horizon_end))
    if include_trivial:
        return out
    return [ob for ob in out if not ob.trivial]


def obligations_to_csv(obligations: list[Obligation], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "kind", "path", "subformula", "guards", "window",
        "trivial", "witness_formula", "check_formula",
    ]
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(ob.as_row() for ob in obligations)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Print the coverage obligation table for one STL formula.",
    )
    parser.add_argument("--formula", default="")
    parser.add_argument("--formula-csv", default="")
    parser.add_argument("--formula-row", type=int, default=1)
    parser.add_argument("--time-end", type=float, default=None)
    parser.add_argument("--boundary-eps", type=float, default=DEFAULT_BOUNDARY_EPS)
    parser.add_argument("--include-trivial", action="store_true")
    parser.add_argument("--output-csv", default="")
    args = parser.parse_args()

    formula = args.formula.strip()
    if not formula:
        if not args.formula_csv:
            raise SystemExit("pass --formula or --formula-csv")
        rows = list(csv.DictReader(open(args.formula_csv, newline="")))
        row = rows[args.formula_row - 1]
        pieces = [row.get("formula") or ""]
        pieces.extend(row.get(None) or [])
        formula = ",".join(pieces).strip().strip('"')

    obligations = derive_obligations(
        formula,
        boundary_eps=args.boundary_eps,
        time_end=args.time_end,
        include_trivial=args.include_trivial,
    )
    print(f"phi = {formula}")
    print(f"{len(obligations)} obligations\n")
    for index, ob in enumerate(obligations):
        guard_text = " AND ".join(ob.guards) or "-"
        print(f"[{index:02d}] {ob.kind:<8} {ob.path}")
        print(f"     psi     : {ob.subformula}")
        print(f"     guards  : {guard_text}")
        print(f"     window  : [{format_number(ob.window[0])},{format_number(ob.window[1])}]")
        print(f"     witness : {ob.witness_formula}")
    if args.output_csv:
        obligations_to_csv(obligations, Path(args.output_csv))
        print(f"\nwrote {args.output_csv}")


if __name__ == "__main__":
    main()
