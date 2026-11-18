#!/usr/bin/env python3
# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.


from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
C534_ROOT = ROOT / "stl-trace-generation" / "CE-class-C534"
if str(C534_ROOT) not in sys.path:   # external CE-class-C534 package
    sys.path.insert(0, str(C534_ROOT))
from ceclass.formula.stl_node import STLNode as CNode
from ceclass.lattice.parser import Parser as C534Parser
from restless.common.stl_epoch.parser import parse_stl


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Formula-only adapter for CE-class-C534.")
    parser.add_argument("--formula-csv", required=True)
    parser.add_argument("--formula-row", type=int, default=1)
    parser.add_argument("--formula-column", default="formula")
    parser.add_argument("--mode", choices=["positive", "negative"], required=True)
    parser.add_argument("--k", type=int, default=1)
    parser.add_argument(
        "--time-step", type=float, default=0.0,
        help="Sample step; when > 0 each temporal operator splits into at most b-a+1 (in samples) segments.",
    )
    parser.add_argument("--signals", default="x1")
    parser.add_argument("--class-limit", type=int, default=0)
    parser.add_argument(
        "--max-graph-classes",
        type=int,
        default=20000,
        help=(
            "Abort or reduce k before building the full C534 lattice when the "
            "estimated node count exceeds this value. This protects nested "
            "temporal formulas whose Cartesian products explode before "
            "--class-limit can be applied."
        ),
    )
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--summary-csv", required=True)
    return parser.parse_args()


def read_formula(path: Path, row_index: int, column: str) -> str:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if row_index < 1 or row_index > len(rows):
        raise IndexError(f"row {row_index} outside CSV range 1..{len(rows)}")
    if column not in rows[row_index - 1]:
        raise KeyError(f'column "{column}" not found in {path}')
    row = rows[row_index - 1]
    pieces = [row[column] or ""]
    pieces.extend(row.get(None) or [])
    formula = ",".join(pieces).strip().strip('"')
    if not formula:
        raise ValueError(f"empty formula in {path}, row={row_index}, column={column}")
    return formula


_ARCHCOMP_DEFAULT_SIGNAL_ALIASES = {
    "M_g,d": "Mgd",
    "θ_d": "theta_d",
    "θ": "theta",
    "Ω": "Omega",
    "ω": "omega",
    "μ": "mu",
}

_ARCHCOMP_NUM = r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_ARCHCOMP_IDENT = r"[A-Za-z_]\w*"


def _replace_signal_aliases(text: str, signal_aliases: dict[str, str] | None) -> str:
    aliases = dict(_ARCHCOMP_DEFAULT_SIGNAL_ALIASES)
    if signal_aliases:
        aliases.update({
            str(source): str(target)
            for source, target in signal_aliases.items()
            if str(source) and str(target)
        })
    for source, target in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if not source or source == target:
            continue
        if re.fullmatch(r"[A-Za-z_]\w*", source):
            text = re.sub(rf"\b{re.escape(source)}\b", target, text)
        else:
            text = text.replace(source, target)
    return text


def _expand_indexed_archcomp_conjunction(text: str) -> str:
    # The operator is a group: error injection rewrites G into F (CCx mutant),
    # so matching G only would leave the whole indexed form unparsable.
    pattern = re.compile(
        r"∧_\{i=1\.\.4\}\s*(?P<op>[GF□◇])_\[(?P<a>[^,\]]+),(?P<b>[^\]]+)\]"
        r"\(\s*y_\{i\+1\}\s*-\s*y_i\s*(?P<cmp>>=|<=|==|=|>|<|≥|≤)\s*(?P<thr>"
        + _ARCHCOMP_NUM
        + r")\s*\)"
    )
    _OPERATORS = {"G": "G", "□": "G", "F": "F", "◇": "F"}

    def repl(match: re.Match[str]) -> str:
        operator = _OPERATORS[match.group("op")]
        a = match.group("a").strip()
        b = match.group("b").strip()
        cmp_text = match.group("cmp").strip()
        threshold = match.group("thr").strip()
        atoms = [
            f"{operator}[{a},{b}](y{i + 1}_minus_y{i} {cmp_text} {threshold})"
            for i in range(1, 5)
        ]
        return "(" + " and ".join(atoms) + ")"

    return pattern.sub(repl, text)


def _rewrite_nn_tolerance_terms(text: str) -> str:
    # ARCH-COMP NN uses |Pos-Ref| compared with 0.005 + 0.03|Ref|.
    # The current STL parser only accepts signal-vs-constant atoms, so encode
    # the whole expression as a derived signal:
    #   pos_ref_error_margin = |Pos-Ref| - (0.005 + 0.03|Ref|)
    tol = r"\(?\s*0\.005\s*\+\s*0\.03\s*\|\s*ref\s*\|\s*\)?"
    dist = r"\|\s*pos\s*-\s*ref\s*\|"
    text = re.sub(rf"{dist}\s*>\s*{tol}", "pos_ref_error_margin > 0", text)
    text = re.sub(rf"{dist}\s*>=\s*{tol}", "pos_ref_error_margin >= 0", text)
    text = re.sub(rf"{dist}\s*<\s*{tol}", "pos_ref_error_margin < 0", text)
    text = re.sub(rf"{dist}\s*<=\s*{tol}", "pos_ref_error_margin <= 0", text)
    text = re.sub(rf"{tol}\s*>=\s*{dist}", "pos_ref_error_margin <= 0", text)
    text = re.sub(rf"{tol}\s*>\s*{dist}", "pos_ref_error_margin < 0", text)
    text = re.sub(rf"{tol}\s*<=\s*{dist}", "pos_ref_error_margin >= 0", text)
    text = re.sub(rf"{tol}\s*<\s*{dist}", "pos_ref_error_margin > 0", text)
    return text


def _expression_signal(expr: str) -> str:
    diff = re.fullmatch(rf"\s*({_ARCHCOMP_IDENT})\s*-\s*({_ARCHCOMP_IDENT})\s*", expr)
    if diff:
        return f"{diff.group(1)}_minus_{diff.group(2)}"
    return expr.strip()


def _format_archcomp_number(value: float) -> str:
    if abs(value - round(value)) <= 1e-10:
        return str(int(round(value)))
    return f"{value:.12g}"


def _split_absolute_comparison(expr: str, cmp_text: str, raw_threshold: str) -> str:
    signal = _expression_signal(expr)
    threshold = float(raw_threshold)
    positive = _format_archcomp_number(threshold)
    negative = _format_archcomp_number(-threshold)
    if cmp_text == "=":
        cmp_text = "=="
    if cmp_text == "<":
        return f"({signal} < {positive} and {signal} > {negative})"
    if cmp_text == "<=":
        return f"({signal} <= {positive} and {signal} >= {negative})"
    if cmp_text == ">":
        return f"({signal} > {positive} or {signal} < {negative})"
    if cmp_text == ">=":
        return f"({signal} >= {positive} or {signal} <= {negative})"
    if cmp_text == "==":
        return f"({signal} == {positive} or {signal} == {negative})"
    raise ValueError(f"unsupported absolute comparison: {cmp_text}")


def _rewrite_absolute_value_comparisons(text: str) -> str:
    expr = rf"{_ARCHCOMP_IDENT}(?:\s*-\s*{_ARCHCOMP_IDENT})?"
    abs_left = re.compile(
        rf"\|\s*(?P<expr>{expr})\s*\|\s*(?P<cmp>>=|<=|==|=|>|<)\s*(?P<thr>{_ARCHCOMP_NUM})"
    )
    abs_right = re.compile(
        rf"(?P<thr>{_ARCHCOMP_NUM})\s*(?P<cmp>>=|<=|==|=|>|<)\s*\|\s*(?P<expr>{expr})\s*\|"
    )
    reverse = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "==", "=": "=="}

    text = abs_left.sub(
        lambda match: _split_absolute_comparison(
            match.group("expr"), match.group("cmp"), match.group("thr")
        ),
        text,
    )
    text = abs_right.sub(
        lambda match: _split_absolute_comparison(
            match.group("expr"), reverse[match.group("cmp")], match.group("thr")
        ),
        text,
    )
    # FRET's absReal has the same pointwise real semantics as |expr| for the
    # exact fragment supported here (one signal or one signal difference).
    func_left = re.compile(
        rf"absReal\s*\(\s*(?P<expr>{expr})\s*\)\s*"
        rf"(?P<cmp>>=|<=|==|=|>|<)\s*(?P<thr>{_ARCHCOMP_NUM})"
    )
    func_right = re.compile(
        rf"(?P<thr>{_ARCHCOMP_NUM})\s*(?P<cmp>>=|<=|==|=|>|<)\s*"
        rf"absReal\s*\(\s*(?P<expr>{expr})\s*\)"
    )
    text = func_left.sub(
        lambda match: _split_absolute_comparison(
            match.group("expr"), match.group("cmp"), match.group("thr")
        ), text,
    )
    text = func_right.sub(
        lambda match: _split_absolute_comparison(
            match.group("expr"), reverse[match.group("cmp")], match.group("thr")
        ), text,
    )
    return text


def _rewrite_differences(text: str) -> str:
    text = re.sub(
        rf"\b({_ARCHCOMP_IDENT})\s*-\s*({_ARCHCOMP_IDENT})\b",
        lambda match: f"{match.group(1)}_minus_{match.group(2)}",
        text,
    )
    return text


def _rewrite_chained_comparisons(text: str) -> str:
    cmp_left_reverse = {
        "<": ">",
        "<=": ">=",
        ">": "<",
        ">=": "<=",
        "==": "==",
        "=": "==",
    }
    pattern = re.compile(
        rf"(?P<low>{_ARCHCOMP_NUM})\s*(?P<cmp1><=|>=|==|=|<|>)\s*"
        rf"(?P<var>{_ARCHCOMP_IDENT})\s*(?P<cmp2><=|>=|==|=|<|>)\s*"
        rf"(?P<high>{_ARCHCOMP_NUM})"
    )

    def repl(match: re.Match[str]) -> str:
        var = match.group("var")
        low = match.group("low")
        high = match.group("high")
        left_cmp = cmp_left_reverse[match.group("cmp1")]
        right_cmp = "==" if match.group("cmp2") == "=" else match.group("cmp2")
        return f"({var} {left_cmp} {low} and {var} {right_cmp} {high})"

    previous = None
    while previous != text:
        previous = text
        text = pattern.sub(repl, text)
    return text


def _is_default_boolean_signal_name(name: str) -> bool:
    return name in {"rise", "fall"} or re.fullmatch(r"g\d+", str(name)) is not None


def _eval_bool_sample(sample: float, op: str, threshold: float) -> bool:
    if op == "<":
        return sample < threshold
    if op == "<=":
        return sample <= threshold
    if op == ">":
        return sample > threshold
    if op == ">=":
        return sample >= threshold
    if op in {"=", "=="}:
        return abs(sample - threshold) <= 1e-12
    return False


def _bool_predicate_to_bustle(name: str, op: str, threshold: float) -> str:
    sat0 = _eval_bool_sample(0.0, op, threshold)
    sat1 = _eval_bool_sample(1.0, op, threshold)
    if sat0 and sat1:
        return f"OR[{name} == 0,{name} == 1]"
    if sat0:
        return f"{name} == 0"
    if sat1:
        return f"{name} == 1"
    return f"AND[{name} == 0,{name} == 1]"


def _drop_unmatched_closing_parens(text: str) -> str:
    kept: list[str] = []
    balance = 0
    for char in text:
        if char == "(":
            balance += 1
            kept.append(char)
        elif char == ")":
            if balance <= 0:
                continue
            balance -= 1
            kept.append(char)
        else:
            kept.append(char)
    return "".join(kept)


def adapt_archcomp_formula(
    formula: str,
    signal_aliases: dict[str, str] | None = None,
) -> str:
    """Canonicalize ARCH-COMP-style STL into the parser's supported syntax.

    The adapter is deliberately syntactic: it removes the ARCH-COMP underscore
    temporal notation, maps common Unicode/CSV-hostile signal names to ASCII,
    rewrites derived arithmetic expressions into derived signal names, and
    expands chained comparisons into boolean atoms. The caller still needs to
    configure value bounds for the resulting base and derived signal names.
    """
    text = formula.strip().strip('"')
    text = _expand_indexed_archcomp_conjunction(text)
    text = re.sub(r"\b([FG])_\s*\[", r"\1[", text)
    replacements = {
        "¬": " not ",
        "∨": " or ",
        "∧": " and ",
        "⇒": " -> ",
        "→": " -> ",
        "≤": "<=",
        "≥": ">=",
        "−": "-",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = _replace_signal_aliases(text, signal_aliases)
    text = _rewrite_nn_tolerance_terms(text)
    text = _rewrite_absolute_value_comparisons(text)
    text = _rewrite_differences(text)
    text = _rewrite_chained_comparisons(text)
    text = _drop_unmatched_closing_parens(text)
    text = re.sub(r"(\))\s+(?=(?:G|F)\[)", r"\1 and ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def convert_ast(node, signal_indexes: dict[str, int], path: str = "root") -> tuple[CNode, list, dict[str, int]]:
    temporal_k_by_id: dict[str, int] = {}
    # Only outermost temporal operators (no temporal ancestor) are split into k segments;
    # nested ones stay whole, so the lattice grows linearly in k instead of k^depth.
    temporal_depth = [0]

    def visit(current, current_path: str) -> tuple[CNode, list]:
        node_id = sanitize_id(current_path)
        if current.kind == "atom":
            if current.var not in signal_indexes:
                signal_indexes[current.var] = len(signal_indexes)
            cmp_text = "==" if str(current.cmp) == "=" else str(current.cmp)
            if _is_default_boolean_signal_name(str(current.var)):
                sat0 = _eval_bool_sample(0.0, cmp_text, float(current.value))
                sat1 = _eval_bool_sample(1.0, cmp_text, float(current.value))
                if sat0 and sat1:
                    return CNode.true_node(), [1]
                if sat0:
                    return CNode.predicate(
                        str(current.var), "<=", 0.0,
                        signal_indexes[str(current.var)], node_id,
                    ), [1]
                if sat1:
                    return CNode.predicate(
                        str(current.var), ">=", 1.0,
                        signal_indexes[str(current.var)], node_id,
                    ), [1]
                return CNode.false_node(), [1]
            if cmp_text == "==":
                left = CNode.predicate(
                    str(current.var), ">=", float(current.value),
                    signal_indexes[str(current.var)], f"{node_id}_eq_ge",
                )
                right = CNode.predicate(
                    str(current.var), "<=", float(current.value),
                    signal_indexes[str(current.var)], f"{node_id}_eq_le",
                )
                return CNode.and_node(left, right, node_id), [1, [1], [1]]
            return CNode.predicate(
                str(current.var), cmp_text, float(current.value),
                signal_indexes[str(current.var)], node_id,
            ), [1]
        if current.kind == "bool":
            if current.var not in signal_indexes:
                signal_indexes[current.var] = len(signal_indexes)
            return CNode.predicate(
                str(current.var), ">=", 1.0,
                signal_indexes[str(current.var)], node_id,
            ), [1]
        if current.kind == "not":
            child, child_k = visit(current.child, f"{current_path}_child")
            return CNode.not_node(child, node_id), [1, child_k]
        if current.kind in ("and", "or", "implies"):
            left, left_k = visit(current.left, f"{current_path}_left")
            right, right_k = visit(current.right, f"{current_path}_right")
            if current.kind == "and":
                result = CNode.and_node(left, right, node_id)
            elif current.kind == "or":
                result = CNode.or_node(left, right, node_id)
            else:
                result = CNode.or_node(CNode.not_node(left, f"not_{left.id}"), right, node_id)
                # C534 has no native implication node, so only this backend
                # receives OR[NOT[A],B]. Its k hierarchy must include the NOT
                # level as well as A's hierarchy.
                return result, [1, [1, left_k], right_k]
            return result, [1, left_k, right_k]
        if current.kind in ("always", "eventually"):
            outermost = temporal_depth[0] == 0
            temporal_depth[0] += 1
            child, child_k = visit(current.child, f"{current_path}_child")
            temporal_depth[0] -= 1
            interval = (float(current.a), float(current.b))
            split = max(1, ARGS_K) if outermost else 1
            if math.isinf(interval[1]):
                # An unbounded interval has no equal-split boundaries (they would be inf / nan);
                # it stays one segment.
                split = 1
            elif TIME_STEP > 0:
                split = min(split, int(round((interval[1] - interval[0]) / TIME_STEP)) + 1)
            temporal_k_by_id[node_id] = split
            if current.kind == "always":
                result = CNode.always_node(child, interval, node_id)
            else:
                result = CNode.eventually_node(child, interval, node_id)
            return result, [split, child_k]
        if current.kind == "until":
            raise ValueError("CE-class-C534 does not support UNTIL")
        raise ValueError(f"unsupported STL node for C534: {current.kind}")

    converted, hierarchy = visit(node, path)
    return converted, hierarchy, temporal_k_by_id


def sanitize_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value)


def instantiate_params(
    formula: CNode,
    interval_dict: dict[str, tuple[float, float]],
    temporal_k_by_id: dict[str, int],
) -> tuple[CNode, dict[str, float]]:
    import copy

    result = copy.deepcopy(formula)
    params: dict[str, float] = {}
    for name in result.get_param_names():
        low, high = interval_dict.get(name, (0.0, 1.0))
        match = re.match(r"(.+)____t(\d+)$", name)
        if match:
            parent_id = match.group(1)
            boundary_index = int(match.group(2)) - 1
            split = max(1, temporal_k_by_id.get(parent_id, ARGS_K))
            ratio = boundary_index / split
        else:
            ratio = 0.5
        params[name] = float(low) + ratio * (float(high) - float(low))

    def replace(node: CNode) -> None:
        if node.interval is not None:
            node.interval = tuple(params.get(bound, bound) for bound in node.interval)
        for child in node.children:
            replace(child)

    replace(result)
    return result, params


def negate_generation_class(node: CNode) -> CNode:
    if node.node_type == "not":
        return node.children[0]
    return CNode.not_node(node, f"generation_not_{node.id}")


def to_bustle(node: CNode) -> str:
    if node.node_type == "predicate":
        if _is_default_boolean_signal_name(str(node.predicate_name)):
            return _bool_predicate_to_bustle(
                str(node.predicate_name),
                str(node.predicate_op),
                float(node.predicate_threshold),
            )
        return f"{node.predicate_name} {node.predicate_op} {format_number(float(node.predicate_threshold))}"
    if node.node_type == "true":
        return "TRUE"
    if node.node_type == "false":
        return "FALSE"
    if node.node_type == "not":
        return f"NOT[{to_bustle(node.children[0])}]"
    if node.node_type == "and":
        return f"AND[{to_bustle(node.children[0])},{to_bustle(node.children[1])}]"
    if node.node_type == "or":
        return f"OR[{to_bustle(node.children[0])},{to_bustle(node.children[1])}]"
    if node.node_type in ("always", "eventually"):
        operator = "GLOBALLY" if node.node_type == "always" else "EVENTUALLY"
        a, b = node.interval
        return f"{operator} I=[{format_number(float(a))},{format_number(float(b))}][{to_bustle(node.children[0])}]"
    raise ValueError(f"cannot serialize C534 node type: {node.node_type}")


def format_number(value: float) -> str:
    if value == float("inf"):
        return "inf"
    if abs(value - round(value)) <= 1e-10:
        return str(int(round(value)))
    return f"{value:.12g}"


def direct_fallback(formula: str, mode: str) -> list[dict[str, object]]:
    generation_formula = formula if mode == "positive" else f"NOT[{formula}]"
    return [{
        "class_id": f"C534_UNSUPPORTED_{mode}",
        "formula_bustle_concrete": generation_formula,
        "formula_dialect": "bustle",
        "parameters": "{}",
        "source_requirement": formula,
    }]


def estimate_lattice_nodes(node: CNode, k: list, positive: bool = True) -> int:
    """Conservative size estimate for C534 lattice construction.

    CE-class-C534 applies class_limit after parser.parse(), but parser.parse()
    first materializes Cartesian products for temporal operators. This estimate
    lets us lower k or fall back before that materialization can hang.
    """
    if node.node_type == "predicate":
        return 2
    if node.node_type in {"true", "false"}:
        return 1
    if node.node_type == "not":
        return estimate_lattice_nodes(node.children[0], k[1], not positive)
    if node.node_type in {"and", "or"}:
        left = estimate_lattice_nodes(node.children[0], k[1], positive)
        right = estimate_lattice_nodes(node.children[1], k[2], positive)
        return left * right
    if node.node_type in {"always", "eventually"}:
        child = estimate_lattice_nodes(node.children[0], k[1], positive)
        split = max(1, int(k[0]))
        # Avoid enormous intermediate integers; anything over the guard is
        # already enough to trigger fallback.
        value = 1
        for _ in range(split):
            value *= child
            if value > 10**9:
                return value
        return value
    return 10**9


def build_lattice_input(ast, signal_indexes: dict[str, int], mode: str):
    c_formula, hierarchy, temporal_k_by_id = convert_ast(ast, signal_indexes)
    if mode == "positive":
        lattice_root = CNode.not_node(c_formula, f"sat_not_{c_formula.id}")
        lattice_k = [1, hierarchy]
    else:
        lattice_root = c_formula
        lattice_k = hierarchy
    return c_formula, hierarchy, temporal_k_by_id, lattice_root, lattice_k


def main() -> None:
    global ARGS_K, TIME_STEP
    args = parse_args()
    ARGS_K = max(1, args.k)
    TIME_STEP = max(0.0, float(args.time_step))
    formula = adapt_archcomp_formula(
        read_formula(Path(args.formula_csv), args.formula_row, args.formula_column)
    )
    signal_indexes = {
        name.strip(): index
        for index, name in enumerate(args.signals.split(","))
        if name.strip()
    }
    fallback_reason = ""
    try:
        ast = parse_stl(formula)
        c_formula, hierarchy, temporal_k_by_id, lattice_root, lattice_k = build_lattice_input(
            ast, signal_indexes, args.mode
        )
        estimated_nodes = estimate_lattice_nodes(lattice_root, lattice_k)
        # Halve k until the lattice fits. Halving keeps the equal-split boundaries
        # nested with the caller's doubling schedule (1, 2, 4, ...).
        original_k = ARGS_K
        while (
            args.max_graph_classes > 0
            and estimated_nodes > args.max_graph_classes
            and ARGS_K > 1
        ):
            ARGS_K //= 2
            c_formula, hierarchy, temporal_k_by_id, lattice_root, lattice_k = build_lattice_input(
                ast, signal_indexes, args.mode
            )
            estimated_nodes = estimate_lattice_nodes(lattice_root, lattice_k)
        MAX_SPLIT.clear()
        MAX_SPLIT.update(temporal_k_by_id)
        if ARGS_K != original_k:
            print(
                f"C534 {args.mode}: estimated lattice exceeded "
                f"{args.max_graph_classes} nodes at k={original_k}; "
                f"reducing to k={ARGS_K} (estimated {estimated_nodes})",
                flush=True,
            )
        if args.max_graph_classes > 0 and estimated_nodes > args.max_graph_classes:
            fallback_reason = (
                f"estimated C534 lattice has {estimated_nodes} nodes, "
                f"limit={args.max_graph_classes}"
            )
            output_rows = direct_fallback(formula, args.mode)
        else:
            parser = C534Parser(lattice_root, lattice_k)
            graph = parser.parse()
            output_rows: list[dict[str, object]] = []
            seen: set[str] = set()
            for node_index, phi_node in enumerate(graph.nodes):
                if phi_node.formula.node_type in ("true", "false"):
                    continue
                concrete, params = instantiate_params(
                    phi_node.formula,
                    parser.interval_dict,
                    temporal_k_by_id,
                )
                generation_formula = to_bustle(negate_generation_class(concrete))
                if generation_formula in seen:
                    continue
                seen.add(generation_formula)
                output_rows.append({
                    "class_id": phi_node.formula.id or f"C534_{node_index}",
                    "formula_bustle_concrete": generation_formula,
                    "formula_dialect": "bustle",
                    "parameters": json.dumps(params, sort_keys=True),
                    "source_requirement": to_bustle(concrete),
                })
                if args.class_limit > 0 and len(output_rows) >= args.class_limit:
                    break
    except ValueError as exc:
        fallback_reason = str(exc)
        output_rows = direct_fallback(formula, args.mode)

    output_path = Path(args.output_csv)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "class_id", "formula_bustle_concrete", "formula_dialect",
        "parameters", "source_requirement",
    ]
    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    summary_path = Path(args.summary_csv)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "backend", "mode", "requested_k", "k", "max_split", "splits",
            "num_structural_classes", "fallback_reason",
        ])
        writer.writeheader()
        writer.writerow({
            "backend": "CE-class-C534",
            "mode": args.mode,
            "requested_k": max(1, args.k),
            "k": ARGS_K,
            "max_split": max(MAX_SPLIT.values(), default=1),
            "splits": json.dumps(MAX_SPLIT, sort_keys=True),
            "num_structural_classes": len(output_rows),
            "fallback_reason": fallback_reason,
        })
    print(f"C534 {args.mode}: {len(output_rows)} classes -> {output_path}")


ARGS_K = 1
TIME_STEP = 0.0
# Segments per temporal operator in the lattice actually built.
MAX_SPLIT: dict[str, int] = {}


if __name__ == "__main__":
    main()
