"""Phase 2: sharing between accounts, link access, access requests, style sharing, donation consent, admin export."""
import os

os.environ.setdefault("FIOLE_DATABASE_URL", "postgresql+psycopg2://app:app@localhost:5432/statsapp_test")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, mailer  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture(autouse=True)
def no_mail_provider(monkeypatch):
    for k in ("FIOLE_MAIL_FROM", "FIOLE_BREVO_API_KEY", "FIOLE_RESEND_API_KEY", "FIOLE_SMTP_HOST"):
        monkeypatch.delenv(k, raising=False)


def client(email, opt_in=False):
    c = TestClient(app)
    r = c.post("/api/auth/register", json={"email": email, "password": "correct horse", "display_name": email.split("@")[0], "donation_opt_in": opt_in})
    assert r.status_code == 200, r.text
    return c


def login(email):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": email, "password": "correct horse"}).status_code == 200
    return c


def project_with_data(c):
    pid = c.post("/api/projects", json={"name": "Partagé"}).json()["id"]
    content = {"columns": [{"id": "c1", "name": "A", "type": "numeric"}], "rows": [{"id": "r1", "cells": {"c1": 1}}], "design": {"layout": "wide", "group_columns": ["c1"]}}
    did = c.post(f"/api/projects/{pid}/datasets", json={"name": "T", "content": content}).json()["id"]
    return pid, did, content


def test_people_roles_revoke_and_server_enforcement():
    owner, ed, vw, stranger = client("own@x.org"), client("ed@x.org"), client("vw@x.org"), client("str@x.org")
    anon = TestClient(app)
    pid, did, content = project_with_data(owner)

    # nobody else can see it yet; anonymous gets 401
    for c in (ed, vw, stranger):
        assert c.get(f"/api/projects/{pid}").status_code == 404
    assert anon.get(f"/api/projects/{pid}").status_code == 401
    assert anon.get(f"/api/projects/{pid}/sharing").status_code == 401

    r = owner.post(f"/api/projects/{pid}/members", json={"emails": ["ED@x.org"], "role": "editor"}).json()
    assert r["added"] == ["ed@x.org"] and r["email"] == {"ed@x.org": "not_configured"} and r["email_configured"] is False
    owner.post(f"/api/projects/{pid}/members", json={"emails": ["vw@x.org"], "role": "viewer"})
    sh = owner.get(f"/api/projects/{pid}/sharing").json()
    assert [(m["email"], m["role"]) for m in sh["members"]] == [("own@x.org", "owner"), ("ed@x.org", "editor"), ("vw@x.org", "viewer")]

    # "Shared with me"
    assert [p["id"] for p in ed.get("/api/projects?scope=shared").json()] == [pid]
    assert ed.get("/api/projects?scope=mine").json() == []

    # editor edits, viewer only reads/exports
    pe = ed.get(f"/api/projects/{pid}").json()
    assert "edit" in pe["permissions"] and "manage_sharing" not in pe["permissions"] and pe["role"] == "editor"
    d = ed.get(f"/api/projects/{pid}/datasets/{did}").json()
    assert ed.put(f"/api/projects/{pid}/datasets/{did}", json={"content": content, "version": d["version"]}).status_code == 200
    pv = vw.get(f"/api/projects/{pid}").json()
    assert pv["permissions"] == ["export", "view"]
    assert vw.put(f"/api/projects/{pid}/datasets/{did}", json={"content": content, "version": 2}).status_code == 403
    assert vw.post(f"/api/projects/{pid}/analyses", json={"dataset_id": did, "kind": "ttest", "name": "x", "config": {}}).status_code == 403
    assert vw.patch(f"/api/projects/{pid}", json={"name": "hack", "version": 1}).status_code == 403
    assert vw.get(f"/api/projects/{pid}/archive").status_code == 200
    # neither editor nor viewer can manage sharing or delete
    assert ed.post(f"/api/projects/{pid}/members", json={"emails": ["str@x.org"]}).status_code == 403
    assert vw.put(f"/api/projects/{pid}/access", json={"general_access": "link_editor"}).status_code == 403
    assert ed.delete(f"/api/projects/{pid}").status_code == 403
    # sharing lists hide invites/requests from non-managers
    assert vw.get(f"/api/projects/{pid}/sharing").json()["can_manage"] is False

    # conflict detection between two editors
    stale = owner.get(f"/api/projects/{pid}/datasets/{did}").json()["version"]
    assert ed.put(f"/api/projects/{pid}/datasets/{did}", json={"content": content, "version": stale}).status_code == 200
    r = owner.put(f"/api/projects/{pid}/datasets/{did}", json={"content": content, "version": stale})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_conflict"

    # owner can let editors share
    owner.put(f"/api/projects/{pid}/access", json={"editors_can_share": True})
    assert ed.put(f"/api/projects/{pid}/access", json={"editors_can_share": False}).status_code == 403
    assert "manage_sharing" in ed.get(f"/api/projects/{pid}").json()["permissions"]
    owner.put(f"/api/projects/{pid}/access", json={"editors_can_share": False})

    # role change, owner protected, revoke, leave
    vw_id = pv_id = [m for m in sh["members"] if m["email"] == "vw@x.org"][0]["user_id"]
    owner_id = sh["members"][0]["user_id"]
    assert owner.patch(f"/api/projects/{pid}/members/{vw_id}", json={"role": "editor"}).status_code == 200
    assert "edit" in vw.get(f"/api/projects/{pid}").json()["permissions"]
    assert owner.patch(f"/api/projects/{pid}/members/{owner_id}", json={"role": "viewer"}).status_code == 400
    assert owner.delete(f"/api/projects/{pid}/members/{owner_id}").status_code == 400
    assert owner.delete(f"/api/projects/{pid}/members/{pv_id}").status_code == 200
    assert vw.get(f"/api/projects/{pid}").status_code == 404
    ed_id = [m for m in sh["members"] if m["email"] == "ed@x.org"][0]["user_id"]
    assert ed.delete(f"/api/projects/{pid}/members/{ed_id}").json() == {"ok": True}  # leave
    assert ed.get("/api/projects?scope=shared").json() == []


def test_link_access_and_access_requests():
    owner, visitor = client("own2@x.org"), client("vis@x.org")
    anon = TestClient(app)
    pid, did, content = project_with_data(owner)
    assert visitor.get(f"/api/projects/{pid}").status_code == 404

    # access request -> owner approves
    r = visitor.post(f"/api/projects/{pid}/access-requests", json={"message": "Bonjour", "role": "editor"}).json()
    assert r["status"] == "pending" and r["email"] == "not_configured"
    assert visitor.get(f"/api/projects/{pid}/access-requests/mine").json()["status"] == "pending"
    reqs = owner.get(f"/api/projects/{pid}/sharing").json()["requests"]
    assert len(reqs) == 1 and reqs[0]["email"] == "vis@x.org" and reqs[0]["message"] == "Bonjour"
    assert visitor.post(f"/api/projects/{pid}/access-requests/{reqs[0]['id']}/approve", json={}).status_code == 404  # not a manager
    owner.post(f"/api/projects/{pid}/access-requests/{reqs[0]['id']}/approve", json={"role": "viewer"})
    assert visitor.get(f"/api/projects/{pid}").json()["role"] == "viewer"
    assert visitor.post(f"/api/projects/{pid}/access-requests", json={}).status_code == 409
    vid = [m for m in owner.get(f"/api/projects/{pid}/sharing").json()["members"] if m["email"] == "vis@x.org"][0]["user_id"]
    owner.delete(f"/api/projects/{pid}/members/{vid}")

    # deny
    visitor.post(f"/api/projects/{pid}/access-requests", json={})
    rid = owner.get(f"/api/projects/{pid}/sharing").json()["requests"][0]["id"]
    owner.post(f"/api/projects/{pid}/access-requests/{rid}/deny")
    assert visitor.get(f"/api/projects/{pid}").status_code == 404
    assert owner.get(f"/api/projects/{pid}/sharing").json()["requests"] == []

    # anyone with the link (signed in): viewer
    owner.put(f"/api/projects/{pid}/access", json={"general_access": "link_viewer"})
    p = visitor.get(f"/api/projects/{pid}").json()
    assert p["role"] == "viewer" and p["permissions"] == ["export", "view"]
    assert visitor.put(f"/api/projects/{pid}/datasets/{did}", json={"content": content, "version": 1}).status_code == 403
    shared = visitor.get("/api/projects?scope=shared").json()
    assert [x["id"] for x in shared] == [pid] and shared[0]["via_link"] is True
    assert anon.get(f"/api/projects/{pid}").status_code == 401  # the link never opens to anonymous visitors
    # not a member: does not show up in the people list
    assert "vis@x.org" not in [m["email"] for m in owner.get(f"/api/projects/{pid}/sharing").json()["members"]]
    # link editor
    owner.put(f"/api/projects/{pid}/access", json={"general_access": "link_editor"})
    assert "edit" in visitor.get(f"/api/projects/{pid}").json()["permissions"]
    # back to restricted: access and the "shared with me" entry disappear
    owner.put(f"/api/projects/{pid}/access", json={"general_access": "restricted"})
    assert visitor.get(f"/api/projects/{pid}").status_code == 404
    assert visitor.get("/api/projects?scope=shared").json() == []
    assert owner.put(f"/api/projects/{pid}/access", json={"general_access": "public"}).status_code == 422


def test_invite_by_email_before_signup():
    owner = client("own3@x.org")
    pid, _, _ = project_with_data(owner)
    r = owner.post(f"/api/projects/{pid}/members", json={"emails": ["newbie@x.org"], "role": "editor", "message": "Regarde ça"}).json()
    assert r["invited"] == ["newbie@x.org"] and r["added"] == []
    sh = owner.get(f"/api/projects/{pid}/sharing").json()
    assert [(i["email"], i["role"]) for i in sh["invites"]] == [("newbie@x.org", "editor")]
    assert owner.post(f"/api/projects/{pid}/members", json={"emails": ["not-an-email"]}).status_code == 422
    newbie = client("newbie@x.org")
    p = newbie.get(f"/api/projects/{pid}").json()
    assert p["role"] == "editor"
    assert owner.get(f"/api/projects/{pid}/sharing").json()["invites"] == []
    # cancel a pending invite
    owner.post(f"/api/projects/{pid}/members", json={"emails": ["later@x.org"]})
    inv = owner.get(f"/api/projects/{pid}/sharing").json()["invites"][0]
    owner.delete(f"/api/projects/{pid}/invites/{inv['id']}")
    assert client("later@x.org").get(f"/api/projects/{pid}").status_code == 404


def test_style_sharing_public_gallery_and_copy():
    a, b, c = client("sa@x.org"), client("sb@x.org"), client("sc@x.org")
    st = a.post("/api/styles", json={"name": "Style de Gab", "settings": {"font_size_pt": 9, "frame": "closed"}}).json()
    assert b.get("/api/styles").json()["shared"] == []
    r = a.post(f"/api/styles/{st['id']}/shares", json={"emails": ["sb@x.org", "nobody@x.org"]}).json()
    assert r["unknown"] == ["nobody@x.org"] and r["style"]["visibility"] == "shared"
    assert [u["email"] for u in r["style"]["shared_with"]] == ["sb@x.org"]
    lb = b.get("/api/styles").json()
    assert [(s["name"], s["owner"], s["mine"]) for s in lb["shared"]] == [("Style de Gab", "sa", False)]
    assert c.get("/api/styles").json()["shared"] == [] and c.get("/api/styles").json()["public"] == []
    # b can apply (settings visible), copy into own styles, but not modify or delete a's
    assert b.put(f"/api/styles/{st['id']}", json={"name": "x", "version": 2}).status_code == 404
    assert b.delete(f"/api/styles/{st['id']}").status_code == 404
    cp = b.post(f"/api/styles/{st['id']}/copy").json()
    assert cp["mine"] and cp["name"] == "Style de Gab" and cp["source_style_id"] == st["id"]
    # public gallery
    cur = a.get("/api/styles").json()["mine"][0]
    a.put(f"/api/styles/{st['id']}", json={"visibility": "public", "version": cur["version"]})
    assert [s["name"] for s in c.get("/api/styles").json()["public"]] == ["Style de Gab"]
    assert b.get("/api/styles").json()["public"] == []  # already listed under "shared"
    assert c.post(f"/api/styles/{st['id']}/copy").status_code == 200
    # unshare + private again
    cur = a.get("/api/styles").json()["mine"][0]
    a.put(f"/api/styles/{st['id']}", json={"visibility": "private", "version": cur["version"]})
    bid = cur["shared_with"][0]["user_id"]
    after = a.delete(f"/api/styles/{st['id']}/shares/{bid}").json()
    assert after["shared_with"] == [] and after["visibility"] == "private"
    assert b.get("/api/styles").json()["shared"] == [] and c.get("/api/styles").json()["public"] == []
    assert b.get(f"/api/styles/{st['id']}/export").status_code == 404


def test_donation_consent_logins_and_admin_export(monkeypatch):
    yes = client("donor@x.org", opt_in=True)
    no = client("nodonor@x.org", opt_in=False)
    me = yes.get("/api/auth/me").json()
    assert me["donation_opt_in"] is True and me["donation_opt_in_at"] and me["is_admin"] is False
    assert no.get("/api/auth/me").json()["donation_opt_in"] is False
    # change of mind later
    r = no.patch("/api/auth/me", json={"donation_opt_in": True}).json()
    assert r["donation_opt_in"] is True
    no.patch("/api/auth/me", json={"donation_opt_in": False})
    login("donor@x.org")
    # admin only
    assert yes.get("/api/admin/summary").status_code == 403
    assert yes.get("/api/admin/donation-optins.csv").status_code == 403
    assert TestClient(app).get("/api/admin/donation-optins.csv").status_code == 401
    monkeypatch.setattr(config, "ADMIN_EMAILS", {"donor@x.org"})
    import app.main as m
    monkeypatch.setattr(m, "ADMIN_EMAILS", {"donor@x.org"})
    assert yes.get("/api/auth/me").json()["is_admin"] is True
    s = yes.get("/api/admin/summary").json()
    assert s["donation_opt_in"] >= 1 and s["accounts"] >= 2 and s["email_configured"] is False
    csv_text = yes.get("/api/admin/donation-optins.csv").text
    assert "donor@x.org" in csv_text and "nodonor@x.org" not in csv_text  # never people who did not opt in
    assert "opted_in_at" in csv_text.splitlines()[0]


def test_mailer_never_fakes(monkeypatch):
    assert mailer.send("a@x.org", "s", "t") == "not_configured"
    monkeypatch.setenv("FIOLE_MAIL_FROM", "Fiole <noreply@example.org>")
    monkeypatch.setenv("FIOLE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("FIOLE_SMTP_PORT", "1")  # nothing listens: reported as failed, not as sent
    assert mailer.provider() == "smtp" and mailer.send("a@x.org", "s", "t") == "failed"


def test_signins_reported_to_tools_usage_only_with_key(monkeypatch):
    from app import usage_report
    sent = []
    monkeypatch.setattr(usage_report, "_post", lambda payload, endpoint: sent.append((payload, endpoint)))
    monkeypatch.delenv("FIOLE_USAGE_KEY", raising=False)
    client("quiet@x.org")
    assert sent == []  # no key: nothing leaves the server
    monkeypatch.setenv("FIOLE_USAGE_KEY", "k123")
    client("tracked@x.org")
    login("tracked@x.org")
    import time
    time.sleep(0.2)
    assert [p["email"] for p, _ in sent] == ["tracked@x.org", "tracked@x.org"]
    assert sent[0][0] == {"tool": "fiole", "event": "login", "email": "tracked@x.org", "key": "k123"}
    assert sent[0][1].startswith("https://script.google.com/")
