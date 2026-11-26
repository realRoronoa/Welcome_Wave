"""Is each integration set up, and does it work? Called by ./scripts/selfhost.sh check, which has loaded backend/selfhost.env
into the environment; this reads its settings from there and prints one line per integration.

    PASS  Gemini (the AI)  The key is accepted; 52 models listed.
    FAIL  GitHub           GitHub refused the token (HTTP 401): expired, revoked or mistyped.
    skip  Calendly         No CALENDLY_WEBHOOK_SIGNING_KEY: bookings are not received.
    set   Google Calendar  The OAuth client is set (a sign-in is needed to test it). ...

PASS: a real call worked. FAIL: it was made and failed. set: configured, nothing to call without a person or a delivery.
skip: not configured. The exit code is 1 if any configured integration failed. No secret is ever printed: each line is built
from a host, a count, an HTTP status or an error's type (app/services/integration_status.py).

Then the legal details the terms of service, the privacy policy and the data-processing agreement show (app/legal.py), one line
per LEGAL_* setting: `set`, or `not set` with what the documents say meanwhile. A missing one never fails the check and never
stops the server: the documents say "the company that operates this service" until it is filled in.

    not set  LEGAL_OPERATOR_NAME      The company's registered legal name. Until it is set, the documents say "the company ..."
"""
from __future__ import annotations

import sys

from app.config import get_settings
from app.services import integration_status as I


def label(item: dict) -> str:
    if not item["configured"]:
        return "FAIL" if item["ok"] is False else "skip"
    return {True: "PASS", False: "FAIL", None: "set"}[item["ok"]]


def main() -> int:
    settings = get_settings()
    items = I.check_all(settings, live=True)
    print("Integrations (checked now; no secret is printed)")
    for item in items:
        print(f"  {label(item):4s}  {item['name']:28s} {item['detail']}")
    failed = [i["name"] for i in items if label(i) == "FAIL"]
    print(f"\n{len(failed)} failed: {', '.join(failed)}" if failed else "\nNothing configured is failing.")
    legal_details(settings)
    return 1 if failed else 0


def legal_details(settings) -> int:
    """Print the LEGAL_* settings as set or not set. Returns how many are missing; never changes the exit code."""
    from app import legal

    rows = legal.settings_report(settings)
    missing = [r["env"] for r in rows if not r["set"]]
    print("\nLegal details (shown on the terms, the privacy policy and the data-processing agreement)")
    for r in rows:
        print(f"  {'set' if r['set'] else 'not set':7s}  {r['env']:24s} {r['detail']}")
    if missing:
        print(f"\n{len(missing)} not set. Add them to the environment file (backend/selfhost.env, or the service's variables on Google "
              "Cloud) before selling:\n  " + ", ".join(missing) + "\nThe server runs without them; ask your lawyer for the governing law "
              "and the courts.")
    else:
        print("\nAll legal details are set.")
    notes = legal.deployment_report(settings)
    if notes:
        print("\nSettings that change what the legal documents must say (the pages show the result as \"On this service\")")
        for n in notes:
            print(f"  {n['level']:7s}  {n['env']:24s} {n['detail']}")
    return len(missing)


if __name__ == "__main__":
    sys.exit(main())
