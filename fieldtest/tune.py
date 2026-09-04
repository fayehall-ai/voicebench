"""
tune.py — change one setting on the fixture and measure the difference.

Built for the waitSeconds finding: it is a delay applied AFTER all
processing completes, so it is pure padding. At 0.4s it was 26% of a
measured ~1520ms median reply gap and bought nothing.

    export VAPI_API_KEY=...
    python tune.py --show
    python tune.py --show --verbose         # plus the raw transcriber block
    python tune.py --wait 0
    python tune.py --numbers 0.5
    python tune.py --model nova-3           # model change ONLY
    python tune.py --keyterms               # biasing on the CURRENT model
    python tune.py --model nova-3 --keyterms   # both, explicitly
    python tune.py --keywords               # legacy biasing, any model
    python tune.py --revert                 # back to the rigged values

Biasing is model-dependent. nova-2 takes `keywords` (legacy, weaker);
nova-3 and flux take `keyterm`. Sending keyterm on nova-2 returns:

    transcriber.keyterm must be undefined if model is not nova-3 or flux

To test biasing on nova-3 you must change TWO things, so do them in two
steps: --model nova-3, five calls, then --keyterms, five more. Otherwise a
difference cannot be attributed to either.

Change ONE thing, make five calls, re-run review.py, compare medians.
Changing two things at once means you cannot attribute the difference.

The rigged baseline lives in rig.py, shared with create_assistant.py, so
--revert restores the fixture the corpus was actually recorded against.
"""

from __future__ import annotations

import argparse
import json
import sys

import rig
import vapi

DEFAULT_MATCH = "Lakeview"

# Terms the 8 kHz phone path mangled in the corpus: implant -> "In pen" /
# "IMEImed" / "intern" / "InPam", Cigna -> "Stigma", Aetna -> "Anna",
# Reyes -> "Ray". Biasing tells Deepgram these strings are likely, which
# is the standard fix and the thing the rigged fixture deliberately omits.
#
# Deepgram recommends short, focused lists: the boost is a budget, and
# padding it with words that never failed dilutes the ones that did.
KEYTERMS = ["implant", "implants", "Cigna", "Aetna", "Reyes", "Feld",
            "Concordia", "orthodontics", "Fairmount", "Bayview"]

# Multi-word phrases. keyterm (nova-3/flux) accepts these; keywords
# (nova-2) does NOT — it takes single tokens only, in the form 'word' or
# 'word:boost'. That is a real capability gap between the two mechanisms,
# not just a formatting rule, so the comparison is not apples to apples.
KEYPHRASES = ["Delta Dental", "dental implant", "United Concordia"]


def biasing(assistant: dict) -> str:
    """How this assistant is biased, named by the FIELD carrying it.

    A bare count reads identically whether the list sits under `keyterm`
    (nova-3/flux) or `keywords` (nova-2) — two different experiments,
    indistinguishable output.
    """
    transcriber = assistant.get("transcriber") or {}
    for field in ("keyterm", "keywords"):
        terms = transcriber.get(field)
        if terms:
            return f"{field} ({len(terms)} terms)"
    return "none"


def current_model(targets: list[dict], default: str = rig.TRANSCRIBER_MODEL) -> str:
    """The model actually configured, so a biasing flag cannot silently
    change it as a side effect."""
    for assistant in targets:
        return ((assistant.get("transcriber") or {}).get("model")) or default
    return default


def current_wait(targets: list[dict], default: float = rig.WAIT_SECONDS) -> float:
    """The waitSeconds actually configured.

    A PATCH replaces startSpeakingPlan wholesale, so a flag that touches
    only the endpointing plan still has to resend waitSeconds or it
    reverts underneath the experiment — two variables moving on a run that
    claims to move one.
    """
    for assistant in targets:
        value = (assistant.get("startSpeakingPlan") or {}).get("waitSeconds")
        if value is not None:
            return value
    return default


def show(targets: list[dict], verbose: bool = False) -> None:
    """Print the config that determines what a batch is measuring."""
    for assistant in targets:
        plan = assistant.get("startSpeakingPlan") or {}
        endpointing = plan.get("transcriptionEndpointingPlan") or {}
        transcriber = assistant.get("transcriber") or {}
        model = assistant.get("model") or {}
        bias = biasing(assistant)

        print(f"\n  {assistant['id']}  {assistant.get('name')}")
        print("    -- transcriber ------------------------------------")
        print(f"    provider:               {transcriber.get('provider')}")
        print(f"    model:                  {transcriber.get('model')}")
        print(f"    biasing:                {bias}")
        print(f"    endpointing:            {transcriber.get('endpointing')}")
        print("    -- llm --------------------------------------------")
        print(f"    provider/model:         {model.get('provider')} / {model.get('model')}")
        print(f"    tools:                  {len(model.get('tools') or [])}")
        print("    -- turn taking ------------------------------------")
        print(f"    waitSeconds:            {plan.get('waitSeconds')}")
        print(f"    onPunctuationSeconds:   {endpointing.get('onPunctuationSeconds')}")
        print(f"    onNoPunctuationSeconds: {endpointing.get('onNoPunctuationSeconds')}")
        print(f"    onNumberSeconds:        {endpointing.get('onNumberSeconds')}")

        smart = plan.get("smartEndpointingPlan")
        if smart:
            # smartEndpointingPlan OVERRIDES transcriptionEndpointingPlan.
            # If it is set, the three values above are inert and the rig
            # is not doing what it looks like it is doing.
            print(f"    smartEndpointingPlan:   {smart}   <<< OVERRIDES THE PLAN ABOVE")
        else:
            print("    smartEndpointingPlan:   none (endpointing plan is live)")

        if verbose:
            print("\n    -- raw transcriber block --------------------------")
            print("    " + json.dumps(transcriber, indent=2).replace("\n", "\n    "))

        # One line to paste into a lab notebook next to the batch results.
        print(f"\n    config: {transcriber.get('model')} / {bias} / "
              f"wait={plan.get('waitSeconds')} "
              f"noPunc={endpointing.get('onNoPunctuationSeconds')} "
              f"num={endpointing.get('onNumberSeconds')}")
    print()


def biased_transcriber(model: str, field: str | None = None) -> dict:
    """The rig's transcriber block, optionally carrying biasing terms."""
    transcriber = rig.transcriber(model)

    if field == "keyterm":
        transcriber["keyterm"] = KEYTERMS + KEYPHRASES
    elif field == "keywords":
        # Single tokens only. Multi-word phrases are dropped, and any that
        # slipped through would 400 with:
        #   each keyword must be in the format 'word' or 'word:number'
        dropped = [k for k in KEYTERMS + KEYPHRASES if " " in k]
        if dropped:
            print(f"  dropping {len(dropped)} multi-word term(s) — "
                  f"keywords cannot express phrases: {', '.join(dropped)}")
        transcriber["keywords"] = [k for k in KEYTERMS if " " not in k]

    return transcriber


def bias_field(args, targets: list[dict]) -> tuple[str, str]:
    """Resolve (model, field) for a biasing run, or exit explaining why not.

    Biasing does NOT choose the model. An earlier version hardcoded nova-3
    into --keyterms, so the flag silently changed two variables and a
    "nova-2 + keyterms" batch was really nova-3. Pair the flag with
    --model, and the field is picked from the model you asked for.
    """
    model = args.model or current_model(targets)

    if args.keywords:
        return model, "keywords"
    if model.startswith("nova-3") or "flux" in model:
        return model, "keyterm"

    # keyterm on nova-2 returns:
    #   transcriber.keyterm must be undefined if model is not nova-3 or flux
    sys.exit(
        f"--keyterms needs nova-3 or flux; assistant is on {model}.\n"
        f"  either:  python tune.py --model nova-3 --keyterms\n"
        f"  or:      python tune.py --keywords   (legacy, works on nova-2)")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="tune.py",
        description="Change one setting on the fixture and measure the difference.")
    parser.add_argument("--show", action="store_true",
                        help="print the current config and exit (the default)")
    parser.add_argument("--verbose", action="store_true",
                        help="with --show, also dump the raw transcriber block")
    parser.add_argument("--match", default=DEFAULT_MATCH,
                        help=f"only assistants whose name contains this "
                             f"(default: {DEFAULT_MATCH})")
    parser.add_argument("--wait", type=float, metavar="SECONDS",
                        help="startSpeakingPlan.waitSeconds; pure padding")
    parser.add_argument("--numbers", type=float, metavar="SECONDS",
                        help="onNumberSeconds; 0.2 fragmented phone numbers, "
                             "Vapi's default is 0.5")
    parser.add_argument("--model", metavar="NAME",
                        help="transcriber model, e.g. nova-3. Model change ONLY "
                             "unless you also pass a biasing flag")
    parser.add_argument("--keyterms", action="store_true",
                        help="keyterm biasing; needs nova-3 or flux")
    parser.add_argument("--keywords", action="store_true",
                        help="legacy keywords biasing; works on nova-2")
    parser.add_argument("--revert", action="store_true",
                        help="restore the rigged values from rig.py")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    targets = vapi.assistants(args.match)

    changing = (args.wait is not None or args.numbers is not None
                or args.model or args.keyterms or args.keywords or args.revert)
    if args.show or not changing:
        show(targets, verbose=args.verbose)
        return 0

    if args.revert:
        vapi.patch_all({"startSpeakingPlan": rig.start_speaking_plan()}, targets,
                       f"reverted to rigged values (waitSeconds {rig.WAIT_SECONDS})")
        return 0

    body: dict = {}

    # The endpointing rig is resent in full whenever the plan is touched,
    # so only the named setting changes. waitSeconds comes from the live
    # config unless asked for, because a PATCH replaces the whole plan.
    if args.wait is not None or args.numbers is not None:
        overrides = {} if args.numbers is None else {"onNumberSeconds": args.numbers}
        wait = current_wait(targets) if args.wait is None else args.wait
        body["startSpeakingPlan"] = rig.start_speaking_plan(wait, **overrides)

    if args.keyterms or args.keywords:
        model, field = bias_field(args, targets)
        body["transcriber"] = biased_transcriber(model, field)
        print(f"  biasing: {field} on {model}")
    elif args.model:
        # Step 1: change the model, NO biasing. Isolates whether nova-3
        # alone fixes the narrowband entity errors.
        body["transcriber"] = biased_transcriber(args.model)

    vapi.patch_all(body, targets, "changed. now make five calls and re-run review.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
