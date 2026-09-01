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
    text = header + "".join(clause.format(i=i) for i in range(500))
    return text[: approx_tokens * 4]          # ~4 chars per token


SYSTEM_BIG = build_big_system()


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
    effort: str | None = None    # UNVERIFIED parameter shape — see providers
    note: str = ""


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
        Scenario("effort-low", effort="low", note="UNVERIFIED parameter shape"),
        Scenario("effort-medium", effort="medium", note="UNVERIFIED parameter shape"),
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

    "prompt": dict(
        providers=["anthropic"], models=["haiku-4.5", "sonnet-4.5"],
        scenarios=["plain", "bigprompt", "bigprompt-cached"], paths=["streaming"]),

    "tools": dict(
        providers=["anthropic"], models=["haiku-4.5"],
        scenarios=["plain", "schema", "lookup"], paths=["blocking", "streaming"]),

    "effort": dict(
        providers=["anthropic"], models=["sonnet-5"],
        scenarios=["effort-low", "effort-medium"], paths=["streaming"]),
}
