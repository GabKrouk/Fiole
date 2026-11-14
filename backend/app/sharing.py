"""Project sharing (Google Docs style): people with roles, pending invitations by email, general access
(restricted / anyone with the link), access requests. Every check goes through authz."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import mailer
from .auth import current_user
from .authz import LINK_ROLE, Action, allowed_actions, effective_role, require
from .config import COOKIE_SECURE
from .db import get_db
from .models import (
    AccessRequest,
    AuditEvent,
    GeneralAccess,
    Project,
    ProjectInvite,
    ProjectLinkVisit,
    ProjectMember,
    Role,
    User,
)

router = APIRouter()

SHAREABLE_ROLES = ("editor", "viewer")  # commenter comes with comments
ROLE_LABEL = {"owner": ("propriétaire", "owner"), "editor": ("éditeur", "editor"), "viewer": ("lecteur", "viewer"), "commenter": ("commentateur", "commenter")}


def utcnow():
    return datetime.now(timezone.utc)


def audit(db: Session, user: User | None, project_id: str | None, action: str, target_type="", target_id="", **detail):
    db.add(AuditEvent(project_id=project_id, user_id=user.id if user else None, action=action, target_type=target_type, target_id=target_id, detail=detail))


def err(status: int, code: str, fr: str, en: str):
    raise HTTPException(status, detail={"code": code, "message": {"fr": fr, "en": en}})


def public_url(request: Request) -> str:
    base = (os.environ.get("FIOLE_PUBLIC_URL") or str(request.base_url)).rstrip("/")
    if COOKIE_SECURE and base.startswith("http://"):
        base = "https://" + base[7:]
    return base


def _member(db: Session, project_id: str, user_id: str) -> ProjectMember | None:
    return db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user_id)).scalar_one_or_none()


def _user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()


def _lang(u: User | None, fallback: str = "fr") -> str:
    return (u.locale if u else fallback) or "fr"


def notify(db: Session, to: str, lang: str, subject: tuple[str, str], body: tuple[str, str], project_id: str | None, kind: str) -> str:
    """Send a notification and record the outcome honestly (sent / not_configured / failed)."""
    i = 0 if lang == "fr" else 1
    status = mailer.send(to, subject[i], body[i])
    audit(db, None, project_id, f"email.{status}", "email", "", kind=kind)
    return status


def accept_invites(db: Session, user: User) -> int:
    """Turn invitations sent to this email into memberships (on sign-up / sign-in)."""
    invites = db.execute(select(ProjectInvite).where(ProjectInvite.email == user.email)).scalars().all()
    n = 0
    for inv in invites:
        p = db.get(Project, inv.project_id)
        if p and p.deleted_at is None and not _member(db, p.id, user.id):
            db.add(ProjectMember(project_id=p.id, user_id=user.id, role=inv.role, added_by=inv.invited_by))
            audit(db, user, p.id, "sharing.invite_accepted", "user", user.id, role=inv.role.value)
            n += 1
        db.delete(inv)
    return n


def sharing_state(db: Session, p: Project, user: User) -> dict:
    actions = allowed_actions(db, user, p)
    manage = Action.MANAGE_SHARING in actions
    members = db.execute(select(ProjectMember).where(ProjectMember.project_id == p.id)).scalars().all()
    order = {Role.owner: 0, Role.editor: 1, Role.commenter: 2, Role.viewer: 3}
    members = sorted(members, key=lambda m: (order[m.role], m.user.display_name.lower()))
    out = {
        "project_id": p.id,
        "general_access": p.general_access.value,
        "editors_can_share": p.editors_can_share,
        "can_manage": manage,
        "is_owner": p.owner_id == user.id,
        "my_role": (effective_role(db, user, p) or Role.viewer).value,
        "members": [{"user_id": m.user_id, "email": m.user.email, "display_name": m.user.display_name, "role": m.role.value, "you": m.user_id == user.id}
                    for m in members],
        "invites": [],
        "requests": [],
        "email_configured": mailer.configured(),
    }
    if manage:
        out["invites"] = [{"id": i.id, "email": i.email, "role": i.role.value, "created_at": i.created_at.isoformat()}
                          for i in db.execute(select(ProjectInvite).where(ProjectInvite.project_id == p.id).order_by(ProjectInvite.created_at)).scalars()]
        out["requests"] = [{"id": r.id, "user_id": r.user_id, "email": r.user.email, "display_name": r.user.display_name, "role": r.role.value,
                            "message": r.message, "created_at": r.created_at.isoformat()}
                           for r in db.execute(select(AccessRequest).where(AccessRequest.project_id == p.id, AccessRequest.status == "pending")
                                               .order_by(AccessRequest.created_at)).scalars()]
    return out


@router.get("/api/projects/{project_id}/sharing")
def get_sharing(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.VIEW)
    return sharing_state(db, p, user)


class AddPeopleIn(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=50)
    role: Literal["editor", "viewer"] = "viewer"
    notify: bool = True
    message: str = Field(default="", max_length=2000)


@router.post("/api/projects/{project_id}/members")
def add_people(project_id: str, body: AddPeopleIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.MANAGE_SHARING)
    from .main import EMAIL_RE  # same validation as sign-up

    role = Role(body.role)
    added, invited, updated, emails = [], [], [], []
    for raw in body.emails:
        email = raw.strip().lower()
        if not email:
            continue
        if not EMAIL_RE.match(email):
            err(422, "invalid_email", f"Adresse invalide : {raw}", f"Invalid address: {raw}")
        target = _user_by_email(db, email)
        if target:
            m = _member(db, p.id, target.id)
            if m and m.role == Role.owner:
                continue  # the owner keeps ownership
            if m:
                if m.role != role:
                    m.role = role
                    updated.append(email)
                continue
            db.add(ProjectMember(project_id=p.id, user_id=target.id, role=role, added_by=user.id))
            # a pending access request from this person is now answered
            for r in db.execute(select(AccessRequest).where(AccessRequest.project_id == p.id, AccessRequest.user_id == target.id, AccessRequest.status == "pending")).scalars():
                r.status, r.decided_at, r.decided_by = "approved", utcnow(), user.id
            added.append(email)
            emails.append((email, target))
        else:
            inv = db.execute(select(ProjectInvite).where(ProjectInvite.project_id == p.id, ProjectInvite.email == email)).scalar_one_or_none()
            if inv:
                inv.role = role
            else:
                db.add(ProjectInvite(project_id=p.id, email=email, role=role, invited_by=user.id))
            invited.append(email)
            emails.append((email, None))
        audit(db, user, p.id, "sharing.add", "email", "", email=email, role=role.value)
    db.commit()

    mail = {}
    if body.notify:
        url = f"{public_url(request)}/p/{p.id}"
        for email, target in emails:
            lang = _lang(target, user.locale)
            rl = ROLE_LABEL[role.value]
            note_fr = f"\n\nMessage de {user.display_name} :\n{body.message}\n" if body.message.strip() else ""
            note_en = f"\n\nMessage from {user.display_name}:\n{body.message}\n" if body.message.strip() else ""
            signup_fr = "" if target else "\n\nCréez votre compte Fiole (gratuit) avec cette adresse email pour y accéder."
            signup_en = "" if target else "\n\nCreate your free Fiole account with this email address to open it."
            mail[email] = notify(
                db, email, lang,
                (f"{user.display_name} a partagé « {p.name} » avec vous", f"{user.display_name} shared “{p.name}” with you"),
                (f"{user.display_name} ({user.email}) vous a donné accès au projet « {p.name} » en tant que {rl[0]}.{note_fr}{signup_fr}\n\nOuvrir : {url}\n\n— Fiole",
                 f"{user.display_name} ({user.email}) gave you {rl[1]} access to the project “{p.name}”.{note_en}{signup_en}\n\nOpen: {url}\n\n— Fiole"),
                p.id, "share")
        db.commit()
    return {"added": added, "invited": invited, "updated": updated, "email": mail, "email_configured": mailer.configured(),
            "sharing": sharing_state(db, p, user)}


class RoleIn(BaseModel):
    role: Literal["editor", "viewer"]


@router.patch("/api/projects/{project_id}/members/{user_id}")
def change_role(project_id: str, user_id: str, body: RoleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.MANAGE_SHARING)
    m = _member(db, p.id, user_id)
    if not m:
        err(404, "member_not_found", "Cette personne n’a pas accès.", "This person has no access.")
    if m.role == Role.owner:
        err(400, "owner_role_fixed", "Le rôle du propriétaire ne peut pas être modifié.", "The owner's role cannot be changed.")
    m.role = Role(body.role)
    audit(db, user, p.id, "sharing.role", "user", user_id, role=body.role)
    db.commit()
    return sharing_state(db, p, user)


@router.delete("/api/projects/{project_id}/members/{user_id}")
def remove_member(project_id: str, user_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Revoke someone's access (managers), or leave a project shared with me (anyone but the owner)."""
    p = require(db, user, project_id, Action.VIEW)
    if user_id != user.id and Action.MANAGE_SHARING not in allowed_actions(db, user, p):
        err(403, "forbidden", "Vous ne pouvez pas gérer le partage de ce projet.", "You cannot manage sharing for this project.")
    m = _member(db, p.id, user_id)
    if not m:
        err(404, "member_not_found", "Cette personne n’a pas accès.", "This person has no access.")
    if m.role == Role.owner:
        err(400, "owner_cannot_be_removed", "Le propriétaire ne peut pas être retiré.", "The owner cannot be removed.")
    db.delete(m)
    v = db.execute(select(ProjectLinkVisit).where(ProjectLinkVisit.project_id == p.id, ProjectLinkVisit.user_id == user_id)).scalar_one_or_none()
    if v:
        db.delete(v)
    audit(db, user, p.id, "sharing.remove" if user_id != user.id else "sharing.leave", "user", user_id)
    db.commit()
    return sharing_state(db, p, user) if user_id != user.id else {"ok": True}


class InviteRoleIn(BaseModel):
    role: Literal["editor", "viewer"]


@router.patch("/api/projects/{project_id}/invites/{invite_id}")
def change_invite(project_id: str, invite_id: int, body: InviteRoleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.MANAGE_SHARING)
    inv = db.get(ProjectInvite, invite_id)
    if not inv or inv.project_id != p.id:
        err(404, "invite_not_found", "Invitation introuvable.", "Invitation not found.")
    inv.role = Role(body.role)
    db.commit()
    return sharing_state(db, p, user)


@router.delete("/api/projects/{project_id}/invites/{invite_id}")
def delete_invite(project_id: str, invite_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.MANAGE_SHARING)
    inv = db.get(ProjectInvite, invite_id)
    if not inv or inv.project_id != p.id:
        err(404, "invite_not_found", "Invitation introuvable.", "Invitation not found.")
    db.delete(inv)
    audit(db, user, p.id, "sharing.invite_revoked", "email", "", email=inv.email)
    db.commit()
    return sharing_state(db, p, user)


class AccessIn(BaseModel):
    general_access: Literal["restricted", "link_viewer", "link_editor"] | None = None
    editors_can_share: bool | None = None


@router.put("/api/projects/{project_id}/access")
def set_access(project_id: str, body: AccessIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.MANAGE_SHARING)
    if body.general_access is not None:
        p.general_access = GeneralAccess(body.general_access)
        audit(db, user, p.id, "sharing.general_access", "project", p.id, value=body.general_access)
    if body.editors_can_share is not None:
        if p.owner_id != user.id:
            err(403, "owner_only", "Seul le propriétaire peut changer ce réglage.", "Only the owner can change this setting.")
        p.editors_can_share = body.editors_can_share
    db.commit()
    return sharing_state(db, p, user)


# ------------------------------------------------------------------ access requests


class RequestIn(BaseModel):
    message: str = Field(default="", max_length=2000)
    role: Literal["editor", "viewer"] = "viewer"


@router.post("/api/projects/{project_id}/access-requests")
def request_access(project_id: str, body: RequestIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = db.get(Project, project_id)
    if p is None or p.deleted_at is not None:
        err(404, "project_not_found", "Projet introuvable.", "Project not found.")
    if _member(db, p.id, user.id):
        err(409, "already_has_access", "Vous avez déjà accès à ce projet.", "You already have access to this project.")
    r = db.execute(select(AccessRequest).where(AccessRequest.project_id == p.id, AccessRequest.user_id == user.id)).scalar_one_or_none()
    if r is None:
        r = AccessRequest(project_id=p.id, user_id=user.id)
        db.add(r)
    r.role, r.message, r.status, r.created_at, r.decided_at, r.decided_by = Role(body.role), body.message, "pending", utcnow(), None, None
    audit(db, user, p.id, "sharing.request", "user", user.id, role=body.role)
    db.commit()
    # tell the owner (and editors allowed to share)
    managers = [m.user for m in db.execute(select(ProjectMember).where(ProjectMember.project_id == p.id)).scalars()
                if m.role == Role.owner or (m.role == Role.editor and p.editors_can_share)]
    url = f"{public_url(request)}/p/{p.id}?share=1"
    rl = ROLE_LABEL[body.role]
    note_fr = f"\n\nMessage :\n{body.message}\n" if body.message.strip() else ""
    note_en = f"\n\nMessage:\n{body.message}\n" if body.message.strip() else ""
    status = "not_configured"
    for mgr in managers:
        status = notify(db, mgr.email, _lang(mgr),
                        (f"Demande d’accès à « {p.name} »", f"Access request for “{p.name}”"),
                        (f"{user.display_name} ({user.email}) demande l’accès {rl[0]} au projet « {p.name} ».{note_fr}\n\nRépondre : {url}\n\n— Fiole",
                         f"{user.display_name} ({user.email}) is asking for {rl[1]} access to the project “{p.name}”.{note_en}\n\nRespond: {url}\n\n— Fiole"),
                        p.id, "access_request")
    db.commit()
    return {"ok": True, "status": "pending", "email": status}


class DecideIn(BaseModel):
    role: Literal["editor", "viewer"] | None = None


def _decide(db: Session, user: User, project_id: str, request_id: int, approve: bool, role: str | None, request: Request) -> dict:
    p = require(db, user, project_id, Action.MANAGE_SHARING)
    r = db.get(AccessRequest, request_id)
    if not r or r.project_id != p.id or r.status != "pending":
        err(404, "request_not_found", "Demande introuvable ou déjà traitée.", "Request not found or already handled.")
    r.status, r.decided_at, r.decided_by = ("approved" if approve else "denied"), utcnow(), user.id
    if approve and not _member(db, p.id, r.user_id):
        db.add(ProjectMember(project_id=p.id, user_id=r.user_id, role=Role(role) if role else r.role, added_by=user.id))
    audit(db, user, p.id, "sharing.request_" + r.status, "user", r.user_id)
    db.commit()
    requester = db.get(User, r.user_id)
    url = f"{public_url(request)}/p/{p.id}"
    if approve:
        notify(db, requester.email, _lang(requester),
               (f"Accès accordé à « {p.name} »", f"Access granted to “{p.name}”"),
               (f"{user.display_name} vous a donné accès au projet « {p.name} ».\n\nOuvrir : {url}\n\n— Fiole",
                f"{user.display_name} gave you access to the project “{p.name}”.\n\nOpen: {url}\n\n— Fiole"),
               p.id, "access_granted")
        db.commit()
    return sharing_state(db, p, user)


@router.post("/api/projects/{project_id}/access-requests/{request_id}/approve")
def approve_request(project_id: str, request_id: int, body: DecideIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _decide(db, user, project_id, request_id, True, body.role, request)


@router.post("/api/projects/{project_id}/access-requests/{request_id}/deny")
def deny_request(project_id: str, request_id: int, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _decide(db, user, project_id, request_id, False, None, request)


@router.get("/api/projects/{project_id}/access-requests/mine")
def my_request(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = db.execute(select(AccessRequest).where(AccessRequest.project_id == project_id, AccessRequest.user_id == user.id)).scalar_one_or_none()
    return {"status": r.status if r else None}


def record_link_visit(db: Session, p: Project, user: User) -> None:
    if p.general_access in LINK_ROLE and not _member(db, p.id, user.id):
        v = db.execute(select(ProjectLinkVisit).where(ProjectLinkVisit.project_id == p.id, ProjectLinkVisit.user_id == user.id)).scalar_one_or_none()
        if v:
            v.last_opened_at = utcnow()
        else:
            db.add(ProjectLinkVisit(project_id=p.id, user_id=user.id, last_opened_at=utcnow()))
