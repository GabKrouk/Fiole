"""Single authorization layer.

Every project-scoped endpoint calls `require()`. Phase 1 only creates owner
memberships, but the role → action table is already the phase-2 one, so adding
sharing means adding members, not touching checks scattered across endpoints.
"""
from __future__ import annotations

import enum

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GeneralAccess, Project, ProjectMember, Role, User


class Action(str, enum.Enum):
    VIEW = "view"
    EXPORT = "export"
    COMMENT = "comment"
    EDIT = "edit"  # data, analyses, figures
    MANAGE_SHARING = "manage_sharing"
    RENAME = "rename"
    DELETE = "delete"
    TRANSFER_OWNERSHIP = "transfer_ownership"


ROLE_ACTIONS: dict[Role, set[Action]] = {
    Role.viewer: {Action.VIEW, Action.EXPORT},
    Role.commenter: {Action.VIEW, Action.EXPORT, Action.COMMENT},
    Role.editor: {Action.VIEW, Action.EXPORT, Action.COMMENT, Action.EDIT, Action.RENAME},
    Role.owner: set(Action),
}

LINK_ROLE = {
    GeneralAccess.link_viewer: Role.viewer,
    GeneralAccess.link_commenter: Role.commenter,
    GeneralAccess.link_editor: Role.editor,
}


def effective_role(db: Session, user: User, project: Project) -> Role | None:
    member = db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id)
    ).scalar_one_or_none()
    if member is not None:
        return member.role
    # Phase 2: link access applies only to signed-in, identified users.
    return LINK_ROLE.get(project.general_access)


def allowed_actions(db: Session, user: User, project: Project) -> set[Action]:
    role = effective_role(db, user, project)
    if role is None:
        return set()
    actions = set(ROLE_ACTIONS[role])
    if role == Role.editor and project.editors_can_share:
        actions.add(Action.MANAGE_SHARING)
    return actions


def require(db: Session, user: User, project_id: str, action: Action) -> Project:
    project = db.get(Project, project_id)
    # 404 rather than 403 when the user has no access at all, so project ids do not leak.
    if project is None or project.deleted_at is not None:
        raise HTTPException(404, detail={"code": "project_not_found"})
    actions = allowed_actions(db, user, project)
    if not actions:
        raise HTTPException(404, detail={"code": "project_not_found"})
    if action not in actions:
        raise HTTPException(403, detail={"code": "forbidden", "action": action.value})
    return project
