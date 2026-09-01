# voicebench

LLM-leg latency measurement for voice agents.

Measures **one term** of the voice latency budget. Endpointing, ASR, TTS and
network sit outside this instrument, so the numbers are an input to the
caller's experience, not a description of it — a 700ms time-to-first-token
is not a 700ms pause for the person on the phone.

---

## Findings

What a caller hears, and when. Every number below comes from a committed run
in `results/`, named at the point it is used and re-checkable with
`--replay`.

TTFT here is the `spoken` column: wall-clock from request start until the
first token that could be *spoken*. Tool-call JSON does not count — nobody
hears it. On blocking rows the column is deliberately blank, because
nothing is speakable until the call returns.

### 1. Streaming buys ~350ms of perceived latency and costs nothing

`results/quick-*.json` — anthropic, haiku-4.5, plain, n=7

| path | first audible token | total p50 |
|---|---|---|
| blocking | — (nothing until 1041ms) | 1041ms |
| streaming | **700ms** | 1043ms |

End-to-end the two are identical: +1ms, well inside the 8% noise floor.
The suite's own delta table therefore labels this row `(noise)` — which is
true of the total and misses the point. The caller hears audio **341ms
earlier** for no additional work.

This replicated. A second quick run, overlapping the first by 15 seconds:
blocking 1120ms, streaming first token 709ms — a 411ms gap. Agreement to
within 9ms on TTFT is the strongest single result here, with the caveat
that the two runs shared conditions rather than being fully independent.

**Take:** stream unconditionally on single-hop turns. There is no tradeoff
to weigh.

### 2. First token lands at ~55–65% of total

`results/providers-*.json` — plain, streaming, n=7

| provider | model | TTFT | total p50 | TTFT as % of total |
|---|---|---|---|---|
| bedrock | haiku-4.5 | 624ms | 962ms | 65% |
| anthropic | haiku-4.5 | 661ms | 1149ms | 58% |
| bedrock | sonnet-4.5 | 1282ms | 2274ms | 56% |
| anthropic | sonnet-4.5 | 1290ms | 2332ms | 55% |

Consistent enough across two models and two providers to plan against:
**budget TTFT at roughly 60% of measured total latency.**

### 3. Model choice is the largest lever measured

Same run, holding provider constant:

| provider | haiku-4.5 | sonnet-4.5 | penalty |
|---|---|---|---|
| bedrock | 624ms | 1282ms | **+658ms (+105%)** |
| anthropic | 661ms | 1290ms | **+629ms (+95%)** |

Sonnet roughly doubles TTFT. Nothing else in these runs moves the number
this much for a single-hop turn.

### 4. Bedrock vs Anthropic: no meaningful TTFT difference

| model | bedrock | anthropic | delta |
|---|---|---|---|
| haiku-4.5 | 624ms | 661ms | +37ms (+6%) — noise |
| sonnet-4.5 | 1282ms | 1290ms | +8ms (+0.6%) — noise |

Both deltas sit under the 8% noise floor. On this machine, this network,
this afternoon: **pick a provider on cost, quota, or ergonomics — not on
TTFT.**

### 5. Tool lookups destroy TTFT, and streaming cannot rescue them

`results/frameworks-*.json` — strands, sonnet-4.5, streaming, n=7

| scenario | TTFT |
|---|---|
| plain | 1901ms |
| lookup (two hops) | **8869ms (+367%)** |

The mechanism matters more than the number. On a two-hop turn the first
hop emits tool-call JSON, which is not speech. Nothing is audible until
the *second* hop starts generating — so the streaming advantage from
finding 1 collapses:

| path | total p50 |
|---|---|
| lookup, blocking | 8943ms |
| lookup, streaming | 9245ms (+3%, noise) |

**Take:** a tool call is not a latency cost to shave, it is a turn where
the caller hears silence for ~9 seconds. Fill it (an acknowledgement
before dispatching the tool), avoid it, or make the first hop speak before
it calls.

### Results that are NOT trustworthy

Stated plainly so they are not quoted later as if they were findings.

**The framework comparison did not happen.** The `frameworks` suite exists
to measure Bedrock-vs-Strands overhead. 5 of 6 bedrock rows died with
`ThrottlingException`; 6 of 6 strands rows survived. That asymmetry is
retry policy, not speed — see the retry rule under [Method](#method).
Every strands row is visibly bimodal:

```
strands plain  blocking  [1969, 2061, 2106, 2303, 2406, 6911, 6991]
strands schema blocking  [2058, 2110, 2139, 2571, 6542, 6959, 7349]
```

A tight cluster plus a second cluster ~4.5s higher. Those are absorbed
retries, not tails. **No framework overhead number should be quoted from
this run.** One bedrock-vs-strands pair did survive (plain/blocking, 2409
vs 2303ms, −4%, inside the noise floor) — a hint, not the answer.

**`schema` appearing 24% faster than `plain`** (1439ms vs 1901ms) is
almost certainly contamination, not a finding. An attached-but-unused
schema should cost slightly more, never less. Both rows carry the retry
bimodality above.

**Strands `tokens: 0` on blocking rows** is hardcoded in `providers.py`,
not measured. It reads like data. It isn't.

### Conditions

One machine, one residential network, us-east-1, on-demand capacity
throughout, medians of n=7 after discarding each row's warm-up run,
`gap=2.0s`. p95 at n=7 is indicative only. The `frameworks` run was
additionally throttle-contaminated as described above. Reproduce before
trusting any of it.

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
python -m voicebench --replay results/providers-20260901-144209.json
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
  the one that started this project. **This holds at the SDK layer only.**
  Strands wraps its own throttle-retry around the model call and defeats it,
  which is why strands rows survive throttling that kills raw bedrock rows,
  and why the framework comparison above is not usable.
- **Calls paced.** A burst measures your quota, not the model. `gap=2.0s`
  was still not enough for sonnet-4.5 on on-demand capacity; raise it if
  rows fail.
- **First run discarded.** Warm-up, and cache-write on cached scenarios.
- **Rows interleaved**, provider varying fastest. Comparing a number taken
  this morning against one taken this evening measures capacity drift.
  For the same reason, never run two suites concurrently — they contend for
  the same quota and the delta becomes contention.
- **Text deltas only.** A tool-argument delta is JSON nobody hears and must
  not set time-to-first-spoken-token.
- **Raw providers run the loop by hand.** Comparing one raw call against a
  framework's two hops would report "extra call" as "framework overhead".
- **Non-firing runs excluded, not averaged.** A one-hop run and a two-hop
  run are not the same measurement.
- **Noise floor applied.** Deltas under 8% are labelled, not read as signal.
- **Comparisons are per group, and absences are named.** Each comparison
  states the metric it used — streaming groups on `spoken_p50`, blocking
  groups on `total_p50`, since spoken time does not exist on a blocking row.
  Comparisons that could not be made are printed as unavailable rather than
  silently omitted; an absent table must never look like a null result.

## Output

Every run writes JSON containing raw per-call samples plus a manifest with
SDK versions, region, timestamp, platform and known deviations. Re-analyse
without re-measuring via `--replay`. The runs behind the findings above are
committed in `results/`.

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
