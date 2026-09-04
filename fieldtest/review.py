"""
review.py — turn saved calls into a listening sheet.

Does two things you cannot do by eye:

  1. Computes per-turn response latency from message timestamps. This is
     the ~725ms of budget that voicebench cannot see — endpointer, ASR,
     TTS and network, measured on real audio instead of estimated from
     published ranges.

  2. Flags turns worth listening to closely: very short user turns (an
     endpointer cut mid-sentence looks like a one-word turn), long agent
     pauses, and turns containing digits or spelled letters.

    python review.py                 # all calls under calls/
    python review.py calls/1423-a1b2c3d4.json
    python review.py --fields        # raw message keys on the first call
    python review.py --csv > labels.csv
    python review.py --summary       # n and median per configuration

--summary exists because the write-up once paired one configuration's turn
count with another's median, and nothing recomputed the pair. Every latency
figure quoted in FIELD_STUDY.md should be readable off its output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Relative to the repo, not the caller's cwd, so the scripts work from
# either directory. Calls are filed one directory per configuration.
CALLS = Path(__file__).resolve().parent.parent / "calls"
SHORT_USER_TURN = 3          # words; fewer than this may be a truncation
SLOW_AGENT_REPLY = 1.2       # seconds; past this the caller may talk over


def messages(payload: dict) -> list[dict]:
    return payload.get("message", {}).get("artifact", {}).get("messages", [])


def turns(payload: dict) -> list[dict]:
    """Vapi message shapes drift, so try several field names.

    An earlier version looked only for `endTime` and silently found
    nothing, so every latency calculation was skipped and the run looked
    fine. Same failure class as a stream filter that never matches: no
    error, no data, plausible-looking output.

    Run with --fields to print the actual keys on one message.
    """
    out = []
    for m in messages(payload):
        role = m.get("role")
        if role not in ("user", "bot", "assistant"):
            continue

        start = m.get("secondsFromStart")
        if start is None and isinstance(m.get("time"), (int, float)):
            start = m["time"] / 1000.0

        # end time, in call-relative seconds, from whichever field exists
        end = None
        if isinstance(m.get("endTime"), (int, float)) and isinstance(m.get("time"), (int, float)):
            # absolute epoch ms on both -> convert the delta
            end = start + (m["endTime"] - m["time"]) / 1000.0
        elif isinstance(m.get("duration"), (int, float)) and start is not None:
            # duration is milliseconds
            end = start + m["duration"] / 1000.0
        elif isinstance(m.get("endTime"), (int, float)) and m["endTime"] < 1e6:
            end = m["endTime"]

        out.append({
            "role": "agent" if role in ("bot", "assistant") else "user",
            "text": (m.get("message") or "").strip(),
            "start": start,
            "end": end,
        })
    return out


def show_fields(path: Path) -> None:
    """Print the raw keys on the first few messages. Do this before
    trusting any timing number."""
    payload = json.loads(path.read_text())
    print(f"\n{path.name} — raw message keys\n{'-' * 60}")
    for m in messages(payload)[:4]:
        print(f"  {json.dumps(m)[:300]}")
    print()


def reply_gaps(payload: dict) -> list[float]:
    """Agent turn start minus the previous user turn end, in seconds.

    prev_user_end is deliberately not cleared after a pairing: when the
    agent takes two turns in a row, both are waiting on the same user
    utterance and both count. show() reports the same way.
    """
    gaps = []
    prev_user_end = None
    for turn in turns(payload):
        if turn["role"] == "user":
            prev_user_end = turn["end"] if turn["end"] is not None else turn["start"]
        elif prev_user_end is not None and turn["start"] is not None:
            gap = turn["start"] - prev_user_end
            if gap >= 0:
                gaps.append(gap)
    return gaps


def summarise(paths: list[Path]) -> None:
    """n and median reply gap per configuration, one row per calls/ subdir."""
    by_config: dict[str, list[float]] = {}
    for path in paths:
        config = path.parent.name
        by_config.setdefault(config, []).extend(
            reply_gaps(json.loads(path.read_text())))

    def median(values: list[float]) -> float:
        values = sorted(values)
        return values[len(values) // 2] * 1000      # as show() does

    everything = []
    print(f"{'configuration':<20} {'calls':>6} {'gaps':>6} {'median':>9}")
    for config, gaps in sorted(by_config.items()):
        if not gaps:
            continue
        everything += gaps
        calls = sum(1 for p in paths if p.parent.name == config)
        print(f"{config:<20} {calls:>6} {len(gaps):>6} {median(gaps):>7.0f}ms")
    if everything:
        print(f"{'all':<20} {len(paths):>6} {len(everything):>6} "
              f"{median(everything):>7.0f}ms")
    print("\n  (endpointer + ASR + model + TTS + network, end to end)")
    print("  Medians are the upper of the two middle values, as show() reports.")


def show(path: Path) -> None:
    payload = json.loads(path.read_text())
    message = payload.get("message", {})
    artifact = message.get("artifact", {})

    print(f"\n{'=' * 74}")
    print(f"{path.name}   {message.get('durationSeconds')}s   "
          f"${message.get('cost', 0):.4f}   ended: {message.get('endedReason')}")
    if artifact.get("recordingUrl"):
        print(f"audio: {artifact['recordingUrl']}")
    print("=" * 74)

    prev_user_end = None
    latencies = []

    for turn in turns(payload):
        flags = []

        if turn["role"] == "user":
            words = len(turn["text"].split())
            if words and words <= SHORT_USER_TURN:
                flags.append("SHORT — endpointer may have cut this")
            if any(c.isdigit() for c in turn["text"]):
                flags.append("digits")
            prev_user_end = turn["end"] if turn["end"] is not None else turn["start"]
        else:
            if prev_user_end is None and turn["start"] is not None:
                flags.append("no end time — see --fields")
            if prev_user_end is not None and turn["start"] is not None:
                gap = turn["start"] - prev_user_end
                if gap >= 0:
                    latencies.append(gap)
                    flags.append(f"reply gap {gap * 1000:.0f}ms")
                    if gap > SLOW_AGENT_REPLY:
                        flags.append("SLOW")

        stamp = f"{turn['start']:6.1f}" if turn["start"] is not None else "     ?"
        tag = "  <<< " + " | ".join(flags) if flags else ""
        print(f"{stamp}  {turn['role']:<6} {turn['text'][:60]:<60}{tag}")

    if latencies:
        latencies.sort()
        mid = latencies[len(latencies) // 2]
        print(f"\n  reply gaps: n={len(latencies)}  "
              f"median {mid * 1000:.0f}ms  max {latencies[-1] * 1000:.0f}ms")
        print("  (endpointer + ASR + model + TTS + network, end to end)")

    print("""
  Now LISTEN to the recording with this open. For every place the audio
  and the text disagree, add a row to labels.csv:

    call, timestamp, what_caller_said, what_transcript_says, agent_did,
    cause  = heard | retrieved | reasoned | latency
""")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="review.py",
        description="Turn saved Vapi calls into a listening sheet.")
    parser.add_argument("paths", nargs="*", type=Path,
                        help=f"call JSON files (default: every {CALLS}/*.json)")
    parser.add_argument("--csv", action="store_true",
                        help="print the labels.csv header and exit")
    parser.add_argument("--summary", action="store_true",
                        help="n and median reply gap per configuration")
    parser.add_argument("--fields", action="store_true",
                        help="print raw message keys on the first call and exit")
    args = parser.parse_args(argv)

    if args.csv:
        print("call,timestamp,what_caller_said,what_transcript_says,"
              "agent_did,cause,notes")
        return 0

    # rglob, not glob: tune.py files each configuration under its own
    # directory, so a flat calls/*.json matched nothing and printed the
    # empty-corpus message on a corpus of 37 calls.
    paths = args.paths or sorted(CALLS.rglob("*.json"))
    if not paths:
        sys.exit("no calls found — run webhook.py and make some calls first")

    if args.fields:
        show_fields(paths[0])
        return 0

    if args.summary:
        summarise(paths)
        return 0

    for path in paths:
        show(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
