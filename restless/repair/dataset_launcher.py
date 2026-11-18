#!/usr/bin/env python3
# Provenance: STL Repair repository-specific component.
# This file is not part of the identified BUSTLE, CE-Class, or ATG upstream source.
# See the repository THIRD_PARTY.md for dependency and method attribution.


"""Config-driven CE-class / trace-generator / repair loop launcher.

This is the dataset-facing entry point.  Dataset-specific facts live in CLI
arguments or a JSON config:

  - formula CSV paths and rows
  - formula syntax conversion into BUSTLE prefix STL
  - signal names and value bounds
  - time grid
  - optional Java grammar path

The underlying loop is restless/repair/repair_loop.py; it accepts arbitrary
signal names, bounds, and time grids.
Numeric STL constants are never normalized or rescaled here. The constants
that come from the input formula are the constants passed into CE-class, trace
generation, Java repair, and monitor replay.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
from restless.common.formula_io import (  # noqa: E402
    convert_formula_to_bustle,
    formula_metadata,
    read_formula,
    write_formula_csv,
)


# Keep in sync with repair_loop.py --ceclass-k.
DEFAULT_CECLASS_K = 2
TEMPORAL_INTERVAL = re.compile(r"I=\[\s*([-\d.eE+]+)\s*,\s*([-\d.eE+]+)\s*\]")


@dataclass(frozen=True)
class SignalSpec:
    source: str
    target: str
    minimum: float
    maximum: float
    discrete: bool = False
    static: bool = False


def parse_args() -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        description="Run a generic STL repair loop from dataset configuration.",
    )
    parser.add_argument("--config", default="", help="Optional JSON dataset config.")
    parser.add_argument("--dataset-name", default=None)
    parser.add_argument("--llm-formula-csv", default=None)
    parser.add_argument("--oracle-formula-csv", default=None)
    parser.add_argument("--formula-row", type=int, default=None)
    parser.add_argument("--oracle-formula-row", type=int, default=None)
    parser.add_argument("--output-root", default=None)
    parser.add_argument(
        "--signals",
        default=None,
        help=(
            "Signal specs. Use source:target:min:max entries separated by commas, "
            "for example z1:z1:0:2,z2:z2:-1000:1000. "
            "Use name:min:max when source and target are the same."
        ),
    )
    parser.add_argument(
        "--normalize-values",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Deprecated no-op. Numeric STL constants are never normalized."
        ),
    )
    parser.add_argument("--time-start", type=float, default=None)
    parser.add_argument("--time-end", type=float, default=None)
    parser.add_argument("--time-step", type=float, default=None)
    parser.add_argument(
        "--sampling-frequency",
        type=float,
        default=None,
        help=(
            "Optional samples per time unit. If --time-step is omitted, "
            "time_step is derived as 1/sampling_frequency. When both are set, "
            "they must agree."
        ),
    )
    parser.add_argument(
        "--boolean-signals",
        default=None,
        help=(
            "Optional comma-separated Boolean signal names. Generated trace values "
            "for these signals are snapped to 0/1; STL constants are preserved."
        ),
    )
    parser.add_argument(
        "--ceclass-k",
        type=int,
        default=None,
        help=(
            "CE-class temporal split granularity. Each temporal window is cut "
            "into k segments, so the lattice grows like (child classes)^k per "
            "temporal operator. Config key: ceclass_k."
        ),
    )
    parser.add_argument(
        "--k-gen",
        type=int,
        default=None,
        help=(
            "SMT trace generation budget: number of signal switchings per "
            "generated trace. Forwarded as --k-gen-init. Config key: k_gen."
        ),
    )
    parser.add_argument(
        "--ceclass-class-limit",
        type=int,
        default=None,
        help="Max CE-classes expanded per round. Config key: ceclass_class_limit.",
    )
    parser.add_argument(
        "--round-trace-budget",
        type=int,
        default=None,
        help=(
            "Total traces generated per round, split across CE-classes. "
            "Config key: round_trace_budget."
        ),
    )
    parser.add_argument(
        "--generation-mode",
        choices=("ceclass", "obligation"),
        default=None,
        help=(
            "Stage-2 trace generation: CE-class behaviour partitioning, or "
            "syntax-tree coverage obligations. Config key: generation_mode."
        ),
    )
    parser.add_argument("--grammar-path", default=None)
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_known_args()


def load_config(path_text: str) -> dict[str, Any]:
    if not path_text:
        return {}
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    with path.open() as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Generic repair config must be a JSON object")
    return data


def cfg_value(args: argparse.Namespace, config: dict[str, Any], key: str, default: Any = None) -> Any:
    cli_value = getattr(args, key, None)
    if cli_value is not None and cli_value != "":
        return cli_value
    return config.get(key, default)


def cfg_bool(args: argparse.Namespace, config: dict[str, Any], key: str, default: bool) -> bool:
    value = cfg_value(args, config, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value)


def parse_signal_entry(entry: Any) -> SignalSpec:
    if isinstance(entry, dict):
        source = str(entry.get("source") or entry.get("name") or entry.get("target") or "").strip()
        target = str(entry.get("target") or entry.get("name") or source).strip()
        minimum = float(entry.get("min", entry.get("minimum")))
        maximum = float(entry.get("max", entry.get("maximum")))
        discrete = bool(entry.get("discrete", entry.get("integer", False)))
        static = bool(entry.get("static", False))
    elif isinstance(entry, str):
        pieces = [piece.strip() for piece in entry.split(":")]
        if len(pieces) == 3:
            source = target = pieces[0]
            minimum = float(pieces[1])
            maximum = float(pieces[2])
            discrete = False
        elif len(pieces) == 4:
            source, target = pieces[0], pieces[1]
            minimum = float(pieces[2])
            maximum = float(pieces[3])
            discrete = False
        else:
            raise ValueError(
                "Signal entries must be source:target:min:max or name:min:max; "
                f"got {entry!r}"
            )
    else:
        raise ValueError(f"Invalid signal entry: {entry!r}")
    if not isinstance(entry, dict):
        static = False
    if not source or not target:
        raise ValueError(f"Signal source/target cannot be empty: {entry!r}")
    if static and (discrete or minimum != maximum):
        raise ValueError(f"Static signal {target} needs min == max and discrete=false: {entry!r}")
    if not static and minimum >= maximum:
        raise ValueError(f"Signal {target} has invalid bounds [{minimum}, {maximum}]")
    return SignalSpec(source=source, target=target, minimum=minimum, maximum=maximum,
                      discrete=discrete, static=static)


def parse_signal_specs(args: argparse.Namespace, config: dict[str, Any]) -> list[SignalSpec]:
    cli_signals = cfg_value(args, config, "signals")
    if cli_signals is None:
        return []
    if isinstance(cli_signals, str):
        entries = [part.strip() for part in cli_signals.split(",") if part.strip()]
    elif isinstance(cli_signals, list):
        entries = cli_signals
    else:
        raise ValueError("signals must be a string or a list")
    specs = [parse_signal_entry(entry) for entry in entries]
    sources = [spec.source for spec in specs]
    if len(set(sources)) != len(sources):
        raise ValueError(f"Duplicate source signal names: {sources}")
    return specs


def signal_aliases(specs: list[SignalSpec]) -> dict[str, str]:
    return {
        spec.target: spec.source
        for spec in specs
        if spec.target and spec.source and spec.target != spec.source
    }


def convert_required_formula(
    formula: str,
    label: str,
    aliases: dict[str, str] | None = None,
) -> str:
    try:
        return convert_formula_to_bustle(formula, signal_aliases=aliases)
    except Exception as exc:
        raise ValueError(
            f"{label} formula cannot be converted to BUSTLE/prefix STL. "
            "Check the CSV formula syntax before repair/CE-class/trace generation."
        ) from exc


def infer_signal_specs(formulas: list[str]) -> list[SignalSpec]:
    signal_names: set[str] = set()
    for index, formula in enumerate(formulas, start=1):
        _, names = formula_metadata(convert_required_formula(formula, f"formula[{index}]"))
        signal_names.update(names)
    return [
        SignalSpec(source=name, target=name, minimum=1e-6, maximum=0.999999)
        for name in sorted(signal_names)
    ]


def remap_formula_signals(formula: str, specs: list[SignalSpec]) -> str:
    result = formula
    for spec in sorted(specs, key=lambda item: len(item.target), reverse=True):
        if spec.source == spec.target:
            continue
        if re.fullmatch(r"[A-Za-z_]\w*", spec.target):
            result = re.sub(rf"\b{re.escape(spec.target)}\b", spec.source, result)
        else:
            result = result.replace(spec.target, spec.source)
    return result


def add_derived_signal_specs(specs: list[SignalSpec], formulas: list[str]) -> list[SignalSpec]:
    """Add bounds for common ARCH-COMP derived signals introduced by the adapter."""
    by_source = {spec.source: spec for spec in specs}
    wanted: set[str] = set()
    for formula in formulas:
        _horizon, names = formula_metadata(formula)
        wanted.update(names)

    def bounds(name: str) -> tuple[float, float] | None:
        spec = by_source.get(name)
        if spec is None:
            return None
        return spec.minimum, spec.maximum

    def add(name: str, minimum: float, maximum: float) -> None:
        if name in by_source:
            return
        if minimum >= maximum:
            pad = max(1e-6, abs(minimum) * 1e-6)
            minimum -= pad
            maximum += pad
        spec = SignalSpec(source=name, target=name, minimum=float(minimum), maximum=float(maximum))
        specs.append(spec)
        by_source[name] = spec

    for name in sorted(wanted):
        if name in by_source:
            continue
        if name == "pos_ref_error_margin":
            pos = bounds("pos") or bounds("Pos")
            ref = bounds("ref") or bounds("Ref")
            if pos and ref:
                pos_min, pos_max = pos
                ref_min, ref_max = ref
                max_abs = max(abs(pos_min - ref_max), abs(pos_max - ref_min))
                max_ref_abs = max(abs(ref_min), abs(ref_max))
                min_tolerance = 0.005
                max_tolerance = 0.005 + 0.03 * max_ref_abs
                add(name, -max_tolerance, max_abs - min_tolerance)
            continue
        abs_match = re.fullmatch(r"abs_(.+)_minus_(.+)", name)
        if abs_match:
            left = bounds(abs_match.group(1))
            right = bounds(abs_match.group(2))
            if left and right:
                left_min, left_max = left
                right_min, right_max = right
                max_abs = max(abs(left_min - right_max), abs(left_max - right_min))
                add(name, 0.0, max_abs)
            continue
        abs_single = re.fullmatch(r"abs_(.+)", name)
        if abs_single:
            base = bounds(abs_single.group(1))
            if base:
                add(name, 0.0, max(abs(base[0]), abs(base[1])))
            continue
        diff_match = re.fullmatch(r"(.+)_minus_(.+)", name)
        if diff_match:
            left = bounds(diff_match.group(1))
            right = bounds(diff_match.group(2))
            if left and right:
                add(name, left[0] - right[1], left[1] - right[0])
            continue
        if name in {"rise", "fall"} or re.fullmatch(r"g\d+", name):
            add(name, 0.0, 1.0)
    return specs


def infer_derived_signal_definitions(
    specs: list[SignalSpec],
    formulas: list[str],
    configured: Any = None,
) -> dict[str, dict[str, str]]:
    """Return algebraic identities that ATG must enforce for derived signals.

    The ARCH-COMP adapter canonicalizes ``x - y`` as ``x_minus_y`` so the
    Python/Java STL parsers can keep using signal-vs-constant atoms.  This
    metadata restores the lost identity at the SMT boundary instead of letting
    ATG synthesize the derived channel independently.
    """
    sources = {spec.source for spec in specs}
    used: set[str] = set()
    for formula in formulas:
        _horizon, names = formula_metadata(formula)
        used.update(names)

    definitions: dict[str, dict[str, str]] = {}
    configured_names = set(configured) if isinstance(configured, dict) else set()
    for name in sorted(used):
        if name in configured_names:
            continue
        match = re.fullmatch(r"(.+)_minus_(.+)", name)
        if not match:
            continue
        left, right = match.groups()
        if name not in sources:
            continue
        missing_bases = sorted({left, right} - sources)
        if missing_bases:
            raise ValueError(
                f"Derived signal {name!r} requires configured base signals "
                f"{left!r} and {right!r}; missing {missing_bases}. Add their real "
                "domains to signals instead of generating the derived channel independently."
            )
        definitions[name] = {
            "op": "subtract",
            "left": left,
            "right": right,
        }

    if configured is None:
        return definitions
    if not isinstance(configured, dict):
        raise ValueError("derived_signal_definitions must be a JSON object")
    for derived, raw in configured.items():
        derived = str(derived).strip()
        if isinstance(raw, str):
            match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*-\s*([A-Za-z_]\w*)\s*", raw)
            if not match:
                raise ValueError(
                    f"Unsupported derived expression for {derived!r}: {raw!r}; "
                    "currently only 'left - right' is supported"
                )
            definition = {"op": "subtract", "left": match.group(1), "right": match.group(2)}
        elif isinstance(raw, dict):
            definition = {str(key): str(value).strip() for key, value in raw.items()}
        else:
            raise ValueError(f"Invalid derived definition for {derived!r}: {raw!r}")
        if definition["op"] not in {"subtract", "linear_difference", "linear_margin_subtract",
                                    "abs_difference",
                                    "equals"}:
            raise ValueError(
                f"Unsupported derived operation for {derived!r}: {definition['op']!r}"
            )
        if definition["op"] in {"subtract", "linear_difference", "abs_difference"}:
            required_keys = ("left", "right")
        elif definition["op"] == "equals":
            required_keys = ("left", "value")
        elif definition["op"] == "linear_margin_subtract":
            required_keys = ("left", "right", "subtrahend")
        else:
            required_keys = ("position_a", "position_b", "size", "velocity_1", "velocity_2")
        missing_keys = [key for key in required_keys if not definition.get(key)]
        if missing_keys:
            raise ValueError(f"Derived definition {derived!r} misses keys {missing_keys}")
        signal_keys = tuple(key for key in required_keys if key != "value")
        missing = sorted(
            name for name in (derived, *(definition[key] for key in signal_keys))
            if name not in sources
        )
        if missing:
            raise ValueError(
                f"Derived definition {derived!r} references unconfigured signals: {missing}"
            )
        definitions[derived] = definition
    return definitions


def is_default_boolean_signal_name(name: str) -> bool:
    return name in {"rise", "fall"} or re.fullmatch(r"g\d+", name) is not None


def parse_name_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    raise ValueError(f"Expected a string or list of names, got {value!r}")


def infer_boolean_signal_names(
    args: argparse.Namespace,
    config: dict[str, Any],
    specs: list[SignalSpec],
) -> list[str]:
    """Return source signal names whose trace values must stay Boolean 0/1."""
    explicit = parse_name_list(cfg_value(args, config, "boolean_signals", None))
    name_to_source: dict[str, str] = {}
    for spec in specs:
        name_to_source[spec.source] = spec.source
        name_to_source[spec.target] = spec.source

    boolean_sources: set[str] = set()
    for name in explicit:
        source = name_to_source.get(name)
        if source is None:
            raise ValueError(
                f"Boolean signal {name!r} is not configured. "
                "Use a source or target name from the signal specs."
            )
        boolean_sources.add(source)

    for spec in specs:
        if is_default_boolean_signal_name(spec.source) or is_default_boolean_signal_name(spec.target):
            boolean_sources.add(spec.source)
    # A static parameter is pinned to one value; it is never a free 0/1 signal.
    boolean_sources -= {spec.source for spec in specs if spec.static}
    return sorted(boolean_sources)


def warn_ceclass_k_resolution(formulas: list[str], ceclass_k: int, time_step: float) -> None:
    """Warn when a CE-class temporal segment is too short for the sample grid.

    CE-class cuts every temporal window into ceclass_k equal segments. If a
    segment spans fewer than ~2 samples, the resulting classes are
    indistinguishable once traces are replayed on the sample grid.
    """
    if ceclass_k <= 1 or time_step <= 0.0:
        return
    widths = [
        float(end) - float(start)
        for formula in formulas
        for start, end in TEMPORAL_INTERVAL.findall(formula)
    ]
    widths = [width for width in widths if width > 0.0]
    if not widths:
        return
    segment = min(widths) / ceclass_k
    if segment < 2.0 * time_step:
        print(
            f"WARNING   : ceclass_k={ceclass_k} splits the shortest temporal window "
            f"({min(widths):g}) into {segment:g}-wide segments, below 2 samples at "
            f"time_step={time_step:g}. Lower ceclass_k or lower time_step, "
            "otherwise the extra classes collapse on the sample grid."
        )


def resolve_path_arg(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    args, forwarded = parse_args()
    config = load_config(args.config)

    llm_formula_csv = cfg_value(args, config, "llm_formula_csv", "dataset/LLM_generated_STL.csv")
    oracle_formula_csv = cfg_value(args, config, "oracle_formula_csv", "dataset/Oracle_STL.csv")
    formula_row = int(cfg_value(args, config, "formula_row", 1))
    oracle_formula_row = int(cfg_value(args, config, "oracle_formula_row", formula_row))
    output_root = cfg_value(args, config, "output_root", "output/generic_repair_loop")
    dataset_name = cfg_value(args, config, "dataset_name", "generic")
    grammar_path = cfg_value(args, config, "grammar_path", "")
    time_start = float(cfg_value(args, config, "time_start", 0.0))
    time_step_value = cfg_value(args, config, "time_step", None)
    sampling_frequency_value = cfg_value(args, config, "sampling_frequency", None)
    if time_step_value is None:
        if sampling_frequency_value is None:
            time_step = 5.0
        else:
            sampling_frequency = float(sampling_frequency_value)
            if sampling_frequency <= 0.0:
                raise ValueError("sampling_frequency must be positive")
            time_step = 1.0 / sampling_frequency
    else:
        time_step = float(time_step_value)
        if time_step <= 0.0:
            raise ValueError("time_step must be positive")
        if sampling_frequency_value is not None:
            sampling_frequency = float(sampling_frequency_value)
            if sampling_frequency <= 0.0:
                raise ValueError("sampling_frequency must be positive")
            implied = 1.0 / time_step
            if abs(implied - sampling_frequency) > max(1e-9, 1e-9 * abs(implied)):
                raise ValueError(
                    f"time_step={time_step:g} and sampling_frequency="
                    f"{sampling_frequency:g} are inconsistent; expected {implied:g}"
                )
    sampling_frequency = 1.0 / time_step
    configured_normalize_values = cfg_bool(args, config, "normalize_values", False)
    normalize_values = False

    ceclass_k = cfg_value(args, config, "ceclass_k", None)
    ceclass_k = int(ceclass_k) if ceclass_k is not None else None
    if ceclass_k is not None and ceclass_k < 1:
        raise ValueError("ceclass_k must be >= 1")
    k_gen = cfg_value(args, config, "k_gen", None)
    k_gen = int(k_gen) if k_gen is not None else None
    if k_gen is not None and k_gen < 1:
        raise ValueError("k_gen must be >= 1")
    ceclass_class_limit = cfg_value(args, config, "ceclass_class_limit", None)
    ceclass_class_limit = int(ceclass_class_limit) if ceclass_class_limit is not None else None
    round_trace_budget = cfg_value(args, config, "round_trace_budget", None)
    round_trace_budget = int(round_trace_budget) if round_trace_budget is not None else None
    generation_mode = cfg_value(args, config, "generation_mode", None)
    if generation_mode is not None and generation_mode not in ("ceclass", "obligation"):
        raise ValueError("generation_mode must be 'ceclass' or 'obligation'")

    llm_raw = read_formula(resolve_path_arg(llm_formula_csv), formula_row).strip()
    oracle_raw = read_formula(resolve_path_arg(oracle_formula_csv), oracle_formula_row).strip()
    specs = parse_signal_specs(args, config)
    aliases = signal_aliases(specs)
    llm_bustle = convert_required_formula(llm_raw, "LLM", aliases=aliases)
    oracle_bustle = convert_required_formula(oracle_raw, "Oracle", aliases=aliases)
    specs = specs or infer_signal_specs([llm_bustle, oracle_bustle])
    llm_bustle = remap_formula_signals(llm_bustle, specs)
    oracle_bustle = remap_formula_signals(oracle_bustle, specs)
    specs = add_derived_signal_specs(specs, [llm_bustle, oracle_bustle])
    derived_signal_definitions = infer_derived_signal_definitions(
        specs,
        [llm_bustle, oracle_bustle],
        config.get("derived_signal_definitions"),
    )

    configured_sources = {spec.source for spec in specs}
    _, llm_sources = formula_metadata(llm_bustle)
    _, oracle_sources = formula_metadata(oracle_bustle)
    formula_sources = set(llm_sources) | set(oracle_sources)
    missing_sources = sorted(formula_sources - configured_sources)
    if missing_sources:
        raise ValueError(
            f"Signals {missing_sources} appear in formulas but are not configured. "
            "Add them to --signals or the JSON config."
        )

    llm_formula = llm_bustle
    oracle_formula = oracle_bustle
    llm_horizon, _ = formula_metadata(llm_formula)
    oracle_horizon, _ = formula_metadata(oracle_formula)
    inferred_horizon = max(llm_horizon, oracle_horizon)
    time_end_config = cfg_value(args, config, "time_end", None)
    time_end = float(time_end_config) if time_end_config is not None else inferred_horizon
    if time_end < inferred_horizon:
        raise ValueError(
            f"time_end={time_end:g} is shorter than formula horizon {inferred_horizon:g}"
        )

    output_root_path = resolve_path_arg(output_root)
    prepared_dir = output_root_path / "_prepared_input"
    llm_prepared = prepared_dir / "LLM_generated_STL.csv"
    oracle_prepared = prepared_dir / "Oracle_STL.csv"
    write_formula_csv(llm_prepared, llm_formula)
    write_formula_csv(oracle_prepared, oracle_formula)

    boolean_signal_names = infer_boolean_signal_names(args, config, specs)
    if boolean_signal_names:
        boolean_sources = set(boolean_signal_names)
        specs = [
            SignalSpec(spec.source, spec.target, 0.0, 1.0, spec.discrete)
            if spec.source in boolean_sources and not spec.static
            else spec
            for spec in specs
        ]
    signal_names = [spec.source for spec in specs]
    signal_bounds = {spec.source: [spec.minimum, spec.maximum] for spec in specs}
    discrete_signal_names = [spec.source for spec in specs if spec.discrete]
    static_signal_names = [spec.source for spec in specs if spec.static]
    print(f"Dataset   : {dataset_name}")
    print(f"LLM raw   : {llm_raw}")
    print(f"LLM BUSTLE: {llm_bustle}")
    print(f"LLM run   : {llm_formula}")
    print(f"Oracle raw: {oracle_raw}")
    print(f"Oracle STL: {oracle_bustle}")
    print(f"Oracle run: {oracle_formula}")
    print(
        "Signals   : "
        + ", ".join(
            f"{spec.target}->{spec.source}[{spec.minimum:g},{spec.maximum:g}]"
            for spec in specs
        )
    )
    if configured_normalize_values:
        print("Value norm: disabled (normalize_values config ignored; STL constants are preserved)")
    else:
        print("Value norm: disabled")
    print(
        "Run bounds: "
        + ", ".join(
            f"{name}[{bounds[0]:g},{bounds[1]:g}]"
            for name, bounds in signal_bounds.items()
        )
    )
    if derived_signal_definitions:
        print(
            "Derived   : "
            + ", ".join(
                (f"{name}={definition['left']}-{definition['right']}"
                 if definition["op"] == "subtract" else f"{name}:{definition['op']}")
                for name, definition in sorted(derived_signal_definitions.items())
            )
            + " (enforced in ATG and verified after sampling)"
        )
    if boolean_signal_names:
        print(
            "Boolean   : "
            + ",".join(boolean_signal_names)
            + " (generated trace values forced to 0/1)"
        )
    if static_signal_names:
        print(
            "Static    : "
            + ",".join(static_signal_names)
            + " (time-invariant scenario parameters, value pinned)"
        )
    if discrete_signal_names:
        print(
            "Discrete  : "
            + ",".join(discrete_signal_names)
            + " (domain guard is inclusive; generated trace values rounded to integers)"
        )
    print(
        f"Time grid : [{time_start:g},{time_end:g}] step={time_step:g} "
        f"sampling_frequency={sampling_frequency:g}"
    )
    print(
        "Budgets   : "
        + ", ".join(
            f"{label}={'default' if value is None else value}"
            for label, value in (
                ("ceclass_k", ceclass_k),
                ("k_gen", k_gen),
                ("ceclass_class_limit", ceclass_class_limit),
                ("round_trace_budget", round_trace_budget),
            )
        )
    )
    warn_ceclass_k_resolution(
        [llm_formula, oracle_formula],
        ceclass_k if ceclass_k is not None else DEFAULT_CECLASS_K,
        time_step,
    )
    print(f"Prepared  : {prepared_dir}", flush=True)

    if args.prepare_only:
        return

    command = [
        sys.executable,
        "-m", "restless.repair.repair_loop",
        "--dataset-name", str(dataset_name),
        "--signal-names", ",".join(signal_names),
        "--signal-bounds", json.dumps(signal_bounds, separators=(",", ":")),
        "--derived-signal-definitions", json.dumps(
            derived_signal_definitions, separators=(",", ":")
        ),
        "--one-hot-groups", json.dumps(
            config.get("one_hot_groups", {}), separators=(",", ":")
        ),
        "--boolean-signal-names", ",".join(boolean_signal_names),
        "--discrete-signal-names", ",".join(discrete_signal_names),
        "--static-signal-names", ",".join(static_signal_names),
        "--start-formula-csv", str(llm_prepared),
        "--start-formula-row", "1",
        "--oracle-formula-csv", str(oracle_prepared),
        "--oracle-formula-row", "1",
        "--time-start", str(time_start),
        "--time-end", str(time_end),
        "--time-step", str(time_step),
        "--output-root", str(output_root),
        "--benchmark", str(cfg_value(args, config, "benchmark", dataset_name)),
        "--instance-id", str(cfg_value(args, config, "instance_id", f"{dataset_name}_row{formula_row}")),
        "--source", str(cfg_value(args, config, "source", Path(str(llm_formula_csv)).stem)),
    ]
    for flag, value in (
        ("--ceclass-k", ceclass_k),
        ("--k-gen-init", k_gen),
        ("--ceclass-class-limit", ceclass_class_limit),
        ("--round-trace-budget", round_trace_budget),
        ("--generation-mode", generation_mode),
    ):
        if value is not None:
            command.extend([flag, str(value)])
    configured_repair_args = config.get("repair_args", [])
    if not isinstance(configured_repair_args, list) or not all(
        isinstance(value, (str, int, float)) for value in configured_repair_args
    ):
        raise ValueError(
            "repair_args in the JSON config must be a flat list of CLI flags/values"
        )
    # Configured low-level repair flags come before explicit extra CLI flags,
    # so a value typed on the command line remains the final override.
    command.extend(str(value) for value in configured_repair_args)
    # Forwarded args come last so an explicit CLI flag overrides the config.
    command.extend(forwarded)
    if grammar_path:
        command.extend(["--grammar-path", str(grammar_path)])
    print("$", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
