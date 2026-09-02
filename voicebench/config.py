"""Settings, prompts, models and scenarios.

Everything a reader needs in order to know WHAT was measured lives here.
How it was measured lives in runner.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# --------------------------------------------------------------------------
# Measurement settings
# --------------------------------------------------------------------------

RUNS = 8            # per row; the first is always discarded
GAP = 2.0           # seconds between calls
MAX_TOKENS = 256
NOISE_PCT = 8.0     # measured repeat-run variance on identical configs;
                    # deltas below this are labelled, not read as signal

ANTHROPIC_WORKSPACE = os.getenv("ANTHROPIC_WORKSPACE_ID", "")
DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")

# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

SYSTEM_SMALL = ("You are a provider services phone assistant. "
                "One or two short sentences.")

PLAIN_PROMPT = "Hi, are you open on Saturdays?"
LOOKUP_PROMPT = "Does member A4471 have coverage on March 3rd 2026?"

# On a two-hop turn the first hop emits tool JSON, which is not speech, so
# the caller hears nothing until hop two starts generating. This asks for a
# spoken sentence BEFORE the tool call. If the model complies, time to first
# audible token should collapse toward hop-one TTFT while total is unchanged
# -- the silence gets filled rather than shortened.
SYSTEM_PREAMBLE = (SYSTEM_SMALL +
                   " If you need to look something up, say so first in one "
                   "short sentence, then look it up.")


def build_big_system(approx_tokens: int = 3000) -> str:
    """Realistic in SIZE and SHAPE, filler in content.

    Prefill cost tracks token count, not meaning, which is what makes a
    filler prompt a fair proxy for a real one. Say so when publishing.
    """
    header = SYSTEM_SMALL + "\n\nABSOLUTE RULES\n"
    clause = ("POLICY {i}: Claims submitted beyond the timely filing window "
              "of ninety days require a documented exception reason and "
              "supervisor review before reprocessing. Escalate to a human "
              "representative if the caller disputes the determination.\n")

    # 5.2, not the usual 4: measured against the tokenizer for THIS filler
    # text (120,000 chars -> 23,109 tokens on haiku-4.5). The generic
    # estimate undershot by 23%, which on a scaling curve is a mislabelled
    # x-axis rather than a rounding error.
    target = int(approx_tokens * 5.2)
    # Clause count is DERIVED from the request. A fixed pool caps the output
    # silently: asking for 30k and 50k both returned the same 29,244-token
    # string, so a scaling curve built on it would read "prefill cost goes
    # flat above 29k" when what went flat is the prompt.
    per_clause = len(clause.format(i=0))
    count = max(1, (target - len(header)) // per_clause + 2)
    text = header + "".join(clause.format(i=i) for i in range(count))

    if len(text) < target:                    # never silently under-deliver
        raise ValueError(
            f"build_big_system({approx_tokens}) produced {len(text)//4} tokens")
    return text[:target]


SYSTEM_BIG = build_big_system()
SYSTEM_MID = build_big_system(10_000)
SYSTEM_HUGE = build_big_system(30_000)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ModelSpec:
    """One logical model, and its id on each provider.

    The ids differ by provider for the same weights, which is precisely
    what makes a provider comparison possible.
    """
    name: str
    bedrock: str | None = None
    anthropic: str | None = None
    supports_effort: bool = False


MODELS: dict[str, ModelSpec] = {
    m.name: m for m in [
        ModelSpec("haiku-4.5",
                  bedrock="us.anthropic.claude-haiku-4-5-20251001-v1:0",
                  anthropic="claude-haiku-4-5-20251001"),
        ModelSpec("sonnet-4.5",
                  bedrock="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                  anthropic="claude-sonnet-4-5-20250929"),
        ModelSpec("sonnet-4.6", anthropic="claude-sonnet-4-6", supports_effort=True),
        ModelSpec("sonnet-5",   anthropic="claude-sonnet-5",   supports_effort=True),
        ModelSpec("opus-5",     anthropic="claude-opus-5",     supports_effort=True),
    ]
}


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    """One experimental condition."""
    name: str
    prompt: str = PLAIN_PROMPT
    system: str = SYSTEM_SMALL
    tools: bool = False          # attach the schema
    run_loop: bool = False       # execute the tool hop
    cache: bool = False          # prefix caching on the system block
    effort: str | None = None    # set by the runner from the effort axis,
                                 # never in the table below
    note: str = ""


# Effort is an AXIS, not a scenario. Baking it into a scenario made it
# impossible to ask the only question worth asking -- what effort costs on a
# turn hard enough to think about -- because a scenario cannot compose with
# another scenario. As an axis it crosses with every one of them.
#
# None is not a level. It means output_config is not sent at all, which is
# the API default, and it is the baseline every other level is read against.
EFFORTS: tuple[str | None, ...] = (None, "low", "medium", "high", "xhigh", "max")


SCENARIOS: dict[str, Scenario] = {
    s.name: s for s in [
        Scenario("plain", note="baseline"),
        Scenario("schema", tools=True,
                 note="schema attached but never invoked: the per-turn tax "
                      "paid on every turn of a real call"),
        Scenario("lookup", prompt=LOOKUP_PROMPT, tools=True, run_loop=True,
                 note="two hops: model requests tool, code runs it, model speaks"),
        Scenario("bigprompt", system=SYSTEM_BIG,
                 note="~3k token system prompt: does prefill cost anything?"),
        Scenario("bigprompt-cached", system=SYSTEM_BIG, cache=True,
                 note="does prefix caching recover it?"),
        Scenario("lookup-preamble", prompt=LOOKUP_PROMPT, system=SYSTEM_PREAMBLE,
                 tools=True, run_loop=True,
                 note="lookup, but told to speak before calling the tool"),
        Scenario("midprompt", system=SYSTEM_MID,
                 note="~10k token system prompt"),
        Scenario("hugeprompt", system=SYSTEM_HUGE,
                 note="~30k token system prompt: prefill cost at 10x bigprompt"),
    ]
}


# --------------------------------------------------------------------------
# Suites
# --------------------------------------------------------------------------

SUITES: dict[str, dict] = {
    "quick": dict(
        providers=["anthropic"], models=["haiku-4.5"],
        scenarios=["plain"], paths=["blocking", "streaming"]),

    "frameworks": dict(
        providers=["bedrock", "strands"], models=["sonnet-4.5"],
        scenarios=["plain", "schema", "lookup"], paths=["blocking", "streaming"]),

    "providers": dict(
        providers=["bedrock", "anthropic"], models=["haiku-4.5", "sonnet-4.5"],
        scenarios=["plain"], paths=["streaming"]),

    "regions": dict(
        providers=["bedrock", "bedrock:us-west-2"], models=["sonnet-4.5"],
        scenarios=["plain"], paths=["streaming"]),

    "models": dict(
        providers=["anthropic"],
        models=["haiku-4.5", "sonnet-4.5", "sonnet-4.6", "sonnet-5"],
        scenarios=["plain"], paths=["streaming"]),

    # Does filling the tool-call silence actually work? Same two-hop turn,
    # with and without an instruction to speak before calling. total should
    # be unchanged; only spoken should move.
    "preamble": dict(
        providers=["anthropic"], models=["haiku-4.5", "sonnet-4.5"],
        scenarios=["lookup", "lookup-preamble"], paths=["streaming"]),

    # Four points on one axis -- 0, 3k, 10k, 30k tokens -- across two model
    # sizes. Two points cannot distinguish a slope from a step, and a null
    # at 3k alone cannot distinguish "prefill is free" from "prefill is
    # below the noise floor at this size".
    "scaling": dict(
        providers=["anthropic"], models=["haiku-4.5", "sonnet-4.5"],
        scenarios=["plain", "bigprompt", "midprompt", "hugeprompt"],
        paths=["streaming"]),

    "prompt": dict(
        providers=["anthropic"], models=["haiku-4.5", "sonnet-4.5"],
        scenarios=["plain", "bigprompt", "bigprompt-cached"], paths=["streaming"]),

    "tools": dict(
        providers=["anthropic"], models=["haiku-4.5"],
        scenarios=["plain", "schema", "lookup"], paths=["blocking", "streaming"]),

    # Crosses effort with a trivial turn and a two-hop one. plain alone
    # cannot answer whether effort matters: a one-sentence question gives
    # adaptive thinking almost nothing to engage, so a null there says
    # little about a turn that has to reason.
    "effort": dict(
        providers=["anthropic"], models=["sonnet-5"],
        scenarios=["plain", "lookup"], paths=["streaming"],
        efforts=[None, "low", "medium"]),
}
