# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A research repository, not a product: latency and behaviour measurement for voice agents, plus the write-ups of the studies run with it. The Markdown study files (`LAB-STUDY.md`, `FIELD-STUDY.md`, `GUARDRAIL-STUDY.md`) are primary deliverables — findings in them must be traceable to a committed run file. Findings that didn't hold up are kept in the open (e.g. "Results that are NOT trustworthy"), not deleted.

There are no tests, linter, or build step. Python ≥3.11, managed with `uv` (`uv sync`; `.venv/`).

Root-level `agent*.py`, `latency*.py`, `usecase.json` are gitignored scratch (drafts that became `voicebench/`, and unrelated product experiments). Don't build on them.

## Three studies, three harnesses, three output dirs

| harness | measures | writes to | run from |
|---|---|---|---|
| `voicebench/` (package) | the LLM leg in isolation | `results/lab/` | repo root: `python -m voicebench ...` |
| `fieldtest/` (scripts) | whole turns over a real phone line via Vapi | `calls/<configuration>/` | `fieldtest/` (scripts `import vapi` as a sibling module) |
| `guardrail/guardrail.py` | policy adherence under a persistent caller | `results/guardrail/`, calibration files in `guardrail/` | `guardrail/` |

Keep one output directory per study — every run file carries a manifest, and mixing studies' outputs is exactly what makes a number unattributable. Output paths are anchored to `__file__`, not cwd; do the same in new code.

## Commands

```bash
# Lab harness
python -m voicebench --list                  # models, scenarios, suites
python -m voicebench --inspect               # real stream event shapes
python -m voicebench --suite quick           # smallest real run
python -m voicebench --replay results/lab/<file>.json   # re-report without re-measuring
python -m voicebench --providers bedrock,anthropic --models haiku-4.5 \
  --scenarios plain --paths streaming --runs 20         # custom run

# Field rig (needs VAPI_API_KEY; webhook needs an ngrok tunnel)
python webhook.py                      # capture end-of-call reports
python create_assistant.py --print     # dry run, no key needed
python tune.py --show | --wait 0 | --model nova-3 | --revert
python update_webhook.py <url>         # after every ngrok restart
python review.py --summary             # n and median per configuration
python scrub.py                        # strip identifiers before committing calls/

# Guardrail
python guardrail.py --run [--repeats N]
python guardrail.py --calibrate ../results/guardrail/<file>.json   # writes calibration.md sheet
python guardrail.py --score [FILE]     # reads human labels back
```

Env: `ANTHROPIC_API_KEY`, `ANTHROPIC_WORKSPACE_ID` (identity-linked keys only — set it in `~/.zshenv`, since an interactive-only rc gives a mid-suite 400 rather than a startup failure), AWS credentials for `bedrock`/`strands` rows.

## voicebench architecture

Split by **layer**, not by scenario — separate processes can't interleave, which would make cross-scenario deltas invalid.

- `config.py` — models, scenarios, suites, prompts, and the constants `RUNS`, `GAP`, `NOISE_PCT`.
- `measure.py` — `Cell` (one point in provider × model × scenario × path × effort), `Sample`, `Result`, `Clock`.
- `providers.py` — bedrock / anthropic / strands adapters.
- `runner.py` — the measurement rules: calls paced by `GAP`; first run per row discarded (warm-up and cache-write); provider varies fastest in `build_cells` so compared rows are seconds apart; runs where the model skipped the tool are excluded, not averaged in.
- `report.py` — `compare_axis` only compares cells equal on every other axis; metric chosen per group (`spoken_p50` is undefined for blocking rows, so those use `total_p50`); a comparison that couldn't be made is reported as missing, not silently dropped; deltas under `NOISE_PCT` are flagged as noise.
- `cli.py` — args, `--list`, `--inspect`, `--replay`, save.

Effort is an **axis** crossed with every scenario, not a scenario; `None`/`default` means `output_config` is not sent at all.

## fieldtest specifics

- `create_assistant.py` builds a deliberately **broken** dental-office fixture (crushed endpointing, knowledge-base holes, hard rules to pressure). The rigged values live only in `rig.py`; `tune.py` changes exactly one setting and `--revert` restores from `rig.py`. Never duplicate rig values elsewhere.
- `vapi.py` is the shared REST client (custom User-Agent to get past Cloudflare error 1010; key read lazily; failed patches must not print success).
- `calls/` subdirectories are configurations (`call-nova2`, `call-waitime-0`, …); `review.py --summary` groups by them. Every latency figure in `FIELD-STUDY.md` should be reproducible from its output.
- Run `scrub.py` before committing call reports (removes phone numbers, signed recording URLs, account IDs, spoken digit runs; keeps names on purpose).

## guardrail specifics

- Measures a survival curve (pressure depth at which a policy breaks, per model), not a pass rate. `JUDGE_MODEL` must never be a model under test.
- Calibration samples **from the run being published**, oversampling judge-flagged VIOLATION/UNCLEAR plus matched CLEANs. The violation rate in the calibration sample is therefore not a base rate — `--score` deliberately refuses to print it. Preserve that.
- Runs and a labelled calibration sample exist, but no rate is published yet; don't quote numbers from `results/guardrail/` until the judge is scored and the write-up states n.

## Conventions visible in the history

- Module docstrings carry the usage and the *why* (often a past mistake the design prevents). Keep them accurate when changing behaviour.
- State n alongside any figure, and take n and the statistic from the same batch — one published figure had to be corrected for pairing a turn count from a different run.
