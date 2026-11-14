"""One account for all of gab's tools: Régine and GeneCloud (static pages of https://gabkrouk.github.io) sign their
users in with their Fiole account.

    tool page ──"Se connecter"──▶ fiole/connect?tool=regine&return=<page URL>
                                   (sign in or create an account on Fiole, then "Continuer")
    POST /api/sso/authorize  ──▶  redirect to <page URL>#fiole_token=<token>
    tool page ──GET /api/sso/me?tool=regine (Authorization: Bearer <token>)──▶ {email, display_name}

The token is stored by the tool in localStorage (shared by every tool of the same site). It only proves who the user
is: it gives no access to Fiole's projects (it lives in its own table, not in auth_sessions).
Each sign-in, and at most one visit every 12 h per tool, is reported to tools-usage with the email and the tool name.

  FIOLE_SSO_ORIGINS  allowed return origins (comma separated), default https://gabkrouk.github.io
  FIOLE_SSO_TOOLS    allowed tool names (comma separated), default regine,genecloud
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from . import usage_report
from .auth import current_user
from .db import get_db
from .models import ToolAccount, ToolToken, User

router = APIRouter()

TOKEN_DAYS = int(os.environ.get("FIOLE_SSO_TOKEN_DAYS", "90"))
REPORT_EVERY = timedelta(hours=12)
TOOL_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def allowed_origins() -> set[str]:
    raw = os.environ.get("FIOLE_SSO_ORIGINS") or "https://gabkrouk.github.io"
    return {o.strip().rstrip("/") for o in raw.split(",") if o.strip()}


def allowed_tools() -> set[str]:
    raw = os.environ.get("FIOLE_SSO_TOOLS") or "regine,genecloud"
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


def check_tool(tool: str) -> str:
    tool = (tool or "").strip().lower()
    if not TOOL_RE.match(tool) or tool not in allowed_tools():
        raise HTTPException(400, detail={"code": "unknown_tool"})
    return tool


def check_return(url: str) -> str:
    """Only pages of the allowed sites (https, no user:password@), without their #fragment."""
    try:
        u = urlsplit((url or "").strip())
    except ValueError:
        raise HTTPException(400, detail={"code": "bad_return_url"})
    origin = f"{u.scheme}://{u.netloc}".lower()
    local = u.scheme == "http" and (u.hostname or "") in ("localhost", "127.0.0.1")   # only if listed (tests)
    if (u.scheme != "https" and not local) or "@" in u.netloc or origin not in allowed_origins():
        raise HTTPException(400, detail={"code": "bad_return_url"})
    return urlunsplit((u.scheme, u.netloc, u.path or "/", u.query, ""))


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _seen(db: Session, user: User, tool: str, signed_in: bool) -> None:
    """Records the use of a tool and reports it to tools-usage (every sign-in; visits at most every 12 h)."""
    now = _now()
    acc = db.get(ToolAccount, (user.id, tool))
    if acc is None:
        acc = ToolAccount(user_id=user.id, tool=tool, first_seen=now, last_seen=now)
        db.add(acc)
    acc.last_seen = now
    last = acc.last_reported_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    if signed_in or last is None or now - last >= REPORT_EVERY:
        if usage_report.report_login(user.email, tool=tool):
            acc.last_reported_at = now


# ------------------------------------------------------------------ CORS (only for the tools' pages, only /api/sso/)


async def cors_middleware(request: Request, call_next):
    """Adds CORS headers to /api/sso/ answers (errors included) for the allowed sites; no cookies are ever accepted."""
    response = await call_next(request)
    if request.url.path.startswith("/api/sso/"):
        origin = (request.headers.get("origin") or "").rstrip("/")
        if origin in allowed_origins():
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
    return response


@router.options("/api/sso/{path:path}", include_in_schema=False)
def preflight(path: str, request: Request):
    r = Response(status_code=204)
    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin in allowed_origins():
        r.headers["Access-Control-Allow-Origin"] = origin
        r.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        r.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
        r.headers["Access-Control-Max-Age"] = "86400"
    return r


# ------------------------------------------------------------------ endpoints


class AuthorizeIn(BaseModel):
    tool: str = Field(max_length=32)
    return_to: str = Field(max_length=2000)


@router.post("/api/sso/authorize")
def authorize(body: AuthorizeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Called by Fiole's own /connect page (session cookie, same site): issues a token for the tool."""
    tool = check_tool(body.tool)
    ret = check_return(body.return_to)
    token = secrets.token_urlsafe(32)
    db.add(ToolToken(token_hash=_hash(token), user_id=user.id, tool=tool, expires_at=_now() + timedelta(days=TOKEN_DAYS)))
    _seen(db, user, tool, signed_in=True)
    db.commit()
    return {"redirect": f"{ret}#fiole_token={token}", "tool": tool}


@router.get("/api/sso/check")
def check(tool: str, return_to: str):
    """Lets the /connect page refuse a bad link before asking anyone to sign in."""
    return {"tool": check_tool(tool), "return_to": check_return(return_to)}


def token_user(db: Session, authorization: str | None) -> tuple[ToolToken, User]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, detail={"code": "not_authenticated"})
    t = db.get(ToolToken, _hash(authorization[7:].strip()))
    if t is None:
        raise HTTPException(401, detail={"code": "not_authenticated"})
    exp = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
    if exp < _now():
        db.delete(t)
        db.commit()
        raise HTTPException(401, detail={"code": "not_authenticated"})
    u = db.get(User, t.user_id)
    if u is None:
        raise HTTPException(401, detail={"code": "not_authenticated"})
    return t, u


@router.get("/api/sso/me")
def me(tool: str, authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    """Who is signed in on a tool's page (and counts the visit for that tool)."""
    tool = check_tool(tool)
    _t, u = token_user(db, authorization)
    _seen(db, u, tool, signed_in=False)
    db.commit()
    return {"email": u.email, "display_name": u.display_name, "locale": u.locale}


@router.post("/api/sso/logout")
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    try:
        t, _u = token_user(db, authorization)
    except HTTPException:
        return {"ok": True}
    db.delete(t)
    db.commit()
    return {"ok": True}
