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
    assert [s["name"] for s in lst["mine"]] == ["Style de Gab"] and lst["sharing_available"] is True
    # rename with optimistic version; stale version -> 409
    r = c.put(f"/api/styles/{st['id']}", json={"name": "Gab 2", "version": st["version"]})
    assert r.status_code == 200 and r.json()["name"] == "Gab 2"
    assert c.put(f"/api/styles/{st['id']}", json={"name": "x", "version": st["version"]}).status_code == 409
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


def test_duplicate_per_variable_and_layout_export():
    c = client("panels@example.org")
    pid = c.post("/api/projects", json={"name": "Panels"}).json()["id"]
    rows = [{"id": f"r{i}", "cells": {"g": "Col" if i % 2 else "mut", "a": 5 + i % 4 + (i % 2) * 2, "b": 20 - i % 3 - (i % 2) * 4, "n": 1 + i * 0.1}} for i in range(16)]
    content = {"columns": [{"id": "g", "name": "Genotype", "type": "text"}, {"id": "a", "name": "N%", "type": "numeric"},
                           {"id": "b", "name": "C%", "type": "numeric"}, {"id": "n", "name": "Dry weight", "type": "numeric"}],
               "rows": rows, "design": {"layout": "wide", "group_columns": []}}
    did = c.post(f"/api/projects/{pid}/datasets", json={"name": "T", "content": content}).json()["id"]
    an = c.post(f"/api/projects/{pid}/analyses", json={"dataset_id": did, "kind": "pairwise", "name": "Étoiles – Fig – N%",
                                                       "config": {"group_col": "g", "value_col": "a", "mode": "all", "test": "welch", "correction": "holm"}}).json()
    c.post(f"/api/projects/{pid}/analyses/{an['id']}/run")
    fig = c.post(f"/api/projects/{pid}/figures", json={"dataset_id": did, "name": "Fig – N%", "config": {
        "type": "box", "group_col": "g", "value_col": "a", "y_min": 0, "y_max": 12, "frame": "closed",
        "annotations": [{"type": "pvalue", "analysis_id": an["id"], "all": True}, {"type": "text", "text": "note"}]}}).json()

    # plain copy: everything identical, same analyses
    cp = c.post(f"/api/projects/{pid}/figures/{fig['id']}/duplicate", json={}).json()
    assert len(cp) == 1 and cp[0]["name"] == "Fig – N% (copie)" and cp[0]["config"] == fig["config"]

    # one figure per variable: style kept, axis range reset, stars recomputed on that variable
    out = c.post(f"/api/projects/{pid}/figures/{fig['id']}/duplicate", json={"columns": ["b", "n"]}).json()
    assert [f["name"] for f in out] == ["Fig – C%", "Fig – Dry weight"]
    fb = out[0]["config"]
    assert fb["value_col"] == "b" and fb["frame"] == "closed" and fb["y_min"] is None and fb["y_max"] is None
    star = [a for a in fb["annotations"] if a["type"] == "pvalue"][0]
    assert star["analysis_id"] != an["id"]
    assert any(a["type"] == "text" for a in fb["annotations"])
    proj = c.get(f"/api/projects/{pid}").json()
    new_an = [a for a in proj["analyses"] if a["id"] == star["analysis_id"]][0]
    assert new_an["config"]["value_col"] == "b" and new_an["status"] == "up_to_date" and new_an["name"] == "Étoiles – Fig – C%"
    assert not [f for f in proj["figures"] if f["id"] == out[0]["id"]][0]["stale"]
    # back to the original variable restores its saved axis range
    back = c.post(f"/api/projects/{pid}/figures/{out[0]['id']}/duplicate", json={"columns": ["a"]}).json()[0]["config"]
    assert back["y_max"] == 12 and [a for a in back["annotations"] if a["type"] == "pvalue"][0]["analysis_id"] == an["id"]
    assert c.post(f"/api/projects/{pid}/figures/{fig['id']}/duplicate", json={"columns": ["zz"]}).status_code == 422

    # layout: 2 x 2 page, three panels, letters A B C, exports
    lay = c.post(f"/api/projects/{pid}/layouts", json={"name": "Figure 1", "config": {
        "width_mm": 180, "height_mm": 140, "rows": 2, "cols": 2,
        "panels": [{"cell": 0, "figure_id": fig["id"]}, {"cell": 1, "figure_id": out[0]["id"]}, {"cell": 3, "figure_id": out[1]["id"]},
                   {"cell": 9, "figure_id": fig["id"]}, {"cell": 2, "figure_id": "nope"}]}}).json()
    assert [p["cell"] for p in lay["config"]["panels"]] == [0, 1, 3]  # invalid cells / figures dropped
    r = c.post(f"/api/projects/{pid}/layouts/render", json={"config": lay["config"]}).json()
    svg = r["svg"]
    assert svg.startswith("<svg") and 'width="180mm"' in svg
    import re
    assert re.findall(r'data-el="panel-label"[^>]*>([^<]+)<', svg) == ["A", "B", "C"]
    assert len(set(re.findall(r'id="([^"]+)"', svg))) == len(re.findall(r'id="([^"]+)"', svg))  # ids unique
    assert c.get(f"/api/projects/{pid}").json()["layouts"][0]["name"] == "Figure 1"
    for fmt, ctype in (("svg", "image/svg+xml"), ("pdf", "application/pdf"), ("png", "image/png")):
        e = c.get(f"/api/projects/{pid}/layouts/{lay['id']}/export?format={fmt}&dpi=150")
        assert e.status_code == 200 and e.headers["content-type"].startswith(ctype), fmt
    # unequal column widths, version conflict
    u = c.put(f"/api/projects/{pid}/layouts/{lay['id']}", json={"config": {**lay["config"], "col_widths": [2, 1], "label_style": "(a)"}, "version": lay["version"]}).json()
    assert u["config"]["col_widths"] == [2, 1]
    assert c.put(f"/api/projects/{pid}/layouts/{lay['id']}", json={"name": "x", "version": lay["version"]}).status_code == 409
    svg2 = c.post(f"/api/projects/{pid}/layouts/render", json={"config": u["config"]}).json()
    assert re.findall(r'data-el="panel-label"[^>]*>([^<]+)<', svg2["svg"])[0] == "(a)"
    assert abs(svg2["cells"][0]["w"] - 2 * svg2["cells"][1]["w"]) < 0.01
    # a deleted figure leaves an empty cell
    c.delete(f"/api/projects/{pid}/figures/{out[1]['id']}")
    assert len(re.findall(r'data-el="panel-label"', c.post(f"/api/projects/{pid}/layouts/render", json={"config": u["config"]}).json()["svg"])) == 2
    # archive round trip keeps the layout
    arch = c.get(f"/api/projects/{pid}/archive").content
    imp = c.post("/api/projects/import-archive", files={"file": ("p.fiole.json", arch, "application/json")}).json()
    lays = c.get(f"/api/projects/{imp['id']}").json()["layouts"]
    assert len(lays) == 1 and len(lays[0]["config"]["panels"]) == 2
    # viewers cannot duplicate or edit layouts
    v = client("panels-viewer@example.org")
    c.post(f"/api/projects/{pid}/members", json={"emails": ["panels-viewer@example.org"], "role": "viewer", "notify": False})
    assert v.post(f"/api/projects/{pid}/figures/{fig['id']}/duplicate", json={}).status_code == 403
    assert v.post(f"/api/projects/{pid}/layouts", json={"name": "x"}).status_code == 403
    assert v.get(f"/api/projects/{pid}/layouts/{lay['id']}/export?format=svg").status_code == 200


def test_bulk_delete_impact_and_undo():
    c = client("cleanup@example.org")
    pid = c.post("/api/projects", json={"name": "Ménage"}).json()["id"]
    rows = [{"id": f"r{i}", "cells": {"g": "A" if i % 2 else "B", "v": i + (i % 2) * 3}} for i in range(12)]
    did = c.post(f"/api/projects/{pid}/datasets", json={"name": "T", "content": {"columns": [{"id": "g", "name": "G", "type": "text"}, {"id": "v", "name": "V", "type": "numeric"}],
                                                                                "rows": rows, "design": {"layout": "wide", "group_columns": []}}}).json()["id"]
    an = c.post(f"/api/projects/{pid}/analyses", json={"dataset_id": did, "kind": "pairwise", "name": "Étoiles", "config": {"group_col": "g", "value_col": "v"}}).json()
    c.post(f"/api/projects/{pid}/analyses/{an['id']}/run")
    other = c.post(f"/api/projects/{pid}/analyses", json={"dataset_id": did, "kind": "ttest", "name": "Brouillon", "config": {}}).json()
    f1 = c.post(f"/api/projects/{pid}/figures", json={"dataset_id": did, "name": "Figure 1", "config": {"type": "box", "group_col": "g", "value_col": "v",
               "annotations": [{"type": "pvalue", "analysis_id": an["id"], "all": True}, {"type": "text", "text": "x"}]}}).json()
    f2 = c.post(f"/api/projects/{pid}/figures", json={"dataset_id": did, "name": "Figure 2", "config": {"type": "box", "group_col": "g", "value_col": "v"}}).json()
    lay = c.post(f"/api/projects/{pid}/layouts", json={"name": "Page", "config": {"rows": 1, "cols": 2, "panels": [{"cell": 0, "figure_id": f1["id"]}, {"cell": 1, "figure_id": f2["id"]}]}}).json()

    # dry run: says what is affected, deletes nothing
    r = c.post(f"/api/projects/{pid}/bulk-delete", json={"analyses": [an["id"], other["id"]], "figures": [f2["id"]], "dry_run": True}).json()
    assert r["trash_id"] is None
    assert r["impact"]["figures_losing_stars"] == [{"id": f1["id"], "name": "Figure 1", "count": 1}]
    assert r["impact"]["layouts_losing_panels"] == [{"id": lay["id"], "name": "Page", "count": 1}]
    assert len(c.get(f"/api/projects/{pid}").json()["analyses"]) == 2

    r = c.post(f"/api/projects/{pid}/bulk-delete", json={"analyses": [an["id"], other["id"]], "figures": [f2["id"]]}).json()
    proj = c.get(f"/api/projects/{pid}").json()
    assert proj["analyses"] == [] and [f["name"] for f in proj["figures"]] == ["Figure 1"]
    fig1 = proj["figures"][0]
    assert [a["type"] for a in fig1["config"]["annotations"]] == ["text"] and not fig1["stale"]
    assert proj["layouts"][0]["config"]["panels"] == [{"cell": 0, "figure_id": f1["id"]}]

    # undo puts everything back, including results, stars and the layout panel
    assert c.post(f"/api/projects/{pid}/trash/{r['trash_id']}/restore").json()["ok"]
    proj = c.get(f"/api/projects/{pid}").json()
    assert sorted(a["name"] for a in proj["analyses"]) == ["Brouillon", "Étoiles"]
    st = [a for a in proj["analyses"] if a["id"] == an["id"]][0]
    assert st["status"] == "up_to_date"
    assert sorted(f["name"] for f in proj["figures"]) == ["Figure 1", "Figure 2"]
    fig1 = [f for f in proj["figures"] if f["id"] == f1["id"]][0]
    assert any(a.get("analysis_id") == an["id"] for a in fig1["config"]["annotations"])
    assert len(proj["layouts"][0]["config"]["panels"]) == 2
    assert c.post(f"/api/projects/{pid}/trash/{r['trash_id']}/restore").status_code == 409
    # viewers cannot delete
    v = client("cleanup-viewer@example.org")
    c.post(f"/api/projects/{pid}/members", json={"emails": ["cleanup-viewer@example.org"], "role": "viewer", "notify": False})
    assert v.post(f"/api/projects/{pid}/bulk-delete", json={"figures": [f1["id"]]}).status_code == 403
