"""Dashboard folders. Personal: each user files the projects they are a member of in their own folder tree."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import current_user
from .db import get_db
from .models import Folder, Project, ProjectFolder, ProjectMember, User

router = APIRouter()


class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = None  # only applied when sent (null = top level)


class PlaceIn(BaseModel):
    folder_id: str | None = None  # null = top level


def _mine(db: Session, user: User, folder_id: str | None) -> Folder | None:
    if folder_id is None:
        return None
    f = db.get(Folder, folder_id)
    if f is None or f.user_id != user.id:
        raise HTTPException(404, detail={"code": "folder_not_found"})
    return f


def _out(f: Folder, n_projects: int = 0, n_folders: int = 0) -> dict:
    return {"id": f.id, "name": f.name, "parent_id": f.parent_id, "n_projects": n_projects, "n_folders": n_folders,
            "created_at": f.created_at.isoformat()}


def folder_of(db: Session, user: User, project_ids: list[str]) -> dict[str, str]:
    if not project_ids:
        return {}
    rows = db.execute(select(ProjectFolder.project_id, ProjectFolder.folder_id)
                      .where(ProjectFolder.user_id == user.id, ProjectFolder.project_id.in_(project_ids))).all()
    return {p: f for p, f in rows}


def place(db: Session, user: User, project_id: str, folder_id: str | None) -> None:
    """File a project (no commit). folder_id None: back to the top level."""
    _mine(db, user, folder_id)
    row = db.get(ProjectFolder, (user.id, project_id))
    if folder_id is None:
        if row is not None:
            db.delete(row)
    elif row is None:
        db.add(ProjectFolder(user_id=user.id, project_id=project_id, folder_id=folder_id))
    else:
        row.folder_id = folder_id


@router.get("/api/folders")
def list_folders(user: User = Depends(current_user), db: Session = Depends(get_db)):
    folders = db.execute(select(Folder).where(Folder.user_id == user.id).order_by(func.lower(Folder.name))).scalars().all()
    counts = dict(db.execute(
        select(ProjectFolder.folder_id, func.count())
        .join(Project, Project.id == ProjectFolder.project_id)
        .join(ProjectMember, (ProjectMember.project_id == Project.id) & (ProjectMember.user_id == user.id))
        .where(ProjectFolder.user_id == user.id, Project.deleted_at.is_(None))
        .group_by(ProjectFolder.folder_id)
    ).all())
    subs: dict[str, int] = {}
    for f in folders:
        if f.parent_id:
            subs[f.parent_id] = subs.get(f.parent_id, 0) + 1
    return [_out(f, counts.get(f.id, 0), subs.get(f.id, 0)) for f in folders]


@router.post("/api/folders")
def create_folder(body: FolderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _mine(db, user, body.parent_id)
    f = Folder(user_id=user.id, name=body.name.strip(), parent_id=body.parent_id)
    db.add(f)
    db.commit()
    db.refresh(f)
    return _out(f)


@router.patch("/api/folders/{folder_id}")
def patch_folder(folder_id: str, body: FolderPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    f = _mine(db, user, folder_id)
    if body.name is not None:
        f.name = body.name.strip()
    if "parent_id" in body.model_fields_set:
        target = _mine(db, user, body.parent_id)
        # a folder cannot go inside itself or one of its own subfolders
        while target is not None:
            if target.id == f.id:
                raise HTTPException(422, detail={"code": "folder_cycle", "message": {
                    "fr": "Un dossier ne peut pas être rangé dans lui-même.", "en": "A folder cannot go inside itself."}})
            target = db.get(Folder, target.parent_id) if target.parent_id else None
        f.parent_id = body.parent_id
    db.commit()
    return _out(f)


@router.delete("/api/folders/{folder_id}")
def delete_folder(folder_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Deletes the folder only: its projects and subfolders move up to where the folder was. Nothing is lost."""
    f = _mine(db, user, folder_id)
    parent = f.parent_id
    for sub in db.execute(select(Folder).where(Folder.parent_id == f.id)).scalars():
        sub.parent_id = parent
    for row in db.execute(select(ProjectFolder).where(ProjectFolder.folder_id == f.id)).scalars():
        if parent is None:
            db.delete(row)
        else:
            row.folder_id = parent
    db.flush()
    db.delete(f)
    db.commit()
    return {"ok": True, "parent_id": parent}


@router.put("/api/projects/{project_id}/folder")
def move_project(project_id: str, body: PlaceIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)).scalar_one_or_none()
    p = db.get(Project, project_id)
    if m is None or p is None:
        raise HTTPException(404, detail={"code": "project_not_found"})
    place(db, user, project_id, body.folder_id)
    db.commit()
    return {"ok": True, "folder_id": body.folder_id}
