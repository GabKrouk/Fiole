"""Dashboard folders and .fio project files."""
import io
import json
import os
import zipfile

os.environ.setdefault("FIOLE_DATABASE_URL", "postgresql+psycopg2://app:app@localhost:5432/statsapp_test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


def client(email):
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": email, "password": "correct horse", "display_name": email.split("@")[0]})
    assert r.status_code == 200, r.text
    return c


def test_folders():
    c = client("fold@example.org")
    a = c.post("/api/folders", json={"name": "Thèse"}).json()
    b = c.post("/api/folders", json={"name": "Chapitre 1", "parent_id": a["id"]}).json()
    p1 = c.post("/api/projects", json={"name": "P1", "folder_id": b["id"]}).json()
    assert p1["folder_id"] == b["id"]
    p2 = c.post("/api/projects", json={"name": "P2"}).json()
    assert c.put(f"/api/projects/{p2['id']}/folder", json={"folder_id": a["id"]}).status_code == 200
    mine = {p["name"]: p["folder_id"] for p in c.get("/api/projects?scope=mine").json()}
    assert mine == {"P1": b["id"], "P2": a["id"]}
    fs = {f["name"]: f for f in c.get("/api/folders").json()}
    assert fs["Thèse"]["n_projects"] == 1 and fs["Thèse"]["n_folders"] == 1 and fs["Chapitre 1"]["n_projects"] == 1
    # rename, no cycles
    assert c.patch(f"/api/folders/{a['id']}", json={"name": "Doctorat"}).json()["name"] == "Doctorat"
    assert c.patch(f"/api/folders/{a['id']}", json={"parent_id": b["id"]}).status_code == 422
    assert c.patch(f"/api/folders/{a['id']}", json={"parent_id": a["id"]}).status_code == 422
    # someone else cannot see or use my folders
    o = client("other-fold@example.org")
    assert o.get("/api/folders").json() == []
    assert o.post("/api/projects", json={"name": "x", "folder_id": a["id"]}).status_code == 404
    assert o.put(f"/api/projects/{p2['id']}/folder", json={"folder_id": None}).status_code == 404
    # a project shared with me can be filed in my folders, and then shows in "my projects"
    c.post(f"/api/projects/{p2['id']}/members", json={"emails": ["other-fold@example.org"], "role": "viewer", "notify": False})
    assert o.get("/api/projects?scope=mine").json() == []
    of = o.post("/api/folders", json={"name": "Labo"}).json()
    assert o.put(f"/api/projects/{p2['id']}/folder", json={"folder_id": of["id"]}).status_code == 200
    assert [(p["name"], p["folder_id"]) for p in o.get("/api/projects?scope=mine").json()] == [("P2", of["id"])]
    assert c.get("/api/projects?scope=mine").json()[0]["folder_id"] in (a["id"], b["id"])  # the owner's own filing is untouched
    # deleting a folder keeps its contents: they move up one level
    assert c.delete(f"/api/folders/{b['id']}").json()["parent_id"] == a["id"]
    mine = {p["name"]: p["folder_id"] for p in c.get("/api/projects?scope=mine").json()}
    assert mine == {"P1": a["id"], "P2": a["id"]}
    c.delete(f"/api/folders/{a['id']}")
    assert all(p["folder_id"] is None for p in c.get("/api/projects?scope=mine").json())
    assert c.get("/api/folders").json() == []
    # back to the top level
    f3 = c.post("/api/folders", json={"name": "Z"}).json()
    c.put(f"/api/projects/{p1['id']}/folder", json={"folder_id": f3["id"]})
    c.put(f"/api/projects/{p1['id']}/folder", json={"folder_id": None})
    assert c.get("/api/folders").json()[0]["n_projects"] == 0


def test_fio_round_trip():
    c = client("fio@example.org")
    folder = c.post("/api/folders", json={"name": "Partages"}).json()
    pid = c.post("/api/projects", json={"name": "Croissance"}).json()["id"]
    text = "Contrôle\tTraité\n12,1\t16,0\n14,3\t13,1\n9,8\tNA\n11,6\t18,9\n13,9\t15,5\n"
    prev = c.post(f"/api/projects/{pid}/import/preview", files={"file": ("mesures.tsv", text.encode(), "text/tab-separated-values")}).json()
    content = {"columns": prev["columns"], "rows": prev["rows"], "design": {"layout": "wide", "group_columns": ["c1", "c2"]}}
    d = c.post(f"/api/projects/{pid}/datasets", json={"name": "Mesures", "content": content, "upload_id": prev.get("upload_id")}).json()
    content["rows"][0]["cells"]["c1"] = 12.5
    d = c.put(f"/api/projects/{pid}/datasets/{d['id']}", json={"content": content, "version": d["version"], "change_note": "typo"}).json()
    assert d["version"] == 2
    a = c.post(f"/api/projects/{pid}/analyses", json={"dataset_id": d["id"], "kind": "ttest", "name": "Welch", "config": {"variant": "welch"}}).json()
    c.post(f"/api/projects/{pid}/analyses/{a['id']}/run")
    f = c.post(f"/api/projects/{pid}/figures", json={"dataset_id": d["id"], "name": "Fig", "config": {"type": "scatter_groups", "font_size": 13}}).json()
    c.post(f"/api/projects/{pid}/layouts", json={"name": "Planche", "config": {"rows": 1, "cols": 1, "panels": [{"cell": 0, "figure_id": f["id"]}]}})
    c.post("/api/styles", json={"name": "Revue", "settings": {"font_size": 9, "font_family": "Arial"}})

    r = c.get(f"/api/projects/{pid}/archive")
    assert r.status_code == 200 and 'Croissance.fio' in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    doc = json.loads(z.read("fiole.json"))
    assert doc["format"] == "fiole-project" and doc["format_version"] == 2
    assert [h["version"] for h in doc["datasets"][0]["history"]] == [1, 2]
    assert doc["styles"][0]["name"] == "Revue"
    assert z.read(doc["uploads"][0]["path"]) == text.encode()

    # someone else opens the file: a full project of theirs, in their folder, with my style added to theirs
    b = client("fio-friend@example.org")
    bf = b.post("/api/folders", json={"name": "Reçus"}).json()
    o = b.post("/api/projects/import-archive", files={"file": ("Croissance.fio", r.content, "application/octet-stream")},
               data={"folder_id": bf["id"]}).json()
    assert o["name"] == "Croissance" and o["styles_added"] == 1 and o["folder_id"] == bf["id"]
    p2 = b.get(f"/api/projects/{o['id']}").json()
    assert len(p2["datasets"]) == 1 and len(p2["analyses"]) == 1 and len(p2["figures"]) == 1 and len(p2["layouts"]) == 1
    assert p2["analyses"][0]["status"] == "up_to_date"
    assert p2["layouts"][0]["config"]["panels"][0]["figure_id"] == p2["figures"][0]["id"]
    ds2 = p2["datasets"][0]
    assert ds2["version"] == 2
    assert [v["version"] for v in b.get(f"/api/projects/{o['id']}/datasets/{ds2['id']}/versions").json()] in ([2, 1], [1, 2])
    assert b.get(f"/api/projects/{o['id']}/datasets/{ds2['id']}").json()["content"]["rows"][0]["cells"]["c1"] == 12.5
    assert [s["name"] for s in b.get("/api/styles").json()["mine"]] == ["Revue"]
    assert b.get("/api/projects?scope=mine").json()[0]["folder_id"] == bf["id"]
    # opening it again does not add the style twice
    assert b.post("/api/projects/import-archive", files={"file": ("Croissance.fio", r.content)}).json()["styles_added"] == 0
    # someone cannot drop a file in a folder that is not theirs
    assert c.post("/api/projects/import-archive", files={"file": ("x.fio", r.content)}, data={"folder_id": bf["id"]}).status_code == 404

    # older .fiole.json exports still open; anything else is refused
    old = {k: v for k, v in doc.items() if k not in ("uploads", "styles")}
    old["format_version"] = 1
    assert c.post("/api/projects/import-archive", files={"file": ("x.fiole.json", json.dumps(old).encode())}).status_code == 200
    assert c.post("/api/projects/import-archive", files={"file": ("x.fio", b"PK\x03\x04garbage")}).status_code == 422
    assert c.post("/api/projects/import-archive", files={"file": ("x.fio", b"hello")}).status_code == 422
