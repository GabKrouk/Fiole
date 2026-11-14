"""One account for Fiole, Régine and GeneCloud: /api/sso/*."""
import os

os.environ.setdefault("FIOLE_DATABASE_URL", "postgresql+psycopg2://app:app@localhost:5432/statsapp_test")

import time  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import usage_report  # noqa: E402
from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402

PAGE = "https://gabkrouk.github.io/Regine/?x=1"
ORIGIN = "https://gabkrouk.github.io"


@pytest.fixture(scope="module", autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(usage_report, "_post", lambda payload, endpoint: out.append(payload))
    monkeypatch.setenv("FIOLE_USAGE_KEY", "k123")
    return out


def signed_in(email):
    c = TestClient(app)
    assert c.post("/api/auth/register", json={"email": email, "password": "correct horse", "display_name": "Ana"}).status_code == 200
    return c


def wait_for(sent, n):
    for _ in range(50):
        if len(sent) >= n:
            return
        time.sleep(0.02)


def test_authorize_me_logout_and_reporting(sent):
    c = signed_in("ana@x.org")
    wait_for(sent, 1)
    r = c.post("/api/sso/authorize", json={"tool": "regine", "return_to": PAGE + "#from=genecloud"})
    assert r.status_code == 200, r.text
    redirect = r.json()["redirect"]
    assert redirect.startswith(PAGE + "#fiole_token=")  # the page's own #fragment is dropped
    token = redirect.split("#fiole_token=")[1]
    wait_for(sent, 2)
    assert sent[-1] == {"tool": "regine", "event": "login", "email": "ana@x.org", "key": "k123"}

    tool_page = TestClient(app)  # no Fiole cookie: the token alone identifies the user
    r = tool_page.get("/api/sso/me?tool=genecloud", headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN})
    assert r.status_code == 200 and r.json()["email"] == "ana@x.org"
    assert r.headers["access-control-allow-origin"] == ORIGIN
    wait_for(sent, 3)
    assert sent[-1]["tool"] == "genecloud"  # first visit of the other tool of the site: counted
    n = len(sent)
    tool_page.get("/api/sso/me?tool=genecloud", headers={"Authorization": f"Bearer {token}"})
    tool_page.get("/api/sso/me?tool=regine", headers={"Authorization": f"Bearer {token}"})
    time.sleep(0.2)
    assert len(sent) == n  # later visits within 12 h are not reported again

    # the tool token gives no access to Fiole itself
    assert tool_page.get("/api/projects", headers={"Authorization": f"Bearer {token}"}).status_code == 401

    assert tool_page.post("/api/sso/logout", headers={"Authorization": f"Bearer {token}"}).json() == {"ok": True}
    r = tool_page.get("/api/sso/me?tool=regine", headers={"Authorization": f"Bearer {token}", "Origin": ORIGIN})
    assert r.status_code == 401 and r.headers["access-control-allow-origin"] == ORIGIN  # errors are readable by the page


def test_refusals():
    c = signed_in("bob@x.org")
    for ret in ("https://evil.example/", "http://gabkrouk.github.io/Regine/", "https://x@gabkrouk.github.io/", "javascript:alert(1)",
                "https://gabkrouk.github.io.evil.example/"):
        assert c.post("/api/sso/authorize", json={"tool": "regine", "return_to": ret}).status_code == 400, ret
    assert c.post("/api/sso/authorize", json={"tool": "nope", "return_to": PAGE}).status_code == 400
    assert TestClient(app).post("/api/sso/authorize", json={"tool": "regine", "return_to": PAGE}).status_code == 401
    assert TestClient(app).get("/api/sso/me?tool=regine", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert TestClient(app).get("/api/sso/check", params={"tool": "genecloud", "return_to": PAGE}).status_code == 200
    assert TestClient(app).get("/api/sso/check", params={"tool": "genecloud", "return_to": "https://evil.example/"}).status_code == 400
    # the CORS preflight answers only the allowed site
    r = TestClient(app).options("/api/sso/me", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == ORIGIN
    r = TestClient(app).options("/api/sso/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_fiole_signins_unchanged(sent):
    c = signed_in("carl@x.org")
    assert c.get("/api/auth/me").json()["email"] == "carl@x.org"
    wait_for(sent, 1)
    assert sent[-1]["tool"] == "fiole"
