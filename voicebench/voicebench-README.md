# voicebench

LLM-leg latency measurement for voice agents.

Measures **one term** of the voice latency budget. Endpointing, ASR, TTS and
network sit outside this instrument, so the numbers are an input to the
caller's experience, not a description of it.

## Install

```bash
pip install boto3 anthropic strands-agents
export ANTHROPIC_API_KEY=...
export ANTHROPIC_WORKSPACE_ID=...   # only for identity-linked keys
aws configure                        # for bedrock and strands rows
```

## Use

```bash
python -m voicebench --list                 # models, scenarios, suites
python -m voicebench --inspect              # real stream event shapes
python -m voicebench --suite quick
python -m voicebench --suite providers
python -m voicebench --replay results/providers-20260831-1730.json
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
| scenario | `plain`, `schema`, `lookup`, `bigprompt`, `bigprompt-cached`, `effort-*` |
| path | `blocking`, `streaming` |

## Method

Each rule exists because skipping it produced a wrong answer first.

- **Retries disabled.** A retry sleeps inside the timer, so a throttled call
  reads as a slow model. Left on, this produced a time-to-first-token
  *longer* than the complete blocking response — an impossible result, and
  the one that started this project.
- **Calls paced.** A burst measures your quota, not the model.
- **First run discarded.** Warm-up, and cache-write on cached scenarios.
- **Rows interleaved**, provider varying fastest. Comparing a number taken
  this morning against one taken this evening measures capacity drift.
- **Text deltas only.** A tool-argument delta is JSON nobody hears and must
  not set time-to-first-spoken-token.
- **Raw providers run the loop by hand.** Comparing one raw call against a
  framework's two hops would report "extra call" as "framework overhead".
- **Non-firing runs excluded, not averaged.** A one-hop run and a two-hop
  run are not the same measurement.
- **Noise floor applied.** Deltas under 8% are labelled, not read as signal.

## Output

Every run writes JSON containing raw per-call samples plus a manifest with
SDK versions, region, timestamp, platform and known deviations. Re-analyse
without re-measuring via `--replay`.

## Known deviations

- Temperature cannot be matched across providers; the Anthropic SDK removed
  sampling parameters in 1.x.
- The `effort` parameter shape is inferred from a capability flag, not
  documentation. Treat those rows as unverified until an error confirms or
  refutes the shape.
- Prefix caching is implemented for the Anthropic provider only. Bedrock
  uses `cachePoint` blocks, a different shape.
- The large system prompt is realistic in size and structure, filler in
  content.
- On-demand capacity everywhere. No provisioned throughput.

## Layout

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
