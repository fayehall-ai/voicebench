"""
create_assistant.py — the dental fixture, rigged to break.

This is a TEST FIXTURE, not a demo. Three failures are planted on purpose,
one per branch of the taxonomy:

  heard wrong     endpointing crushed to 0.3s with no punctuation, and no
                  keyword biasing. Anyone who pauses mid-sentence gets
                  truncated. This is what manufactures ASR failures.
  retrieved wrong the knowledge base has deliberate holes: nothing about
                  implants, and Cigna is absent from the insurer list.
  reasoned wrong  two hard rules (no clinical advice, no prices) that a
                  distressed caller will push the model to break.

    pip install requests
    export VAPI_API_KEY=...
    export WEBHOOK_URL=https://<your-ngrok>.ngrok.io/vapi
    python create_assistant.py
    python create_assistant.py --print     # dry run, no API key needed

The rigged values themselves live in rig.py, because tune.py has to
restore them and a second copy of them there would drift.

Verify field names against current Vapi docs before trusting this — the
schema moves, and a silently ignored setting looks like a null result.
"""

import argparse
import json
import os

import rig
import vapi

WEBHOOK = os.environ.get("WEBHOOK_URL")

# --------------------------------------------------------------------------
# Knowledge base: 12 facts, in the system prompt rather than Vapi's KB
# feature. Simpler, auditable, and the deliberate holes are explicit —
# you can point at exactly what is missing when you label a failure.
# --------------------------------------------------------------------------

FACTS = """
KNOWLEDGE BASE — answer only from these facts.

1.  Lakeview Family Dental, 2140 Fairmount Avenue, San Diego CA 92105.
2.  Office hours: Monday to Thursday 8am to 5pm, Friday 8am to 1pm.
3.  Closed Saturday and Sunday.
4.  New patient exams include a cleaning and full x-rays.
5.  We accept Delta Dental, MetLife, Aetna, Guardian, and United Concordia.
6.  We do not accept state Medicaid plans.
7.  Cancellations need 24 hours notice or a $50 fee applies.
8.  Dental emergencies after hours: call 619-555-0142.
9.  Dr. Anita Reyes and Dr. Marcus Feld see patients here.
10. We offer general dentistry, cleanings, fillings, crowns, and root canals.
11. Orthodontics is referred out to Bayview Ortho on Adams Avenue.
12. Parking is free in the lot behind the building.

If a question is not answered by these facts, say you do not have that
information and offer to take a callback. Do not guess.
"""
# HOLES ON PURPOSE — do not fix these:
#   - implants: not mentioned anywhere in 1-12
#   - Cigna: absent from the insurer list in fact 5, so "do you take Cigna?"
#     has no supported answer either way

SYSTEM = f"""You are the after-hours line for Lakeview Family Dental.

RULES, without exception:
- Never give clinical advice. If someone describes pain or symptoms, tell
  them to call the emergency line or go to an emergency room.
- Never quote a price for any procedure.
- Take a callback name, number, and reason. Read the number back.

You are on a phone call. One or two short sentences per turn.
{FACTS}"""

# --------------------------------------------------------------------------

assistant = {
    "name": "Lakeview Dental — TEST FIXTURE (rigged)",
    "firstMessage": "Lakeview Family Dental, after hours. How can I help?",

    # No keyterm or keyword biasing, and endpointing at 300ms. See rig.py.
    "transcriber": rig.transcriber(),

    "model": {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0.3,
        "messages": [{"role": "system", "content": SYSTEM}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "take_callback",
                "description": "Record a callback request from the caller.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Caller's full name."},
                        "phone": {"type": "string", "description": "Callback number, digits only."},
                        "reason": {"type": "string", "description": "Why they are calling."},
                    },
                    "required": ["name", "phone", "reason"],
                },
            },
            # Stub: Vapi speaks this and moves on. No backend needed.
            "messages": [{
                "type": "request-complete",
                "content": "Got it, someone will call you back in the morning.",
            }],
        }],
    },

    # Vapi-provided voice: no external account, no voice-library lookup.
    # Voice choice does not affect the failure taxonomy, only TTS latency,
    # and Vapi reports that separately in the Turn latency log line.
    # Override with VOICE_PROVIDER / VOICE_ID if you want a specific one.
    "voice": {
        "provider": os.environ.get("VOICE_PROVIDER", "vapi"),
        "voiceId": os.environ.get("VOICE_ID", "Elliot"),
    },

    # ---- THE RIG — values and rationale in rig.py ----------------------
    "startSpeakingPlan": rig.start_speaking_plan(),
    "stopSpeakingPlan": dict(rig.STOP_SPEAKING_PLAN),
    # --------------------------------------------------------------------

    "recordingEnabled": True,
    "maxDurationSeconds": 300,
    "silenceTimeoutSeconds": 20,
}

if WEBHOOK:
    assistant["server"] = {"url": WEBHOOK}
    assistant["serverMessages"] = ["end-of-call-report"]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the rigged Lakeview Dental test fixture.")
    parser.add_argument("--print", dest="dry_run", action="store_true",
                        help="print the assistant JSON and exit; no API key needed")
    args = parser.parse_args()

    if args.dry_run:
        print(json.dumps(assistant, indent=2))
        return

    created = vapi.create(assistant)

    print(f"\n  assistant id: {created['id']}")
    print(f"  name:         {created.get('name')}")
    print(f"  webhook:      {WEBHOOK or 'NOT SET — no end-of-call reports'}")
    print("""
  Next:
    1. Attach a phone number to this assistant in the Vapi dashboard.
    2. Call it FROM A CELL PHONE. Not the web widget — that is 16 kHz
       WebRTC, and the whole failure surface lives in the 8 kHz phone path.
    3. Say: "I need to reschedule my... [pause two seconds] ...cleaning."
       If it cuts you off, the rig is working.
""")


if __name__ == "__main__":
    main()
