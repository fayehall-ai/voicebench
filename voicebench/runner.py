"""Execution: ordering, pacing, warm-up, aggregation, persistence.

The rules here are the ones that separate a measurement from a guess:

  * calls paced           a burst measures your quota, not the model
  * first run discarded   warm-up, and cache-WRITE on cached scenarios
  * rows interleaved      variants of the same comparison must sit adjacent
                          in time, or the delta between them is capacity
                          drift rather than a real difference
  * exclusion, not mixing a run where the model answered directly is not a
                          two-hop run and cannot be averaged with one
"""

from __future__ import annotations

import asyncio
import itertools
import json
import platform
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .config import (GAP, MAX_TOKENS, MODELS, NOISE_PCT, RUNS, SCENARIOS,
                     SYSTEM_BIG)
from .providers import active_providers, get_provider
from .measure import Cell, Result, percentile


def build_cells(providers, models, scenarios, paths) -> list[Cell]:
    """Provider varies FASTEST.

    Provider comparison is the measurement most easily corrupted by
    capacity drift, so the two legs of it should be seconds apart rather
    than minutes. Everything else varies more slowly.
    """
    cells = []
    for scenario, path, model in itertools.product(scenarios, paths, models):
        for provider in providers:
            cells.append(Cell(provider, model, scenario, path))
    return cells


async def run_cell(cell: Cell, runs: int = RUNS, gap: float = GAP) -> Result | None:
    provider = get_provider(cell.provider)
    model = MODELS[cell.model]
    scenario = SCENARIOS[cell.scenario]

    supported, why = provider.supports(model, scenario)
    if not supported:
        print(f"  {cell.label():<62} SKIP — {why}")
        return None

    samples = []
    for i in range(runs):
        try:
            if cell.path == "blocking":
                samples.append(provider.blocking(model, scenario))
            else:
                samples.append(await provider.streaming(model, scenario))
        except Exception as exc:
            print(f"  {cell.label():<62} FAIL — {type(exc).__name__}")
            print(f"    {exc}")
            return None
        if i < runs - 1:
            await asyncio.sleep(gap)

    samples = samples[1:]                     # warm-up / cache-write
    fired = sum(1 for s in samples if s.tool_fired)

    if scenario.run_loop:
        samples = [s for s in samples if s.tool_fired]
        if not samples:
            print(f"  {cell.label():<62} tool never fired — prompt is not "
                  f"forcing the loop, or the probe is wrong")
            return None

    totals = [s.total_ms for s in samples]
    spokens = [s.spoken_ms for s in samples if s.spoken_ms is not None]

    result = Result(
        spoken_p50=percentile(spokens, 50) if spokens else None,
        total_p50=percentile(totals, 50),
        total_p95=percentile(totals, 95),
        tokens=statistics.median(s.tokens for s in samples),
        chars=statistics.median(s.chars for s in samples),
        fired=fired,
        n=len(samples),
        cache_read=statistics.median(s.cache_read for s in samples),
        samples=samples,
    )

    spoken_col = f"{result.spoken_p50:8.0f}" if result.spoken_p50 else f"{'-':>8}"
    tool_col = f"{fired}/{runs - 1}" if scenario.run_loop else "-"
    cache_col = f"{result.cache_read:.0f}" if scenario.cache else "-"

    print(f"  {cell.label():<62} {spoken_col} {result.total_p50:8.0f} "
          f"{result.total_p95:8.0f} {result.tokens:7.0f} "
          f"{tool_col:>6} {cache_col:>7}  n={result.n}")
    return result


async def run_suite(cells: list[Cell], runs: int = RUNS,
                    gap: float = GAP) -> dict[Cell, Result | None]:
    minutes = len(cells) * runs * gap / 60
    print(f"\n{len(cells)} rows x {runs} runs, {gap}s apart  (~{minutes:.0f} min)")
    print("first run of each row discarded; provider varies fastest so "
          "variants sit adjacent in time\n")
    print(f"  {'provider':<16} {'model':<11} {'scenario':<17} {'path':<10} "
          f"{'spoken':>8} {'tot50':>8} {'tot95':>8} {'tokens':>7} "
          f"{'tool':>6} {'cache':>7}")
    print("  " + "-" * 104)

    results: dict[Cell, Result | None] = {}
    for i, cell in enumerate(cells):
        results[cell] = await run_cell(cell, runs, gap)
        if i < len(cells) - 1:
            await asyncio.sleep(gap)
    return results


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def manifest(runs: int, gap: float, suite: str | None) -> dict:
    """Everything needed to attribute a number to the conditions that
    produced it. 'One machine, one afternoon' should say WHICH afternoon."""
    env = {
        "voicebench": __version__,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "suite": suite,
        "runs_per_row": runs,
        "gap_seconds": gap,
        "max_tokens": MAX_TOKENS,
        "noise_pct": NOISE_PCT,
        "big_system_approx_tokens": len(SYSTEM_BIG) // 4,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "providers": [p.env() for p in active_providers()],
        "known_deviations": [
            "temperature not matched across providers; the Anthropic SDK "
            "removed sampling parameters in 1.x",
            "effort parameter shape is inferred from a capability flag, "
            "not documentation; treat effort rows as unverified",
            "large system prompt is realistic in size and shape, filler in "
            "content",
            "on-demand capacity everywhere; no provisioned throughput",
        ],
    }
    return env


def save(results: dict[Cell, Result | None], path: Path,
         runs: int, gap: float, suite: str | None) -> Path:
    payload = {
        "manifest": manifest(runs, gap, suite),
        "rows": [
            {**cell.as_dict(),
             **(result.as_dict() if result else {"skipped": True})}
            for cell, result in results.items()
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return path


def load(path: Path) -> tuple[dict[Cell, Result | None], dict]:
    """Re-analyse without re-measuring."""
    from .measure import Sample

    payload = json.loads(Path(path).read_text())
    results: dict[Cell, Result | None] = {}

    for row in payload["rows"]:
        cell = Cell(row["provider"], row["model"], row["scenario"], row["path"])
        if row.get("skipped"):
            results[cell] = None
            continue
        results[cell] = Result(
            spoken_p50=row["spoken_p50"],
            total_p50=row["total_p50"],
            total_p95=row["total_p95"],
            tokens=row["tokens"],
            chars=row["chars"],
            fired=row["tool_fired_runs"],
            n=row["n"],
            cache_read=row.get("cache_read", 0.0),
            samples=[Sample(**s) for s in row.get("raw_samples", [])],
        )
    return results, payload["manifest"]
