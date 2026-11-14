"""Reports each sign-in (the account email) to gab's tools-usage dashboard (github.com/GabKrouk/tools-usage),
the Google Apps Script that already counts the use of his other tools.

  FIOLE_USAGE_KEY       the dashboard's ADMIN_KEY (required: nothing is sent without it)
  FIOLE_USAGE_ENDPOINT  the Apps Script web-app URL (defaults to the one in tools-usage/usage.js)

Sent from the server only (the key never reaches a browser), in a background thread, never blocking a sign-in.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

log = logging.getLogger("fiole.usage")
DEFAULT_ENDPOINT = "https://script.google.com/macros/s/AKfycbzIEgCDSycK8a0nOOkMZIqnfZzvXVxMYb2D0U4xbRVvF_zCsE82doYtcvSh2Egktq3_/exec"


def configured() -> bool:
    return bool((os.environ.get("FIOLE_USAGE_KEY") or "").strip())


def _post(payload: dict, endpoint: str) -> None:
    try:
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), method="POST", headers={"Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (fixed https URL)
            body = r.read(500).decode("utf-8", "replace")
            if '"ok":true' not in body.replace(" ", ""):
                log.warning("tools-usage refused the sign-in report: %s", body[:200])
    except Exception as e:  # never break a sign-in
        log.warning("tools-usage report failed: %s", str(e)[:200])


def report_login(email: str, wait: bool = False) -> bool:
    """Returns False when not configured (nothing sent)."""
    key = (os.environ.get("FIOLE_USAGE_KEY") or "").strip()
    if not key:
        return False
    endpoint = (os.environ.get("FIOLE_USAGE_ENDPOINT") or DEFAULT_ENDPOINT).strip()
    payload = {"tool": "fiole", "event": "login", "email": email, "key": key}
    t = threading.Thread(target=_post, args=(payload, endpoint), daemon=True)
    t.start()
    if wait:
        t.join(20)
    return True
