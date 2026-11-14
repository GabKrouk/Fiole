"""Outgoing email through a real provider, chosen by environment variables.

  FIOLE_MAIL_FROM        sender, e.g. "Fiole <noreply@example.org>"   (required)
  then ONE provider:
  FIOLE_BREVO_API_KEY    Brevo (free tier; sender verified in Brevo)
  FIOLE_RESEND_API_KEY   Resend
  FIOLE_SMTP_HOST (+ FIOLE_SMTP_PORT, FIOLE_SMTP_USER, FIOLE_SMTP_PASSWORD)  any SMTP server

When nothing is configured, nothing is sent and the caller is told so ("not_configured"): a send is never faked.
"""
from __future__ import annotations

import json
import logging
import os
import re
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import parseaddr

log = logging.getLogger("fiole.mail")


def _env(k: str) -> str:
    return (os.environ.get(k) or "").strip()


def provider() -> str | None:
    if not _env("FIOLE_MAIL_FROM"):
        return None
    if _env("FIOLE_BREVO_API_KEY"):
        return "brevo"
    if _env("FIOLE_RESEND_API_KEY"):
        return "resend"
    if _env("FIOLE_SMTP_HOST"):
        return "smtp"
    return None


def configured() -> bool:
    return provider() is not None


def _post_json(url: str, payload: dict, headers: dict) -> None:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (fixed https URLs)
        if r.status >= 300:
            raise RuntimeError(f"HTTP {r.status}")


def send(to: str, subject: str, text: str) -> str:
    """Send one plain-text email. Returns "sent", "not_configured" or "failed"."""
    prov = provider()
    if prov is None:
        log.info("mail not sent (no provider configured): %s", subject)
        return "not_configured"
    sender = _env("FIOLE_MAIL_FROM")
    name, addr = parseaddr(sender)
    try:
        if prov == "brevo":
            _post_json("https://api.brevo.com/v3/smtp/email",
                       {"sender": {"email": addr, **({"name": name} if name else {})}, "to": [{"email": to}], "subject": subject, "textContent": text},
                       {"api-key": _env("FIOLE_BREVO_API_KEY")})
        elif prov == "resend":
            _post_json("https://api.resend.com/emails", {"from": sender, "to": [to], "subject": subject, "text": text},
                       {"Authorization": "Bearer " + _env("FIOLE_RESEND_API_KEY")})
        else:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = sender, to, subject
            msg.set_content(text)
            port = int(_env("FIOLE_SMTP_PORT") or 587)
            ctx = ssl.create_default_context()
            if port == 465:
                srv = smtplib.SMTP_SSL(_env("FIOLE_SMTP_HOST"), port, timeout=10, context=ctx)
            else:
                srv = smtplib.SMTP(_env("FIOLE_SMTP_HOST"), port, timeout=10)
                srv.starttls(context=ctx)
            with srv:
                if _env("FIOLE_SMTP_USER"):
                    srv.login(_env("FIOLE_SMTP_USER"), _env("FIOLE_SMTP_PASSWORD"))
                srv.send_message(msg)
        return "sent"
    except Exception as e:  # never break the request because a mail failed; report it instead
        log.warning("mail to provider %s failed: %s", prov, re.sub(r"\s+", " ", str(e))[:200])
        return "failed"
