# I predicted a voice agent's latency, then called it 30 times to check

*Companion to the [lab study](./LAB-STUDY.md), which measured the LLM leg
in isolation. This measures whole turns on a real phone line.
One rigged test agent, ~30 calls from a cell phone over PSTN, 213 measured
reply gaps across five configurations — 134 of them in the baseline. Raw
call JSON is in `calls/`.*

The lab study measured one term of a five-term budget: time to first token
from the model. Endpointing, ASR, TTS and network were outside the
instrument, so the honest conclusion was "this is an input to the caller's
experience, not a description of it."

This is the other half. I built a dental after-hours agent on Vapi, rigged
it to fail in specific ways, called it from my phone, and measured every
turn.

---

## The prediction held

From component measurements plus published ranges for the stages I could
not measure, I predicted a first-audio latency around 1375ms.

Measured median on the baseline fixture: **1527ms**, across 134 real turns.
On the `waitSeconds: 0` configuration: **1435ms**, across 19.

So the prediction lands about 11% under the baseline and about 4% under the
faster configuration. Those two medians are 92ms apart, and
same-configuration replications in this corpus varied by 265ms — wider than
the gap between them — so this data does not separate the two, and the
prediction sits inside the same band as both.

**Call it right to within roughly 10%.** That is the most useful thing in
this document, because it means component benchmarking is not useless for
voice — it is directionally right for the plain case, if you are honest
about which terms you did not measure.

It is also where the agreement stops.

---

## Three latency knobs, three nulls

### `waitSeconds` is a floor, not a surcharge

Vapi's pipeline diagram reads as sequential:

```
User Audio -> VAD -> Transcription -> Start Speaking Decision
           -> LLM -> TTS -> waitSeconds -> Assistant Audio
```

I read that as additive and predicted that dropping `waitSeconds` from 0.4
to 0 would save 400ms.

Measured: 1527ms (n=134) -> 1435ms (n=19). About 6%, inside noise.

The SDK type definition explains it. `waitSeconds` is described there as a
minimum that pipeline latency will exceed, intended as a stopgap for when
the pipeline runs too fast. So the cost is `max(0, wait - processing)`, and
at 400ms against a ~1500ms pipeline that is zero.

This matters because lowering `waitSeconds` is the standard advice in
Vapi's own community threads when someone reports lag. On a cascaded phone
stack it cannot help. It only starts costing you once everything else is
fast.

### Prefix caching bought tokens, not milliseconds

From the [lab study](./LAB-STUDY.md): four measurements across two models, all
within noise, including one where the cache verifiably engaged on a model
with ~700ms of real prefill cost.

### Reasoning effort did not move time to first token

Also from the [lab study](./LAB-STUDY.md), on prompts short enough that nothing triggered
extended thinking — so there were no thinking tokens for effort to scale.

### The generalisation

All three knobs act on a stage that was not the bottleneck. Caching removes
prefill you barely have. Effort modulates thinking you do not trigger.
`waitSeconds` enforces a floor you are nowhere near.

**A latency knob only pays when the term it controls dominates.** Measure
your decomposition before you tune anything.

---

## Where the lab understated reality: tool calls

Lab estimate for a turn with one tool hop, built from measured TTFT plus
the structure of the agent loop: ~2600ms.

Measured in the field:

```
4777ms   "It seems the phone number provided is incomplete"
4947ms   "Your callback request has been recorded"
5103ms   "I've recorded your callback request"
6011ms   "Your callback request has been noted"
3854ms   "Thank you, Andre Hall. I have noted your request"
```

Consistently 4-6 seconds. Roughly double the estimate, every time.

Every one of these followed a spoken preamble — "Just a sec", "Give me a
moment", "This will just take a sec". So the model does speak-before-fetch
spontaneously, without being prompted to. It is still 4-6 seconds of call
time to record a name and a number.

If you take one number from this document into a design review, take this
one. Component benchmarks are directionally right for plain turns and
badly optimistic for tool turns.

---

## The endpointer, on purpose

The fixture was configured with `onNoPunctuationSeconds: 0.3` against a
default of 1.5, no keyword biasing, and `smartEndpointingPlan` deliberately
omitted — if that is set it overrides the heuristic plan entirely and the
rig does nothing.

It reproduced the canonical voice-agent failure on demand:

```
 7.8  user   "I need to reschedule my"
11.8  agent  "Our office is currently closed. May I take"
13.7  user   "cleaning."
```

And again in a later call, and four times in a single call:

```
 6.0  user   "What's"
 9.2  agent  "Could you"
 9.4  user   "patient service include?"
```

The agent behaved correctly on the input it received. A transcript-only
judge scores these as passes. The failure happened two stages upstream of
anything the transcript can see.

Three callers said some version of "Are you still there?" unprompted. One
call went 13 seconds of dead air before the caller said "Hello?". Four of
the first twenty calls ended `silence-timed-out` — a fifth of the corpus
died on turn-taking rather than content.

---

## The ASR experiment

Two words the fixture's knowledge base deliberately has no answer for:
"Cigna" and "implant". Both mis-transcribed at baseline. Four configs, one
variable at a time.

```
                     implant     Cigna      Feld / Reyes   false positives
nova-2               "intern"    "Stigma"   "Ray"          --
nova-2 + keywords    correct     correct    "felt" / "Ray" none
nova-3               "InPam"     correct    correct        --
nova-3 + keyterm     correct     correct    correct        none
```

Three things fall out.

**A model upgrade fixed one target term and not the other.** nova-3 alone
resolved Cigna and still produced "InPam" for implant. Overall word error
rate improvements are not distributed across the words you care about —
proper nouns and domain vocabulary are a tiny fraction of total words, so a
model can improve on average while failing every time on the term that
costs you a patient.

**The legacy mechanism is weaker on the hard cases.** `keywords` on nova-2
fixed the two words I asked about and left the proper names broken. It also
cannot express multi-word phrases at all — it takes single tokens only,
which is a capability gap and not just a formatting rule.

**Biasing did not create false positives.** Two acoustically adjacent words
appeared in the biased batches: "stigma" four times and "implement" three
times, both boosted-neighbour candidates. I read both as boost
contamination. Listening to the audio showed the callers had said exactly
those words. The boost broke ties without overriding real acoustic
evidence, which is the behaviour you want and the half nobody checks.

**Recommended config: nova-3 with keyterm biasing.** The only cell with no
entity errors anywhere.

On latency cost: same-configuration replications varied by 265ms, which is
wider than most of the deltas between configs. The accuracy ordering above
is solid. **I am not claiming a latency ordering between the biased
configs** — the sample does not support it.

---

## Two findings I did not go looking for

### Fixing ASR reclassifies failures, it does not remove them

Once "Cigna" transcribed correctly, this appeared:

```
user   "Do you accept Cigna?"
agent  "We accept Delta Dental, MetLife, Aetna, Guardian, and United
        Concordia."
```

The transcription is perfect. The agent answered a question nobody asked,
in a way a caller hears as a denial. That is a reasoning failure, and at
baseline it was invisible — hidden behind "Stigma", where I would have
labelled it a transcription error and moved on.

So failure attribution is **conditional on the pipeline you are running**.
It is not a fixed distribution you measure once. Fix a stage and the
failures underneath it become visible.

There is a mirror-image case in the nova-2 batch: a mis-transcribed word
caused the agent to respond "I'm unable to assist with that topic" — a
false guardrail trigger, refusing a caller for something they never said.

### The caller talked the agent out of its own knowledge base

```
user   "it's Thursday 10:52 AM right now. You should be open."
agent  "Thank you for pointing that out. In that case, the office
        should be open."
```

The knowledge base says Monday to Thursday, 8am to 5pm. The agent had
already answered correctly. One assertion from the caller and it reversed
itself.

This is not heard-wrong, retrieved-wrong, or reasoned-wrong in the usual
sense. It is a distinct category — the agent abandoning a correct answer
under social pressure — and it is the commercially dangerous one. An agent
that concedes to whoever pushes hardest will confirm appointments that do
not exist and agree to coverage it cannot offer.

I have not seen anyone scoring for it.

---

## The part where I was wrong

Twice in one afternoon I read a transcript, inferred a failure mode, and
was wrong. "Stigma" and "implement" both looked like biasing contamination.
Both were correct transcriptions of what the caller actually said. Five
seconds of listening settled what I could not determine from text after a
week of thinking about exactly this problem.

If someone working on this specific failure class cannot tell from the
transcript, an agency reviewing calls at volume has no chance.

**The transcript is not ground truth. It is what the ASR produced.** That
sentence is easy to agree with and hard to internalise, and the only thing
that taught it to me properly was being wrong about my own data in public.

---

## Method

One Vapi assistant, GPT-4o, Deepgram transcriber, Vapi voice, called from a
cell phone over PSTN — never the web widget, because that is 16 kHz WebRTC
and the entire failure surface lives in the 8 kHz phone path.

Reply gaps are computed from `secondsFromStart` and `endTime` on the
end-of-call report messages: agent turn start minus previous user turn end.
That covers endpointing, ASR finalisation, model, TTS and network — every
term the lab study could not see.

Knowledge base held in the system prompt rather than a retrieval tool, so
the deliberate holes are auditable.

The apparatus produced eight distinct bugs over the course of this work,
including a webhook that reported success after every patch had failed, a
`--keyterms` flag that silently changed the transcriber model as well, and
a module named `types.py` that shadowed the standard library and broke the
Python interpreter before any of my code ran.

Only one of them produced an obviously wrong answer. The rest produced
plausible ones.

---

## What this does not cover

n=134 gaps for the baseline and 19 for `waitSeconds: 0`, far fewer per
tuned configuration. One
machine, one phone, one voice for most calls, one afternoon. Medians only —
same-configuration replications varied by 265ms, so treat any delta below
that as noise.

Failure counts here are from transcript review plus targeted listening, not
a full hand-labelled pass over every call. The cause distribution is not
established.

The fixture is a test rig, deliberately configured to fail. It tells you
what these failures look like and how to measure them. It does not tell you
how often they occur on a well-tuned production agent.

---

## Reproduce it

`create_assistant.py` builds the fixture, `webhook.py` captures end-of-call
reports, `review.py` turns them into a listening sheet with per-turn gaps
and low-confidence words flagged, `tune.py` changes one setting at a time,
`scrub.py` strips identifiers before anything is committed.

Raw call JSON is committed in `calls/`, scrubbed: recording, pcap and log
URLs removed, phone numbers and Vapi account identifiers redacted, and
spoken digit runs replaced. Names are deliberately kept, because the ASR
findings are about proper nouns. No timing field is touched — every latency
figure above recomputes from the committed files with:

```
$ python fieldtest/review.py --summary
configuration         calls   gaps    median
call-nova2               21    134    1527ms
call-nova2-bias           2      7    1974ms
call-nova3                3     19    1821ms
call-nova3-bias           4     34    1762ms
call-waitime-0            7     19    1435ms
all                      37    213    1602ms
```

The audio is not committed and cannot be, which is a real limit given that
"[the transcript is not ground truth](#the-part-where-i-was-wrong)" is one
of the findings. You can reproduce the corpus, not re-listen to mine.

If your numbers differ from mine, that is the expected outcome and I would
like to see them.