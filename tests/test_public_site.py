"""Public site edges: well-known files, HEAD probes and HTML 404 pages.

Everything runs against the real parent application in a temporary directory.
"""
from fastapi.testclient import TestClient
import pytest

from app.main import create_app


OWNER_PASSWORD = "owner-secret-long"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield client


def login(client):
    csrf = client.get("/api/auth/session").json()["csrf_token"]
    response = client.post("/api/auth/login", json={"email": "filo", "password": OWNER_PASSWORD}, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("path,media_type,signature", [
    ("/favicon.ico", "image/x-icon", b"\x00\x00\x01\x00"),
    ("/apple-touch-icon.png", "image/png", b"\x89PNG"),
    ("/apple-touch-icon-precomposed.png", "image/png", b"\x89PNG"),
    ("/robots.txt", "text/plain", b"User-agent: *"),
])
def test_well_known_files_are_public_for_anonymous_and_signed_in_visitors(client, path, media_type, signature):
    for signed_in in (False, True):
        if signed_in:
            login(client)
        response = client.get(path)
        assert response.status_code == 200, (path, signed_in, response.text[:80])
        assert response.headers["content-type"].startswith(media_type)
        assert response.content.startswith(signature)


def test_robots_keep_private_areas_out_of_search_engines(client):
    rules = client.get("/robots.txt").text.splitlines()
    for private in ("/api/", "/app", "/account"):
        assert f"Disallow: {private}" in rules


def test_unknown_pages_render_html_404_without_exposing_workspace_api_docs(client):
    for signed_in in (False, True):
        if signed_in:
            login(client)
        for path in ("/pagina-inesistente", "/docs", "/redoc", "/openapi.json", "/app/extra"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 404, (path, signed_in)
            assert response.headers["content-type"].startswith("text/html")
            assert "Pagina non trovata" in response.text
            assert response.headers["cache-control"] == "no-store"
    # API clients keep JSON errors.
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_anonymous_private_api_still_requires_login(client):
    response = client.get("/api/does-not-exist")
    assert response.status_code == 401
    assert response.json()["login_url"] == "/login"


def test_head_probes_match_get_for_pages_and_health(client):
    for path in ("/", "/login", "/register", "/api/health", "/favicon.ico", "/robots.txt"):
        response = client.head(path)
        assert response.status_code == 200, path
        assert response.content == b""
    response = client.head("/app", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/app"
    login(client)
    for path in ("/app", "/account"):
        assert client.head(path).status_code == 200, path


def test_expired_session_returning_from_oauth_lands_on_login(client):
    for provider in ("gmail", "outlook"):
        response = client.get(f"/api/{provider}/oauth/callback?state=abc&code=xyz", follow_redirects=False)
        assert response.status_code == 303
        assert response.headers["location"] == "/login?next=/app"


@pytest.mark.parametrize("host", ["localhost/static/x?", "localhost@evil.com", "evil.com\\@localhost", "localhost#x", "localhost:", ""])
def test_malformed_host_headers_are_rejected_before_routing(client, host):
    response = client.get("/api/health", headers={"host": host})
    assert response.status_code == 400


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST:8000", "127.0.0.1:8000", "[::1]:8000"])
def test_well_formed_allowed_hosts_are_accepted(client, host):
    assert client.get("/api/health", headers={"host": host}).status_code == 200


def test_only_workspaces_with_scheduled_work_open_at_startup(tmp_path, monkeypatch):
    from app.accounts import AccountStore
    from app.db import Database

    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    store = AccountStore(tmp_path / "accounts.sqlite3")
    ids = {}
    for name in ("attivo", "dormiente", "corrotto"):
        _, session = store.register(name.title(), f"{name}@example.test", "una-password-lunga-12", store.new_guest_session()[0], "127.0.0.1")
        ids[name] = session["user"]["id"]
    active = Database(tmp_path / "workspaces" / ids["attivo"] / "alexchiara.sqlite3")
    service = active.get_setting("service")
    service["status"] = "active"
    active.set_setting("service", service)
    Database(tmp_path / "workspaces" / ids["dormiente"] / "alexchiara.sqlite3")
    broken = tmp_path / "workspaces" / ids["corrotto"] / "alexchiara.sqlite3"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not a database")
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app):
        loaded = set(app.state.workspaces)
    assert ids["attivo"] in loaded and "owner" in loaded
    assert ids["dormiente"] not in loaded
    assert ids["corrotto"] not in loaded


def test_a_damaged_workspace_returns_a_clear_error_instead_of_crashing(tmp_path, monkeypatch):
    from app.accounts import AccountStore, SESSION_COOKIE

    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    store = AccountStore(tmp_path / "accounts.sqlite3")
    cookie, session = store.register("Rotto", "rotto@example.test", "una-password-lunga-12", store.new_guest_session()[0], "127.0.0.1")
    broken = tmp_path / "workspaces" / session["user"]["id"] / "alexchiara.sqlite3"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not a database")
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, cookie)
        response = client.get("/api/bootstrap")
        assert response.status_code == 503
        assert "non è disponibile" in response.json()["detail"]
        assert client.get("/api/health").status_code == 200
