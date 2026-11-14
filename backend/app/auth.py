from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from .config import SESSION_DAYS
from .db import get_db
from .models import AuthSession, User

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, dk_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(dk, base64.b64decode(dk_b64))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token_hash=_hash_token(token),
            user_id=user.id,
            expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
        )
    )
    db.commit()
    return token


def delete_session(db: Session, token: str) -> None:
    s = db.get(AuthSession, _hash_token(token))
    if s:
        db.delete(s)
        db.commit()


def current_user(
    db: Session = Depends(get_db),
    fiole_session: str | None = Cookie(default=None),
    authorization: str | None = Header(default=None),
) -> User:
    token = fiole_session
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(401, detail={"code": "not_authenticated"})
    s = db.get(AuthSession, _hash_token(token))
    if s is None or s.expires_at < datetime.now(timezone.utc):
        raise HTTPException(401, detail={"code": "not_authenticated"})
    user = db.get(User, s.user_id)
    if user is None:
        raise HTTPException(401, detail={"code": "not_authenticated"})
    return user
