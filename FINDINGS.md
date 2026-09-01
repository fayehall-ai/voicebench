# Time to First Token

What a caller hears, and when. Measured with `voicebench`; every number
below comes from a run in `results/`, named at the point it is used.

TTFT here is the `spoken` column: wall-clock from request start until the
first token that could be *spoken*. Tool-call JSON does not count — nobody
hears it. On blocking rows the column is deliberately blank, because
nothing is speakable until the call returns.

---

## 1. Streaming buys ~350ms of perceived latency and costs nothing

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

---

## 2. First token lands at ~55–65% of total

`results/providers-*.json` — plain, streaming, n=7

| provider | model | TTFT | total p50 | TTFT as % of total |
|---|---|---|---|---|
| bedrock | haiku-4.5 | 624ms | 962ms | 65% |
| anthropic | haiku-4.5 | 661ms | 1149ms | 58% |
| bedrock | sonnet-4.5 | 1282ms | 2274ms | 56% |
| anthropic | sonnet-4.5 | 1290ms | 2332ms | 55% |

Consistent enough across two models and two providers to plan against:
**budget TTFT at roughly 60% of measured total latency.**

---

## 3. Model choice is the largest lever measured

Same run, holding provider constant:

| provider | haiku-4.5 | sonnet-4.5 | penalty |
|---|---|---|---|
| bedrock | 624ms | 1282ms | **+658ms (+105%)** |
| anthropic | 661ms | 1290ms | **+629ms (+95%)** |

Sonnet roughly doubles TTFT. Nothing else in these runs moves the number
this much for a single-hop turn.

---

## 4. Bedrock vs Anthropic: no meaningful TTFT difference

| model | bedrock | anthropic | delta |
|---|---|---|---|
| haiku-4.5 | 624ms | 661ms | +37ms (+6%) — noise |
| sonnet-4.5 | 1282ms | 1290ms | +8ms (+0.6%) — noise |

Both deltas sit under the 8% noise floor. On this machine, this network,
this afternoon: **pick a provider on cost, quota, or ergonomics — not on
TTFT.**

---

## 5. Tool lookups destroy TTFT, and streaming cannot rescue them

`results/frameworks-*.json` — strands, sonnet-4.5, streaming, n=7

| scenario | TTFT |
|---|---|
| plain | 1901ms |
| lookup (two hops) | **8869ms (+367%)** |

The mechanism matters more than the number. On a two-hop turn the first
hop emits tool-call JSON, which is not speech. Nothing is audible until
the *second* hop starts generating — so the streaming advantage from §1
collapses:

| path | total p50 |
|---|---|
| lookup, blocking | 8943ms |
| lookup, streaming | 9245ms (+3%, noise) |

**Take:** a tool call is not a latency cost to shave, it is a turn where
the caller hears silence for ~9 seconds. Fill it (an acknowledgement
before dispatching the tool), avoid it, or make the first hop speak before
it calls.

---

## Results that are NOT trustworthy

Stated plainly so they are not quoted later as if they were findings.

**The framework comparison did not happen.** The `frameworks` suite exists
to measure Bedrock-vs-Strands overhead. 5 of 6 bedrock rows died with
`ThrottlingException`; 6 of 6 strands rows survived. That asymmetry is
retry policy, not speed: `providers.py:70` gives boto `max_attempts: 1`
so bedrock fails fast, while Strands wraps its own throttle-retry layer
(`strands/event_loop/event_loop.py:460`) and absorbs the same errors.
Every strands row is visibly bimodal —

    strands plain  blocking  [1969, 2061, 2106, 2303, 2406, 6911, 6991]
    strands schema blocking  [2058, 2110, 2139, 2571, 6542, 6959, 7349]

— a tight cluster plus a second cluster ~4.5s higher. Those are absorbed
retries, not tails. **No framework overhead number should be quoted from
this run.**

**`schema` appearing 24% faster than `plain`** (1439ms vs 1901ms) is
almost certainly contamination, not a finding. An attached-but-unused
schema should cost slightly more, never less. Both rows carry the retry
bimodality above.

**Strands `tokens: 0` on blocking rows** is hardcoded at
`providers.py:434`, not measured. It reads like data. It isn't.

---

## Conditions

One machine, one network, us-east-1, on-demand capacity throughout,
medians of n=7 after discarding each row's warm-up run, `gap=2.0s`.
p95 at n=7 is indicative only. The `frameworks` run was additionally
throttle-contaminated as described above.

This measures **the LLM leg alone.** Endpointing, ASR, TTS and network sit
outside the instrument. These numbers are an input to the caller's
experience, not a description of it — a 700ms TTFT is not a 700ms pause
for the person on the phone.

Reproduce: `voicebench --replay results/<file>.json`
