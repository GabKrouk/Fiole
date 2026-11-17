"""Fiole HTTP API."""
from __future__ import annotations

import csv
from contextlib import asynccontextmanager
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import importers
from .auth import create_session, current_user, delete_session, hash_password, verify_password
from .authz import LINK_ROLE, Action, allowed_actions, effective_role, require
from .config import COOKIE_SECURE, MAX_UPLOAD_BYTES, SESSION_COOKIE, SESSION_DAYS
from .db import Base, engine, get_db
from .figures.render import FigureError, render_figure, to_pdf, to_png
from .figures import layout as layout_lib
from .models import (
    Analysis,
    AnalysisResult,
    AuditEvent,
    Dataset,
    DatasetVersion,
    Figure,
    Layout,
    Project,
    Trash,
    ProjectMember,
    RawUpload,
    FigureStyle,
    FigureStyleShare,
    ProjectFolder,
    GeneralAccess,
    ProjectLinkVisit,
    ADDED_COLUMNS,
    Role,
    StyleVisibility,
    User,
)
from . import mailer
from . import usage_report
from . import sharing
from . import sso
from . import folders as folders_lib
from . import styles as style_lib
from .stats import ENGINE, catalog, run_analysis

@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(engine)
    add_missing_columns()
    yield


def add_missing_columns():
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text
    with engine.begin() as conn:
        for table, col, ddl in ADDED_COLUMNS:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}'))


app = FastAPI(title="Fiole API", version="0.1.0", lifespan=lifespan)
app.include_router(sharing.router)
app.include_router(sso.router)
app.include_router(folders_lib.router)
app.middleware("http")(sso.cors_middleware)


def utcnow():
    return datetime.now(timezone.utc)


def audit(db: Session, user: User | None, project_id: str | None, action: str, target_type="", target_id="", **detail):
    db.add(AuditEvent(project_id=project_id, user_id=user.id if user else None, action=action, target_type=target_type, target_id=target_id, detail=detail))


def conflict(entity: str, current_version: int):
    raise HTTPException(409, detail={"code": "version_conflict", "entity": entity, "current_version": current_version})


# ------------------------------------------------------------------ auth


class RegisterIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    locale: Literal["fr", "en"] = "fr"


class LoginIn(BaseModel):
    email: str
    password: str


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "locale": u.locale}


def signed_in(db: Session, u: User) -> None:
    u.last_login_at = utcnow()
    u.login_count = (u.login_count or 0) + 1
    sharing.accept_invites(db, u)
    usage_report.report_login(u.email)


def _set_cookie(resp: Response, token: str):
    resp.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=COOKIE_SECURE, max_age=SESSION_DAYS * 86400, path="/")


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.post("/api/auth/register")
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, detail={"code": "invalid_email"})
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise HTTPException(409, detail={"code": "email_taken"})
    u = User(email=email, display_name=body.display_name.strip(), password_hash=hash_password(body.password), locale=body.locale)
    db.add(u)
    db.flush()
    signed_in(db, u)
    db.commit()
    _set_cookie(response, create_session(db, u))
    return user_out(u)


@app.post("/api/auth/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    u = db.execute(select(User).where(User.email == body.email.strip().lower())).scalar_one_or_none()
    if u is None or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, detail={"code": "bad_credentials"})
    signed_in(db, u)
    db.commit()
    _set_cookie(response, create_session(db, u))
    return user_out(u)


@app.post("/api/auth/logout")
def logout(response: Response, db: Session = Depends(get_db), fiole_session: str | None = Cookie(default=None)):
    if fiole_session:
        delete_session(db, fiole_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


class MeIn(BaseModel):
    locale: Literal["fr", "en"] | None = None
    display_name: str | None = None


@app.patch("/api/auth/me")
def update_me(body: MeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    u = db.get(User, user.id)
    if body.locale:
        u.locale = body.locale
    if body.display_name:
        u.display_name = body.display_name.strip()
    db.commit()
    return user_out(u)


# ------------------------------------------------------------------ projects


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    folder_id: str | None = None  # created inside this dashboard folder of mine


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    version: int


def project_summary(db: Session, p: Project, user: User, member: ProjectMember | None = None, opened_at=None) -> dict:
    role = member.role.value if member else (LINK_ROLE[p.general_access].value if p.general_access in LINK_ROLE else None)
    opened = member.last_opened_at if member else opened_at
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "owner": {"id": p.owner.id, "display_name": p.owner.display_name, "email": p.owner.email},
        "role": role,
        "via_link": member is None,
        "version": p.version,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
        "last_opened_at": opened.isoformat() if opened else None,
        "url": f"/p/{p.id}",
    }


@app.get("/api/projects")
def list_projects(scope: Literal["mine", "shared", "recent", "trash"] = "mine", user: User = Depends(current_user), db: Session = Depends(get_db)):
    if scope == "trash":
        rows = db.execute(
            select(Project, ProjectMember).join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == user.id, Project.owner_id == user.id, Project.deleted_at.is_not(None))
            .order_by(Project.deleted_at.desc())
        ).all()
        return [{**project_summary(db, p, user, m), "deleted_at": p.deleted_at.isoformat()} for p, m in rows]
    q = (
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id, Project.deleted_at.is_(None))
    )
    if scope == "mine":
        # mine + the projects shared with me that I filed in one of my folders
        filed = select(ProjectFolder.project_id).where(ProjectFolder.user_id == user.id)
        q = q.where(or_(Project.owner_id == user.id, Project.id.in_(filed))).order_by(Project.updated_at.desc())
    elif scope == "shared":
        q = q.where(Project.owner_id != user.id).order_by(Project.updated_at.desc())
    else:
        q = q.where(ProjectMember.last_opened_at.is_not(None)).order_by(ProjectMember.last_opened_at.desc()).limit(20)
    out = [project_summary(db, p, user, m) for p, m in db.execute(q).all()]
    if scope != "mine":
        # projects opened through "anyone with the link" (listed while the link stays open)
        visits = db.execute(
            select(Project, ProjectLinkVisit).join(ProjectLinkVisit, ProjectLinkVisit.project_id == Project.id)
            .where(ProjectLinkVisit.user_id == user.id, Project.deleted_at.is_(None), Project.general_access != GeneralAccess.restricted)
        ).all()
        seen = {o["id"] for o in out}
        out += [project_summary(db, p, user, None, v.last_opened_at) for p, v in visits if p.id not in seen]
        key = "updated_at" if scope == "shared" else "last_opened_at"
        out.sort(key=lambda o: o[key] or "", reverse=True)
        out = out[:20] if scope == "recent" else out
    where = folders_lib.folder_of(db, user, [o["id"] for o in out])
    for o in out:
        o["folder_id"] = where.get(o["id"])
    return out


@app.post("/api/projects")
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = Project(name=body.name.strip(), description=body.description, owner_id=user.id)
    db.add(p)
    db.flush()
    m = ProjectMember(project_id=p.id, user_id=user.id, role=Role.owner, added_by=user.id, last_opened_at=utcnow())
    db.add(m)
    folders_lib.place(db, user, p.id, body.folder_id)
    audit(db, user, p.id, "project.create", "project", p.id)
    db.commit()
    db.refresh(p)
    return {**project_summary(db, p, user, m), "folder_id": body.folder_id}


def _analysis_out(a: Analysis, ds_version: int, last: AnalysisResult | None) -> dict:
    stale = None
    if last is not None:
        stale = last.dataset_version != ds_version or last.analysis_version != a.version
    return {
        "id": a.id,
        "name": a.name,
        "kind": a.kind,
        "dataset_id": a.dataset_id,
        "config": a.config,
        "version": a.version,
        "updated_at": a.updated_at.isoformat(),
        "last_result": (
            {"id": last.id, "dataset_version": last.dataset_version, "analysis_version": last.analysis_version,
             "created_at": last.created_at.isoformat(), "result": last.result, "engine_version": last.engine_version}
            if last else None
        ),
        "stale": stale,
        "status": "never_run" if last is None else ("stale" if stale else "up_to_date"),
    }


def _last_result(db: Session, analysis_id: str) -> AnalysisResult | None:
    return db.execute(
        select(AnalysisResult).where(AnalysisResult.analysis_id == analysis_id).order_by(AnalysisResult.id.desc()).limit(1)
    ).scalar_one_or_none()


def _figure_out(db: Session, f: Figure, analyses_by_id: dict[str, dict]) -> dict:
    refs = [a.get("analysis_id") for a in (f.config.get("annotations") or []) if a.get("type") == "pvalue"]
    stale = any((analyses_by_id.get(r) or {}).get("status") != "up_to_date" for r in refs)
    return {"id": f.id, "name": f.name, "kind": f.kind, "dataset_id": f.dataset_id, "config": f.config, "version": f.version,
            "updated_at": f.updated_at.isoformat(), "stale": stale, "linked_analyses": refs}


def _dataset_meta(d: Dataset) -> dict:
    c = d.content
    return {"id": d.id, "name": d.name, "version": d.version, "updated_at": d.updated_at.isoformat(),
            "n_rows": len(c.get("rows", [])), "n_columns": len(c.get("columns", [])), "n_excluded": len(c.get("exclusions", []))}


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.VIEW)
    m = db.execute(select(ProjectMember).where(ProjectMember.project_id == p.id, ProjectMember.user_id == user.id)).scalar_one_or_none()
    if m:
        m.last_opened_at = utcnow()
    else:
        sharing.record_link_visit(db, p, user)
    db.commit()
    datasets = db.execute(select(Dataset).where(Dataset.project_id == p.id).order_by(Dataset.created_at)).scalars().all()
    ds_versions = {d.id: d.version for d in datasets}
    analyses = db.execute(select(Analysis).where(Analysis.project_id == p.id).order_by(Analysis.created_at)).scalars().all()
    a_out = [_analysis_out(a, ds_versions.get(a.dataset_id, 0), _last_result(db, a.id)) for a in analyses]
    by_id = {a["id"]: a for a in a_out}
    figures = db.execute(select(Figure).where(Figure.project_id == p.id).order_by(Figure.created_at)).scalars().all()
    return {
        **project_summary(db, p, user, m),
        "folder_id": folders_lib.folder_of(db, user, [p.id]).get(p.id),
        "permissions": sorted(a.value for a in allowed_actions(db, user, p)),
        "datasets": [_dataset_meta(d) for d in datasets],
        "analyses": a_out,
        "figures": [_figure_out(db, f, by_id) for f in figures],
        "layouts": [_layout_out(x) for x in db.execute(select(Layout).where(Layout.project_id == p.id).order_by(Layout.created_at)).scalars()],
    }


@app.patch("/api/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.RENAME)
    if body.version != p.version:
        conflict("project", p.version)
    if body.name is not None:
        p.name = body.name.strip()
    if body.description is not None:
        p.description = body.description
    p.version += 1
    p.updated_at = utcnow()
    audit(db, user, p.id, "project.update", "project", p.id)
    db.commit()
    return {"id": p.id, "name": p.name, "description": p.description, "version": p.version}


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.DELETE)
    p.deleted_at = utcnow()  # soft delete; recoverable
    audit(db, user, p.id, "project.delete", "project", p.id)
    db.commit()
    return {"ok": True}


def _own_trashed(db: Session, user: User, project_id: str) -> Project:
    p = db.get(Project, project_id)
    if p is None or p.owner_id != user.id or p.deleted_at is None:
        raise HTTPException(404, detail={"code": "project_not_found"})
    return p


@app.post("/api/projects/{project_id}/restore")
def restore_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Take a project out of the trash."""
    p = _own_trashed(db, user, project_id)
    p.deleted_at = None
    audit(db, user, p.id, "project.restore", "project", p.id)
    db.commit()
    return {"ok": True, "url": f"/p/{p.id}"}


@app.delete("/api/projects/{project_id}/permanent")
def purge_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Delete for good a project that is already in the trash (owner only)."""
    p = _own_trashed(db, user, project_id)
    audit(db, user, None, "project.purge", "project", p.id, name=p.name)
    db.delete(p)
    db.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/duplicate")
def duplicate_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A full copy (data, analyses, figures, layouts) owned by me, not shared with anyone."""
    p = require(db, user, project_id, Action.EXPORT)
    doc = _archive_doc(db, p, styles_of=None, uploads=None)
    doc["project"]["name"] = (f"{p.name} (copie)" if user.locale == "fr" else f"{p.name} (copy)")[:300]
    # the copy goes next to the original in my folders
    return _import_doc(db, user, doc, "project.duplicate", folders_lib.folder_of(db, user, [p.id]).get(p.id))


def touch(p: Project):
    p.updated_at = utcnow()


# ------------------------------------------------------------------ import


@app.post("/api/projects/{project_id}/import/preview")
async def import_preview(
    project_id: str,
    request: Request,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    # Parsed by hand: the default form limit (1 MB per field) is far too small for a pasted table.
    form = await request.form(max_part_size=MAX_UPLOAD_BYTES, max_files=2, max_fields=20)
    file = form.get("file")
    file = file if hasattr(file, "read") else None
    get = lambda k: (v if isinstance(v := form.get(k), str) else None)  # noqa: E731
    text, delimiter, decimal, header, sheet = get("text"), get("delimiter"), get("decimal"), get("header"), get("sheet")
    require(db, user, project_id, Action.EDIT)
    hdr = None if header in (None, "", "auto") else header in ("1", "true", "yes")
    delim = {"tab": "\t", "\\t": "\t"}.get(delimiter or "", delimiter) or None
    dec = decimal or None
    upload_id = None
    if file is not None:
        data = await file.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, detail={"code": "file_too_large"})
        name = file.filename or "upload"
        up = RawUpload(project_id=project_id, filename=name, content_type=file.content_type or "", content=data, uploaded_by=user.id)
        db.add(up)
        db.commit()
        upload_id = up.id
        if name.lower().endswith((".xlsx", ".xlsm")):
            try:
                table = importers.parse_excel(data, sheet=sheet, header=hdr, decimal=dec)
            except Exception as e:  # corrupted file etc.
                raise HTTPException(422, detail={"code": "excel_unreadable", "message": str(e)[:200]})
            kind = "excel"
        elif name.lower().endswith(".xls"):
            raise HTTPException(422, detail={"code": "xls_unsupported"})
        else:
            table = importers.parse_text(importers.decode_bytes(data), delimiter=delim, decimal=dec, header=hdr)
            kind = "text"
    elif text is not None:
        table = importers.parse_text(text, delimiter=delim, decimal=dec, header=hdr)
        kind = "paste"
    else:
        raise HTTPException(422, detail={"code": "nothing_to_import"})
    table["upload_id"] = upload_id
    table["source_kind"] = kind
    return table


@app.post("/api/projects/{project_id}/uploads/{upload_id}/reparse")
def reparse_upload(project_id: str, upload_id: str, body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Re-run the preview on an already uploaded file with other options (sheet, delimiter, decimal, header)."""
    require(db, user, project_id, Action.EDIT)
    up = db.get(RawUpload, upload_id)
    if up is None or up.project_id != project_id:
        raise HTTPException(404, detail={"code": "upload_not_found"})
    hdr = body.get("header")
    hdr = None if hdr in (None, "auto") else bool(hdr)
    if up.filename.lower().endswith((".xlsx", ".xlsm")):
        table = importers.parse_excel(up.content, sheet=body.get("sheet"), header=hdr, decimal=body.get("decimal") or None)
    else:
        delim = {"tab": "\t"}.get(body.get("delimiter") or "", body.get("delimiter")) or None
        table = importers.parse_text(importers.decode_bytes(up.content), delimiter=delim, decimal=body.get("decimal") or None, header=hdr)
    table["upload_id"] = upload_id
    return table


# ------------------------------------------------------------------ datasets


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    content: dict
    upload_id: str | None = None


class DatasetUpdate(BaseModel):
    content: dict | None = None
    name: str | None = None
    version: int
    change_note: str = ""


def validate_content(content: dict) -> dict:
    cols = content.get("columns")
    rows = content.get("rows")
    if not isinstance(cols, list) or not isinstance(rows, list):
        raise HTTPException(422, detail={"code": "invalid_table"})
    ids = set()
    for c in cols:
        if not isinstance(c, dict) or not c.get("id") or c["id"] in ids:
            raise HTTPException(422, detail={"code": "invalid_column"})
        ids.add(c["id"])
        c.setdefault("type", "numeric")
        c.setdefault("name", c["id"])
    row_ids = set()
    for r in rows:
        if not isinstance(r, dict) or not r.get("id") or r["id"] in row_ids:
            raise HTTPException(422, detail={"code": "invalid_row"})
        row_ids.add(r["id"])
        cells = r.get("cells") or {}
        clean_cells = {}
        for k, v in cells.items():
            if k not in ids:
                continue
            if v is not None and not isinstance(v, (int, float, str)):
                raise HTTPException(422, detail={"code": "invalid_cell"})
            if isinstance(v, float) and v != v:  # NaN → missing, never 0
                v = None
            clean_cells[k] = v
        r["cells"] = clean_cells
    content.setdefault("design", {"layout": "wide"})
    content["exclusions"] = [e for e in content.get("exclusions", []) if e.get("row_id") in row_ids]
    content.setdefault("transformations", [])
    return content


@app.post("/api/projects/{project_id}/datasets")
def create_dataset(project_id: str, body: DatasetIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    content = validate_content(body.content)
    d = Dataset(project_id=p.id, name=body.name.strip(), content=content, version=1, updated_by=user.id, source_upload_id=body.upload_id)
    db.add(d)
    db.flush()
    db.add(DatasetVersion(dataset_id=d.id, version=1, content=content, change_note="created", created_by=user.id))
    audit(db, user, p.id, "dataset.create", "dataset", d.id)
    touch(p)
    db.commit()
    return {**_dataset_meta(d), "content": d.content}


def _get_dataset(db: Session, project_id: str, dataset_id: str) -> Dataset:
    d = db.get(Dataset, dataset_id)
    if d is None or d.project_id != project_id:
        raise HTTPException(404, detail={"code": "dataset_not_found"})
    return d


@app.get("/api/projects/{project_id}/datasets/{dataset_id}")
def get_dataset(project_id: str, dataset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(db, user, project_id, Action.VIEW)
    d = _get_dataset(db, project_id, dataset_id)
    return {**_dataset_meta(d), "content": d.content}


@app.put("/api/projects/{project_id}/datasets/{dataset_id}")
def update_dataset(project_id: str, dataset_id: str, body: DatasetUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    d = db.execute(select(Dataset).where(Dataset.id == dataset_id, Dataset.project_id == project_id).with_for_update()).scalar_one_or_none()
    if d is None:
        raise HTTPException(404, detail={"code": "dataset_not_found"})
    if body.version != d.version:
        conflict("dataset", d.version)
    if body.name is not None:
        d.name = body.name.strip() or d.name
    if body.content is not None:
        d.content = validate_content(body.content)
    d.version += 1
    d.updated_at = utcnow()
    d.updated_by = user.id
    db.add(DatasetVersion(dataset_id=d.id, version=d.version, content=d.content, change_note=body.change_note[:500], created_by=user.id))
    audit(db, user, p.id, "dataset.update", "dataset", d.id, version=d.version, note=body.change_note[:200])
    db.flush()
    if body.content is not None:
        _refresh_dataset_analyses(db, user, d)
    touch(p)
    db.commit()
    return _dataset_meta(d)


@app.delete("/api/projects/{project_id}/datasets/{dataset_id}")
def delete_dataset(project_id: str, dataset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    d = _get_dataset(db, project_id, dataset_id)
    db.delete(d)
    audit(db, user, p.id, "dataset.delete", "dataset", dataset_id)
    db.commit()
    return {"ok": True}


@app.get("/api/projects/{project_id}/datasets/{dataset_id}/versions")
def dataset_versions(project_id: str, dataset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(db, user, project_id, Action.VIEW)
    _get_dataset(db, project_id, dataset_id)
    vs = db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id).order_by(DatasetVersion.version.desc())).scalars().all()
    return [{"version": v.version, "change_note": v.change_note, "created_at": v.created_at.isoformat()} for v in vs]


def _cell_csv(v: Any, decimal: str) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        s = repr(v) if not v.is_integer() else str(int(v))
        return s.replace(".", ",") if decimal == "," else s
    return str(v)


@app.get("/api/projects/{project_id}/datasets/{dataset_id}/export.csv")
def export_dataset_csv(project_id: str, dataset_id: str, decimal: Literal[".", ","] = ".", user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(db, user, project_id, Action.EXPORT)
    d = _get_dataset(db, project_id, dataset_id)
    c = d.content
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if decimal == "," else ",")
    excl = {e["row_id"]: e.get("reason", "") for e in c.get("exclusions", [])}
    w.writerow([col["name"] for col in c["columns"]] + ["_excluded"])
    for r in c["rows"]:
        w.writerow([_cell_csv(r["cells"].get(col["id"]), decimal) for col in c["columns"]] + [excl.get(r["id"], "") if r["id"] in excl else ""])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": disposition(_safe(d.name) + f".csv")})


def disposition(filename: str) -> str:
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode() or "export"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _safe(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", name).strip() or "export"


# ------------------------------------------------------------------ analyses


@app.get("/api/analysis-types")
def analysis_types():
    return catalog()


class AnalysisIn(BaseModel):
    dataset_id: str
    kind: str
    name: str = Field(min_length=1, max_length=300)
    config: dict = {}


class AnalysisUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    version: int


@app.post("/api/projects/{project_id}/analyses")
def create_analysis(project_id: str, body: AnalysisIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    d = _get_dataset(db, project_id, body.dataset_id)
    a = Analysis(project_id=p.id, dataset_id=d.id, kind=body.kind, name=body.name.strip(), config=body.config)
    db.add(a)
    audit(db, user, p.id, "analysis.create", "analysis", a.id)
    touch(p)
    db.commit()
    return _analysis_out(a, d.version, None)


def _get_analysis(db: Session, project_id: str, analysis_id: str) -> Analysis:
    a = db.get(Analysis, analysis_id)
    if a is None or a.project_id != project_id:
        raise HTTPException(404, detail={"code": "analysis_not_found"})
    return a


@app.put("/api/projects/{project_id}/analyses/{analysis_id}")
def update_analysis(project_id: str, analysis_id: str, body: AnalysisUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    a = _get_analysis(db, project_id, analysis_id)
    if body.version != a.version:
        conflict("analysis", a.version)
    if body.name is not None:
        a.name = body.name.strip() or a.name
    if body.config is not None:
        a.config = body.config
    a.version += 1
    a.updated_at = utcnow()
    touch(p)
    db.commit()
    d = db.get(Dataset, a.dataset_id)
    return _analysis_out(a, d.version, _last_result(db, a.id))


@app.post("/api/projects/{project_id}/analyses/{analysis_id}/run")
def run(project_id: str, analysis_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    a = _get_analysis(db, project_id, analysis_id)
    d = db.get(Dataset, a.dataset_id)
    result = run_analysis(a.kind, d.content, a.config)
    result["dataset"] = {"id": d.id, "name": d.name, "version": d.version}
    r = AnalysisResult(analysis_id=a.id, analysis_version=a.version, dataset_version=d.version, result=result, engine_version=ENGINE, created_by=user.id)
    db.add(r)
    audit(db, user, p.id, "analysis.run", "analysis", a.id, dataset_version=d.version)
    touch(p)
    db.commit()
    return _analysis_out(a, d.version, r)


@app.delete("/api/projects/{project_id}/analyses/{analysis_id}")
def delete_analysis(project_id: str, analysis_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    a = _get_analysis(db, project_id, analysis_id)
    db.delete(a)
    audit(db, user, p.id, "analysis.delete", "analysis", analysis_id)
    db.commit()
    return {"ok": True}


def result_rows(name: str, res: dict, lang: str = "en") -> list[list[str]]:
    """Flatten a result to (analysis, item, value) rows for CSV export."""
    rows = []
    L = lambda d: (d or {}).get(lang) if isinstance(d, dict) else d  # noqa: E731
    add = lambda k, v: rows.append([name, k, "" if v is None else (repr(v) if isinstance(v, float) else str(v))])  # noqa: E731
    add("test", L(res.get("title")))
    add("status", res.get("status"))
    for g in res.get("groups", []):
        for k in ("n", "mean", "sd", "sem", "median", "q1", "q3", "min", "max", "ci_low", "ci_high", "n_missing", "n_excluded"):
            if k in g:
                add(f"{g['name']}: {k}", g[k])
    t = res.get("test")
    if t:
        add("statistic " + t["statistic_name"], t.get("statistic"))
        add("df", t.get("df"))
        add("p", t.get("p"))
        add("p (display)", t.get("p_display"))
        add("alternative", t.get("alternative"))
        if t.get("method"):
            add("p-value method", t["method"])
    e = res.get("estimate")
    if e:
        add(L(e["name"]), e.get("value"))
        if e.get("ci"):
            add(f"{L(e['name'])} CI low", e["ci"][0])
            add(f"{L(e['name'])} CI high", e["ci"][1])
    es = res.get("effect_size")
    if es:
        add(L(es["name"]), es.get("value"))
        if es.get("ci"):
            add(f"{L(es['name'])} CI low", es["ci"][0])
            add(f"{L(es['name'])} CI high", es["ci"][1])
    for c in res.get("coefficients", []):
        for k in ("estimate", "se", "t", "df", "p"):
            add(f"{L(c['name'])}: {k}", c[k])
        add(f"{L(c['name'])}: CI low", c["ci"][0])
        add(f"{L(c['name'])}: CI high", c["ci"][1])
    if res.get("fit"):
        for k, v in res["fit"].items():
            add(k, v if not isinstance(v, list) else ";".join(map(str, v)))
    for w in res.get("warnings", []):
        add("warning", w.get(lang))
    for w in res.get("errors", []):
        add("error", w.get(lang))
    add("method", L(res.get("method_text")))
    add("engine", res.get("engine"))
    if res.get("dataset"):
        add("dataset version", res["dataset"].get("version"))
    return rows


@app.get("/api/projects/{project_id}/results.csv")
def export_results(project_id: str, lang: Literal["en", "fr"] = "en", user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EXPORT)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["analysis", "item", "value"])
    for a in db.execute(select(Analysis).where(Analysis.project_id == p.id).order_by(Analysis.created_at)).scalars():
        last = _last_result(db, a.id)
        if last:
            for row in result_rows(a.name, last.result, lang):
                w.writerow(row)
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": disposition(_safe(p.name) + f"-results.csv")})


# ------------------------------------------------------------------ figures


class FigureIn(BaseModel):
    dataset_id: str
    name: str = Field(min_length=1, max_length=300)
    config: dict = {}


class FigureUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    dataset_id: str | None = None
    version: int


class RenderIn(BaseModel):
    dataset_id: str
    config: dict
    lang: Literal["en", "fr"] = "en"


STAR_OPTS = ("mode", "control", "test", "correction")
STAR_DEFAULTS = {"mode": "all", "test": "welch", "correction": "holm"}


def _stars_config(content: dict, cfg: dict, base: dict | None) -> dict | None:
    """Pairwise-analysis settings the figure's automatic stars need right now: the groups shown,
    the Y variable, and the test options saved in the figure (falling back to the analysis's own)."""
    if (cfg.get("type") or "scatter_groups") not in GROUP_TYPES:
        return None
    from .data import DataError, design_for, extract_groups

    try:
        groups = extract_groups(content, design_for(content, cfg))
    except (DataError, KeyError, TypeError):
        return None
    order = cfg.get("group_order") or list(groups.keys())
    names = [n for n in order if n in groups]
    base = dict(base or {})
    opts = {**STAR_DEFAULTS,
            **{k: base[k] for k in STAR_OPTS if base.get(k) not in (None, "")},
            **{k: v for k, v in (cfg.get("stars") or {}).items() if k in STAR_OPTS and v not in (None, "")}}
    if opts.get("mode") != "control":
        opts.pop("control", None)
    elif opts.get("control") not in names:
        opts["control"] = names[0] if names else None
    return {**{k: v for k, v in base.items() if k not in STAR_OPTS}, **opts, "group_col": cfg.get("group_col") or None,
            "value_col": cfg.get("value_col") or None, "average_technical": bool(cfg.get("average_technical")), "groups": names}


_STARS_CACHE: dict[str, dict] = {}


def _pairwise_now(d: Dataset, config: dict) -> dict:
    """Stars for the preview, computed for the data and settings on screen (small in-process cache:
    previews re-render on every edit, and the statistics only change when data or settings do)."""
    key = json.dumps([d.id, d.version, config], sort_keys=True, default=str)
    hit = _STARS_CACHE.get(key)
    if hit is None:
        hit = run_analysis("pairwise", d.content, config)
        if len(_STARS_CACHE) > 200:
            _STARS_CACHE.clear()
        _STARS_CACHE[key] = hit
    return hit


def _is_stale(db: Session, a: Analysis, last: AnalysisResult | None) -> bool:
    if last is None:
        return True
    d = db.get(Dataset, a.dataset_id)
    return last.dataset_version != d.version or last.analysis_version != a.version


def _results_for(db: Session, project_id: str, cfg: dict, d: Dataset) -> dict:
    """Results behind the figure's p-value annotations. They are always current: automatic stars are
    computed for the data and settings on screen, and an analysis behind a p-value that is out of date
    is re-run in memory (the saved copy is refreshed when an editor saves)."""
    out = {}
    for ann in cfg.get("annotations") or []:
        aid = ann.get("analysis_id")
        if ann.get("type") != "pvalue" or aid in out:
            continue
        a = db.get(Analysis, aid) if aid else None
        if a is not None and a.project_id != project_id:
            a = None
        if ann.get("all"):
            desired = _stars_config(d.content, cfg, {**(a.config or {})} if a else None)
            if desired is None:
                continue
            last = _last_result(db, a.id) if a else None
            if a and last and a.dataset_id == d.id and {**a.config, "figure_id": None} == {**desired, "figure_id": None} and not _is_stale(db, a, last):
                res = last.result
            else:
                res = _pairwise_now(d, desired)
            out[aid] = {"result": res, "stale": False, "value_col": cfg.get("value_col"), "dataset_id": d.id}
            continue
        if a is None:
            continue
        last = _last_result(db, aid)
        if last is None:
            continue
        res = last.result
        if _is_stale(db, a, last):
            ad = db.get(Dataset, a.dataset_id)
            res = run_analysis(a.kind, ad.content, a.config)
            res["dataset"] = {"id": ad.id, "name": ad.name, "version": ad.version}
        out[aid] = {"result": res, "stale": False, "value_col": (a.config or {}).get("value_col"), "dataset_id": a.dataset_id}
    return out


def _ensure_run(db: Session, user: User, a: Analysis) -> None:
    """Save a fresh result if the last one is missing or out of date."""
    last = _last_result(db, a.id)
    if not _is_stale(db, a, last):
        return
    d = db.get(Dataset, a.dataset_id)
    res = run_analysis(a.kind, d.content, a.config)
    res["dataset"] = {"id": d.id, "name": d.name, "version": d.version}
    db.add(AnalysisResult(analysis_id=a.id, analysis_version=a.version, dataset_version=d.version, result=res,
                          engine_version=ENGINE, created_by=user.id))
    db.flush()


def _refs(cfg: dict) -> set[str]:
    ids = {a.get("analysis_id") for a in (cfg or {}).get("annotations") or [] if a.get("type") == "pvalue"}
    for v in ((cfg or {}).get("per_y") or {}).values():
        ids |= {a.get("analysis_id") for a in (v or {}).get("annotations") or [] if a.get("type") == "pvalue"}
    return {i for i in ids if i}


def _owns(db: Session, f: Figure, a: Analysis) -> bool:
    """Each figure has its own stars analysis. Older ones carry no owner: the oldest figure using it gets it."""
    owner = (a.config or {}).get("figure_id")
    if owner:
        return owner == f.id
    users = [x for x in db.execute(select(Figure).where(Figure.project_id == f.project_id)).scalars() if a.id in _refs(x.config)]
    users.sort(key=lambda x: (x.created_at, x.id))
    return not users or users[0].id == f.id


def _stars_name(db: Session, user: User, f: Figure, d: Dataset, value_col: str | None) -> str:
    col = next((c["name"] for c in d.content.get("columns", []) if c["id"] == value_col), "") if value_col else ""
    name = f"{'Étoiles' if user.locale == 'fr' else 'Stars'} – {f.name}"
    if col and not f.name.endswith(col):
        name += f" – {col}"
    return name[:300]


def _sync_figure_stars(db: Session, user: User, f: Figure) -> str | None:
    """Keep the saved analyses behind a figure's stars and p-values in step with the figure and its data,
    so nobody ever has to press "recompute". Returns the id of the figure's automatic stars analysis."""
    cfg = f.config or {}
    anns = list(cfg.get("annotations") or [])
    d = db.get(Dataset, f.dataset_id)
    if d is None:
        return None
    stars_id, changed = None, False
    for i, ann in enumerate(anns):
        if ann.get("type") != "pvalue":
            continue
        a = db.get(Analysis, ann["analysis_id"]) if ann.get("analysis_id") else None
        if a is not None and a.project_id != f.project_id:
            a = None
        if not ann.get("all"):
            if a is not None:
                _ensure_run(db, user, a)
            continue
        if a is None or not _owns(db, f, a):
            a = next((x for x in db.execute(select(Analysis).where(Analysis.project_id == f.project_id, Analysis.kind == "pairwise")).scalars()
                      if (x.config or {}).get("figure_id") == f.id), None)
        desired = _stars_config(d.content, cfg, a.config if a else None)
        if desired is None:
            continue
        desired["figure_id"] = f.id
        if a is None:
            a = Analysis(project_id=f.project_id, dataset_id=d.id, kind="pairwise", name=_stars_name(db, user, f, d, desired["value_col"]), config=desired)
            db.add(a)
            db.flush()
        elif a.config != desired or a.dataset_id != d.id:
            if (a.config or {}).get("value_col") != desired["value_col"] or a.dataset_id != d.id:
                a.name = _stars_name(db, user, f, d, desired["value_col"])
            a.config = desired
            a.dataset_id = d.id
            a.version += 1
            a.updated_at = utcnow()
            db.flush()
        _ensure_run(db, user, a)
        stars_id = a.id
        if ann.get("analysis_id") != a.id:
            anns[i] = {**ann, "analysis_id": a.id}
            changed = True
    if changed:
        f.config = {**cfg, "annotations": anns}
    return stars_id


def _safe_sync(db: Session, user: User, f: Figure) -> str | None:
    """Stars bookkeeping must never block saving the figure or the data."""
    try:
        with db.begin_nested():
            return _sync_figure_stars(db, user, f)
    except Exception:  # pragma: no cover - logged, the preview still computes stars itself
        import logging

        logging.getLogger("fiole").exception("stars sync failed for figure %s", f.id)
        return None


def _refresh_dataset_analyses(db: Session, user: User, d: Dataset) -> None:
    """After a data change: the figures' stars follow the new data, and every analysis already run on
    this table is re-run, so results are never left behind the data."""
    for f in db.execute(select(Figure).where(Figure.dataset_id == d.id)).scalars():
        if any(a.get("type") == "pvalue" for a in (f.config or {}).get("annotations") or []):
            _safe_sync(db, user, f)
    for a in db.execute(select(Analysis).where(Analysis.dataset_id == d.id)).scalars():
        if _last_result(db, a.id) is not None:
            try:
                with db.begin_nested():
                    _ensure_run(db, user, a)
            except Exception:  # pragma: no cover
                pass


def _render(db: Session, project_id: str, dataset_id: str, cfg: dict, lang: str) -> dict:
    d = _get_dataset(db, project_id, dataset_id)
    try:
        return render_figure(d.content, cfg, _results_for(db, project_id, cfg, d), lang)
    except FigureError as e:
        raise HTTPException(422, detail={"code": "figure_error", "message": e.message})
    except Exception as e:  # data problems
        from .data import DataError

        if isinstance(e, DataError):
            raise HTTPException(422, detail={"code": "figure_error", "message": e.message})
        raise


@app.post("/api/projects/{project_id}/figures")
def create_figure(project_id: str, body: FigureIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    d = _get_dataset(db, project_id, body.dataset_id)
    f = Figure(project_id=p.id, dataset_id=d.id, name=body.name.strip(), kind=body.config.get("type", "scatter_groups"), config=body.config)
    db.add(f)
    audit(db, user, p.id, "figure.create", "figure", f.id)
    touch(p)
    db.commit()
    return _figure_out(db, f, {})


def _get_figure(db: Session, project_id: str, figure_id: str) -> Figure:
    f = db.get(Figure, figure_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(404, detail={"code": "figure_not_found"})
    return f


@app.put("/api/projects/{project_id}/figures/{figure_id}")
def update_figure(project_id: str, figure_id: str, body: FigureUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    f = _get_figure(db, project_id, figure_id)
    if body.version != f.version:
        conflict("figure", f.version)
    if body.name is not None:
        f.name = body.name.strip() or f.name
    if body.config is not None:
        f.config = body.config
        f.kind = body.config.get("type", f.kind)
    if body.dataset_id is not None:
        f.dataset_id = _get_dataset(db, project_id, body.dataset_id).id
    stars_id = _safe_sync(db, user, f)
    f.version += 1
    f.updated_at = utcnow()
    touch(p)
    db.commit()
    return {"id": f.id, "version": f.version, "stars_analysis_id": stars_id}


@app.delete("/api/projects/{project_id}/figures/{figure_id}")
def delete_figure(project_id: str, figure_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    f = _get_figure(db, project_id, figure_id)
    db.delete(f)
    audit(db, user, p.id, "figure.delete", "figure", figure_id)
    db.commit()
    return {"ok": True}


@app.post("/api/projects/{project_id}/render")
def render_preview(project_id: str, body: RenderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Render an (unsaved) figure configuration. The returned SVG is exactly what exports use."""
    require(db, user, project_id, Action.VIEW)
    return _render(db, project_id, body.dataset_id, body.config, body.lang)


class SuggestIn(BaseModel):
    dataset_id: str
    lang: Literal["en", "fr"] = "en"
    style: dict = Field(default_factory=dict)  # the current figure's look (font, palette…), kept on every suggestion


@app.post("/api/projects/{project_id}/figure-suggestions")
def figure_suggestions(project_id: str, body: SuggestIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """A gallery of figures that fit this table, each rendered with the real data: the user picks one by clicking it."""
    from .figures.suggest import STYLE_KEYS, suggestions

    require(db, user, project_id, Action.VIEW)
    d = _get_dataset(db, project_id, body.dataset_id)
    style = {k: v for k, v in (body.style or {}).items() if k in STYLE_KEYS and k not in ("group_order", "group_labels")}
    out = []
    for s in suggestions(d.content):
        cfg = {**style, **s["config"]}
        try:
            r = _render(db, project_id, d.id, cfg, body.lang)
        except HTTPException:
            continue
        # several thumbnails share one page: keep their ids (clip paths) unique
        svg = re.sub(r'(id="|url\(#)([A-Za-z_][\w-]*)', lambda m, k=len(out): f"{m.group(1)}sg{k}-{m.group(2)}", r["svg"])
        out.append({**s, "config": cfg, "svg": svg})
    return out


@app.get("/api/projects/{project_id}/figures/{figure_id}/export")
def export_figure(
    project_id: str,
    figure_id: str,
    format: Literal["svg", "pdf", "png"] = "svg",
    dpi: int = 300,
    background: Literal["white", "transparent"] | None = None,
    lang: Literal["en", "fr"] = "en",
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    require(db, user, project_id, Action.EXPORT)
    f = _get_figure(db, project_id, figure_id)
    cfg = dict(f.config)
    if background:
        cfg["background"] = background
    if format == "pdf" and cfg.get("background") == "transparent":
        cfg["background"] = "transparent"  # PDF keeps no page fill when transparent
    out = _render(db, project_id, f.dataset_id, cfg, lang)
    name = _safe(f.name)
    headers = {"Content-Disposition": disposition(name + f".{format}")}
    if out["warnings"]:
        headers["X-Fiole-Warnings"] = str(len(out["warnings"]))
    if format == "svg":
        return Response(out["svg"], media_type="image/svg+xml", headers=headers)
    if format == "pdf":
        return Response(to_pdf(out["svg"]), media_type="application/pdf", headers=headers)
    dpi = max(72, min(dpi, 1200))
    return Response(to_png(out["svg"], dpi), media_type="image/png", headers=headers)


# ------------------------------------------------------------------ duplicate figures (one per variable)

Y_KEYS = ("y_min", "y_max", "y_tick_step", "y_scale", "y_label", "y_unit")
GROUP_TYPES = ("scatter_groups", "bar", "box", "violin")


class DuplicateIn(BaseModel):
    name: str | None = Field(default=None, max_length=300)
    columns: list[str] | None = None  # one copy per Y column (stars recomputed for each); None = a plain copy


def _y_key(cfg: dict) -> str:
    if (cfg.get("type") or "scatter_groups") in GROUP_TYPES:
        return str(cfg.get("value_col") or "")
    y = cfg.get("y") or []
    return "+".join(y) if isinstance(y, list) else str(y)


def _retarget(db: Session, user: User, p: Project, src: Figure, cfg: dict, col: str, colname: str) -> dict:
    """Copy of `cfg` showing column `col` on Y: axis settings and stars of that variable (saved, or recomputed)."""
    import copy

    cfg = copy.deepcopy(cfg)
    anns = cfg.get("annotations") or []
    old_k = _y_key(cfg)
    per = dict(cfg.get("per_y") or {})
    per[old_k] = {**{k: cfg.get(k) for k in Y_KEYS}, "annotations": [a for a in anns if a.get("type") == "pvalue"]}
    if (cfg.get("type") or "scatter_groups") in GROUP_TYPES:
        cfg["value_col"] = col
    else:
        cfg["y"] = [col]
    new_k = _y_key(cfg)
    saved = per.get(new_k)
    for k in Y_KEYS:
        cfg[k] = saved.get(k) if saved else None
    keep = [a for a in anns if a.get("type") != "pvalue"]
    if saved and saved.get("annotations"):
        stars = saved["annotations"]
    else:
        # recompute each linked significance analysis on the new variable
        stars = []
        done: dict[str, str] = {}
        for a in anns:
            if a.get("type") != "pvalue" or not a.get("analysis_id"):
                continue
            if a.get("all"):
                stars.append(dict(a))  # automatic stars: the new figure gets its own analysis just below
                continue
            aid = a["analysis_id"]
            if aid not in done:
                an = db.get(Analysis, aid)
                if an is None or an.project_id != p.id or "value_col" not in (an.config or {}):
                    continue
                if an.config.get("value_col") == col:
                    done[aid] = aid
                else:
                    base = re.sub(r"\s+–\s+[^–]+$", "", an.name)
                    na = Analysis(project_id=p.id, dataset_id=an.dataset_id, name=f"{base} – {colname}"[:300], kind=an.kind,
                                  config={**an.config, "value_col": col})
                    db.add(na)
                    db.flush()
                    d = db.get(Dataset, na.dataset_id)
                    res = run_analysis(na.kind, d.content, na.config)
                    res["dataset"] = {"id": d.id, "name": d.name, "version": d.version}
                    db.add(AnalysisResult(analysis_id=na.id, analysis_version=na.version, dataset_version=d.version, result=res,
                                          engine_version=ENGINE, created_by=user.id))
                    done[aid] = na.id
            stars.append({**a, "analysis_id": done[aid]})
    cfg["annotations"] = keep + stars
    cfg["per_y"] = per
    return cfg


@app.post("/api/projects/{project_id}/figures/{figure_id}/duplicate")
def duplicate_figure(project_id: str, figure_id: str, body: DuplicateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    import copy

    p = require(db, user, project_id, Action.EDIT)
    src = _get_figure(db, project_id, figure_id)
    fr = user.locale == "fr"
    made: list[Figure] = []
    if not body.columns:
        name = (body.name or "").strip() or (f"{src.name} (copie)" if fr else f"{src.name} (copy)")
        made.append(Figure(project_id=p.id, dataset_id=src.dataset_id, name=name[:300], kind=src.kind, config=copy.deepcopy(src.config)))
    else:
        d = _get_dataset(db, project_id, src.dataset_id)
        names = {c["id"]: c["name"] for c in d.content.get("columns", [])}
        base = re.sub(r"\s+–\s+[^–]+$", "", src.name)
        for col in body.columns[:30]:
            if col not in names:
                raise HTTPException(422, detail={"code": "unknown_column", "message": {"fr": f"Colonne inconnue : {col}", "en": f"Unknown column: {col}"}})
            cfg = _retarget(db, user, p, src, src.config or {}, col, names[col])
            made.append(Figure(project_id=p.id, dataset_id=src.dataset_id, name=f"{base} – {names[col]}"[:300], kind=src.kind, config=cfg))
    for f in made:
        db.add(f)
        db.flush()
        if body.columns:
            _safe_sync(db, user, f)
        audit(db, user, p.id, "figure.duplicate", "figure", f.id, source=src.id)
    touch(p)
    db.commit()
    return [_figure_out(db, f, {}) for f in made]


# ------------------------------------------------------------------ layouts (multi-panel pages)


class LayoutIn(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    config: dict = {}


class LayoutUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    config: dict | None = None
    version: int


def _layout_out(lay: Layout) -> dict:
    return {"id": lay.id, "name": lay.name, "config": layout_lib.merged(lay.config), "version": lay.version, "updated_at": lay.updated_at.isoformat()}


def _get_layout(db: Session, project_id: str, layout_id: str) -> Layout:
    lay = db.get(Layout, layout_id)
    if lay is None or lay.project_id != project_id:
        raise HTTPException(404, detail={"code": "layout_not_found"})
    return lay


def _clean_layout_cfg(db: Session, project_id: str, cfg: dict) -> dict:
    cfg = layout_lib.merged(cfg)
    n = cfg["rows"] * cfg["cols"]
    panels, seen = [], set()
    for pnl in cfg.get("panels") or []:
        try:
            cell = int(pnl.get("cell"))
        except (TypeError, ValueError, AttributeError):
            continue
        fid = pnl.get("figure_id")
        if 0 <= cell < n and cell not in seen and fid and (f := db.get(Figure, fid)) is not None and f.project_id == project_id:
            panels.append({"cell": cell, "figure_id": fid})
            seen.add(cell)
    cfg["panels"] = sorted(panels, key=lambda x: x["cell"])
    return cfg


def _render_layout(db: Session, project_id: str, cfg: dict, lang: str) -> dict:
    cfg = _clean_layout_cfg(db, project_id, cfg)
    by_cell = {pnl["cell"]: pnl["figure_id"] for pnl in cfg["panels"]}
    placed, warnings = [], []
    for c in layout_lib.cells(cfg):
        fid = by_cell.get(c["index"])
        if not fid:
            placed.append({"cell": c, "svg": None})
            continue
        f = db.get(Figure, fid)
        fcfg = {**(f.config or {}), "width_mm": round(c["w"], 2), "height_mm": round(c["h"], 2)}
        if cfg["background"] == "transparent":
            fcfg["background"] = "transparent"
        try:
            fd = _get_dataset(db, project_id, f.dataset_id)
            out = render_figure(fd.content, fcfg, _results_for(db, project_id, fcfg, fd), lang)
            placed.append({"cell": c, "svg": out["svg"]})
            warnings += [{"en": f"{f.name}: {w['en']}", "fr": f"{f.name} : {w['fr']}"} for w in out["warnings"]]
        except Exception as e:  # one broken panel never blanks the page
            msg = getattr(e, "message", None) or {"en": str(e), "fr": str(e)}
            placed.append({"cell": c, "svg": None})
            warnings.append({"en": f"{f.name}: {msg['en']}", "fr": f"{f.name} : {msg['fr']}"})
    return {"svg": layout_lib.compose(cfg, placed), "warnings": warnings, "cells": layout_lib.cells(cfg), "config": cfg}


@app.post("/api/projects/{project_id}/layouts")
def create_layout(project_id: str, body: LayoutIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    lay = Layout(project_id=p.id, name=body.name.strip(), config=_clean_layout_cfg(db, project_id, body.config))
    db.add(lay)
    db.flush()
    audit(db, user, p.id, "layout.create", "layout", lay.id)
    touch(p)
    db.commit()
    return _layout_out(lay)


@app.put("/api/projects/{project_id}/layouts/{layout_id}")
def update_layout(project_id: str, layout_id: str, body: LayoutUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    lay = _get_layout(db, project_id, layout_id)
    if body.version != lay.version:
        conflict("layout", lay.version)
    if body.name is not None:
        lay.name = body.name.strip()
    if body.config is not None:
        lay.config = _clean_layout_cfg(db, project_id, body.config)
    lay.version += 1
    lay.updated_at = utcnow()
    touch(p)
    db.commit()
    return _layout_out(lay)


@app.delete("/api/projects/{project_id}/layouts/{layout_id}")
def delete_layout(project_id: str, layout_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    db.delete(_get_layout(db, project_id, layout_id))
    audit(db, user, p.id, "layout.delete", "layout", layout_id)
    db.commit()
    return {"ok": True}


class LayoutRenderIn(BaseModel):
    config: dict
    lang: Literal["en", "fr"] = "en"


@app.post("/api/projects/{project_id}/layouts/render")
def render_layout_preview(project_id: str, body: LayoutRenderIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(db, user, project_id, Action.VIEW)
    return _render_layout(db, project_id, body.config, body.lang)


@app.get("/api/projects/{project_id}/layouts/{layout_id}/export")
def export_layout(project_id: str, layout_id: str, format: Literal["svg", "pdf", "png"] = "pdf", dpi: int = 300,
                  lang: Literal["en", "fr"] = "en", user: User = Depends(current_user), db: Session = Depends(get_db)):
    require(db, user, project_id, Action.EXPORT)
    lay = _get_layout(db, project_id, layout_id)
    svg = _render_layout(db, project_id, lay.config, lang)["svg"]
    headers = {"Content-Disposition": disposition(_safe(lay.name) + f".{format}")}
    if format == "svg":
        return Response(svg, media_type="image/svg+xml", headers=headers)
    if format == "pdf":
        return Response(to_pdf(svg), media_type="application/pdf", headers=headers)
    return Response(to_png(svg, max(72, min(dpi, 1200))), media_type="image/png", headers=headers)


# ------------------------------------------------------------------ cleanup: delete several items, see the impact, undo


class BulkDeleteIn(BaseModel):
    analyses: list[str] = []
    figures: list[str] = []
    layouts: list[str] = []
    dry_run: bool = False


def _dt(v):
    return v.isoformat() if isinstance(v, datetime) else v


def _undt(v):
    return datetime.fromisoformat(v) if isinstance(v, str) else v


def _strip_refs(cfg: dict, gone: set[str]) -> tuple[dict, list, dict]:
    """Remove star annotations pointing at deleted analyses. Returns (new cfg, removed top-level, removed per-variable)."""
    import copy

    cfg = copy.deepcopy(cfg or {})
    anns = cfg.get("annotations") or []
    removed = [a for a in anns if a.get("type") == "pvalue" and a.get("analysis_id") in gone]
    cfg["annotations"] = [a for a in anns if a not in removed]
    removed_per = {}
    for k, v in (cfg.get("per_y") or {}).items():
        sa = (v or {}).get("annotations") or []
        out = [a for a in sa if a.get("analysis_id") in gone]
        if out:
            removed_per[k] = out
            v["annotations"] = [a for a in sa if a not in out]
    return cfg, removed, removed_per


@app.post("/api/projects/{project_id}/bulk-delete")
def bulk_delete(project_id: str, body: BulkDeleteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    an = [a for a in (db.get(Analysis, i) for i in dict.fromkeys(body.analyses)) if a and a.project_id == p.id]
    fg = [f for f in (db.get(Figure, i) for i in dict.fromkeys(body.figures)) if f and f.project_id == p.id]
    ly = [x for x in (db.get(Layout, i) for i in dict.fromkeys(body.layouts)) if x and x.project_id == p.id]
    gone_a, gone_f, gone_l = {a.id for a in an}, {f.id for f in fg}, {x.id for x in ly}
    # impact on what stays
    fig_hits, lay_hits = [], []
    for f in db.execute(select(Figure).where(Figure.project_id == p.id)).scalars():
        if f.id in gone_f:
            continue
        _, rem, rem_per = _strip_refs(f.config, gone_a)
        if rem or rem_per:
            fig_hits.append((f, rem, rem_per))
    for x in db.execute(select(Layout).where(Layout.project_id == p.id)).scalars():
        if x.id in gone_l:
            continue
        lost = [pn for pn in (x.config or {}).get("panels") or [] if pn.get("figure_id") in gone_f]
        if lost:
            lay_hits.append((x, lost))
    impact = {
        "analyses": [{"id": a.id, "name": a.name} for a in an],
        "figures": [{"id": f.id, "name": f.name} for f in fg],
        "layouts": [{"id": x.id, "name": x.name} for x in ly],
        "figures_losing_stars": [{"id": f.id, "name": f.name, "count": len(rem) + sum(len(v) for v in rp.values())} for f, rem, rp in fig_hits],
        "layouts_losing_panels": [{"id": x.id, "name": x.name, "count": len(lost)} for x, lost in lay_hits],
    }
    if body.dry_run or not (an or fg or ly):
        return {"impact": impact, "trash_id": None}
    payload = {
        "analyses": [{"id": a.id, "dataset_id": a.dataset_id, "name": a.name, "kind": a.kind, "config": a.config, "version": a.version,
                      "created_at": _dt(a.created_at), "updated_at": _dt(a.updated_at),
                      "results": [{"analysis_version": r.analysis_version, "dataset_version": r.dataset_version, "result": r.result,
                                   "engine_version": r.engine_version, "created_at": _dt(r.created_at), "created_by": r.created_by}
                                  for r in db.execute(select(AnalysisResult).where(AnalysisResult.analysis_id == a.id).order_by(AnalysisResult.id)).scalars()]}
                     for a in an],
        "figures": [{"id": f.id, "dataset_id": f.dataset_id, "name": f.name, "kind": f.kind, "config": f.config, "version": f.version,
                     "created_at": _dt(f.created_at), "updated_at": _dt(f.updated_at)} for f in fg],
        "layouts": [{"id": x.id, "name": x.name, "config": x.config, "version": x.version, "created_at": _dt(x.created_at), "updated_at": _dt(x.updated_at)} for x in ly],
        "figure_refs": [{"id": f.id, "annotations": rem, "per_y": rp} for f, rem, rp in fig_hits],
        "layout_refs": [{"id": x.id, "panels": lost} for x, lost in lay_hits],
    }
    for f, _rem, _rp in fig_hits:
        f.config = _strip_refs(f.config, gone_a)[0]
        f.version += 1
        f.updated_at = utcnow()
    for x, _lost in lay_hits:
        x.config = {**(x.config or {}), "panels": [pn for pn in (x.config or {}).get("panels") or [] if pn.get("figure_id") not in gone_f]}
        x.version += 1
        x.updated_at = utcnow()
    for obj in [*an, *fg, *ly]:
        db.delete(obj)
    t = Trash(project_id=p.id, user_id=user.id, payload=payload)
    db.add(t)
    db.flush()
    audit(db, user, p.id, "bulk.delete", "trash", str(t.id), analyses=len(an), figures=len(fg), layouts=len(ly))
    touch(p)
    db.commit()
    return {"impact": impact, "trash_id": t.id}


@app.post("/api/projects/{project_id}/trash/{trash_id}/restore")
def restore_trash(project_id: str, trash_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EDIT)
    t = db.get(Trash, trash_id)
    if t is None or t.project_id != p.id:
        raise HTTPException(404, detail={"code": "trash_not_found"})
    if t.restored_at is not None:
        raise HTTPException(409, detail={"code": "already_restored"})
    pl = t.payload
    ds_ids = {d for (d,) in db.execute(select(Dataset.id).where(Dataset.project_id == p.id)).all()}
    skipped = 0
    for a in pl.get("analyses", []):
        if a["dataset_id"] not in ds_ids or db.get(Analysis, a["id"]):
            skipped += 1
            continue
        db.add(Analysis(id=a["id"], project_id=p.id, dataset_id=a["dataset_id"], name=a["name"], kind=a["kind"], config=a["config"],
                        version=a["version"], created_at=_undt(a["created_at"]), updated_at=_undt(a["updated_at"])))
        db.flush()
        for r in a["results"]:
            db.add(AnalysisResult(analysis_id=a["id"], analysis_version=r["analysis_version"], dataset_version=r["dataset_version"], result=r["result"],
                                  engine_version=r["engine_version"], created_at=_undt(r["created_at"]), created_by=r.get("created_by")))
    for f in pl.get("figures", []):
        if f["dataset_id"] not in ds_ids or db.get(Figure, f["id"]):
            skipped += 1
            continue
        db.add(Figure(id=f["id"], project_id=p.id, dataset_id=f["dataset_id"], name=f["name"], kind=f["kind"], config=f["config"],
                      version=f["version"], created_at=_undt(f["created_at"]), updated_at=_undt(f["updated_at"])))
    for x in pl.get("layouts", []):
        if db.get(Layout, x["id"]):
            skipped += 1
            continue
        db.add(Layout(id=x["id"], project_id=p.id, name=x["name"], config=x["config"], version=x["version"],
                      created_at=_undt(x["created_at"]), updated_at=_undt(x["updated_at"])))
    db.flush()
    # put the stars and panels back into what stayed (without overwriting later edits)
    for ref in pl.get("figure_refs", []):
        f = db.get(Figure, ref["id"])
        if not f:
            continue
        import copy

        cfg = copy.deepcopy(f.config or {})
        cfg["annotations"] = (cfg.get("annotations") or []) + [a for a in ref["annotations"] if a not in (cfg.get("annotations") or [])]
        for k, anns in (ref.get("per_y") or {}).items():
            slot = (cfg.setdefault("per_y", {})).setdefault(k, {})
            slot["annotations"] = (slot.get("annotations") or []) + anns
        f.config = cfg
        f.version += 1
        f.updated_at = utcnow()
    for ref in pl.get("layout_refs", []):
        x = db.get(Layout, ref["id"])
        if not x:
            continue
        panels = list((x.config or {}).get("panels") or [])
        used = {pn.get("cell") for pn in panels}
        panels += [pn for pn in ref["panels"] if pn.get("cell") not in used]
        x.config = {**(x.config or {}), "panels": sorted(panels, key=lambda pn: pn.get("cell", 0))}
        x.version += 1
        x.updated_at = utcnow()
    t.restored_at = utcnow()
    audit(db, user, p.id, "bulk.restore", "trash", str(t.id), skipped=skipped)
    touch(p)
    db.commit()
    return {"ok": True, "skipped": skipped}


# ------------------------------------------------------------------ figure styles (per user; sharing in phase 2)


class StyleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    settings: dict | None = None      # explicit settings, or
    from_config: dict | None = None   # a figure configuration to take the look from


class StyleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    settings: dict | None = None
    visibility: Literal["private", "shared", "public"] | None = None
    version: int


def _get_style(db: Session, user: User, style_id: str, own: bool = False) -> FigureStyle:
    st = db.get(FigureStyle, style_id)
    if st is None or not style_lib.can_view(db, user, st) or (own and st.owner_id != user.id):
        raise HTTPException(404, detail={"code": "style_not_found"})
    return st


def _new_style(db: Session, user: User, name: str, description: str, settings: dict, source: str | None = None) -> FigureStyle:
    clean = style_lib.extract(settings or {})
    if not clean:
        raise HTTPException(422, detail={"code": "empty_style", "message": {"en": "This style has no appearance settings.", "fr": "Ce style ne contient aucun réglage d’apparence."}})
    st = FigureStyle(owner_id=user.id, name=name.strip(), description=description, settings=clean, source_style_id=source)
    db.add(st)
    db.flush()
    audit(db, user, None, "style.create", "style", st.id)
    db.commit()
    db.refresh(st)
    return st


def _style_full(db: Session, st: FigureStyle, user: User) -> dict:
    out = style_lib.style_out(st, user, st_owner_name(db, st))
    if st.owner_id == user.id:
        out["shared_with"] = [{"user_id": u.id, "email": u.email, "display_name": u.display_name}
                              for u in db.execute(select(User).join(FigureStyleShare, FigureStyleShare.user_id == User.id)
                                                  .where(FigureStyleShare.style_id == st.id).order_by(User.email)).scalars()]
    return out


def st_owner_name(db: Session, st: FigureStyle) -> str:
    o = db.get(User, st.owner_id)
    return o.display_name if o else ""


@app.get("/api/styles")
def list_styles(user: User = Depends(current_user), db: Session = Depends(get_db)):
    mine = db.execute(select(FigureStyle).where(FigureStyle.owner_id == user.id).order_by(FigureStyle.name)).scalars().all()
    shared = db.execute(
        select(FigureStyle).join(FigureStyleShare, FigureStyleShare.style_id == FigureStyle.id)
        .where(FigureStyleShare.user_id == user.id, FigureStyle.owner_id != user.id,
               FigureStyle.visibility.in_([StyleVisibility.shared, StyleVisibility.public]))
        .order_by(FigureStyle.name)).scalars().all()
    seen = {st.id for st in shared}
    public = db.execute(select(FigureStyle).where(FigureStyle.visibility == StyleVisibility.public, FigureStyle.owner_id != user.id)
                        .order_by(FigureStyle.updated_at.desc()).limit(200)).scalars().all()
    return {"mine": [_style_full(db, st, user) for st in mine],
            "shared": [_style_full(db, st, user) for st in shared],
            "public": [_style_full(db, st, user) for st in public if st.id not in seen],
            "sharing_available": True}


@app.post("/api/styles")
def create_style(body: StyleIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return style_lib.style_out(_new_style(db, user, body.name, body.description, body.settings or body.from_config or {}), user, user.display_name)


@app.put("/api/styles/{style_id}")
def update_style(style_id: str, body: StyleUpdate, user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = _get_style(db, user, style_id, own=True)
    if body.version != st.version:
        conflict("style", st.version)
    if body.visibility:
        st.visibility = StyleVisibility(body.visibility)
        audit(db, user, None, "style.visibility", "style", st.id, value=body.visibility)
    if body.name is not None:
        st.name = body.name.strip()
    if body.description is not None:
        st.description = body.description
    if body.settings is not None:
        clean = style_lib.extract(body.settings)
        if clean:
            st.settings = clean
    st.version += 1
    st.updated_at = utcnow()
    db.commit()
    return _style_full(db, st, user)


class StyleShareIn(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=50)
    notify: bool = True


@app.post("/api/styles/{style_id}/shares")
def share_style(style_id: str, body: StyleShareIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Share a style with people who have a Fiole account (they can apply it and copy it into their styles)."""
    st = _get_style(db, user, style_id, own=True)
    added, unknown = [], []
    for raw in body.emails:
        email = raw.strip().lower()
        if not email:
            continue
        target = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if target is None:
            unknown.append(email)
            continue
        if target.id == user.id:
            continue
        if not db.execute(select(FigureStyleShare).where(FigureStyleShare.style_id == st.id, FigureStyleShare.user_id == target.id)).first():
            db.add(FigureStyleShare(style_id=st.id, user_id=target.id))
            added.append(target)
    if added and st.visibility == StyleVisibility.private:
        st.visibility = StyleVisibility.shared
        st.version += 1
    audit(db, user, None, "style.share", "style", st.id, n=len(added))
    db.commit()
    mail = {}
    if body.notify:
        url = sharing.public_url(request)
        for t in added:
            mail[t.email] = sharing.notify(
                db, t.email, t.locale,
                (f"{user.display_name} a partagé un style de figure avec vous : « {st.name} »", f"{user.display_name} shared a figure style with you: “{st.name}”"),
                (f"{user.display_name} ({user.email}) a partagé le style « {st.name} » avec vous.\n\nDans une figure : « Style rapide » → « Partagés avec moi ».\n{url}\n\n— Fiole",
                 f"{user.display_name} ({user.email}) shared the style “{st.name}” with you.\n\nIn a figure: “Quick style” → “Shared with me”.\n{url}\n\n— Fiole"),
                None, "style_share")
        db.commit()
    return {"style": _style_full(db, st, user), "unknown": unknown, "email": mail}


@app.delete("/api/styles/{style_id}/shares/{user_id}")
def unshare_style(style_id: str, user_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = _get_style(db, user, style_id, own=True)
    sh = db.execute(select(FigureStyleShare).where(FigureStyleShare.style_id == st.id, FigureStyleShare.user_id == user_id)).scalar_one_or_none()
    if sh:
        db.delete(sh)
        db.flush()
        if st.visibility == StyleVisibility.shared and not db.execute(select(FigureStyleShare).where(FigureStyleShare.style_id == st.id)).first():
            st.visibility = StyleVisibility.private
            st.version += 1
        audit(db, user, None, "style.unshare", "style", st.id)
        db.commit()
    return _style_full(db, st, user)


@app.delete("/api/styles/{style_id}")
def delete_style(style_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = _get_style(db, user, style_id, own=True)
    db.delete(st)
    audit(db, user, None, "style.delete", "style", style_id)
    db.commit()
    return {"ok": True}


@app.post("/api/styles/{style_id}/copy")
def copy_style(style_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Copy a style I can see (mine now; someone else's shared or public style in phase 2) into my styles."""
    st = _get_style(db, user, style_id)
    name = st.name if st.owner_id != user.id else (f"{st.name} (copie)" if user.locale == "fr" else f"{st.name} (copy)")
    return style_lib.style_out(_new_style(db, user, name, st.description, st.settings, source=st.id), user, user.display_name)


@app.get("/api/styles/{style_id}/export")
def export_style(style_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    st = _get_style(db, user, style_id)
    return Response(json.dumps(style_lib.to_file(st), ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": disposition(_safe(st.name) + ".fiole-style.json")})


@app.post("/api/styles/import")
def import_style(body: dict, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if body.get("format") != style_lib.FILE_FORMAT or not isinstance(body.get("settings"), dict):
        raise HTTPException(422, detail={"code": "not_a_style_file", "message": {"en": "This is not a Fiole style file (.fiole-style.json).", "fr": "Ce fichier n’est pas un style Fiole (.fiole-style.json)."}})
    name = str(body.get("name") or "Style")[:200]
    return style_lib.style_out(_new_style(db, user, name, str(body.get("description") or ""), body["settings"]), user, user.display_name)


# ------------------------------------------------------------------ .fio project files
#
# A .fio file is a ZIP holding everything needed to rebuild a project on any Fiole account:
#   fiole.json      data tables (with their version history), analyses, figures, layouts, and the
#                   figure styles of the person who saved it
#   uploads/<n>     the original imported files, byte for byte
# Older exports (plain JSON, .fiole.json) open too. Analysis results are recomputed on opening, never trusted.

ARCHIVE_FORMAT = "fiole-project"
ARCHIVE_VERSION = 2
FIO_MAX_UNZIPPED = 400 * 1024 * 1024
FIO_MIME = "application/vnd.fiole.project+zip"


@app.get("/api/projects/{project_id}/archive")
def export_archive(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Save the project as a .fio file."""
    p = require(db, user, project_id, Action.EXPORT)
    files: dict[str, bytes] = {}
    doc = _archive_doc(db, p, styles_of=user, uploads=files)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("mimetype", FIO_MIME, compress_type=zipfile.ZIP_STORED)
        z.writestr("fiole.json", json.dumps(doc, ensure_ascii=False, indent=1))
        for path, data in files.items():
            z.writestr(path, data)
    audit(db, user, p.id, "project.save_fio", "project", p.id)
    db.commit()
    return Response(buf.getvalue(), media_type=FIO_MIME, headers={"Content-Disposition": disposition(_safe(p.name) + ".fio")})


def _archive_doc(db: Session, p: Project, styles_of: User | None = None, uploads: dict[str, bytes] | None = None) -> dict:
    datasets = db.execute(select(Dataset).where(Dataset.project_id == p.id).order_by(Dataset.created_at)).scalars().all()
    analyses = db.execute(select(Analysis).where(Analysis.project_id == p.id).order_by(Analysis.created_at)).scalars().all()
    figures = db.execute(select(Figure).where(Figure.project_id == p.id).order_by(Figure.created_at)).scalars().all()
    doc: dict[str, Any] = {
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_VERSION,
        "exported_at": utcnow().isoformat(),
        "engine": ENGINE,
        "project": {"name": p.name, "description": p.description},
        "datasets": [],
        "analyses": [
            {"id": a.id, "dataset_id": a.dataset_id, "name": a.name, "kind": a.kind, "config": a.config,
             "last_result": (lambda r: {"dataset_version": r.dataset_version, "result": r.result} if r else None)(_last_result(db, a.id))}
            for a in analyses
        ],
        "figures": [{"id": f.id, "dataset_id": f.dataset_id, "name": f.name, "kind": f.kind, "config": f.config} for f in figures],
        "layouts": [{"name": x.name, "config": x.config} for x in db.execute(select(Layout).where(Layout.project_id == p.id).order_by(Layout.created_at)).scalars()],
    }
    for d in datasets:
        entry = {"id": d.id, "name": d.name, "version": d.version, "content": d.content}
        if uploads is not None:  # a .fio file: full history and the original files
            entry["history"] = [
                {"version": v.version, "content": v.content, "change_note": v.change_note, "created_at": v.created_at.isoformat() if v.created_at else None}
                for v in db.execute(select(DatasetVersion).where(DatasetVersion.dataset_id == d.id).order_by(DatasetVersion.version)).scalars()
            ]
            entry["source_upload_id"] = d.source_upload_id
        doc["datasets"].append(entry)
    if uploads is not None:
        doc["uploads"] = []
        for i, up in enumerate(db.execute(select(RawUpload).where(RawUpload.project_id == p.id).order_by(RawUpload.created_at)).scalars(), 1):
            path = f"uploads/{i}"
            uploads[path] = up.content
            doc["uploads"].append({"id": up.id, "filename": up.filename, "content_type": up.content_type, "path": path})
    if styles_of is not None:
        doc["styles"] = [{"name": st.name, "description": st.description, "settings": st.settings}
                         for st in db.execute(select(FigureStyle).where(FigureStyle.owner_id == styles_of.id).order_by(FigureStyle.created_at)).scalars()]
    return doc


def _read_fio(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    unreadable = HTTPException(422, detail={"code": "archive_unreadable", "message": {
        "fr": "Ce fichier n’est pas un projet Fiole (.fio).", "en": "This file is not a Fiole project (.fio)."}})
    files: dict[str, bytes] = {}
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                if sum(i.file_size for i in z.infolist()) > FIO_MAX_UNZIPPED:
                    raise unreadable
                doc = json.loads(z.read("fiole.json").decode("utf-8"))
                for u in doc.get("uploads") or []:
                    path = str(u.get("path", ""))
                    if path in z.namelist():
                        files[path] = z.read(path)
        except HTTPException:
            raise
        except Exception:
            raise unreadable
    else:
        try:
            doc = json.loads(raw.decode("utf-8-sig"))
        except Exception:
            raise unreadable
    if not isinstance(doc, dict) or doc.get("format") != ARCHIVE_FORMAT or not isinstance(doc.get("project"), dict):
        raise unreadable
    return doc, files


@app.post("/api/projects/import-archive")
async def import_archive(file: UploadFile = File(...), folder_id: str | None = Form(default=None),
                         user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Open a .fio file (or an older .fiole.json export): creates a new project of mine with everything in it."""
    raw = await file.read()
    if len(raw) > max(MAX_UPLOAD_BYTES, 200 * 1024 * 1024):
        raise HTTPException(413, detail={"code": "file_too_large"})
    doc, files = _read_fio(raw)
    folders_lib._mine(db, user, folder_id or None)
    return _import_doc(db, user, doc, "project.open_fio", folder_id or None, files)


def _import_doc(db: Session, user: User, doc: dict, action: str, folder_id: str | None = None,
                files: dict[str, bytes] | None = None) -> dict:
    name = (str(doc["project"].get("name") or "Projet").strip() or "Projet")[:300]
    p = Project(name=name, description=doc["project"].get("description", "") or "", owner_id=user.id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=user.id, role=Role.owner, added_by=user.id, last_opened_at=utcnow()))
    folders_lib.place(db, user, p.id, folder_id)
    up_map: dict[str, str] = {}
    for u in doc.get("uploads") or []:
        data = (files or {}).get(str(u.get("path", "")))
        if data is None:
            continue
        nu = RawUpload(project_id=p.id, filename=str(u.get("filename") or "fichier")[:500], content_type=str(u.get("content_type") or "")[:200],
                       content=data, uploaded_by=user.id)
        db.add(nu)
        db.flush()
        up_map[u.get("id")] = nu.id
    ds_map, a_map, f_map = {}, {}, {}
    for d in doc.get("datasets", []):
        content = validate_content(d["content"])
        versions: dict[int, dict] = {}
        for h in d.get("history") or []:
            try:
                versions[int(h["version"])] = {"content": validate_content(h["content"]), "note": str(h.get("change_note") or "")}
            except Exception:
                continue
        current = int(d.get("version") or 1) if versions else 1
        versions[current] = {"content": content, "note": versions.get(current, {}).get("note") or "imported from archive"}
        nd = Dataset(project_id=p.id, name=d["name"], content=content, version=current, updated_by=user.id,
                     source_upload_id=up_map.get(d.get("source_upload_id")))
        db.add(nd)
        db.flush()
        for v in sorted(versions):
            db.add(DatasetVersion(dataset_id=nd.id, version=v, content=versions[v]["content"], change_note=versions[v]["note"], created_by=user.id))
        ds_map[d["id"]] = nd.id
    for a in doc.get("analyses", []):
        if a["dataset_id"] not in ds_map:
            continue
        na = Analysis(project_id=p.id, dataset_id=ds_map[a["dataset_id"]], name=a["name"], kind=a["kind"], config=a["config"])
        db.add(na)
        db.flush()
        a_map[a["id"]] = na.id
    for f in doc.get("figures", []):
        if f["dataset_id"] not in ds_map:
            continue
        cfg = json.loads(json.dumps(f["config"]))
        for ann in cfg.get("annotations") or []:
            if ann.get("analysis_id") in a_map:
                ann["analysis_id"] = a_map[ann["analysis_id"]]
        nf = Figure(project_id=p.id, dataset_id=ds_map[f["dataset_id"]], name=f["name"], kind=f.get("kind") or cfg.get("type", "scatter_groups"), config=cfg)
        db.add(nf)
        db.flush()
        f_map[f.get("id")] = nf.id
    for lay in doc.get("layouts", []):
        lc = dict(lay.get("config") or {})
        lc["panels"] = [{**pn, "figure_id": f_map[pn.get("figure_id")]} for pn in lc.get("panels") or [] if pn.get("figure_id") in f_map]
        db.add(Layout(project_id=p.id, name=lay.get("name") or "Layout", config=layout_lib.merged(lc)))
    # the figure styles that came with the file join "My styles" (those I already have are not added twice)
    styles_added = 0
    have = {(st.name, json.dumps(st.settings, sort_keys=True)) for st in db.execute(select(FigureStyle).where(FigureStyle.owner_id == user.id)).scalars()}
    for st in doc.get("styles") or []:
        try:
            clean = style_lib.extract(st.get("settings") or {})
            sname = str(st.get("name") or "").strip()[:200]
        except Exception:
            continue
        key = (sname, json.dumps(clean, sort_keys=True))
        if not clean or not sname or key in have:
            continue
        db.add(FigureStyle(owner_id=user.id, name=sname, description=str(st.get("description") or ""), settings=clean))
        have.add(key)
        styles_added += 1
    audit(db, user, p.id, action, "project", p.id)
    db.commit()
    # Results are recomputed rather than trusted from the file.
    for old_id, new_id in a_map.items():
        a = db.get(Analysis, new_id)
        d = db.get(Dataset, a.dataset_id)
        try:
            res = run_analysis(a.kind, d.content, a.config)
        except Exception:
            continue
        res["dataset"] = {"id": d.id, "name": d.name, "version": d.version}
        db.add(AnalysisResult(analysis_id=a.id, analysis_version=a.version, dataset_version=d.version, result=res, engine_version=ENGINE, created_by=user.id))
    db.commit()
    return {"id": p.id, "url": f"/p/{p.id}", "name": p.name, "folder_id": folder_id, "styles_added": styles_added}


@app.get("/api/health")
def health():
    return {"ok": True, "engine": ENGINE}


# ------------------------------------------------------------------ frontend (production build)

import os as _os  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_DIST = _Path(_os.environ.get("FIOLE_FRONTEND_DIST", _Path(__file__).resolve().parents[2] / "frontend" / "dist"))
if (_DIST / "index.html").exists():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):
            raise HTTPException(404)
        f = _DIST / path
        if path and f.is_file():
            return FileResponse(f)
        return FileResponse(_DIST / "index.html")
