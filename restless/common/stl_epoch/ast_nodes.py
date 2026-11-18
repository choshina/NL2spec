# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ASTNode:
    kind: str
    text: str

    # atom
    var: Optional[str] = None
    cmp: Optional[str] = None
    value: Optional[float] = None

    # temporal
    a: Optional[float] = None
    b: Optional[float] = None

    # children
    left: Optional['ASTNode'] = None
    right: Optional['ASTNode'] = None
    child: Optional['ASTNode'] = None

    # compiled meta
    name: Optional[str] = None
    horizon: Optional[float] = None


def assign_horizon(node: ASTNode) -> float:
    if node.kind in {'atom', 'bool'}:
        node.horizon = 0.0
    elif node.kind == 'not':
        node.horizon = assign_horizon(node.child)
    elif node.kind in ('and', 'or', 'implies'):
        hl = assign_horizon(node.left)
        hr = assign_horizon(node.right)
        node.horizon = max(hl, hr)
    elif node.kind in ('always', 'eventually'):
        hc = assign_horizon(node.child)
        node.horizon = float(node.b) + hc
    elif node.kind == 'until':
        hl = assign_horizon(node.left)
        hr = assign_horizon(node.right)
        node.horizon = float(node.b) + max(hl, hr)
    else:
        raise ValueError(f'未知节点类型: {node.kind}')
    return node.horizon


def collect_signal_names(node: ASTNode, out=None):
    if out is None:
        out = set()

    if node.kind in {'atom', 'bool'}:
        out.add(node.var)
    elif node.kind == 'not':
        collect_signal_names(node.child, out)
    elif node.kind in ('and', 'or', 'implies', 'until'):
        collect_signal_names(node.left, out)
        collect_signal_names(node.right, out)
    elif node.kind in ('always', 'eventually'):
        collect_signal_names(node.child, out)

    return out


def walk_postorder(node: ASTNode):
    if node.kind in {'atom', 'bool'}:
        yield node
    elif node.kind == 'not':
        yield from walk_postorder(node.child)
        yield node
    elif node.kind in ('and', 'or', 'implies', 'until'):
        yield from walk_postorder(node.left)
        yield from walk_postorder(node.right)
        yield node
    elif node.kind in ('always', 'eventually'):
        yield from walk_postorder(node.child)
        yield node


def walk_preorder(node: ASTNode, path: str = 'root'):
    yield node, path
    if node.kind == 'not':
        yield from walk_preorder(node.child, f'{path}.child')
    elif node.kind in ('and', 'or', 'implies', 'until'):
        yield from walk_preorder(node.left, f'{path}.left')
        yield from walk_preorder(node.right, f'{path}.right')
    elif node.kind in ('always', 'eventually'):
        yield from walk_preorder(node.child, f'{path}.child')


def operator_name(node: ASTNode):
    return {
        'bool': 'BOOL',
        'atom': node.cmp,
        'not': 'NOT',
        'and': 'AND',
        'or': 'OR',
        'implies': 'IMPLIES',
        'always': 'GLOBALLY',
        'eventually': 'EVENTUALLY',
        'until': 'UNTIL',
    }.get(node.kind, node.kind)


def child_nodes(node: ASTNode):
    if node.kind == 'not':
        return [node.child]
    if node.kind in ('and', 'or', 'implies', 'until'):
        return [node.left, node.right]
    if node.kind in ('always', 'eventually'):
        return [node.child]
    return []


def serialize_node(node: ASTNode, path: str = 'root', recursive: bool = False):
    children = child_nodes(node)
    record = {
        'astNodeId': node.name,
        'astPath': path,
        'nodeType': node.kind,
        'operator': operator_name(node),
        'variable': node.var if node.kind in {'atom', 'bool'} else None,
        'threshold': float(node.value) if node.kind == 'atom' else None,
        'interval': [float(node.a), float(node.b)] if node.kind in ('always', 'eventually', 'until') else None,
        'grammarNodeRef': node.name,
        'id': node.name,
        'kind': node.kind,
        'formula': node.text,
        'horizon': None if node.horizon is None else float(node.horizon),
        'children': [child.name for child in children],
    }

    if node.kind == 'atom':
        record.update({
            'var': node.var,
            'comparison': node.cmp,
            'threshold': float(node.value),
        })
    if node.kind == 'bool':
        record.update({
            'var': node.var,
            'comparison': None,
            'threshold': None,
        })
    if node.kind in ('always', 'eventually', 'until'):
        record['interval'] = [float(node.a), float(node.b)]

    if recursive:
        recursive_children = []
        for child in children:
            if node.kind in ('not', 'always', 'eventually'):
                child_path = f'{path}.child'
            elif child is node.left:
                child_path = f'{path}.left'
            else:
                child_path = f'{path}.right'
            recursive_children.append(serialize_node(child, child_path, recursive=True))
        record['children'] = recursive_children
    return record


def serialize_ast(root: ASTNode):
    return {
        'root': root.name,
        'nodes': [serialize_node(node, path) for node, path in walk_preorder(root)],
        'tree': serialize_node(root, recursive=True),
    }
