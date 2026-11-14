"""API tests against a real Postgres test database (FIOLE_DATABASE_URL)."""
import io
import os

os.environ.setdefault("FIOLE_DATABASE_URL", "postgresql+psycopg2://app:app@localhost:5432/statsapp_test")

import openpyxl  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def client(email="alice@example.org"):
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": email, "password": "correct horse", "display_name": email.split("@")[0]})
    assert r.status_code == 200, r.text
    return c


def test_full_flow():
    c = client()
    assert c.get("/api/auth/me").json()["email"] == "alice@example.org"
    p = c.post("/api/projects", json={"name": "Expérience 1"}).json()
    pid = p["id"]
    assert p["url"] == f"/p/{pid}" and p["role"] == "owner"

    # Paste French-formatted data (Excel clipboard → TSV, decimal comma, a missing value)
    text = "Contrôle\tTraité\n12,1\t16,0\n14,3\t13,1\n9,8\tNA\n11,6\t18,9\n13,9\t15,5\n10,4\t14,8\n15,2\t19,7\n"
    prev = c.post(f"/api/projects/{pid}/import/preview", data={"text": text}).json()
    assert prev["detected"]["delimiter"] == "\t" and prev["detected"]["decimal"] == ","
    assert prev["missing_count"] == 1
    assert prev["rows"][2]["cells"]["c2"] is None  # never 0
    content = {"columns": prev["columns"], "rows": prev["rows"], "design": {"layout": "wide", "group_columns": ["c1", "c2"]}}
    d = c.post(f"/api/projects/{pid}/datasets", json={"name": "Mesures", "content": content}).json()
    assert d["version"] == 1

    a = c.post(f"/api/projects/{pid}/analyses", json={"dataset_id": d["id"], "kind": "ttest", "name": "Welch", "config": {"variant": "welch"}}).json()
    assert a["status"] == "never_run"
    a = c.post(f"/api/projects/{pid}/analyses/{a['id']}/run").json()
    assert a["status"] == "up_to_date"
    res = a["last_result"]["result"]
    assert res["status"] == "ok" and res["groups"][1]["n"] == 6 and res["groups"][1]["n_missing"] == 1

    # Figure with p-value from the saved analysis
    fig = c.post(f"/api/projects/{pid}/figures", json={"dataset_id": d["id"], "name": "Fig 1",
                 "config": {"type": "scatter_groups", "width_mm": 60, "height_mm": 50, "annotations": [{"type": "pvalue", "analysis_id": a["id"]}]}}).json()
    svg = c.get(f"/api/projects/{pid}/figures/{fig['id']}/export?format=svg").text
    assert 'width="60mm"' in svg and "pvalue-bracket" in svg
    pdf = c.get(f"/api/projects/{pid}/figures/{fig['id']}/export?format=pdf").content
    assert pdf.startswith(b"%PDF")
    png = c.get(f"/api/projects/{pid}/figures/{fig['id']}/export?format=png&dpi=300").content
    import struct
    w, h = struct.unpack(">II", png[16:24])
    assert w == round(60 / 25.4 * 300) and h == round(50 / 25.4 * 300)

    # Editing data makes the analysis stale; optimistic version check
    content["rows"][0]["cells"]["c1"] = 13.0
    r = c.put(f"/api/projects/{pid}/datasets/{d['id']}", json={"content": content, "version": 1, "change_note": "fix typo"})
    assert r.status_code == 200 and r.json()["version"] == 2
    r = c.put(f"/api/projects/{pid}/datasets/{d['id']}", json={"content": content, "version": 1})
    assert r.status_code == 409 and r.json()["detail"]["current_version"] == 2
    proj = c.get(f"/api/projects/{pid}").json()
    assert proj["analyses"][0]["status"] == "stale"
    assert proj["figures"][0]["stale"] is True
    assert len(c.get(f"/api/projects/{pid}/datasets/{d['id']}/versions").json()) == 2

    # Exports
    csv_text = c.get(f"/api/projects/{pid}/datasets/{d['id']}/export.csv").text
    assert csv_text.splitlines()[0].startswith("Contrôle,Traité")
    assert "p (display)" in c.get(f"/api/projects/{pid}/results.csv").text

    # Archive round trip
    arch = c.get(f"/api/projects/{pid}/archive").content
    r = c.post("/api/projects/import-archive", files={"file": ("x.fiole.json", arch, "application/json")})
    assert r.status_code == 200
    p2 = c.get(f"/api/projects/{r.json()['id']}").json()
    assert len(p2["datasets"]) == 1 and len(p2["analyses"]) == 1 and len(p2["figures"]) == 1
    assert p2["analyses"][0]["status"] == "up_to_date"

    # Dashboard
    assert len(c.get("/api/projects?scope=mine").json()) == 2
    assert c.get("/api/projects?scope=shared").json() == []
    assert len(c.get("/api/projects?scope=recent").json()) >= 1


def test_authorization_is_enforced():
    alice = client("owner2@example.org")
    pid = alice.post("/api/projects", json={"name": "Private"}).json()["id"]
    bob = client("bob@example.org")
    # No membership → looks like it does not exist
    assert bob.get(f"/api/projects/{pid}").status_code == 404
    assert bob.post(f"/api/projects/{pid}/import/preview", data={"text": "a\n1"}).status_code == 404
    anon = TestClient(app)
    assert anon.get(f"/api/projects/{pid}").status_code == 401


def test_excel_import_with_sheets():
    c = client("excel@example.org")
    pid = c.post("/api/projects", json={"name": "Excel"}).json()["id"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Notes"
    ws.append(["nothing here"])
    ws2 = wb.create_sheet("Data")
    ws2.append(["WT", "KO"])
    ws2.append([1.5, 2.5])
    ws2.append([None, 3.0])
    ws2.append([1.7, "n.d."])
    buf = io.BytesIO()
    wb.save(buf)
    r = c.post(f"/api/projects/{pid}/import/preview", files={"file": ("data.xlsx", buf.getvalue(), "application/octet-stream")}).json()
    assert r["detected"]["sheets"] == ["Notes", "Data"]
    r2 = c.post(f"/api/projects/{pid}/uploads/{r['upload_id']}/reparse", json={"sheet": "Data"}).json()
    assert [col["name"] for col in r2["columns"]] == ["WT", "KO"]
    assert r2["rows"][1]["cells"]["c1"] is None and r2["rows"][2]["cells"]["c2"] is None
    assert r2["missing_count"] == 2


def test_bad_login():
    c = client("carol@example.org")
    c2 = TestClient(app)
    assert c2.post("/api/auth/login", json={"email": "carol@example.org", "password": "wrong"}).status_code == 401
    assert c2.post("/api/auth/login", json={"email": "carol@example.org", "password": "correct horse"}).status_code == 200


def test_figure_styles_save_apply_export_import_and_privacy():
    c = client("styler@example.org")
    cfg = {"type": "box", "group_col": "g", "value_col": "a", "y_min": 6, "y_max": 9, "title": "Mine", "annotations": [{"type": "pvalue"}],
           "font_size_pt": 9, "line_width_pt": 1.25, "frame": "closed", "tick_direction": "in", "palette": "tol_bright", "p_format": "exact",
           "star_thresholds": [0.05, 0.01, 0.001, 0.0001], "width_mm": 120}
    st = c.post("/api/styles", json={"name": "Style de Gab", "from_config": cfg}).json()
    assert st["name"] == "Style de Gab" and st["visibility"] == "private" and st["mine"]
    # appearance only: no data, columns, axis ranges, titles, annotations or size
    assert st["settings"] == {"font_size_pt": 9, "line_width_pt": 1.25, "frame": "closed", "tick_direction": "in", "palette": "tol_bright",
                              "p_format": "exact", "star_thresholds": [0.05, 0.01, 0.001, 0.0001]}
    lst = c.get("/api/styles").json()
    assert [s["name"] for s in lst["mine"]] == ["Style de Gab"] and lst["sharing_available"] is False
    # rename with optimistic version; stale version -> 409
    r = c.put(f"/api/styles/{st['id']}", json={"name": "Gab 2", "version": st["version"]})
    assert r.status_code == 200 and r.json()["name"] == "Gab 2"
    assert c.put(f"/api/styles/{st['id']}", json={"name": "x", "version": st["version"]}).status_code == 409
    # sharing is not available yet
    r = c.put(f"/api/styles/{st['id']}", json={"visibility": "public", "version": r.json()["version"]})
    assert r.status_code == 400 and r.json()["detail"]["code"] == "sharing_not_available"
    # export / import round trip
    r = c.get(f"/api/styles/{st['id']}/export")
    assert r.status_code == 200 and ".fiole-style.json" in r.headers["content-disposition"]
    f = r.json()
    assert f["format"] == "fiole-style" and f["settings"]["frame"] == "closed"
    imp = c.post("/api/styles/import", json=f).json()
    assert imp["settings"] == st["settings"] and imp["id"] != st["id"]
    assert c.post("/api/styles/import", json={"hello": 1}).status_code == 422
    cp = c.post(f"/api/styles/{st['id']}/copy").json()
    assert cp["source_style_id"] == st["id"] and "copie" in cp["name"]
    # another user can neither see nor touch a private style
    other = client("other-styler@example.org")
    assert other.get("/api/styles").json()["mine"] == []
    assert other.get(f"/api/styles/{st['id']}/export").status_code == 404
    assert other.post(f"/api/styles/{st['id']}/copy").status_code == 404
    assert other.delete(f"/api/styles/{st['id']}").status_code == 404
    assert c.delete(f"/api/styles/{st['id']}").status_code == 200
    assert len(c.get("/api/styles").json()["mine"]) == 2
