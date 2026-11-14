"""Fiole HTTP API."""
from __future__ import annotations

import csv
from contextlib import asynccontextmanager
import io
import json
import re
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import Cookie, Depends, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from . import importers
from .auth import create_session, current_user, delete_session, hash_password, verify_password
from .authz import Action, allowed_actions, require
from .config import COOKIE_SECURE, MAX_UPLOAD_BYTES, SESSION_COOKIE, SESSION_DAYS
from .db import Base, engine, get_db
from .figures.render import FigureError, render_figure, to_pdf, to_png
from .models import (
    Analysis,
    AnalysisResult,
    AuditEvent,
    Dataset,
    DatasetVersion,
    Figure,
    Project,
    ProjectMember,
    RawUpload,
    Role,
    User,
)
from .stats import ENGINE, catalog, run_analysis

@asynccontextmanager
async def lifespan(_app):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Fiole API", version="0.1.0", lifespan=lifespan)


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
    db.commit()
    _set_cookie(response, create_session(db, u))
    return user_out(u)


@app.post("/api/auth/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    u = db.execute(select(User).where(User.email == body.email.strip().lower())).scalar_one_or_none()
    if u is None or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, detail={"code": "bad_credentials"})
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


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    version: int


def project_summary(db: Session, p: Project, user: User, member: ProjectMember | None = None) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "owner": {"id": p.owner.id, "display_name": p.owner.display_name, "email": p.owner.email},
        "role": member.role.value if member else None,
        "version": p.version,
        "created_at": p.created_at.isoformat(),
        "updated_at": p.updated_at.isoformat(),
        "last_opened_at": member.last_opened_at.isoformat() if member and member.last_opened_at else None,
        "url": f"/p/{p.id}",
    }


@app.get("/api/projects")
def list_projects(scope: Literal["mine", "shared", "recent"] = "mine", user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = (
        select(Project, ProjectMember)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == user.id, Project.deleted_at.is_(None))
    )
    if scope == "mine":
        q = q.where(Project.owner_id == user.id).order_by(Project.updated_at.desc())
    elif scope == "shared":
        q = q.where(Project.owner_id != user.id).order_by(Project.updated_at.desc())
    else:
        q = q.where(ProjectMember.last_opened_at.is_not(None)).order_by(ProjectMember.last_opened_at.desc()).limit(20)
    return [project_summary(db, p, user, m) for p, m in db.execute(q).all()]


@app.post("/api/projects")
def create_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = Project(name=body.name.strip(), description=body.description, owner_id=user.id)
    db.add(p)
    db.flush()
    m = ProjectMember(project_id=p.id, user_id=user.id, role=Role.owner, added_by=user.id, last_opened_at=utcnow())
    db.add(m)
    audit(db, user, p.id, "project.create", "project", p.id)
    db.commit()
    db.refresh(p)
    return project_summary(db, p, user, m)


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
        db.commit()
    datasets = db.execute(select(Dataset).where(Dataset.project_id == p.id).order_by(Dataset.created_at)).scalars().all()
    ds_versions = {d.id: d.version for d in datasets}
    analyses = db.execute(select(Analysis).where(Analysis.project_id == p.id).order_by(Analysis.created_at)).scalars().all()
    a_out = [_analysis_out(a, ds_versions.get(a.dataset_id, 0), _last_result(db, a.id)) for a in analyses]
    by_id = {a["id"]: a for a in a_out}
    figures = db.execute(select(Figure).where(Figure.project_id == p.id).order_by(Figure.created_at)).scalars().all()
    return {
        **project_summary(db, p, user, m),
        "permissions": sorted(a.value for a in allowed_actions(db, user, p)),
        "datasets": [_dataset_meta(d) for d in datasets],
        "analyses": a_out,
        "figures": [_figure_out(db, f, by_id) for f in figures],
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


def touch(p: Project):
    p.updated_at = utcnow()


# ------------------------------------------------------------------ import


@app.post("/api/projects/{project_id}/import/preview")
async def import_preview(
    project_id: str,
    file: UploadFile | None = File(default=None),
    text: str | None = Form(default=None),
    delimiter: str | None = Form(default=None),
    decimal: str | None = Form(default=None),
    header: str | None = Form(default=None),
    sheet: str | None = Form(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
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


def _results_for(db: Session, project_id: str, cfg: dict) -> dict:
    out = {}
    for ann in cfg.get("annotations") or []:
        aid = ann.get("analysis_id")
        if ann.get("type") != "pvalue" or not aid or aid in out:
            continue
        a = db.get(Analysis, aid)
        if a is None or a.project_id != project_id:
            continue
        last = _last_result(db, aid)
        if last is None:
            continue
        d = db.get(Dataset, a.dataset_id)
        out[aid] = {"result": last.result, "stale": last.dataset_version != d.version or last.analysis_version != a.version,
                    "value_col": (a.config or {}).get("value_col"), "dataset_id": a.dataset_id}
    return out


def _render(db: Session, project_id: str, dataset_id: str, cfg: dict, lang: str) -> dict:
    d = _get_dataset(db, project_id, dataset_id)
    try:
        return render_figure(d.content, cfg, _results_for(db, project_id, cfg), lang)
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
    f.version += 1
    f.updated_at = utcnow()
    touch(p)
    db.commit()
    return {"id": f.id, "version": f.version}


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


# ------------------------------------------------------------------ archive


ARCHIVE_FORMAT = "fiole-project"
ARCHIVE_VERSION = 1


@app.get("/api/projects/{project_id}/archive")
def export_archive(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = require(db, user, project_id, Action.EXPORT)
    datasets = db.execute(select(Dataset).where(Dataset.project_id == p.id)).scalars().all()
    analyses = db.execute(select(Analysis).where(Analysis.project_id == p.id)).scalars().all()
    figures = db.execute(select(Figure).where(Figure.project_id == p.id)).scalars().all()
    doc = {
        "format": ARCHIVE_FORMAT,
        "format_version": ARCHIVE_VERSION,
        "exported_at": utcnow().isoformat(),
        "engine": ENGINE,
        "project": {"name": p.name, "description": p.description},
        "datasets": [{"id": d.id, "name": d.name, "version": d.version, "content": d.content} for d in datasets],
        "analyses": [
            {"id": a.id, "dataset_id": a.dataset_id, "name": a.name, "kind": a.kind, "config": a.config,
             "last_result": (lambda r: {"dataset_version": r.dataset_version, "result": r.result} if r else None)(_last_result(db, a.id))}
            for a in analyses
        ],
        "figures": [{"id": f.id, "dataset_id": f.dataset_id, "name": f.name, "config": f.config} for f in figures],
    }
    return Response(json.dumps(doc, ensure_ascii=False, indent=1), media_type="application/json",
                    headers={"Content-Disposition": disposition(_safe(p.name) + f".fiole.json")})


@app.post("/api/projects/import-archive")
async def import_archive(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    try:
        doc = json.loads((await file.read()).decode("utf-8"))
    except Exception:
        raise HTTPException(422, detail={"code": "archive_unreadable"})
    if doc.get("format") != ARCHIVE_FORMAT:
        raise HTTPException(422, detail={"code": "archive_unreadable"})
    p = Project(name=doc["project"]["name"], description=doc["project"].get("description", ""), owner_id=user.id)
    db.add(p)
    db.flush()
    db.add(ProjectMember(project_id=p.id, user_id=user.id, role=Role.owner, added_by=user.id, last_opened_at=utcnow()))
    ds_map, a_map = {}, {}
    for d in doc.get("datasets", []):
        nd = Dataset(project_id=p.id, name=d["name"], content=validate_content(d["content"]), updated_by=user.id)
        db.add(nd)
        db.flush()
        db.add(DatasetVersion(dataset_id=nd.id, version=1, content=nd.content, change_note="imported from archive", created_by=user.id))
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
        db.add(Figure(project_id=p.id, dataset_id=ds_map[f["dataset_id"]], name=f["name"], kind=cfg.get("type", "scatter_groups"), config=cfg))
    audit(db, user, p.id, "project.import_archive", "project", p.id)
    db.commit()
    # Results are recomputed rather than trusted from the file.
    for old_id, new_id in a_map.items():
        a = db.get(Analysis, new_id)
        d = db.get(Dataset, a.dataset_id)
        res = run_analysis(a.kind, d.content, a.config)
        res["dataset"] = {"id": d.id, "name": d.name, "version": d.version}
        db.add(AnalysisResult(analysis_id=a.id, analysis_version=a.version, dataset_version=d.version, result=res, engine_version=ENGINE, created_by=user.id))
    db.commit()
    return {"id": p.id, "url": f"/p/{p.id}"}


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
