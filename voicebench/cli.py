"""Command line entry point."""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

from .config import (EFFORTS, GAP, MODELS, RUNS, SCENARIOS, SUITES,
                     SYSTEM_BIG, Scenario)
from .providers import get_provider
from .report import report
from .runner import build_cells, load, run_suite, save
from .measure import quiet


def show_list() -> None:
    print("\nmodels")
    for name, spec in MODELS.items():
        where = [p for p, v in (("bedrock", spec.bedrock),
                                ("anthropic", spec.anthropic)) if v]
        effort = "  effort" if spec.supports_effort else ""
        print(f"  {name:<14} {', '.join(where)}{effort}")

    print("\nscenarios")
    for name, spec in SCENARIOS.items():
        print(f"  {name:<18} {spec.note}")

    print("\nefforts")
    print("  " + ", ".join("default" if e is None else e for e in EFFORTS))
    print("    default means output_config is not sent at all")

    print("\nproviders")
    for name in ("bedrock", "bedrock:<region>", "anthropic", "strands"):
        print(f"  {name}")

    print("\nsuites")
    for name, spec in SUITES.items():
        rows = (len(spec["providers"]) * len(spec["models"])
                * len(spec["scenarios"]) * len(spec["paths"])
                * len(spec.get("efforts", [None])))
        print(f"  {name:<14} {rows:2} rows  ~{rows * RUNS * GAP / 60:.0f} min  "
              f"[{', '.join(spec['providers'])}]")
    print()


HEAD = 10          # events printed per provider


def head(events, n: int = HEAD) -> list:
    """Read the stream to the end, keep the first n events.

    Draining rather than breaking out early is deliberate. An abandoned
    stream is closed later, during interpreter or event-loop shutdown, in a
    different asyncio Context than the one that opened its OpenTelemetry
    span -- which is what buries the strands output under pages of "Failed
    to detach context" tracebacks. Reading to the end of a one-sentence
    reply costs a few hundred milliseconds and nothing is being timed here.
    """
    kept = []
    for event in events:
        if len(kept) < n:
            kept.append(event)
    return kept


def inspect() -> None:
    """Print real stream event shapes from each provider.

    Never guess at an event filter. One that silently never matches yields
    a plausible wrong number rather than an error, which is the expensive
    kind of failure.
    """
    model = MODELS["haiku-4.5"]
    scenario = SCENARIOS["plain"]

    for name in ("bedrock", "anthropic", "strands"):
        print(f"\n{name} events\n{'-' * 72}")
        try:
            provider = get_provider(name)
            ok, why = provider.supports(model, scenario)
            if not ok:
                print(f"  skipped: {why}")
                continue

            if name == "bedrock":
                messages = [{"role": "user", "content": [{"text": scenario.prompt}]}]
                body = provider._request(model, scenario, messages)
                events = head(provider.client.converse_stream(**body)["stream"])
            elif name == "anthropic":
                kwargs = provider._kwargs(
                    model, scenario, [{"role": "user", "content": scenario.prompt}])
                kwargs["stream"] = True
                events = head(provider.client.messages.create(**kwargs))
            else:
                async def grab():
                    agent = provider.build_agent(model, scenario)
                    out = []
                    with quiet():
                        async for chunk in agent.stream_async(scenario.prompt):
                            if len(out) < HEAD:
                                out.append(chunk)
                    return out
                events = asyncio.run(grab())

            for event in events:
                print(f"  {repr(event)[:168]}")
        except Exception as exc:
            print(f"  {type(exc).__name__}: {exc}")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="voicebench",
        description="LLM-leg latency measurement for voice agents.")
    parser.add_argument("--list", action="store_true",
                        help="show models, scenarios, providers and suites")
    parser.add_argument("--inspect", action="store_true",
                        help="print real stream event shapes and exit")
    parser.add_argument("--suite", help="named suite; see --list")
    parser.add_argument("--providers", help="comma separated")
    parser.add_argument("--models", help="comma separated")
    parser.add_argument("--scenarios", help="comma separated")
    parser.add_argument("--paths", default="streaming",
                        help="blocking, streaming, or both")
    parser.add_argument("--efforts", default="default",
                        help="comma separated; 'default' sends no effort param")
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--gap", type=float, default=GAP)
    parser.add_argument("--out", type=Path, default=None,
                        help="JSON output path (default results/<suite>-<ts>.json)")
    parser.add_argument("--replay", type=Path,
                        help="re-report a saved JSON run without measuring")
    args = parser.parse_args(argv)

    if args.list:
        show_list()
        return 0
    if args.inspect:
        inspect()
        return 0

    if args.replay:
        results, saved = load(args.replay)
        print(f"\nreplaying {args.replay}")
        print(f"  measured {saved['timestamp_utc']}  suite={saved['suite']}  "
              f"runs={saved['runs_per_row']}  gap={saved['gap_seconds']}s")
        report(results, saved["runs_per_row"])
        return 0

    if args.suite:
        if args.suite not in SUITES:
            parser.error(f"unknown suite. try: {', '.join(SUITES)}")
        spec = dict(SUITES[args.suite])
    elif args.providers and args.models and args.scenarios:
        spec = dict(providers=args.providers.split(","),
                    models=args.models.split(","),
                    scenarios=args.scenarios.split(","),
                    paths=args.paths.split(","),
                    efforts=[None if e in ("default", "none") else e
                             for e in args.efforts.split(",")])
    else:
        parser.error("pass --suite NAME, or --providers/--models/--scenarios. "
                     "--list shows the options.")

    for key, table in (("models", MODELS), ("scenarios", SCENARIOS)):
        for name in spec[key]:
            if name not in table:
                parser.error(f"unknown {key[:-1]}: {name}")

    for effort in spec.get("efforts", [None]):
        if effort not in EFFORTS:
            known = ", ".join("default" if e is None else e for e in EFFORTS)
            parser.error(f"unknown effort: {effort}. try: {known}")

    cells = build_cells(**spec)
    results = asyncio.run(run_suite(cells, args.runs, args.gap))
    report(results, args.runs)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or Path("results") / f"{args.suite or 'custom'}-{stamp}.json"
    saved = save(results, out, args.runs, args.gap, args.suite)
    print(f"  raw samples and manifest written to {saved}")
    print(f"  re-report without measuring:  voicebench --replay {saved}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
