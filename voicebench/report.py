"""Comparison and presentation.

Two ideas. A delta is only meaningful when everything except the varying
axis is held constant, which compare_axis enforces by grouping on the
other three dimensions and comparing only within a group. And a
comparison that could not be made must say so: a suite whose whole
purpose is one delta should never print a confident-looking table about
something else and leave the reader to notice the absence.
"""

from __future__ import annotations

from .config import NOISE_PCT, RUNS
from .measure import Cell, Result

AXES = ("provider", "model", "scenario", "path", "effort")


def axis_label(value) -> str:
    """None on the effort axis means the parameter was not sent, which
    is the API default. It needs a name, or it prints as a blank that
    reads like a hole in the data."""
    return "default" if value is None else str(value)


def field_for_group(axis: str, cells: list[Cell]) -> str:
    """Which metric this group is judged on.

    spoken_p50 is undefined on blocking rows -- nothing is speakable until
    the call returns -- so a group containing blocking rows must be judged
    on total_p50 or not at all.

    Chosen per GROUP rather than once per run, which is the whole point.
    A run-wide flag set by "did any streaming row survive anywhere" drops
    every blocking row from the provider, model and scenario tables,
    because their spoken_p50 is None. That is how a surviving
    bedrock-vs-strands blocking pair vanishes and leaves the framework
    comparison simply absent rather than reported as unavailable.

    One metric per group, used for BOTH the liveness filter and the
    printed number, so a row can never be admitted on one metric and
    displayed on another.
    """
    if axis == "path":
        # This group spans both paths by construction, and the blocking
        # half has no spoken time, so total is the only metric both halves
        # actually have.
        return "total_p50"
    # Every other axis holds path constant, so the group is entirely
    # streaming or entirely blocking and cells[0] speaks for all of them.
    return "spoken_p50" if cells[0].path == "streaming" else "total_p50"


def _value(results: dict[Cell, Result | None], cell: Cell, field: str):
    result = results.get(cell)
    return None if result is None else getattr(result, field)


def compare_axis(results: dict[Cell, Result | None], axis: str) -> list[tuple]:
    """Print every group along `axis`. Return the groups that were short.

    A group is comparable when at least two of its cells produced the
    metric. Anything else is returned to the caller so the absence can be
    named rather than inferred.
    """
    others = [a for a in AXES if a != axis]

    groups: dict[tuple, list[Cell]] = {}
    for cell in results:
        groups.setdefault(tuple(getattr(cell, a) for a in others), []).append(cell)

    # A group of one was never a comparison; only groups that COULD have
    # compared and didn't are worth reporting as missing.
    groups = {k: v for k, v in groups.items() if len(v) > 1}

    comparable: dict[tuple, tuple[str, list[Cell]]] = {}
    short: list[tuple] = []
    for key, cells in sorted(groups.items(), key=lambda kv: str(kv[0])):
        field = field_for_group(axis, cells)
        live = [c for c in cells if _value(results, c, field) is not None]
        if len(live) > 1:
            comparable[key] = (field, live)
        else:
            short.append((key, cells, live))

    if comparable:
        print(f"\n  varying {axis.upper()}")
        print("  " + "-" * 74)
        for key, (field, cells) in comparable.items():
            # The metric can differ BETWEEN groups, so each one states
            # which it used rather than relying on a single header.
            print(f"  {' / '.join(axis_label(k) for k in key)}  ({field})")
            base = cells[0]
            base_value = _value(results, base, field)
            for cell in cells:
                value = _value(results, cell, field)
                name = axis_label(getattr(cell, axis))
                if cell is base:
                    print(f"      {name:<20} {value:8.0f}ms   (baseline)")
                else:
                    pct = (value - base_value) / base_value * 100
                    flag = "" if abs(pct) >= NOISE_PCT else "  (noise)"
                    print(f"      {name:<20} {value:8.0f}ms  "
                          f"{value - base_value:+7.0f}  {pct:+5.0f}%{flag}")

    return [(axis, key, cells, live) for key, cells, live in short]


def report(results: dict[Cell, Result | None], runs: int = RUNS) -> None:
    live = {c: r for c, r in results.items() if r is not None}
    if not live:
        print("\n  no successful rows")
        return

    missing: list[tuple] = []
    for axis in AXES:
        if len({getattr(c, axis) for c in results}) < 2:
            continue          # axis never varied; nothing was asked of it
        missing += compare_axis(results, axis)

    if missing:
        print("\n  comparisons NOT available")
        print("  " + "-" * 74)
        print("  These rows were requested but could not be compared, because")
        print("  too few of them produced data. Absence here is a result: it is")
        print("  not evidence that the variants are equivalent.\n")
        for axis, key, cells, have in missing:
            names = {axis_label(getattr(c, axis)) for c in cells}
            got = {axis_label(getattr(c, axis)) for c in have}
            label = ' / '.join(axis_label(k) for k in key)
            print(f"  varying {axis.upper():<9} {label}")
            print(f"      have: {', '.join(sorted(got)) or '(none)'}"
                  f"      no data: {', '.join(sorted(names - got))}")

    print(f"""
  Deltas under {NOISE_PCT:.0f}% are marked (noise). That figure is measured
  repeat-run variance on identical configurations, not a confidence
  interval.

  Each comparison names the metric it used. Streaming groups are compared
  on spoken_p50, blocking groups on total_p50, because spoken time does
  not exist on a blocking row.

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
