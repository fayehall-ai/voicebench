"""
scrub.py — strip identifiers from captured call reports before committing.

webhook.py writes Vapi's end-of-call report verbatim, which is the right
default for research and the wrong one for a public repository. The report
carries the caller's phone number, signed URLs to the recording and pcap,
and the Vapi account identifiers, none of which any finding depends on.

What it removes, and why each is safe to lose:

  every *Url key, and the recording block
      Signed R2 links to the raw audio. review.py prints the URL when it is
      present and omits the line when it is not, so the listening sheet
      still builds; you just have to hold the audio yourself.
  customer.number, customer.sipUri, phoneNumber.number
      The cell phone the calls were placed from and the Vapi DID.
  account-sid, application-sid, voip-carrier-sid, cid, forwarded-for, orgId
      Account-level identifiers for the Vapi org.
  spoken digit runs in transcripts and message text
      Callers read a callback number aloud. Seven or more digits in a row
      becomes [redacted-digits]; the tool-call latency finding is about the
      duration of the hop, not the number.

Names are deliberately kept. The ASR findings are largely about proper
nouns, and redacting them would remove the evidence.

    python scrub.py --check          # report what is still identifying
    python scrub.py                  # rewrite calls/ in place

Timing fields are never touched: every number in FIELD-STUDY.md is
recomputed from scrubbed files by review.py.
"""

import argparse
import json
import re
import sys
from pathlib import Path

CALLS = Path(__file__).resolve().parent.parent / "calls"

# Any key whose name ends in "Url", plus the recording block. Vapi ships
# several families of these (recordingUrl, presignedStereoUrl, ...) and a
# hand-listed set missed one of them on the first pass, so match the shape.
DROP_KEY_RE = re.compile(r"Url$", re.IGNORECASE)
DROP_KEYS = {"recording"}


def is_drop_key(k):
    return k in DROP_KEYS or bool(DROP_KEY_RE.search(k))
REDACT_KEYS = {
    "number": "[redacted-phone]",
    "sipUri": "[redacted-sip]",
    "account-sid": "[redacted-id]",
    "application-sid": "[redacted-id]",
    "voip-carrier-sid": "[redacted-id]",
    "cid": "[redacted-id]",
    "forwarded-for": "[redacted-ip]",
    "orgId": "[redacted-id]",
}

# Seven or more digits, however the caller spaced them out when reading
# them aloud, plus E.164 numbers wherever they appear in free text.
SPOKEN_DIGITS = re.compile(r"\b(?:\d[\s\-.]{0,2}){7,}\d?\b")
E164 = re.compile(r"\+\d{10,15}")


def scrub_text(s):
    return SPOKEN_DIGITS.sub("[redacted-digits]", E164.sub("[redacted-phone]", s))


def scrub(node):
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if is_drop_key(k):
                continue
            if k in REDACT_KEYS and isinstance(v, str):
                out[k] = REDACT_KEYS[k]
            else:
                out[k] = scrub(v)
        return out
    if isinstance(node, list):
        return [scrub(v) for v in node]
    if isinstance(node, str):
        return scrub_text(node)
    return node


def find_leaks(node, path=""):
    """What a reviewer would still object to. Runs on the scrubbed tree."""
    leaks = []
    if isinstance(node, dict):
        for k, v in node.items():
            if is_drop_key(k):
                leaks.append(f"{path}/{k}  (url key survived)")
            leaks += find_leaks(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            leaks += find_leaks(v, f"{path}[{i}]")
    elif isinstance(node, str):
        if E164.search(node):
            leaks.append(f"{path}  E.164: {node[:60]}")
        elif SPOKEN_DIGITS.search(node):
            leaks.append(f"{path}  digits: {SPOKEN_DIGITS.search(node).group()[:40]}")
        elif "cloudflarestorage.com" in node or "r2.dev" in node:
            leaks.append(f"{path}  storage url")
    return leaks


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="report identifying data without rewriting")
    args = ap.parse_args()

    files = sorted(CALLS.glob("*/*.json"))
    if not files:
        sys.exit(f"no call files under {CALLS}")

    total = 0
    for path in files:
        original = path.read_text()
        payload = json.loads(original)
        cleaned = scrub(payload)
        leaks = find_leaks(cleaned)
        if leaks:
            total += len(leaks)
            print(f"{path.relative_to(CALLS.parent)}")
            for leak in leaks[:5]:
                print(f"    {leak}")
        if not args.check:
            rendered = json.dumps(cleaned, indent=2) + "\n"
            if rendered != original:
                path.write_text(rendered)

    verb = "would remain" if args.check else "remain"
    print(f"\n{len(files)} files, {total} identifying values {verb}.")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
