"""
update_webhook.py — repoint assistants at a new webhook URL.

Free ngrok URLs change on every restart, and an assistant pointed at a dead
tunnel loses calls SILENTLY — Vapi gets a 502, you get nothing, and you
find out when review.py has no files. Run this after every tunnel restart,
or use a reserved ngrok domain and stop thinking about it.

    export VAPI_API_KEY=...
    python update_webhook.py https://hermitlike-irresoluble-artie.ngrok-free.dev/vapi
    python update_webhook.py --show                 # what is set right now
    python update_webhook.py --match Lakeview URL   # just the fixture

Unlike tune.py, this repoints EVERY assistant by default: after a tunnel
restart they are all pointing at a dead URL. Use --match to narrow it.
"""

from __future__ import annotations

import argparse
import sys

import vapi


def show(targets: list[dict]) -> None:
    for assistant in targets:
        url = (assistant.get("server") or {}).get("url") or "(none)"
        messages = assistant.get("serverMessages") or []
        print(f"\n  {assistant['id']}")
        print(f"    name:     {assistant.get('name')}")
        print(f"    server:   {url}")
        print(f"    messages: {', '.join(messages) or '(none)'}")
    print()


def normalise(url: str) -> str:
    if not url.startswith("https://"):
        sys.exit("URL must start with https://")
    if not url.rstrip("/").endswith("/vapi"):
        # webhook.py serves POST /vapi, not the root.
        print("  note: appending /vapi — webhook.py listens there")
        url = url.rstrip("/") + "/vapi"
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="update_webhook.py",
        description="Repoint assistants at a new webhook URL.")
    parser.add_argument("url", nargs="?", help="the new https webhook URL")
    parser.add_argument("--show", action="store_true",
                        help="print the current server config and exit")
    parser.add_argument("--match", default=None,
                        help="only assistants whose name contains this "
                             "(default: all of them)")
    args = parser.parse_args(argv)

    targets = vapi.assistants(args.match)

    if args.show or not args.url:
        show(targets)
        return 0

    url = normalise(args.url)
    vapi.patch_all(
        {
            "server": {"url": url},
            # Without this the URL is set but nothing is delivered to it.
            "serverMessages": ["end-of-call-report"],
        },
        targets,
        f"now pointing at {url}",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
