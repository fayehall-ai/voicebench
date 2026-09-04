"""
webhook.py — capture Vapi end-of-call reports verbatim.

Assumes nothing about the payload shape. Writes the whole thing to disk so
you can look at what actually arrives rather than what you expected. Every
filter you write before seeing real data is a filter that silently never
matches.

    pip install fastapi uvicorn
    python webhook.py
    ngrok http 8000        # point Vapi's server URL at <ngrok>/vapi
"""

import json
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request

OUT = Path("calls")
OUT.mkdir(exist_ok=True)

app = FastAPI()


@app.post("/vapi")
async def vapi(request: Request):
    body = await request.json()
    message = body.get("message", {})
    kind = message.get("type", "unknown")

    # Vapi sends several event types. Only the end-of-call report has the
    # transcript, recording and timings; the rest are noise for our purpose.
    if kind != "end-of-call-report":
        print(f"  ignoring {kind}")
        return {"ok": True}

    call_id = message.get("call", {}).get("id", "nocallid")[:8]
    stamp = datetime.now().strftime("%H%M%S")
    path = OUT / f"{stamp}-{call_id}.json"
    path.write_text(json.dumps(body, indent=2))

    artifact = message.get("artifact", {})
    print(f"\n  saved {path}")
    print(f"    ended:     {message.get('endedReason')}")
    print(f"    duration:  {message.get('durationSeconds')}s")
    print(f"    cost:      ${message.get('cost', 0):.4f}")
    print(f"    recording: {'yes' if artifact.get('recordingUrl') else 'NO'}")
    print(f"    messages:  {len(artifact.get('messages', []))}")
    return {"ok": True}


@app.get("/")
def health():
    return {"calls_captured": len(list(OUT.glob("*.json")))}


if __name__ == "__main__":
    import uvicorn
    print("\nlistening on :8000/vapi — point Vapi's server URL here\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)