# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.

from __future__ import annotations

from typing import List, Tuple


# IMPLIES precedes OR only because _matching_prefix_name takes the first match
# and neither is a prefix of the other; keeping it first is still the safer
# habit for names that could overlap.
PREFIX_NAMES = ("EVENTUALLY", "GLOBALLY", "UNTIL", "IMPLIES", "AND", "OR", "NOT")


def translate_formula(formula: str) -> str:
    """
    Translate BUSTLE-style STL syntax into the infix/short syntax accepted by
    the local parser.

    Examples:
      EVENTUALLY I=[1, 3][x1 > 0.5] -> F[1,3](x1 > 0.5)
      GLOBALLY I=[1, 3][x1 > 0.5]   -> G[1,3](x1 > 0.5)
      AND[a,b]                      -> (a and b)
      OR[a,b]                       -> (a or b)
      NOT[a]                        -> not (a)
      UNTIL I=[1, 3][a,b]           -> (a U[1,3] b)
    """
    return _translate_fragment(formula.strip())


def _translate_fragment(fragment: str) -> str:
    out = []
    i = 0
    while i < len(fragment):
        name = _matching_prefix_name(fragment, i)
        if name is None:
            out.append(fragment[i])
            i += 1
            continue

        translated, i = _translate_prefix_call(fragment, i, name)
        out.append(translated)
    return "".join(out).strip()


def _matching_prefix_name(text: str, pos: int):
    for name in PREFIX_NAMES:
        if not text.startswith(name, pos):
            continue
        end = pos + len(name)
        if end < len(text) and (text[end].isalnum() or text[end] == "_"):
            continue
        return name
    return None


def _translate_prefix_call(text: str, pos: int, name: str) -> Tuple[str, int]:
    i = pos + len(name)
    i = _skip_ws(text, i)

    if name in ("EVENTUALLY", "GLOBALLY", "UNTIL"):
        interval, i = _read_interval(text, i)
        i = _skip_ws(text, i)
        content, i = _read_bracket_content(text, i, "[", "]")
        args = _split_top_level_args(content)

        if name == "EVENTUALLY":
            _expect_arg_count(name, args, 1)
            return f"F[{interval}]({_translate_fragment(args[0])})", i
        if name == "GLOBALLY":
            _expect_arg_count(name, args, 1)
            return f"G[{interval}]({_translate_fragment(args[0])})", i

        _expect_arg_count(name, args, 2)
        left = _translate_fragment(args[0])
        right = _translate_fragment(args[1])
        return f"({left} U[{interval}] {right})", i

    i = _skip_ws(text, i)
    content, i = _read_bracket_content(text, i, "[", "]")
    args = _split_top_level_args(content)

    if name == "AND":
        if len(args) < 2:
            raise SyntaxError("AND[...] requires at least two arguments")
        return "(" + " and ".join(_translate_fragment(arg) for arg in args) + ")", i

    if name == "OR":
        if len(args) < 2:
            raise SyntaxError("OR[...] requires at least two arguments")
        return "(" + " or ".join(_translate_fragment(arg) for arg in args) + ")", i

    if name == "IMPLIES":
        # Strictly binary and not associative, unlike AND/OR: a -> b -> c is
        # ambiguous, so refuse it rather than pick a grouping.
        _expect_arg_count(name, args, 2)
        left = _translate_fragment(args[0])
        right = _translate_fragment(args[1])
        return f"({left} -> {right})", i

    _expect_arg_count(name, args, 1)
    return f"not ({_translate_fragment(args[0])})", i


def _read_interval(text: str, pos: int) -> Tuple[str, int]:
    i = _skip_ws(text, pos)
    if not text.startswith("I", i):
        raise SyntaxError(f"Expected I=[a,b] at: {text[i:i + 30]}")
    i += 1
    i = _skip_ws(text, i)
    if i >= len(text) or text[i] != "=":
        raise SyntaxError(f"Expected '=' after I at: {text[i:i + 30]}")
    i += 1
    i = _skip_ws(text, i)
    content, i = _read_bracket_content(text, i, "[", "]")
    parts = _split_top_level_args(content)
    _expect_arg_count("I", parts, 2)
    return f"{parts[0].strip()},{parts[1].strip()}", i


def _read_bracket_content(text: str, pos: int, opening: str, closing: str) -> Tuple[str, int]:
    if pos >= len(text) or text[pos] != opening:
        raise SyntaxError(f"Expected '{opening}' at: {text[pos:pos + 30]}")

    depth = 0
    start = pos + 1
    i = pos
    while i < len(text):
        ch = text[i]
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1

    raise SyntaxError(f"Unclosed '{opening}' in: {text[pos:pos + 40]}")


def _split_top_level_args(content: str) -> List[str]:
    parts = []
    current = []
    square_depth = 0
    paren_depth = 0

    for ch in content:
        if ch == "[":
            square_depth += 1
        elif ch == "]":
            square_depth -= 1
        elif ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth -= 1

        if ch == "," and square_depth == 0 and paren_depth == 0:
            parts.append("".join(current).strip())
            current = []
            continue

        current.append(ch)

    if current:
        parts.append("".join(current).strip())
    return parts


def _skip_ws(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _expect_arg_count(name: str, args: List[str], expected: int):
    if len(args) != expected:
        raise SyntaxError(f"{name} expects {expected} argument(s), got {len(args)}")
