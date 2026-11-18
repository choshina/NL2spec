"""LLM-CE baseline: the LLM replaces the repair step, everything else is the ReSTLess loop.

When the equivalence query returns a labelled set D with Mis(phi, D) != {}, llm_ce_repair asks a fixed LLM for
candidates that fit D (R attempts x n independent calls), monitors them on D with the loop's own evaluator, and
hands the fitting candidates (or the best misfitting one) back to the loop through a Java-style *.repair.csv, so
the pool evaluation, active disambiguation and hypothesis selection are shared with every other method.

The reference formula never enters a prompt: assert_no_reference() is run on every prompt before it is sent.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "experiments" / "llm_ce_config.py"
STL_TAG = re.compile(r"<stl>(.*?)</stl>", re.S | re.I)

CSV_HEADER = ("iteration;mode;candidateType;candidateIndex;candidateCount;fitness;accuracy;newError;editDistance;"
              "formulaComplexity;runtimeSeconds;misclassified;hintDomains;structureHints;numericRepairFitness;"
              "numericDeltaSError;numericDeltaSAll;numericNewError;serialized")


class LlmError(RuntimeError):
    """Every call of an attempt failed at the API level (not a parse failure): the run must not look like
    'the LLM found nothing'."""


# ---------------------------------------------------------------------------------------------------------------
# configuration


def load_config(path: str | Path | None = None) -> dict:
    source = Path(path or DEFAULT_CONFIG)
    if source.suffix == ".py":   # experiments/llm_ce_config.py defines CONFIG = {...}
        import runpy  # noqa: PLC0415
        cfg = dict(runpy.run_path(str(source))["CONFIG"])
    else:
        cfg = json.loads(source.read_text(encoding="utf-8"))
    missing = [key for key in ("provider", "model", "model_version") if not str(cfg.get(key, "")).strip()]
    if missing:
        raise ValueError(f"{path or DEFAULT_CONFIG}: {', '.join(missing)} must be filled in before llm_ce can run "
                         "(one fixed model for every instance)")
    if cfg["provider"] not in ("anthropic", "openai", "fake"):
        raise ValueError(f"unknown provider {cfg['provider']!r}")
    return cfg


def config_hash(cfg: dict) -> str:
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------------------------------------------
# formulas: infix rendering, parsing, distance


def _stl():
    from restless.common.stl_epoch.parser import parse_stl  # noqa: PLC0415
    from restless.common.stl_epoch.formula_ops import bustle_print, format_number  # noqa: PLC0415
    return parse_stl, bustle_print, format_number


def to_infix(formula: str) -> str:
    """The formula in the grammar the prompt teaches: G[a,b] F[a,b] U[a,b] ! & | ->."""
    parse_stl, _bustle, num = _stl()

    def walk(node) -> str:
        kind = node.kind
        if kind == "bool":
            return str(node.var)
        if kind == "atom":
            return f"{node.var} {node.cmp} {num(float(node.value))}"
        if kind == "not":
            return f"!({walk(node.child)})"
        if kind in ("and", "or", "implies"):
            symbol = {"and": "&", "or": "|", "implies": "->"}[kind]
            return f"({walk(node.left)} {symbol} {walk(node.right)})"
        if kind in ("always", "eventually"):
            return f"{'G' if kind == 'always' else 'F'}[{num(float(node.a))},{num(float(node.b))}]({walk(node.child)})"
        if kind == "until":
            return f"({walk(node.left)} U[{num(float(node.a))},{num(float(node.b))}] {walk(node.right)})"
        raise ValueError(f"unknown node kind {kind}")

    return walk(parse_stl(formula))


_OPERATOR_FIXES = [("&&", "&"), ("||", "|"), ("∧", "&"), ("∨", "|"), ("¬", "!"), ("→", "->"), ("≤", "<="),
                   ("≥", ">="), ("∞", "inf")]


def _word(name: str) -> str:
    return r"(?<!\w)" + re.escape(name) + r"(?!\w)"


def _swap_names(text: str, mapping: dict[str, str]) -> str:
    for old in sorted(mapping, key=len, reverse=True):
        text = re.sub(_word(old), lambda _m, new=mapping[old]: new, text)
    return text


def repair_syntax(text: str) -> tuple[str, list[str]]:
    """Meaning-preserving fixes of an LLM formula: operator spellings (&&, ∧, →, ...), an infinite bound written
    +inf / \\infty / infinity, and unbalanced parentheses (surplus trailing ')' dropped, missing ones appended).
    Returns the new text and the names of the fixes that changed it."""
    fixes = []
    fixed = text
    for old, new in _OPERATOR_FIXES:
        if old in fixed:
            fixed = fixed.replace(old, new)
            if "operators" not in fixes:
                fixes.append("operators")
    infinite = re.sub(r"\+\s*inf(?:inity)?\b|\\infty|\binfinity\b", "inf", fixed)
    if infinite != fixed:
        fixed = infinite
        fixes.append("infinity")
    surplus = fixed.count(")") - fixed.count("(")
    stripped = fixed.rstrip()
    while surplus > 0 and stripped.endswith(")"):
        stripped, surplus = stripped[:-1].rstrip(), surplus - 1
    if surplus < 0:
        stripped += ")" * (-surplus)
    if stripped != fixed.rstrip():
        fixed = stripped
        fixes.append("parentheses")
    return fixed, fixes


def parse_candidate(text: str, signals: Sequence[str], aliases: dict[str, str] | None = None,
                    repairs: list[str] | None = None) -> str | None:
    """The LLM's formula as the loop's BUSTLE string, or None (parse failure / undeclared signal).
    aliases: {a spelling the LLM may use: the spelling the formulas use} (θ -> theta, omega -> RPM). The parser
    only reads ASCII names and the formulas keep their own spelling, so any other spelling is rewritten first.
    Tries the text as it is, then with names mapped, then with names mapped and the syntax repaired; the names
    of the fixes that were needed are appended to `repairs`."""
    parse_stl, bustle_print, _num = _stl()
    aliases = {variant: used for variant, used in (aliases or {}).items() if variant != used}
    allowed = set(signals)

    def attempt(candidate: str) -> str | None:
        try:
            tree = parse_stl(candidate.strip())
            formula = bustle_print(tree)
            used = _signals_of(tree)
        except Exception:  # noqa: BLE001 - any parser or printer error is a parse failure
            return None
        return formula if used <= allowed else None

    result = attempt(text)
    if result is not None:
        return result
    mapped = _swap_names(text, aliases) if aliases else text
    base = ["names"] if mapped != text else []
    repaired, fixes = repair_syntax(mapped)
    variants = ([(mapped, base)] if base else []) + ([(repaired, base + fixes)] if fixes else [])
    for variant, applied in variants:
        result = attempt(variant)
        if result is not None:
            if repairs is not None:
                repairs.extend(applied)
            return result
    return None


def _signals_of(tree) -> set[str]:
    from restless.common.stl_epoch.ast_nodes import walk_preorder  # noqa: PLC0415
    return {str(node.var) for node, _path in walk_preorder(tree) if node.kind in ("atom", "bool")}


def tree_distance(left: str, right: str) -> int:
    _stl()
    from restless.evaluation.metrics.minimality import tree_edit_distance  # noqa: PLC0415
    return int(tree_edit_distance(left, right))


def formula_length(formula: str) -> int:
    return len(re.findall(r"\b(?:AND|OR|NOT|IMPLIES|GLOBALLY|EVENTUALLY|UNTIL)\b|[A-Za-z_]\w*\s*(?:<=|>=|==|<|>)", formula))


# ---------------------------------------------------------------------------------------------------------------
# no reference in any prompt


def _squash(text: str) -> str:
    return re.sub(r"[\s()\[\]]+", "", text)


def reference_variants(reference: str) -> set[str]:
    """Spellings of one reference formula a prompt could contain (raw, project BUSTLE, prompt infix)."""
    variants = {_squash(reference)}
    try:
        _p, bustle_print, _n = _stl()
        from restless.common.formula_io import convert_formula_to_bustle  # noqa: PLC0415
        bustle = convert_formula_to_bustle(reference)
        variants.update({_squash(bustle), _squash(to_infix(bustle))})
    except Exception:  # noqa: BLE001 - an unparsable reference still gets the raw-string check
        pass
    return {v for v in variants if len(v) >= 4}


def assert_no_reference(prompt: str, phi: str, references: Sequence[str]) -> None:
    """Raise when any reference formula appears in the prompt. The faulty formula itself may share text with
    the reference, so its own rendering is removed before the check."""
    text = prompt
    for rendering in {phi, to_infix(phi)}:
        text = text.replace(rendering, " ")
    squashed = _squash(text)
    for reference in references:
        for variant in reference_variants(reference):
            if variant in squashed:
                raise ValueError("reference formula found in LLM prompt; refusing to send it")


# ---------------------------------------------------------------------------------------------------------------
# trace encoding


def sig3(value: float) -> str:
    return "0" if value == 0 else f"{value:.3g}"


def encode_trace(row: Sequence[float], signal_names: Sequence[str], times: Sequence[float]) -> str:
    """One signal per line, equal neighbouring samples merged into [start,end]: value (3 significant digits).
    Rows are flattened time-major: [s1@t0, s2@t0, ..., s1@t1, ...]."""
    stride = len(signal_names)
    lines = []
    for index, name in enumerate(signal_names):
        series = [row[step * stride + index] for step in range(len(times))]
        parts, start = [], 0
        for step in range(1, len(series) + 1):
            if step == len(series) or series[step] != series[start]:
                parts.append(f"[{sig3(times[start])},{sig3(times[step - 1])}]: {sig3(series[start])}")
                start = step
        lines.append(f"  {name}: " + "; ".join(parts))
    return "\n".join(lines)


def estimate_tokens(text: str, chars_per_token: float) -> int:
    return int(math.ceil(len(text) / chars_per_token))


def build_trace_section(rows: Sequence[Sequence[float]], labels: Sequence[int], predicted: Sequence[int],
                        signal_names: Sequence[str], times: Sequence[float], cfg: dict) -> tuple[str, dict]:
    """Traces of D within T_max tokens: every misclassified trace first (newest first), then the newest correctly
    classified ones. Trace ids are the positions in D."""
    budget = int(cfg["trace_token_max"])
    cpt = float(cfg.get("chars_per_token", 3.0))
    mis = [i for i in range(len(rows)) if predicted[i] != labels[i]]
    ok = [i for i in range(len(rows)) if predicted[i] == labels[i]]
    order = list(reversed(mis)) + list(reversed(ok))
    blocks, used, kept = {}, 0, []
    for i in order:
        verdict = "satisfies the requirement" if labels[i] > 0 else "violates the requirement"
        block = f"Trace #{i} (oracle label: {verdict}):\n{encode_trace(rows[i], signal_names, times)}\n"
        cost = estimate_tokens(block, cpt)
        if used + cost > budget:
            continue
        blocks[i] = block
        used += cost
        kept.append(i)
    truncated = len(rows) - len(kept)
    text = "".join(blocks[i] for i in sorted(kept))
    return text, {"traces_total": len(rows), "traces_shown": len(kept), "truncated_traces": truncated,
                  "misclassified_total": len(mis), "misclassified_shown": sum(1 for i in kept if i in set(mis)),
                  "trace_tokens_est": used}


# ---------------------------------------------------------------------------------------------------------------
# prompt


def render_prompt(cfg: dict, requirement: str, interface: str, phi_infix: str, traces: str, retry_note: str = "") -> str:
    grammar = cfg["grammar"]
    examples = "\n".join(f"  {example}" for example in grammar["examples"])
    prompt = cfg["template"].format(
        requirement=requirement.strip() or "(not available)", interface=interface,
        grammar=grammar["description"].strip(), examples=examples, phi=phi_infix, traces=traces)
    return prompt + (("\n\n" + retry_note) if retry_note else "")


def render_direct_prompt(cfg: dict, requirement: str, interface: str, phi_infix: str,
                         key: str = "direct_template") -> str:
    """LLM-direct: requirement + current formula only, no traces and no labels. key: judge_template | direct_template."""
    grammar = cfg["grammar"]
    examples = "\n".join(f"  {example}" for example in grammar["examples"])
    return cfg[key].format(
        requirement=requirement.strip() or "(not available)", interface=interface,
        grammar=grammar["description"].strip(), examples=examples, phi=phi_infix)


def render_interface(signal_names: Sequence[str], bounds: dict, boolean: Sequence[str], times: Sequence[float],
                     derived: dict | None = None) -> str:
    lines = ["Signals:"]
    for name in signal_names:
        if name in boolean:
            lines.append(f"  {name}: boolean (0 or 1)")
        elif name in bounds:
            lo, hi = bounds[name]
            lines.append(f"  {name}: range [{sig3(lo)}, {sig3(hi)}]")
        else:
            lines.append(f"  {name}: derived signal ({(derived or {}).get(name) or 'computed from the signals above'})")
    step = (times[1] - times[0]) if len(times) > 1 else 1.0
    lines.append(f"Time: samples at {sig3(times[0])}, {sig3(times[0] + step)}, ..., {sig3(times[-1])} "
                 f"(step {sig3(step)}). Temporal intervals are in the same time unit.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------
# clients


def load_api_keys() -> None:
    """Fill ANTHROPIC_API_KEY / OPENAI_API_KEY / DASHSCOPE_API_KEY from <repo>/.env when the environment does not already set them."""
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text().splitlines():
        name, sep, value = line.strip().partition("=")
        name = name.removeprefix("export ").strip()
        if sep and name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY") and value.strip() and not os.environ.get(name):
            os.environ[name] = value.strip().strip("\"'")


@dataclass
class Reply:
    text: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    error: str = ""


class AnthropicClient:
    def __init__(self, cfg: dict):
        import anthropic  # noqa: PLC0415
        load_api_keys()
        self.cfg, self.client = cfg, anthropic.Anthropic(max_retries=2, timeout=float(cfg.get("timeout_s", 120)))

    def complete(self, prompt: str, seed: int) -> Reply:
        started = time.monotonic()
        kwargs = {"model": self.cfg["model"], "max_tokens": int(self.cfg["max_tokens"]),
                  "temperature": float(self.cfg["temperature"]),
                  "messages": [{"role": "user", "content": prompt}]}
        if float(self.cfg["top_p"]) != 1.0:   # some models reject temperature together with top_p; 1.0 is the default
            kwargs["top_p"] = float(self.cfg["top_p"])
        response = self.client.messages.create(**kwargs)
        text = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
        return Reply(text, response.usage.input_tokens, response.usage.output_tokens, time.monotonic() - started)


class OpenAIClient:
    def __init__(self, cfg: dict):
        import openai  # noqa: PLC0415
        load_api_keys()
        self.cfg = cfg
        # api_key_env: which variable holds the key (DASHSCOPE_API_KEY for Qwen); base_url: OpenAI-compatible endpoint.
        self.client = openai.OpenAI(base_url=cfg.get("base_url") or None, max_retries=2,
                                    api_key=os.environ.get(cfg.get("api_key_env") or "OPENAI_API_KEY"),
                                    timeout=float(cfg.get("timeout_s", 120)))

    def complete(self, prompt: str, seed: int) -> Reply:
        started = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.cfg["model"], messages=[{"role": "user", "content": prompt}],
            temperature=float(self.cfg["temperature"]), top_p=float(self.cfg["top_p"]),
            max_tokens=int(self.cfg["max_tokens"]), seed=seed,
            **({"extra_body": self.cfg["extra_body"]} if self.cfg.get("extra_body") else {}))   # e.g. Qwen3: {"enable_thinking": false}
        usage = response.usage
        return Reply(response.choices[0].message.content or "", usage.prompt_tokens, usage.completion_tokens,
                     time.monotonic() - started)


class FakeClient:
    """Offline stand-in for plumbing tests only: perturbs the formula in the prompt, deterministically per seed."""

    def __init__(self, cfg: dict):
        self.cfg = cfg

    def complete(self, prompt: str, seed: int) -> Reply:
        match = re.search(r"Current (?:formula|STL):\n(.*?)(?:\n\n|$)", prompt, re.S)
        phi = match.group(1).strip() if match else "x > 0"
        if "exactly one word" in prompt:   # LLM-direct judge step
            text = "CORRECT" if seed % 3 == 0 else "INCORRECT"
            return Reply(text, len(prompt) // 4, 2, 0.0)
        pick = seed % 4
        if pick == 0:
            text = "no formula here"
        elif pick == 1:
            text = f"<stl>{phi.replace('>', '<', 1)}</stl>"
        elif pick == 2:
            text = f"<stl>{phi.replace('G[', 'F[', 1)}</stl>"
        else:
            text = f"reasoning... <stl>{phi}</stl> <stl>{phi.replace('&', '|', 1)}</stl>"
        return Reply(text, len(prompt) // 4, len(text) // 4, 0.0)


def make_client(cfg: dict):
    return {"anthropic": AnthropicClient, "openai": OpenAIClient, "fake": FakeClient}[cfg["provider"]](cfg)


# ---------------------------------------------------------------------------------------------------------------
# repair


@dataclass
class LlmStats:
    llm_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    time_llm: float = 0.0
    parse_failures: int = 0
    truncated_traces: int = 0
    api_errors: int = 0
    attempts: int = 0
    calls_with_candidates: int = 0
    iterations: int = 0        # LLM-direct: repairs applied by the judge/repair loop
    unchanged_repairs: int = 0   # LLM-direct: repairs that returned the formula unchanged
    stop_reason: str = ""      # LLM-direct: judged_correct | unchanged | unparsable | max_iters | judge_unclear
    syntax_repairs: int = 0   # replies that parsed only after the names were mapped / the syntax was fixed

    def as_dict(self) -> dict:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


@dataclass
class Context:
    cfg: dict
    requirement: str
    signal_names: list[str]
    signal_bounds: dict
    boolean_signals: list[str]
    times: list[float]
    references: list[str]            # only ever used by assert_no_reference
    run_seed: int
    log_path: Path
    aliases: dict = field(default_factory=dict)   # {other spelling: the formulas' spelling} of renamed signals
    derived: dict = field(default_factory=dict)   # {name: description} of derived signals the formulas use
    client: object = None
    stats: LlmStats = field(default_factory=LlmStats)


def call_seed(run_seed: int, round_index: int, attempt: int, i: int) -> int:
    digest = hashlib.sha256(f"{run_seed}|{round_index}|{attempt}|{i}".encode()).hexdigest()
    return int(digest[:8], 16)


def _log(ctx: Context, record: dict) -> None:
    ctx.log_path.parent.mkdir(parents=True, exist_ok=True)
    with ctx.log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def llm_ce_repair(ctx: Context, phi: str, rows: Sequence[Sequence[float]], labels: Sequence[int],
                  monitor: Callable[[Sequence[str]], dict[str, list[int]]], round_index: int,
                  ) -> tuple[list[tuple[str, int]], bool]:
    """phi: the current hypothesis (BUSTLE). monitor: formulas -> their verdicts (+1 / -1) on D, in D's order.
    Returns ([(candidate, misclassified on D)], fits): every candidate that fits D, or the single best one."""
    cfg, stats = ctx.cfg, ctx.stats
    if ctx.client is None:
        ctx.client = make_client(cfg)
    phi_infix = to_infix(phi)
    interface = render_interface(ctx.signal_names, ctx.signal_bounds, ctx.boolean_signals, ctx.times, ctx.derived)
    predicted = monitor([phi])[phi]
    retry_note = ""
    best: tuple[str, int] | None = None
    for attempt in range(1, int(cfg["R"]) + 1):
        stats.attempts += 1
        traces, trace_info = build_trace_section(rows, labels, predicted, ctx.signal_names, ctx.times, cfg)
        stats.truncated_traces += trace_info["truncated_traces"]
        prompt = render_prompt(cfg, ctx.requirement, interface, phi_infix, traces, retry_note)
        assert_no_reference(prompt, phi, ctx.references)
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        started = time.monotonic()
        replies: list[Reply] = [None] * int(cfg["n"])  # type: ignore[list-item]

        def one(i: int) -> Reply:
            seed = call_seed(ctx.run_seed, round_index, attempt, i)
            try:
                return ctx.client.complete(prompt, seed)
            except Exception as error:  # noqa: BLE001 - recorded, and escalated below if every call failed
                return Reply("", 0, 0, 0.0, error=f"{type(error).__name__}: {error}"[:300])

        with concurrent.futures.ThreadPoolExecutor(max_workers=int(cfg["n"])) as pool:
            for i, reply in enumerate(pool.map(one, range(int(cfg["n"])))):
                replies[i] = reply
        stats.time_llm += time.monotonic() - started
        stats.llm_calls += len(replies)
        candidates: dict[str, None] = {}
        parse_failed = api_failed = 0
        for i, reply in enumerate(replies):
            stats.llm_tokens_in += reply.tokens_in
            stats.llm_tokens_out += reply.tokens_out
            parsed = None
            if reply.error:
                api_failed += 1
            else:
                found = STL_TAG.findall(reply.text)
                repairs: list[str] = []
                parsed = parse_candidate(found[-1], ctx.signal_names, ctx.aliases, repairs) if found else None
                stats.syntax_repairs += 1 if (parsed is not None and repairs) else 0
                if parsed is None:
                    parse_failed += 1
                else:
                    candidates[parsed] = None
            _log(ctx, {"round": round_index, "attempt": attempt, "i": i, "model": cfg["model"],
                       "model_version": cfg["model_version"], "prompt_hash": prompt_hash,
                       "seed": call_seed(ctx.run_seed, round_index, attempt, i), "tokens_in": reply.tokens_in,
                       "tokens_out": reply.tokens_out, "latency_s": round(reply.latency_s, 3),
                       "raw_response": reply.text, "error": reply.error, "parsed": parsed,
                       **({"prompt": prompt, "trace_info": trace_info} if i == 0 else {})})
        stats.parse_failures += parse_failed
        stats.api_errors += api_failed
        if api_failed == len(replies):
            raise LlmError(f"all {len(replies)} LLM calls failed at the API level: {replies[0].error}")
        stats.calls_with_candidates += len(replies) - parse_failed - api_failed
        verdicts = monitor(list(candidates)) if candidates else {}
        unmonitorable = [c for c in candidates if c not in verdicts]   # the evaluator rejected them: counted as parse failures
        stats.parse_failures += len(unmonitorable)
        candidates = {c: None for c in candidates if c in verdicts}
        scored = [(cand, sum(1 for p, y in zip(verdicts[cand], labels) if p != y)) for cand in candidates]
        fitting = [item for item in scored if item[1] == 0]
        _log(ctx, {"round": round_index, "attempt": attempt, "candidates": [
            {"formula": c, "misclassified": m} for c, m in scored]})
        if fitting:
            return fitting, True
        def rank(item: tuple[str, int]) -> tuple[int, int, int]:
            return item[1], tree_distance(phi, item[0]), formula_length(item[0])

        if scored:
            attempt_best = min(scored, key=rank)
            if best is None or rank(attempt_best) < rank(best):
                best = attempt_best
        if best is not None:
            # No traces are shown to the LLM (trace_token_max = 0): the note names no trace ids.
            retry_note = (f"Your previous best candidate {to_infix(best[0])} is still not correct. "
                          f"Try a different correction.")
    return ([best] if best else []), False


_VERDICT_INCORRECT = re.compile(r"\bINCORRECT\b", re.I)
_VERDICT_CORRECT = re.compile(r"\bCORRECT\b", re.I)


def _direct_call(ctx: Context, prompt: str, mode: str, iteration: int) -> Reply:
    """One LLM-direct call: seeded, counted in the stats, an API failure raises."""
    stats = ctx.stats
    if ctx.client is None:
        ctx.client = make_client(ctx.cfg)
    seed = call_seed(ctx.run_seed, iteration, 0 if mode == "judge" else 1, 0)
    started = time.monotonic()
    try:
        reply = ctx.client.complete(prompt, seed)
    except Exception as error:  # noqa: BLE001
        reply = Reply("", 0, 0, 0.0, error=f"{type(error).__name__}: {error}"[:300])
    stats.time_llm += time.monotonic() - started
    stats.llm_calls += 1
    stats.llm_tokens_in += reply.tokens_in
    stats.llm_tokens_out += reply.tokens_out
    if reply.error:
        stats.api_errors += 1
    return reply


def llm_direct_loop(ctx: Context, phi: str) -> dict:
    """LLM-direct: the LLM judges whether phi expresses the requirement; if INCORRECT it repairs it once and the
    new formula is judged again; if CORRECT the current formula is the output. No traces, no oracle, no queries.
    Stops on CORRECT, on an unclear verdict, on a repair that cannot be parsed, or after direct_max_iters judge/repair
    iterations; a repair that changes nothing stops it only if direct_stop_on_unchanged is set (default: it does not).
    Returns {"final": BUSTLE formula, "iterations": repairs applied, "stop_reason": ..., "history": [...]}."""
    cfg, stats = ctx.cfg, ctx.stats
    interface = render_interface(ctx.signal_names, ctx.signal_bounds, ctx.boolean_signals, ctx.times, ctx.derived)
    current, history, reason = phi, [], "max_iters"
    for iteration in range(int(cfg["direct_max_iters"])):
        judge_prompt = render_direct_prompt(cfg, ctx.requirement, interface, to_infix(current), "judge_template")
        assert_no_reference(judge_prompt, current, ctx.references)
        reply = _direct_call(ctx, judge_prompt, "judge", iteration)
        verdict = None if reply.error else (False if _VERDICT_INCORRECT.search(reply.text)
                                            else True if _VERDICT_CORRECT.search(reply.text) else None)
        _log(ctx, {"mode": "judge", "iteration": iteration, "formula": current, "verdict": verdict,
                   "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out, "raw_response": reply.text,
                   "error": reply.error, "model": cfg["model"], "model_version": cfg["model_version"],
                   **({"prompt": judge_prompt} if iteration == 0 else {})})
        if reply.error:
            raise LlmError(f"LLM call failed at the API level: {reply.error}")
        history.append({"iteration": iteration, "formula": current, "judge": verdict})
        if verdict is None:
            reason = "judge_unclear"
            break
        if verdict:
            reason = "judged_correct"
            break
        repair_prompt = render_direct_prompt(cfg, ctx.requirement, interface, to_infix(current))
        assert_no_reference(repair_prompt, current, ctx.references)
        reply = _direct_call(ctx, repair_prompt, "repair", iteration)
        repairs: list[str] = []
        parsed = None if reply.error else extract_formula(reply.text, ctx.signal_names, ctx.aliases, repairs)
        stats.syntax_repairs += 1 if (parsed is not None and repairs) else 0
        _log(ctx, {"mode": "repair", "iteration": iteration, "repairs": repairs, "parsed": parsed,
                   "tokens_in": reply.tokens_in, "tokens_out": reply.tokens_out, "raw_response": reply.text,
                   "error": reply.error, "model": cfg["model"], "model_version": cfg["model_version"],
                   **({"prompt": repair_prompt} if iteration == 0 else {})})
        if reply.error:
            raise LlmError(f"LLM call failed at the API level: {reply.error}")
        if parsed is None:
            stats.parse_failures += 1
            reason = "unparsable"
            break
        if parsed == current:
            stats.unchanged_repairs += 1
            if cfg.get("direct_stop_on_unchanged", False):
                reason = "unchanged"
                break
            continue   # the judge runs again; the next repair is a new sample
        current = parsed
        stats.iterations += 1
    stats.stop_reason = reason
    return {"final": current, "iterations": stats.iterations, "stop_reason": reason, "history": history}


def instance_config(instance_id: str) -> dict:
    """The frozen config.json of an instance (identical in every tag derived from the same source)."""
    for path in sorted((ROOT / "experiments" / "frozen").glob(f"*/instances/{instance_id}/config.json")):
        return json.loads(path.read_text())
    return {}


def name_map(instance_id: str) -> dict[str, str]:
    """{every spelling of a signal: the spelling the instance's formulas use}. The formulas of an instance keep
    one spelling per signal (Archcomp: theta / mu in the AFC formulas, RPM / Speed in the AT6 formulas) while
    its config also knows the other one (θ, μ, omega, v), so the spelling is read from phi0 / the reference."""
    config = instance_config(instance_id)
    _stl()
    from restless.common.formula_io import convert_formula_to_bustle, read_formula  # noqa: PLC0415
    renames = {s["source"]: s["target"] for s in config.get("signals", []) if s["source"] != s["target"]} or None
    hint = ""
    for key, row_key in (("llm_formula_csv", "formula_row"), ("oracle_formula_csv", "oracle_formula_row")):
        path = ROOT / (config.get(key) or "")
        if path.is_file():   # the formula as the loop sees it (convert_formula_to_bustle may rename signals)
            hint += convert_formula_to_bustle(read_formula(path, int(config.get(row_key, 1))), renames) + "\n"
    mapping = {}
    for signal in config.get("signals", []):
        source, target = signal["source"], signal["target"]
        used = next((name for name in (source, target) if re.search(_word(name), hint)), source)
        mapping[source] = mapping[target] = used
    return mapping


def derived_signals(instance_id: str) -> dict[str, str]:
    """{name: description} of the signals phi0 / the reference use that are not in the instance's signal list
    (Archcomp CC formulas use differences such as y5_minus_y4 = y5 - y4)."""
    config = instance_config(instance_id)
    _stl()
    from restless.common.formula_io import convert_formula_to_bustle, read_formula  # noqa: PLC0415
    renames = {s["source"]: s["target"] for s in config.get("signals", []) if s["source"] != s["target"]} or None
    known = set(name_map(instance_id).values())
    parse_stl = _stl()[0]
    found: dict[str, str] = {}
    for key, row_key in (("llm_formula_csv", "formula_row"), ("oracle_formula_csv", "oracle_formula_row")):
        path = ROOT / (config.get(key) or "")
        if not path.is_file():
            continue
        formula = convert_formula_to_bustle(read_formula(path, int(config.get(row_key, 1))), renames)
        for name in sorted(_signals_of(parse_stl(formula)) - known):
            pair = re.fullmatch(r"(\w+?)_minus_(\w+)", name)
            found[name] = f"{pair.group(1)} - {pair.group(2)}" if pair else ""
    return found


def instance_aliases(instance_id: str) -> dict[str, str]:
    """{a spelling that is not the formulas' spelling: the formulas' spelling} (theta <- θ, omega <- RPM, ...)."""
    return {variant: used for variant, used in name_map(instance_id).items() if variant != used}


def instance_signals(instance_id: str) -> list[dict]:
    """The signals of an instance: source / target spellings and "name", the one the formulas use."""
    mapping = name_map(instance_id)
    return [{"source": s["source"], "target": s["target"], "name": mapping[s["target"]]}
            for s in instance_config(instance_id).get("signals", [])]


_COMPARATIVE = re.compile(r"greater|larger|bigger|more|less|smaller|fewer|least|most|above|below|over|under|exceed"
                          r"|between|range|within|than|[<>]", re.I)
_NEGATION = re.compile(r"n't|\bnot\b|\bnever\b", re.I)


def _spelled(name: str) -> str:
    """Pattern for a name that may be written with spaces / underscores between its characters."""
    chars = [char for char in name if char != "_"]
    return r"(?<![A-Za-z0-9_])" + r"[\s_]*".join(re.escape(char) for char in chars) + r"(?![A-Za-z0-9_])"


def align_enum_signals(text: str, signals: Sequence[dict]) -> str:
    """Boolean signals ap_<x>_is_<v> mean "x is v" (x an enumerated signal). Replace the phrase that says so
    ("the mode of x is set to v", "x isn't v") by the signal name, prefixed by "not " when negated; append one
    defining sentence when no phrase is found."""
    notes = []
    for signal in signals:
        name = signal["target"]
        if not (name.startswith("ap_") and "_is_" in name):
            continue
        left, value = name[3:].split("_is_", 1)
        pattern = re.compile(r"(?:\b(?:the\s+)?(?:(?:value|mode|state)\s+of\s+)?(?:the\s+)?(?:signal\s+)?)?"
                             + _spelled(left) + r"(?P<mid>[^.;]{0,60}?)" + _spelled(value), re.I)
        match = pattern.search(text)
        if match:
            prefix = "not " if _NEGATION.search(match.group("mid")) else ""
            text = text[:match.start()] + prefix + name + text[match.end():]
        else:
            notes.append(f"{name} is true when {left} is {value}.")
    return text + ((" (Note: " + " ".join(notes) + ")") if notes else "")


def align_derived_signals(text: str, definitions: dict) -> str:
    """Derived boolean signals eq_<x>_<v> stand for "x equals v". Replace the phrase that says so ("the value of
    signal x equals to v", "x needs to be v", "x isn't v") by the derived name (prefixed by "not " when negated);
    when no phrase is found, append one sentence defining the name."""
    notes = []
    for name, definition in definitions.items():
        left, value = definition.get("left"), definition.get("value")
        if definition.get("op") != "equals" or left is None or value is None:
            continue
        pattern = re.compile(
            r"(?:\b(?:the\s+)?(?:value\s+of\s+)?(?:the\s+)?(?:signal\s+)?)?(?<![A-Za-z0-9_])" + re.escape(left)
            + r"(?![A-Za-z0-9_])(?P<mid>[^.;,]{0,60}?)(?<![\d.])(?P<num>-?\d+(?:\.\d+)?)(?!\.?\d)", re.I)
        done = False
        for match in pattern.finditer(text):
            try:
                same = float(match.group("num")) == float(value)
            except ValueError:
                same = False
            if same and not _COMPARATIVE.search(match.group("mid")):
                prefix = "not " if _NEGATION.search(match.group("mid")) else ""
                text = text[:match.start()] + prefix + name + text[match.end():]
                done = True
                break
        if not done:
            notes.append(f"{name} is true when {left} equals {value}.")
    return text + ((" (Note: " + " ".join(notes) + ")") if notes else "")


def align_signal_names(text: str, signals: Sequence[dict]) -> str:
    """Rewrite the signal names in a natural-language requirement to the spelling the formulas use (signal["name"]):
    characters spelled apart ("I k 5 A r _ 6 f u d" -> Ik5Ar6fud), a name in words after a dist_ prefix
    ("kitchen counter" -> dist_kitchen_counter), the other spelling of a renamed signal (θ -> theta), and keyword
    names written sig_<name>."""
    edge_left, edge_right = r"(?<![A-Za-z0-9_])", r"(?![A-Za-z0-9_])"
    for signal in sorted(signals, key=lambda sig: -len(sig["name"])):
        name = signal["name"]
        variants = {signal["source"], signal["target"], name} - {name}
        patterns = []
        for spelling in {name, signal["source"].replace("_", ""), signal["target"]}:
            if len(spelling.replace("_", "")) >= 2:   # characters spelled apart, optional spaces / underscores between
                patterns.append(edge_left + r"[\s_]*".join(re.escape(c) for c in spelling if c != "_") + edge_right)
        tokens = name.split("_")
        if len(tokens) > 1:
            if tokens[0].lower() in ("dist", "distance"):
                tokens = tokens[1:]
            patterns.append(r"(?i:" + edge_left + r"\s+".join(re.escape(token) for token in tokens) + edge_right + ")")
        for variant in variants:
            patterns.append(edge_left + re.escape(variant) + edge_right)
        if name.startswith("sig_") and len(name) > 4:
            patterns.append(edge_left + re.escape(name[4:]) + edge_right)
        for pattern in patterns:
            text = re.sub(pattern, lambda _match, replacement=name: replacement, text)
    return text


def load_requirement(instance_id: str, cfg: dict | None = None) -> str:
    """The natural-language requirement of an instance (never the reference formula), with its signal names
    aligned to the formula's. The file and the fields per dataset come from cfg["requirement_sources"]."""
    dataset = instance_id.split("__")[0]
    source = ((cfg or {}).get("requirement_sources") or {}).get(dataset) or {}
    path = ROOT / (source.get("file") or f"benchmarks/{dataset}/instances.jsonl")
    fields = source.get("fields") or ["english_requirement", "nl_en", "nl"]
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if instance_id in (row.get("uid"), row.get("instance_id"), row.get("id")) or \
                instance_id.replace("__injected__", "__matched__") in (row.get("instance_id"), row.get("id")):   # B3 renamed injected -> matched
            for key in fields:
                if row.get(key):
                    text = str(row[key])
                    if not (cfg or {}).get("align_signal_names", True):
                        return text
                    config = instance_config(instance_id)
                    text = align_signal_names(text, instance_signals(instance_id))
                    text = align_enum_signals(text, instance_signals(instance_id))
                    return align_derived_signals(text, config.get("derived_signal_definitions") or {})
    return ""


def extract_formula(text: str, signals: Sequence[str], aliases: dict[str, str] | None = None,
                    repairs: list[str] | None = None) -> str | None:
    """The formula in an LLM reply: the last <stl>...</stl> if present, else the reply itself (code fences and a
    leading label removed), else its last non-empty line. Returns the loop's BUSTLE string or None."""
    found = STL_TAG.findall(text)
    if found:
        return parse_candidate(found[-1], signals, aliases, repairs)
    cleaned = re.sub(r"```[a-zA-Z]*", "", text).replace("`", "").strip()
    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    for option in ([cleaned] if cleaned else []) + lines[::-1][:3]:
        option = re.sub(r"^(final\s+)?(stl\s+)?(formula|answer)\s*[:：]\s*", "", option, flags=re.I).strip()
        parsed = parse_candidate(option, signals, aliases, repairs)
        if parsed is not None:
            return parsed
    return None


def write_summary(path: Path, ctx: Context) -> None:
    cfg = ctx.cfg
    payload = {**ctx.stats.as_dict(), "provider": cfg["provider"], "model": cfg["model"],
               "model_version": cfg["model_version"], "config_hash": config_hash(cfg),
               "temperature": cfg["temperature"], "top_p": cfg["top_p"], "n": cfg["n"], "R": cfg["R"],
               "direct_max_iters": cfg.get("direct_max_iters"),
               "direct_stop_on_unchanged": cfg.get("direct_stop_on_unchanged", False)}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True))
    temporary.replace(path)


def write_repair_csv(path: Path, items: Sequence[tuple[str, int]], total: int, iteration: int) -> None:
    """Candidates in the Java repair output format, so the loop and run_record read them unchanged."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [CSV_HEADER]
    for index, (formula, wrong) in enumerate(items):
        accuracy = (total - wrong) / total if total else 0.0
        lines.append(";".join([str(iteration), '"llm"', '"llm_ce"', str(index), str(len(items)), f"{1 - accuracy:.8f}",
                               f"{accuracy:.8f}", "", "", str(formula_length(formula)), "0.0", str(wrong), "0", "0",
                               "", "", "", "", f'"{formula}"']))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
