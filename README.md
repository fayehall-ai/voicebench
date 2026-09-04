# voicebench

Latency and behaviour measurement for voice agents, and the studies run
with it.

A voice turn is a five-term budget — endpointing, ASR, model, TTS,
network. Most published numbers measure one term and get quoted as if they
described the whole thing. A 700ms time-to-first-token is not a 700ms
pause for the person on the phone. This repository measures the terms
separately, then checks them against a real phone line.

## The studies

| study | measures | headline |
|---|---|---|
| **[Lab](./LAB-STUDY.md)** | the LLM leg, isolated | Four nulls in a row locate the bottleneck; one sentence of system prompt then removes ~60% of the silence in a tool-calling turn |
| **[Field](./FIELD-STUDY.md)** | whole turns, over PSTN | The lab predicted plain turns to within ~10%, and was 2× optimistic on tool calls |
| **[Guardrail](./GUARDRAIL-STUDY.md)** *(designed, not run)* | policy adherence under persistence | Design only — no results. The agent abandoned a correct answer under one assertion from the caller, and that is still an anecdote |

Findings that did not survive scrutiny are kept in the open rather than
deleted — the lab study carries a
[Results that are NOT trustworthy](./LAB-STUDY.md#results-that-are-not-trustworthy)
section, and its headline latency figure was
[corrected after publication](./FIELD-STUDY.md#the-prediction-held) when a
turn count turned out to belong to a different batch.

---

## Install

```bash
pip install boto3 anthropic strands-agents
export ANTHROPIC_API_KEY=...
export ANTHROPIC_WORKSPACE_ID=...   # only for identity-linked keys
aws configure                        # for bedrock and strands rows
```

Put `ANTHROPIC_WORKSPACE_ID` somewhere non-interactive shells will see it
(`~/.zshenv`, not `~/.zshrc`). Set only in an interactive rc, it produces a
mid-suite 400 rather than a clean startup failure.

## Use

```bash
python -m voicebench --list                 # models, scenarios, suites
python -m voicebench --inspect              # real stream event shapes
python -m voicebench --suite quick
python -m voicebench --suite providers
python -m voicebench --replay results/lab/providers-20260901-144209.json
```

Custom runs:

```bash
python -m voicebench \
  --providers bedrock,anthropic \
  --models haiku-4.5,sonnet-4.5 \
  --scenarios plain,bigprompt \
  --paths streaming --runs 20
```

## Axes

| axis | values |
|---|---|
| provider | `bedrock`, `bedrock:us-west-2`, `anthropic`, `strands` |
| model | `haiku-4.5`, `sonnet-4.5`, `sonnet-4.6`, `sonnet-5`, `opus-5` |
| scenario | `plain`, `schema`, `lookup`, `bigprompt`, `bigprompt-cached` |
| path | `blocking`, `streaming` |
| effort | `default`, `low`, `medium`, `high`, `xhigh`, `max` |

`effort` crosses with every scenario, so "what does effort cost on a turn
hard enough to think about" is expressible. `default` sends no
`output_config` at all and is the baseline the levels are read against.


## Output

Every run writes JSON containing raw per-call samples plus a manifest with
SDK versions, region, timestamp, platform and known deviations. Re-analyse
without re-measuring via `--replay`. The runs behind the
[lab study](./LAB-STUDY.md) are committed in `results/lab/`, the raw call
reports behind the [field study](./FIELD-STUDY.md) in `calls/`, and the
[guardrail](./GUARDRAIL-STUDY.md) runs will land in `results/guardrail/`.
One directory per study, so a manifest is never orphaned from the run that
produced it.

## Layout

One harness per study, and one output directory per harness.

```
voicebench/      lab harness, the LLM leg     ->  results/lab/
fieldtest/       phone rig, whole turns       ->  calls/
guardrail/       persistence probes           ->  results/guardrail/
```

Sharing one output directory across three studies is how you end up unable
to say which run produced which number, which is the problem the manifest
exists to solve.

Inside the lab harness:

```
voicebench/
  config.py      settings, prompts, models, scenarios, suites
  measure.py     Sample, Cell, Result, Clock, percentile
  providers.py   bedrock, anthropic, strands
  runner.py      ordering, pacing, aggregation, save/load
  report.py      comparison and noise flagging
  cli.py         argument parsing, --list, --inspect, --replay
```

Split by **layer**, not by scenario. Splitting by scenario would split
along the axis you most need to compare across, and separate processes
cannot interleave — which makes cross-scenario deltas invalid.

---

Built while working on production voice-agent evaluation at voiciee.ai.
