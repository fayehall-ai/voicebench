"""
rig.py — the fixture's deliberately broken settings, in one place.

create_assistant.py plants these. tune.py has to preserve them while
changing exactly one thing, and restore them on --revert. While each file
kept its own copy, --revert reverted to whatever the SECOND copy said,
which is a fixture that no longer matches the one the corpus was recorded
against — and nothing about the output would tell you.

Nothing in this file is a good idea. It is a rig: see create_assistant.py
for which failure each value manufactures.
"""

from __future__ import annotations

TRANSCRIBER_PROVIDER = "deepgram"
TRANSCRIBER_MODEL = "nova-2"
LANGUAGE = "en"

# Deepgram's own endpointing, in milliseconds. Distinct from Vapi's
# transcriptionEndpointingPlan below, which is applied on top of it.
ENDPOINTING_MS = 300

# onNoPunctuationSeconds defaults to 1.5s. At 0.3 any thinking pause
# finalises the turn mid-sentence, which is what manufactures the
# "No" / "No, I don't want the extended warranty" failure on demand.
# onNumberSeconds defaults to 0.5s. At 0.2 a phone number read with
# natural gaps gets chopped into fragments.
ENDPOINTING_PLAN = {
    "onPunctuationSeconds": 0.1,
    "onNoPunctuationSeconds": 0.3,
    "onNumberSeconds": 0.2,
}

# Applied AFTER all processing completes, so it is pure padding. At 0.4s
# it was 26% of a measured ~1520ms median reply gap and bought nothing.
WAIT_SECONDS = 0.4

STOP_SPEAKING_PLAN = {
    "numWords": 0,
    "voiceSeconds": 0.2,
    "backoffSeconds": 1.0,
}


def transcriber(model: str = TRANSCRIBER_MODEL) -> dict:
    """The base transcriber block, with no keyword or keyterm biasing.

    Omitting biasing is part of the rig: a production agent would bias
    toward "Cigna", "Reyes", "Concordia" and the like.
    """
    return {
        "provider": TRANSCRIBER_PROVIDER,
        "model": model,
        "language": LANGUAGE,
        "endpointing": ENDPOINTING_MS,
    }


def start_speaking_plan(wait: float = WAIT_SECONDS, **endpointing) -> dict:
    """The rig's turn-taking plan, with named endpointing overrides.

    Returns fresh dicts every call, so a caller adjusting one value cannot
    mutate the shared defaults the next caller — or --revert — reads.

    smartEndpointingPlan is deliberately absent. Setting it OVERRIDES
    transcriptionEndpointingPlan, and the values above go inert while
    still being printed by `tune.py --show`.
    """
    plan = dict(ENDPOINTING_PLAN)
    plan.update(endpointing)
    return {"waitSeconds": wait, "transcriptionEndpointingPlan": plan}
