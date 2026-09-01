"""Comparison and presentation.

One idea: a delta is only meaningful when everything except the varying
axis is held constant. compare_axis enforces that by grouping on the other
three dimensions and comparing only within a group.
"""

from __future__ import annotations

from .config import NOISE_PCT, RUNS
from .measure import Cell, Result

AXES = ("provider", "model", "scenario", "path")


def compare_axis(results: dict[Cell, Result], axis: str, field: str) -> bool:
    others = [a for a in AXES if a != axis]

    groups: dict[tuple, list[Cell]] = {}
    for cell, result in results.items():
        if result is None or getattr(result, field) is None:
            continue
        groups.setdefault(tuple(getattr(cell, a) for a in others), []).append(cell)

    groups = {k: v for k, v in groups.items() if len(v) > 1}
    if not groups:
        return False

    print(f"\n  varying {axis.upper()}  ({field})")
    print("  " + "-" * 74)
    for key, cells in sorted(groups.items(), key=lambda kv: str(kv[0])):
        base = cells[0]
        base_value = getattr(results[base], field)
        print(f"  {' / '.join(key)}")
        for cell in cells:
            value = getattr(results[cell], field)
            name = getattr(cell, axis)
            if cell is base:
                print(f"      {name:<20} {value:8.0f}ms   (baseline)")
            else:
                pct = (value - base_value) / base_value * 100
                flag = "" if abs(pct) >= NOISE_PCT else "  (noise)"
                print(f"      {name:<20} {value:8.0f}ms  "
                      f"{value - base_value:+7.0f}  {pct:+5.0f}%{flag}")
    return True


def report(results: dict[Cell, Result | None], runs: int = RUNS) -> None:
    live = {c: r for c, r in results.items() if r is not None}
    if not live:
        print("\n  no successful rows")
        return

    has_streaming = any(c.path == "streaming" for c in live)

    for axis in AXES:
        if len({getattr(c, axis) for c in live}) < 2:
            continue
        if axis == "path":
            # spoken is undefined on blocking rows, so the only honest
            # cross-path comparison is blocking total vs streaming spoken,
            # which is exactly "what streaming buys".
            compare_axis(live, axis, "total_p50")
            continue
        compare_axis(live, axis, "spoken_p50" if has_streaming else "total_p50")

    print(f"""
  Deltas under {NOISE_PCT:.0f}% are marked (noise). That figure is measured
  repeat-run variance on identical configurations, not a confidence
  interval.

  Columns:
    spoken   time until the caller could hear ANY token. Blank on blocking
             rows: nothing is speakable until the call returns.
    tot50    end to end. On lookup rows this covers BOTH hops.
    tool     runs where the tool actually fired. Non-firing runs are
             excluded, not averaged in.
    cache    cached input tokens read. If this is 0 on a cached row, the
             cache never engaged and the row is a null result, not a
             contradiction of anything.

  Caveats: one machine, one network, on-demand capacity everywhere,
  medians only. Tails at n<={runs - 1} are indicative, not reliable.
  See the manifest in the JSON output for SDK versions, region, timestamp
  and known deviations.

  This measures the LLM leg alone. Endpointing, ASR, TTS and network sit
  outside the instrument, so these numbers are an INPUT to the caller's
  experience, not a description of it.
""")
