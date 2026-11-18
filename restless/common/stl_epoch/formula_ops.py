# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.

from __future__ import annotations

import math

from .ast_nodes import ASTNode, walk_preorder


def bustle_print(node: ASTNode) -> str:
    if node.kind == "bool":
        return f"{node.var}"
    if node.kind == "atom":
        return f"{node.var} {node.cmp} {format_number(float(node.value))}"
    if node.kind == "not":
        return f"NOT[{bustle_print(node.child)}]"
    if node.kind == "and":
        return f"AND[{bustle_print(node.left)},{bustle_print(node.right)}]"
    if node.kind == "or":
        return f"OR[{bustle_print(node.left)},{bustle_print(node.right)}]"
    if node.kind == "implies":
        # IMPLIES survives the round trip. Printing it as OR[NOT[a],b] loses the
        # antecedent/consequent distinction the moment the string is parsed
        # again, and with it every guard derived from it downstream.
        return f"IMPLIES[{bustle_print(node.left)},{bustle_print(node.right)}]"
    if node.kind == "always":
        return f"GLOBALLY I=[{format_number(float(node.a))},{format_number(float(node.b))}][{bustle_print(node.child)}]"
    if node.kind == "eventually":
        return f"EVENTUALLY I=[{format_number(float(node.a))},{format_number(float(node.b))}][{bustle_print(node.child)}]"
    if node.kind == "until":
        return (
            f"UNTIL I=[{format_number(float(node.a))},{format_number(float(node.b))}]"
            f"[{bustle_print(node.left)},{bustle_print(node.right)}]"
        )
    raise ValueError(f"unknown node kind: {node.kind}")


def format_number(value: float) -> str:
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    if abs(value - round(value)) <= 1e-10:
        return str(int(round(value)))
    return f"{value:.12g}"


def formula_size(node: ASTNode) -> int:
    return sum(1 for _node, _path in walk_preorder(node))


def temporal_depth(node: ASTNode) -> int:
    if node.kind in {"atom", "bool"}:
        return 0
    if node.kind == "not":
        return temporal_depth(node.child)
    if node.kind in {"always", "eventually"}:
        return 1 + temporal_depth(node.child)
    if node.kind == "until":
        return 1 + max(temporal_depth(node.left), temporal_depth(node.right))
    if node.kind in {"and", "or", "implies"}:
        return max(temporal_depth(node.left), temporal_depth(node.right))
    return 0


def atoms(node: ASTNode) -> list[ASTNode]:
    return [n for n, _path in walk_preorder(node) if n.kind == "atom"]

