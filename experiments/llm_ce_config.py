"""LLM baselines (LLM-CE and LLM-direct): one fixed model for every instance and dataset.

The API key is NOT stored here: put OPENAI_API_KEY (or the variable named by api_key_env) in the project's .env.
Everything below is logged with every run (llm_ce_summary.json, config_hash).
"""

CONFIG = {
    # ---- model (one fixed model for all instances) ----------------------------------------------------------
    "provider": 'openai',                 # "openai" = the OpenAI API or any OpenAI-compatible endpoint (via base_url); also "anthropic"
    "model": 'gpt-5.6-sol',          # GPT-5.6, the model that also produced the initial formulas; written into every run
    "model_version": 'gpt-5.6-sol',  # the exact snapshot, written into every run
    "base_url": '',                  # empty = the default OpenAI endpoint
    "api_key_env": 'OPENAI_API_KEY',  # name of the environment variable that holds the key
    "extra_body": {},                # extra request fields (none for GPT-5.6)

    # ---- LLM-CE only: calls per attempt (n), attempts per round (R), trace budget in the prompt ---------------
    "n": 5,
    "R": 3,
    "trace_token_max": 12000,
    "chars_per_token": 3.0,

    # ---- requirements: where each dataset's natural-language text comes from ------------------------------------
    "align_signal_names": True,       # rewrite signal names in the requirement to the names the formula uses
    "requirement_sources": {
        "DeepSTL": {"file": "benchmarks/DeepSTL/instances.jsonl", "fields": ["english_requirement", "nl_en", "nl"]},
        "STDP": {"file": "benchmarks/STDP/instances.jsonl", "fields": ["english_requirement", "nl_en", "nl"]},
        "Archcomp": {"file": "benchmarks/Archcomp/instances.jsonl", "fields": ["english_requirement", "nl_en", "nl"]},
    },

    # ---- sampling -----------------------------------------------------------------------------------------
    "temperature": 0.7,
    "top_p": 1.0,
    "max_tokens": 800,
    "timeout_s": 120,

    # ---- prompt pieces -----------------------------------------------------------------------------------
    "grammar": {
        "description": (
            "Formulas use signal names, comparisons (<, <=, >, >=, ==) against numeric constants, boolean signals "
            "as bare names, and the operators: ! (not), & (and), | (or), -> (implies), G[a,b](f) (always on [a,b]), "
            "F[a,b](f) (eventually on [a,b]), (f U[a,b] g) (until on [a,b]). Intervals are numeric and in the "
            "trace's time unit. Use parentheses around binary operators."
        ),
        "examples": [
            "G[0,10](speed < 30)",
            "(F[0,5](door == 1) & G[0,5](temp <= 40.5))",
            "G[0,20]((req > 0) -> F[0,3](ack > 0))",
        ],
    },

    # NOTE: not read by any code (the signal list in the prompt is built from each instance's own signals).
    "interface": "The interface is a set of signals, each with a name and a type. The types are: boolean, integer, "
                 "real, and enumeration. Each signal has a name (a string) and a type (one of the four types). "
                 "The interface is given as a list of signals, one per line, in the format: <name> (<type>)",
}


# LLM-direct ("LLM repair without trace feedback"): a self-check loop, no traces, no oracle, no queries.
#   judge   -> the LLM says whether the current formula expresses the requirement (CORRECT / INCORRECT)
#   CORRECT -> output the current formula;  INCORRECT -> repair once, then judge the new formula again
# The loop stops when the judge says CORRECT, when the verdict is unclear, when a repair cannot be parsed, or after
# direct_max_iters iterations (an unchanged repair stops it only if direct_stop_on_unchanged is True).
# Maximum iterations. None = the tag's `rounds` (10), the same number of rounds ReSTLess and LLM-CE get; a number
# overrides it.
CONFIG["direct_max_iters"] = None
# False: a repair that returns the same formula does not end the loop (the judge runs again, the next repair is a
# new sample); True: stop at the first unchanged repair.
CONFIG["direct_stop_on_unchanged"] = False

CONFIG["judge_template"] = (
    "Please check whether the following STL formula accurately expresses the natural-language requirement.\n"
    "Answer with exactly one word: CORRECT if it expresses the requirement exactly, INCORRECT otherwise.\n\n"
    "Natural-language requirement:\n{requirement}\n\n"
    "{interface}\n\n"
    "STL syntax:\n{grammar}\n"
    "Examples:\n{examples}\n\n"
    "Current STL:\n{phi}"
)

CONFIG["direct_template"] = (   # the repair step, used after the judge said INCORRECT
    "The following STL formula does not accurately express the natural-language requirement. "
    "Please correct it and output the final formula.\n"
    "Please output only the final STL formula.\n\n"
    "Natural-language requirement:\n{requirement}\n\n"
    "{interface}\n\n"
    "STL syntax:\n{grammar}\n"
    "Examples:\n{examples}\n\n"
    "Current STL:\n{phi}"
)

# LLM-CE: requirement + current formula + labelled traces, up to R attempts per round.
CONFIG["template"] = (
    "You repair a Signal Temporal Logic (STL) formula.\n\n"
    "Requirement (natural language):\n{requirement}\n\n"
    "{interface}\n\n"
    "STL syntax:\n{grammar}\n"
    "Examples:\n{examples}\n\n"
    "Current formula:\n{phi}\n\n"
    "The current formula is wrong: it disagrees with the oracle labels on some of the traces below. "
    "A trace labelled 'satisfies' meets the requirement, a trace labelled 'violates' does not. "
    "Misclassified traces are listed first.\n\n"
    "{traces}\n"
    "Return a corrected formula that classifies every trace above correctly and changes the current formula "
    "as little as possible. Think briefly, then give the final formula inside <stl>...</stl> tags."
)
