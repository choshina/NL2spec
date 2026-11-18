"""Method definitions and the fairness rule of the ReSTLess evaluation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

METHODS = ("restless", "noloc", "no_self_coeff", "no_ceclass", "llm_ce", "llm_direct")

# Low-level switches each method sets on top of the shared configuration.
METHOD_OVERRIDES = {
    "restless": {"repair_localization": "sgrl"},
    "noloc": {"repair_localization": "off"},
    "no_self_coeff": {"repair_localization": "sgrl", "repair_localization_self_coefficient_cap": 1},
    "no_ceclass": {"repair_localization": "sgrl", "ceclass_schedule": "none"},
    # LLM baselines: llm_ce replaces only the repair component; llm_direct runs no loop (phi0 is checked as is).
    "llm_ce": {"repair_localization": "sgrl", "repair_backend": "llm"},
    "llm_direct": {},
}

# Each method may differ from restless only in the effective loop settings listed here; checked before any run.
RESTLESS_METHODS = ("restless", "noloc", "no_self_coeff", "no_ceclass")
RESTLESS_ALLOWED = {
    "restless": set(),
    "noloc": {"repair_localization"},
    "no_self_coeff": {"repair_localization_self_coefficient_cap"},
    "no_ceclass": {"ceclass_schedule"},
    "llm_ce": {"repair_backend"},
}

PROTOCOL_VERSION = "restless_v1"
EDIT_SPACE_NOTE = "no_replace_predicate"  # signal names are never changed; replace_predicate is disabled in RepairEditMutator

# Fields that legitimately differ per run (paths, attempt, bookkeeping), never per method.
RUN_LOCAL_FIELDS = {"attempt", "fairness_check", "record_jsonl", "output_root", "source_run_id"}


def effective_fairness(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Compare the effective run_config of the methods of one instance; only the declared fields may differ."""
    reference = records.get("restless")
    if reference is None:
        return {"passed": False, "illegal_differences": {}, "reason": "no restless run"}
    differences: dict[str, dict[str, list[Any]]] = {}
    for method, config in records.items():
        allowed = RESTLESS_ALLOWED.get(method, set()) | RUN_LOCAL_FIELDS | {"method", "ablation"}
        illegal = {field: [reference.get(field), config.get(field)]
                   for field in sorted(set(reference) | set(config))
                   if field not in allowed and reference.get(field) != config.get(field)}
        if illegal:
            differences[method] = illegal
    return {"passed": not differences, "illegal_differences": differences,
            "allowed": {m: sorted(RESTLESS_ALLOWED.get(m, set())) for m in records}}


def protocol_hash() -> str:
    frozen = {"version": PROTOCOL_VERSION, "edit_space": EDIT_SPACE_NOTE,
              "allowed": {k: sorted(v) for k, v in RESTLESS_ALLOWED.items()}}
    return hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()


def apply_method(base: dict[str, Any], method: str) -> dict[str, Any]:
    if method not in METHODS:
        raise ValueError(f"unknown method {method!r}")
    result = dict(base)
    result.update(METHOD_OVERRIDES[method])
    result["method"] = method
    return result
