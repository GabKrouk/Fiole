"""Time-course figures: one panel per Y variable, close X values pooled, stars at each X, and the
figure gallery built from the user's own table (real data: oryzalin 15N labelling, GAB-1-2026)."""
import os
import re

os.environ.setdefault("FIOLE_DATABASE_URL", "postgresql+psycopg2://app:app@localhost:5432/statsapp_test")

from scipy import stats as sps  # noqa: E402

from app import importers  # noqa: E402
from app.figures.render import close_x_clusters, render_figure, x_merge_map  # noqa: E402
from app.figures.suggest import suggestions  # noqa: E402

HERE = os.path.dirname(__file__)


def table():
    t = importers.parse_text(open(os.path.join(HERE, "reference", "oryzalin_15N.tsv"), encoding="utf-8").read())
    col = {c["name"].strip(): c["id"] for c in t["columns"]}
    r23 = next(r for r in t["rows"] if r["cells"][col["Echantillon"]] == 23)
    t["exclusions"] = [{"row_id": r23["id"], "reason": "N = 0"}]
    return t, col


def test_close_x_values_are_pooled():
    assert close_x_clusters([21, 23, 59, 60, 180, 332, 334, 1540]) == [[21, 23], [59, 60], [180], [332, 334], [1540]]
    m = x_merge_map([21, 23, 59, 60, 180, 332, 334, 1540])
    assert m[21] == m[23] == 22 and m[59] == m[60] == 60 and m[180] == 180 and m[332] == m[334] == 330 and m[1540] == 1540


def test_hint_then_pooled_line():
    t, col = table()
    cfg = {"type": "line", "x": col["time"], "y": [col["Excès 15N (atom%)"]], "color_by": col["Treat"], "error": "sem"}
    r = render_figure(t, cfg, {}, "fr")
    hint = [w for w in r["warnings"] if w.get("action")]
    assert hint and hint[0]["action"]["patch"] == {"x_merge": True}
    r = render_figure(t, {**cfg, "x_merge": True}, {}, "fr")
    assert not [w for w in r["warnings"] if w.get("action")]
    assert "21 / 23 → 22" in r["caption"]


def test_stars_at_each_x_match_welch():
    t, col = table()
    y, x, g = col["Excès 15N (atom%)"], col["time"], col["Treat"]
    cfg = {"type": "line", "x": x, "y": [y], "color_by": g, "error": "sem", "x_merge": True, "x_stars": True, "p_format": "exact"}
    r = render_figure(t, cfg, {}, "en")
    vals = {"ctrl": [], "oryzalin": []}
    for row in t["rows"]:
        if row["cells"][x] == 1540:
            vals[row["cells"][g]].append(row["cells"][y])
    p = sps.ttest_ind(vals["ctrl"], vals["oryzalin"], equal_var=False).pvalue
    assert round(p, 3) == 0.006  # as in the report
    assert "p = 0.006" in r["svg"] or "p = 0.0063" in r["svg"] or "p = 0.00" in r["svg"]
    assert "Welch’s t-test (ctrl vs oryzalin)" in r["caption"]
    stars = render_figure(t, {**cfg, "p_format": "stars"}, {}, "en")["svg"]
    assert stars.count('data-el="x-stars"') == 1 and ">**<" in stars


def test_one_panel_per_variable():
    t, col = table()
    ys = [col[n] for n in ("Excès 15N (atom%)", "15N incorporé (µg/mg MS)", "delta 15 N", "% N", "% C", "C/N")]
    cfg = {"type": "line", "x": col["time"], "y": ys, "y_layout": "panels", "color_by": col["Treat"], "x_merge": True, "x_stars": True,
           "x_scale": "log10", "width_mm": 180, "height_mm": 110, "y_labels": {ys[2]: "δ<sup>15</sup>N (‰)"}, "title": "GAB-1-2026"}
    r = render_figure(t, cfg, {}, "fr")
    svg = r["svg"]
    assert svg.count("data-ycol=") == 6
    assert [m for m in re.findall(r'data-el="panel-label"[^>]*>([A-Z])<', svg)] == list("ABCDEF")
    assert svg.count('data-el="legend"') == 1  # one legend for the whole figure
    ids = re.findall(r'id="([^"]+)"', svg)
    assert len(ids) == len(set(ids))  # clip paths stay unique
    assert r["caption"].startswith("A, Excès 15N (atom%), B, 15N incorporé (µg/mg MS), C, δ15N (‰)")
    # overlay (older figures) is unchanged: one plot, one entry per variable × group
    over = render_figure(t, {**cfg, "y_layout": "overlay"}, {}, "fr")["svg"]
    assert "data-ycol=" not in over


def test_gallery_from_the_users_table():
    t, col = table()
    s = {x["id"]: x for x in suggestions(t)}
    assert {"panels", "line", "loglog", "bar", "box", "xy"} <= set(s)
    p = s["panels"]["config"]
    assert p["x"] == col["time"] and p["color_by"] == col["Treat"] and p["x_merge"] and p["x_stars"] and p["x_scale"] == "log10"
    assert col["Echantillon"] not in p["y"]  # a sample number is not a measurement
    # a table with only groups and one value: group figures only
    t2 = importers.parse_text("Génotype\tPoids\nWT\t1\nWT\t2\nmut\t3\nmut\t4\n")
    assert {x["id"] for x in suggestions(t2)} == {"bar", "dots", "box"}
    for x in suggestions(t) + suggestions(t2):
        render_figure(t if x in suggestions(t) else t2, x["config"], {}, "fr")


def test_gallery_endpoint():
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.main import app

    Base.metadata.create_all(engine)
    c = TestClient(app)
    assert c.post("/api/auth/register", json={"email": f"gallery-{os.getpid()}-{id(c)}@example.org", "password": "correct horse", "display_name": "g"}).status_code == 200
    pid = c.post("/api/projects", json={"name": "Oryzalin"}).json()["id"]
    t, _ = table()
    d = c.post(f"/api/projects/{pid}/datasets", json={"name": "T", "content": t}).json()
    out = c.post(f"/api/projects/{pid}/figure-suggestions", json={"dataset_id": d["id"], "lang": "fr", "style": {"font_family": "Times New Roman"}}).json()
    assert out and all(x["svg"].startswith("<svg") and x["config"]["font_family"] == "Times New Roman" for x in out)
    ids = [i for x in out for i in re.findall(r'id="([^"]+)"', x["svg"])]
    assert len(ids) == len(set(ids))


def test_excel_letterhead_is_skipped():
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws["A2"] = "Atelier de Quantifications isotopiques"
    ws["A4"] = "Nom de la Manipe :"
    ws["C4"] = "GAB-1-2026"
    for j, h in enumerate(["Echantillon", "% C ", "Treat", "time"]):
        ws.cell(row=8, column=3 + j, value=h)
    for i, (c, tr, tm) in enumerate([(40.1, "ctrl", 21), (39.2, "ctrl", 21), (33.0, "oryzalin", 23), (34.5, "oryzalin", 23)]):
        for j, v in enumerate([str(i + 1), c, tr, tm]):
            ws.cell(row=9 + i, column=3 + j, value=v)
    buf = io.BytesIO()
    wb.save(buf)
    t = importers.parse_excel(buf.getvalue())
    assert [c["name"] for c in t["columns"]] == ["Echantillon", "% C", "Treat", "time"]
    assert len(t["rows"]) == 4 and t["detected"]["located"]["first_row"] == 8
    assert any(w.get("code") == "table_located" for w in t["warnings"])
    # a sheet that starts with its table is untouched
    t2 = importers.parse_text("a\tb\n1\t2\n3\t4\n")
    assert [c["name"] for c in t2["columns"]] == ["a", "b"]


def test_bars_by_time_and_treatment():
    t, col = table()
    cfg = {"type": "bar", "group_col": col["time"], "value_col": col["Excès 15N (atom%)"], "color_by": col["Treat"], "error": "sem",
           "x_merge": True, "x_stars": True, "annotations": [{"type": "pvalue", "all": True}]}
    r = render_figure(t, cfg, {}, "fr")
    svg = r["svg"]
    labels = re.findall(r'data-el="group-label" data-group="([^"]+)"', svg)
    assert labels == ["22", "60", "180", "330", "1540"]  # numeric order, close times pooled
    assert 'data-el="legend"' in svg and svg.count('data-el="group" data-group=') == 2
    assert ">**<" in svg and "test t de Welch (ctrl contre oryzalin)" in r["caption"]
    # three treatments: each compared with the first
    rows = [dict(r, id=r["id"] + "x", cells={**r["cells"], col["Treat"]: "drugX"}) for r in t["rows"] if r["cells"][col["Treat"]] == "ctrl"]
    t3 = {**t, "rows": t["rows"] + rows}
    svg3 = render_figure(t3, {**cfg, "type": "box"}, {}, "en")["svg"]
    assert svg3.count('data-el="group" data-group=') == 3
    s = {x["id"]: x["config"] for x in suggestions(t)}
    assert s["clustered"]["group_col"] == col["time"] and s["clustered"]["color_by"] == col["Treat"]
