# voicebench

LLM-leg latency measurement for voice agents.

Measures **one term** of the voice latency budget. Endpointing, ASR, TTS and
network sit outside this instrument, so the numbers are an input to the
caller's experience, not a description of it — a 700ms time-to-first-token
is not a 700ms pause for the person on the phone.

---

## Findings

**TL;DR**

1. **One sentence of system prompt removes 65% of the silence in a
   tool-calling turn** — first audible token drops 1943ms → 681ms on
   haiku, 2723ms → 1303ms on sonnet, for ~8 extra tokens. [Finding 9]
2. **Stream, always.** 33% of the wait on a single-hop turn, at no cost
   to total time. [Finding 1]
3. **Almost everything else is a null** — provider, framework and effort
   all measure the same. On short prompts you are measuring the
   scheduler, not the model. [Findings 4, 5, 7]

What a caller hears, and when. Every number below comes from a committed run
in `results/`, named at the point it is used and re-checkable with
`--replay`.

TTFT here is the `spoken` column: wall-clock from request start until the
first token that could be *spoken*. Tool-call JSON does not count — nobody
hears it. On blocking rows the column is deliberately blank, because
nothing is speakable until the call returns.

**The through-line:** on short prompts against hosted models, this
instrument is mostly measuring the scheduler — queueing, routing, and
warm-fleet availability — rather than the network or the weights. That is
why provider (finding 4), framework (finding 5) and effort (finding 7)
all came back null, why prefill stays invisible until 30k tokens
(finding 8), and why the two things that did move are which model pool
you land in (finding 3) and how many hops the turn takes (finding 6).
Four nulls in a row is not a boring result; it locates where the time
actually lives — and finding 9 is what you do about it once you know.
Region is the obvious next null to check and **has not been run here**:
the suite exists, the data does not.

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

### 2. First token lands at ~55–65% of total, for short answers

`results/providers-*.json` — plain, streaming, n=7

| provider | model | TTFT | total p50 | TTFT as % of total |
|---|---|---|---|---|
| bedrock | haiku-4.5 | 624ms | 962ms | 65% |
| anthropic | haiku-4.5 | 661ms | 1149ms | 58% |
| bedrock | sonnet-4.5 | 1282ms | 2274ms | 56% |
| anthropic | sonnet-4.5 | 1290ms | 2332ms | 55% |

**This band is a property of short answers, not of the stack.** Total
decomposes as `TTFT + (n_tokens − 1) × decode_time`. TTFT is roughly
fixed; the decode term grows linearly with output length. These responses
are 26-44 tokens, which is what puts the ratio at 55-65%. Using haiku's
measured 9.9ms/token, a 150-token answer would sit at
`624 + 149 × 9.9 = 2099ms` total, dropping TTFT to **30%**.

So: **for 30-45 token responses, TTFT is 55-65% of total.** That is the
right range for a voice turn, where answers are short by design, and
wrong as a general rule.

### 3. The model gap on a trivial prompt is scheduler, not compute

Same run, holding provider constant:

| provider | haiku-4.5 | sonnet-4.5 | penalty |
|---|---|---|---|
| bedrock | 624ms | 1282ms | **+658ms (+105%)** |
| anthropic | 661ms | 1290ms | **+629ms (+95%)** |

Sonnet roughly doubles TTFT, and it is tempting to file that under
"bigger model is slower". Decompose it and that reading does not survive.
The `plain` turn is an 18-token system prompt plus a short question,
about 30 tokens total, and TTFT covers exactly two pieces of compute:
prefill over those tokens, and one decode step.

| component | haiku | sonnet | difference |
|---|---|---|---|
| prefill, 30 tokens @ 4.1 µs/tok (finding 8) | 0.12ms | — | ~0ms |
| one decode step | 9.9ms | 23.1ms | **13ms** |
| **measured TTFT gap** | | | **658ms** |

Decode rates come from the same run: haiku 338ms for 34 tokens
(101 tok/s), sonnet 992ms for 43 tokens (43 tok/s). Prefill on 30 tokens
is 0.12ms using the slope measured in finding 8.

**Compute explains 13ms of a 658ms gap.** The remaining ~645ms is not
work the model is doing — it is queueing, routing, and warm-fleet
availability differing between the two serving pools. That is a
different claim than "bigger model is slower", and a more useful one:
it is a property of the pool you are routed to, not of the weights, so
no amount of prompt engineering moves it and it may not hold on
dedicated capacity.

Stated honestly, this is inference by elimination — the harness measures
TTFT, not a scheduler — but the residual is 50x the compute it would
have to be explained by.

### 4. Bedrock vs Anthropic: no meaningful TTFT difference

| model | bedrock | anthropic | delta |
|---|---|---|---|
| haiku-4.5 | 624ms | 661ms | +37ms (+6%) — noise |
| sonnet-4.5 | 1282ms | 1290ms | +8ms (+0.6%) — noise |

Both deltas sit under the 8% noise floor. On this machine, this network,
this afternoon: **pick a provider on cost, quota, or ergonomics — not on
TTFT.**

### 5. Strands adds no measurable overhead over raw Bedrock

`results/frameworks-20260901-163231.json` — sonnet-4.5, n=7, `gap=6`

| scenario / path | bedrock | strands | delta |
|---|---|---|---|
| plain / blocking | 2276ms | 2200ms | −75ms (−3%) — noise |
| plain / streaming | 1243ms | 1266ms | +23ms (+2%) — noise |
| schema / blocking | 2264ms | 2385ms | +122ms (+5%) — noise |
| schema / streaming | 1244ms | 1335ms | +91ms (+7%) — noise |
| lookup / blocking | 3485ms | 3556ms | +70ms (+2%) — noise |

A later `gap=12` run of the lookup rows alone adds two more, including the
streaming comparison that had failed twice:

| scenario / path | bedrock | strands | delta |
|---|---|---|---|
| lookup / blocking | 3917ms | 3650ms | −267ms (−7%) — noise |
| lookup / streaming | 3267ms | 3255ms | −12ms (−0%) — noise |

Blocking rows compared on `total_p50`, streaming rows on `spoken_p50`.
Every delta sits inside the 8% noise floor, and the sign is not even
consistent — Strands is faster on one row and slower on four.

An earlier run at `gap=2.0` could not make this comparison at all: 5 of 6
bedrock rows died of throttling while Strands retried through the same
errors, so the suite was measuring retry policy rather than speed.
Raising the gap to 6s recovered 11 of 12 rows and removed the
absorbed-retry bulge from the tails.

**Take:** the framework is not the cost. Choose it on ergonomics.

### 6. A tool lookup costs ~1.2s, and the caller hears none of it

`results/frameworks-20260901-163231.json` (`gap=6`) — sonnet-4.5,
blocking, n=7

| provider | plain | lookup (two hops) | penalty |
|---|---|---|---|
| bedrock | 2276ms | 3485ms | +1210ms (+53%) |
| strands | 2200ms | 3556ms | +1355ms (+62%) |

An earlier contaminated run put this at 8943ms and +288%. That was
throttling, not tool cost. **A second hop costs roughly 1.2s, not 6.6s.**

`results/custom-20260901-165804.json` (`gap=12`) — lookup rows only, n=7

The interesting number is not the penalty but where it falls. On a
lookup turn, first audible token arrives at **89-92% of total**:

| provider | TTFT | total p50 | TTFT as % of total |
|---|---|---|---|
| bedrock | 3267ms | 3651ms | **89%** |
| strands | 3255ms | 3538ms | **92%** |

Against ~58% on a plain turn (finding 2). The mechanism is confirmed:
the first hop emits tool-call JSON, which is not speech, so the caller
waits out almost the entire turn before hearing anything.

Streaming still helps, but far less than it does on a single hop. Within
the `gap=12` run, blocking total against streaming TTFT:

| provider | blocking total | streaming TTFT | audio arrives earlier by |
|---|---|---|---|
| bedrock | 3917ms | 3267ms | 650ms (17%) |
| strands | 3650ms | 3255ms | 395ms (11%) |

Compare finding 1, where streaming removed 33% of the wait on a plain
turn — 341ms off a 1041ms wait, and 37% on the replicate run.

**Take:** budget ~1.2s per hop, and expect streaming to recover only
11-17% of a tool turn instead of ~33%. The silence is structural, not a
tuning problem — but structural is not unavoidable: **finding 9 removes
65% of it with one sentence of prompt.**

### 7. Effort does not buy time to first token

Not on a trivial turn, and not on a tool turn either.

`results/effort-20260901-172130.json` — sonnet-5, plain, streaming, n=7

| effort | TTFT | total p50 | tokens |
|---|---|---|---|
| default | 1112ms | 1492ms | 34 |
| low | 1048ms (−6%) — noise | 1349ms | 31 |
| medium | 1024ms (−8%) — noise | 1570ms | 37 |

Three checks say that is a real null rather than an effect too small for
n=7 to resolve. It is **non-monotonic** — if effort drove TTFT the order
would be default > medium > low, and it is 1112 > 1024 < 1048. Every raw
range **contains every other median**, in all six pairwise directions
(default 990-1928, low 921-1434, medium 925-1345). And `total_p50` is
non-monotonic too (1492 / 1349 / 1570) while tracking token count
(34 / 31 / 37) — the response got longer, not more considered. An earlier
two-row run reproduces the low-vs-medium half at 955ms vs 952ms.

`plain` is a one-sentence question, so that null could always be dismissed
as "adaptive thinking had nothing to engage". Effort is an axis now, so
the same sweep runs against a two-hop turn:

`results/custom-20260901-180525.json` — sonnet-5, lookup, streaming, n=7,
`gap=12`

| effort | TTFT | vs default | spread | tokens |
|---|---|---|---|---|
| default | 2410ms | (baseline) | 1.14x | 120 |
| high | 2347ms | −3% — noise | 1.19x | 121 |
| low | 2559ms | +6% — noise | 3.54x | 117 |
| xhigh | 2596ms | +8% — noise | 1.54x | 143 |
| max | 2872ms | +19% | 1.22x | 121 |
| medium | 3138ms | +30% | 1.72x | 119 |

**`high` measures the same as `default`** — −3%, inside the noise floor,
on the two cleanest rows in the set. That is the documented behaviour
(omitting `output_config` means `high`) confirmed end to end, and it is
the check that makes the rest of the table worth reading: the harness is
sending what it claims to send.

Across `low` through `xhigh` the curve is flat within noise. `max` at
+19% on a clean row is the one plausible monotonic signal — most
thinking, slowest first token. `medium` is out of place and is **not**
treated as a finding; see below.

**Take:** effort is a cost and quality lever, not a latency one, on
trivial and tool turns alike. Set it on quality grounds — for a voice
turn there is no TTFT penalty to trade against, which inverts the
intuitive assumption. `max` is the one level worth measuring before
adopting.

### 8. Prefill costs ~4 µs/token, and only haiku shows a clean curve

`results/scaling-20260901-182612.json` — anthropic, streaming, n=7,
`gap=6`. Prompt sizes verified against the tokenizer, not estimated.

| system prompt | haiku TTFT | marginal | sonnet TTFT | marginal |
|---|---|---|---|---|
| 22 tok | 697ms | — | 1468ms | — |
| 3,025 tok | 725ms (+4%) noise | 9.4 µs/tok | 2042ms (+39%) | 191 µs/tok |
| 10,035 tok | 747ms (+7%) noise | 3.1 µs/tok | 1993ms (+36%) | **−6.9 µs/tok** |
| 30,035 tok | 820ms (+18%) | 3.7 µs/tok | 1921ms (+31%) | **−3.6 µs/tok** |

**Haiku gives a real slope: 4.1 µs/token end to end**, or ~4.1ms per
1,000 tokens. All four rows are tight (1.31-1.50x spread), the series is
monotonic, and marginal cost is stable at 3.1-3.7 µs/tok across the two
upper segments. Prefill only clears the 8% noise floor at 30k tokens,
which is why a 3k prompt reads as free.

**`cache_read` is 0 on every row**, so this is genuine prefill and not
the service caching a large prompt on its own. That alternative is ruled
out by measurement rather than assumption.

**Sonnet does not produce a prefill curve at all.** It steps +574ms at 3k
and then *decreases* as the prompt grows; marginal cost goes negative.
Prefill compute cannot be negative, so whatever moves sonnet here is not
prompt length. Its rows are individually tight (1.18-1.37x), so this is
not ordinary contamination either. The end-to-end "15.1 µs/token" that
falls out of the endpoints is arithmetic on a non-monotonic series and
should not be quoted as sonnet's slope.

This is the measurement finding 3 leans on: at 30 tokens, 4.1 µs/token is
0.12ms, which is why prefill cannot account for a 658ms model gap.

### 9. One sentence of instruction removes 65% of the tool-call silence

Finding 6 says the silence on a two-hop turn is structural, because the
first hop emits tool JSON rather than speech. Structural is not the same
as unavoidable. `lookup-preamble` is the identical turn with one sentence
added to the system prompt:

> If you need to look something up, say so first in one short sentence,
> then look it up.

`results/preamble-20260901-185856.json` — anthropic, streaming, n=7,
`gap=8`

| model | scenario | TTFT | total | tokens |
|---|---|---|---|---|
| haiku-4.5 | lookup | 1943ms | 2133ms | 113 |
| haiku-4.5 | lookup-preamble | **681ms (−65%)** | 2322ms (+9%) | 123 |
| sonnet-4.5 | lookup | 2723ms | 3088ms | 112 |
| sonnet-4.5 | lookup-preamble | **1303ms (−52%)** | 3841ms (+24%) | 120 |

**The caller starts hearing speech 1.3-1.4 seconds sooner.**

The mechanism is confirmed rather than assumed: preamble TTFT lands on
the same number as a *single-hop* turn, because with the instruction the
first hop is a single-hop turn as far as the ear is concerned.

| model | plain TTFT | lookup-preamble TTFT | difference |
|---|---|---|---|
| haiku-4.5 | 697ms | 681ms | −16ms (−2%) |
| sonnet-4.5 | 1468ms | 1303ms | −165ms (−11%) |

**What it costs:** total grows 9% on haiku and 24% on sonnet, because the
model emits ~8-10 extra tokens and still makes both hops. That is the
right trade for voice. Total is not what a caller perceives — during
those extra milliseconds they are listening to a sentence instead of to
nothing.

**Take:** this is the only change here that alters what the caller
experiences rather than describing it, and it is one line of prompt. Do
it on every tool-calling turn.

One caveat: haiku's preamble row carries a single 2179ms outlier against
a cluster of `[667, 667, 676, 681, 681, 765]`, giving it a 3.27x spread.
The median is solid; the tail is not characterised at n=7.

### Results that are NOT trustworthy

Stated plainly so they are not quoted later as if they were findings.

**Now resolved.** An earlier `gap=6` run left `lookup / streaming` as
the one throttled row — bedrock failed outright, Strands retried through
it, and the sample was visibly bimodal. Rerunning those rows alone at
`gap=12`, which matches the per-request spacing single-hop rows get
because a lookup makes two calls per run, recovered all four rows with
no bimodality (spreads 1.11-1.73x). Finding 6 now rests on that run.

**`medium` effort on lookup turns is unexplained, and is not a finding.**
It reads +30% above default on the cleanest run, but its magnitude tracks
its own contamination across four runs rather than holding steady:

| spread | TTFT |
|---|---|
| 6.97x | 4998ms |
| 4.21x | 4630ms |
| 2.96x | 4921ms |
| 1.72x | 3138ms |

As the distribution tightens the median falls toward the pack, which is
what contamination looks like and not what a real effect looks like. Row
position was tested and ruled out — `medium` measured the same slow value
from first and last slot, and `default` the same fast value from both — so
it is not ordering. What remains unexplained is why that one level also
draws the widest spreads. An earlier draft of this section called the
effect real on the strength of the position test alone; ruling out one
alternative is not the same as establishing the cause. Treat any `medium`
number here as unresolved.

**Strands `tokens: 0` on blocking rows** is hardcoded in `providers.py`,
not measured. It reads like data. It isn't.

**Now resolved.** An earlier `gap=2.0` run appeared to show `schema` 24%
*faster* than `plain`, flagged here as probable contamination. The clean
run confirms that reading: schema costs +0% (bedrock) and +5% (strands)
on TTFT, both noise. An attached-but-unused schema is close to free, and
was never faster.

### Conditions

One machine, one residential network, us-east-1, on-demand capacity
throughout, medians of n=7 after discarding each row's warm-up run. p95
at n=7 is indicative only. Findings 1-4 and 7 come from runs at
`gap=2.0s`; finding 5 from `gap=6s`, and finding 6's TTFT numbers from
`gap=12s` on the lookup rows alone. Each step up was needed to stop
sonnet-4.5 rows throttling; a lookup row makes two calls per run and so
needs twice the spacing. Finding 7's plain rows run sonnet-5 at `gap=2.0s`,
which did not throttle; its lookup rows need `gap=12s`, since a lookup
makes two calls per run. Findings 2-4 measured sonnet at `gap=2.0s`, a spacing later shown to
kill 5 of 6 bedrock sonnet rows in the frameworks suite. Retries are
disabled, so a throttled call fails the row rather than inflating it, and
those rows all reported n=7 — they were not silently retried. But
near-threshold contention can stretch a number without ever returning a
429, so the +105% in finding 3 is worth re-running at `gap=6` before
being leaned on. Reproduce before trusting any of it.

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
| scenario | `plain`, `schema`, `lookup`, `bigprompt`, `bigprompt-cached` |
| path | `blocking`, `streaming` |
| effort | `default`, `low`, `medium`, `high`, `xhigh`, `max` |

`effort` crosses with every scenario, so "what does effort cost on a turn
hard enough to think about" is expressible. `default` sends no
`output_config` at all and is the baseline the levels are read against.

## Method

Each rule exists because skipping it produced a wrong answer first.

- **Retries disabled.** A retry sleeps inside the timer, so a throttled call
  reads as a slow model. Left on, this produced a time-to-first-token
  *longer* than the complete blocking response — an impossible result, and
  the one that started this project. **This holds at the SDK layer only.**
  Strands wraps its own throttle-retry around the model call and defeats it,
  which is why strands rows survive throttling that kills raw bedrock rows.
  Asymmetric row failure is therefore a red flag: the suite is measuring
  retry policy, not speed. Raise `--gap` until both sides survive before
  reading any provider delta.
- **Calls paced.** A burst measures your quota, not the model. `gap=2.0s`
  was not enough for sonnet-4.5 on on-demand capacity here — it lost 5 of 6
  bedrock rows. `gap=6s` recovered 11 of 12. Raise it until rows stop
  failing; a failed row costs a whole rerun, a slow run costs minutes.
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
- Effort rows compare `low` and `medium` against a `plain` baseline, which
  sends no `output_config` and therefore runs at the API default. There is no
  explicit-default row, so "default" here means "parameter omitted".
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

---

Built while working on production voice-agent evaluation at voiciee.ai.
