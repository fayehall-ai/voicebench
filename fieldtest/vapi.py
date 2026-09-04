"""
vapi.py — the Vapi REST client these scripts share.

One place for the three things each script here previously got right or
wrong on its own:

  * the User-Agent. Cloudflare fronts api.vapi.ai and rejects clients that
    do not look like an ordinary HTTP library, with "error code: 1010"
    before the request reaches Vapi at all. That is bot protection, not
    authentication, and it is worth an hour the first time you meet it.

  * the API key. Read at first request rather than at import, so a dry run
    like `create_assistant.py --print` works on a machine with no key set.

  * reporting a change that did not happen. patch_all() prints the failure
    line AND withholds the success message; update_webhook.py used to
    announce "now pointing at <url>" after every patch had 400'd.
"""

from __future__ import annotations

import os
import sys

import requests

API = "https://api.vapi.ai/assistant"
TIMEOUT = 30
USER_AGENT = "voicebench-corpus/0.1"


def headers() -> dict:
    key = os.environ.get("VAPI_API_KEY")
    if not key:
        sys.exit("VAPI_API_KEY not set")
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _checked(response, limit: int = 400):
    if response.status_code >= 400:
        sys.exit(f"{response.status_code}: {response.text[:limit]}")
    return response


def assistants(match: str | None = None) -> list[dict]:
    """Every assistant, or only those whose name contains `match`.

    The scripts genuinely differ in what they should touch: tune.py
    changes one fixture and must not silently reconfigure a neighbouring
    assistant, while update_webhook.py repoints everything after a tunnel
    restart. Making the filter an argument keeps that difference visible
    at the call site rather than hidden in two divergent copies.
    """
    found = _checked(requests.get(API, headers=headers(), timeout=TIMEOUT)).json()
    if match:
        found = [a for a in found if match in (a.get("name") or "")]
    return found


def create(assistant: dict) -> dict:
    return _checked(
        requests.post(API, json=assistant, headers=headers(), timeout=TIMEOUT),
        limit=600,
    ).json()


def patch_all(body: dict, targets: list[dict], label: str) -> bool:
    """PATCH each target, then say what actually happened.

    Returns True only if every patch succeeded. An empty target list is a
    failure too: "no assistants matched" and "changed nothing" look
    identical from the outside otherwise.
    """
    if not targets:
        print("\n  no assistants matched — nothing was changed\n")
        return False

    failed = False
    for a in targets:
        response = requests.patch(f"{API}/{a['id']}", json=body,
                                  headers=headers(), timeout=TIMEOUT)
        status = "ok" if response.status_code < 400 else f"FAILED {response.status_code}"
        print(f"  {(a.get('name') or a['id'])[:44]:<46} {status}")
        if response.status_code >= 400:
            print(f"    {response.text[:400]}")
            failed = True

    print(f"\n  {'NOTHING CHANGED — see errors above' if failed else label}\n")
    return not failed
