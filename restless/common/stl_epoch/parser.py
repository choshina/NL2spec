# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.

from __future__ import annotations

import re
from typing import List

from .ast_nodes import ASTNode
from .translator import translate_formula


TOKEN_RE = re.compile(
    r"""\s*(
        <->|->|!=|==|=|>=|<=|>|<|
        \[|\]|\(|\)|,|\&|\||!|
        [A-Za-z_]\w*|
        -?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?
    )\s*""",
    re.X,
)


def tokenize(formula: str) -> List[str]:
    pos = 0
    tokens = []
    while pos < len(formula):
        m = TOKEN_RE.match(formula, pos)
        if not m:
            raise SyntaxError(f'无法解析公式, 位置 {pos}: "{formula[pos:pos+20]}"')
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


class STLParser:
    """
    优先级（低 -> 高）:
      implies / ->
      until / U[a,b]
      or
      and
      unary(not, G, F, always, eventually)
      atom / (...)
    """

    def __init__(self, tokens):
        self.tokens = tokens
        self.i = 0

    def peek(self):
        if self.i < len(self.tokens):
            return self.tokens[self.i]
        return None

    def pop(self, expected=None):
        tok = self.peek()
        if tok is None:
            raise SyntaxError('公式提前结束')
        if expected is not None and tok != expected:
            raise SyntaxError(f'期望 {expected}, 实际得到 {tok}')
        self.i += 1
        return tok

    def parse(self):
        node = self.parse_iff()
        if self.peek() is not None:
            raise SyntaxError(f'多余 token: {self.peek()}')
        return node

    def parse_iff(self):
        node = self.parse_implies()
        while self.peek() in ('iff', '<->'):
            self.pop()
            rhs = self.parse_implies()
            forward = ASTNode(kind='implies', left=node, right=rhs,
                              text=f'({node.text} -> {rhs.text})')
            backward = ASTNode(kind='implies', left=rhs, right=node,
                               text=f'({rhs.text} -> {node.text})')
            node = ASTNode(kind='and', left=forward, right=backward,
                           text=f'({node.text} <-> {rhs.text})')
        return node

    def parse_implies(self):
        node = self.parse_until()
        if self.peek() in ('implies', '->'):
            self.pop()
            rhs = self.parse_implies()
            node = ASTNode(kind='implies', left=node, right=rhs, text=f'({node.text} -> {rhs.text})')
        return node

    def parse_until(self):
        node = self.parse_or()
        while self.peek() in ('until', 'Until', 'U'):
            self.pop()
            if self.peek() == '[':
                self.pop('[')
                a = float(self.pop())
                self.pop(',')
                b = float(self.pop())
                self.pop(']')
            else:
                a, b = 0.0, float('inf')
            rhs = self.parse_or()
            node = ASTNode(kind='until', left=node, right=rhs, a=a, b=b, text=f'({node.text} U[{a:g},{b:g}] {rhs.text})')
        return node

    def parse_or(self):
        node = self.parse_and()
        while self.peek() in ('or', '|'):
            self.pop()
            rhs = self.parse_and()
            node = ASTNode(kind='or', left=node, right=rhs, text=f'({node.text} or {rhs.text})')
        return node

    def parse_and(self):
        node = self.parse_unary()
        while self.peek() in ('and', '&'):
            self.pop()
            rhs = self.parse_unary()
            node = ASTNode(kind='and', left=node, right=rhs, text=f'({node.text} and {rhs.text})')
        return node

    def parse_unary(self):
        tok = self.peek()

        if tok in ('not', '!'):
            self.pop()
            child = self.parse_unary()
            return ASTNode(kind='not', child=child, text=f'(not {child.text})')

        if tok in ('X', 'next'):
            self.pop()
            child = self.parse_unary()
            return ASTNode(kind='eventually', a=1.0, b=1.0, child=child,
                           text=f'X({child.text})')

        if tok in ('G', 'F', 'always', 'eventually'):
            op = self.pop()
            if self.peek() == '[':
                self.pop('[')
                a = float(self.pop())
                self.pop(',')
                b = float(self.pop())
                self.pop(']')
            else:
                a, b = 0.0, float('inf')
            child = self.parse_unary()

            if op in ('G', 'always'):
                kind = 'always'
                op_text = 'G'
            else:
                kind = 'eventually'
                op_text = 'F'

            return ASTNode(kind=kind, a=a, b=b, child=child, text=f'{op_text}[{a:g},{b:g}]({child.text})')

        return self.parse_primary()

    def parse_primary(self):
        tok = self.peek()
        if tok == '(':
            self.pop('(')
            node = self.parse_iff()
            self.pop(')')
            return node
        return self.parse_atom()

    def parse_atom(self):
        left = self.pop()
        if self.peek() not in ('>', '<', '>=', '<=', '==', '=', '!='):
            if re.fullmatch(r'[A-Za-z_]\w*', left):
                reserved = {
                    'always', 'eventually', 'until', 'Until', 'U',
                    'implies', 'iff', 'and', 'or', 'not', 'G', 'F', 'X', 'next',
                }
                if left not in reserved:
                    return ASTNode(kind='bool', var=left, text=left)
            raise SyntaxError(f'当前实现只支持 变量-常数 比较或布尔原子, 得到: {left}')
        cmp_ = self.pop()
        right = self.pop()

        if cmp_ not in ('>', '<', '>=', '<=', '==', '=', '!='):
            raise SyntaxError(f'不支持的比较符: {cmp_}')
        if cmp_ == '=':
            cmp_ = '=='

        def is_var(x):
            return re.fullmatch(r'[A-Za-z_]\w*', x) is not None

        def is_num(x):
            return re.fullmatch(r'-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', x) is not None

        if is_var(left) and is_num(right):
            atom_cmp = '==' if cmp_ == '!=' else cmp_
            atom = ASTNode(kind='atom', var=left, cmp=atom_cmp, value=float(right), text=f'{left} {atom_cmp} {right}')
            return ASTNode(kind='not', child=atom, text=f'not ({atom.text})') if cmp_ == '!=' else atom

        if is_num(left) and is_var(right):
            rev = {'>': '<', '<': '>', '>=': '<=', '<=': '>=', '==': '==', '!=': '!='}[cmp_]
            atom_cmp = '==' if rev == '!=' else rev
            atom = ASTNode(kind='atom', var=right, cmp=atom_cmp, value=float(left), text=f'{right} {atom_cmp} {left}')
            return ASTNode(kind='not', child=atom, text=f'not ({atom.text})') if rev == '!=' else atom

        raise SyntaxError(f'当前实现只支持 变量-常数 比较, 得到: {left} {cmp_} {right}')


def parse_stl(formula: str) -> ASTNode:
    formula = translate_formula(formula)
    tokens = tokenize(formula)
    parser = STLParser(tokens)
    return parser.parse()
