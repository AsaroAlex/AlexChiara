"""Real providers share the same private, authorized morning workflow."""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json

from fastapi.testclient import TestClient
import pytest

from app import service as service_module
from app.briefing import mailbox_scope
from app.connectors import ConnectorError
from app.main import create_app, create_workspace_app

NOW = datetime(2026, 10, 5, 9, 1, tzinfo=timezone.utc)
CONTACT = {"name": "Cliente Alfa", "email": "alfa@example.test"}
PREFERENCES = {"priority_contacts": [CONTACT], "hour": 23, "minute": 59, "timezone": "Europe/Rome"}
AUTHORIZATION = {"read": True, "draft": True, "send": False}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    for prefix in ("GOOGLE", "MICROSOFT"):
        for suffix in ("CLIENT_ID", "CLIENT_SECRET"):
            monkeypatch.delenv(f"FILO_{prefix}_{suffix}", raising=False)
    app = create_workspace_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/bootstrap").json()["csrf_token"]
        client.put("/api/company", json={"name": "Studio Alfa"})
        yield app, client


def authorize(app, provider):
    db = app.state.db
    db.set_connection(provider, "connected", "studio@example.test", **({"mail_provider": "icloud"} if provider == "imap" else {}))
    service = db.get_setting("service")
    service.update(**PREFERENCES, provider=provider, status="active", mandate=AUTHORIZATION, activated_at=NOW.isoformat())
    db.set_setting("service", service)


def test_provider_chooser_is_honest_and_never_exposes_credentials(workspace):
    app, client = workspace
    app.state.db.set_setting("imap_credentials", "encrypted-test-placeholder")
    payload = client.get("/api/mail/providers")
    assert payload.status_code == 200
    choices = {item["id"]: item for item in payload.json()["providers"]}
    assert set(choices) == {"gmail", "outlook", "icloud", "yahoo", "aruba", "libero", "imap"}
    assert choices["gmail"]["configured"] is choices["outlook"]["configured"] is False
    assert all(choices[key]["configured"] for key in ("icloud", "yahoo", "aruba", "libero", "imap"))
    assert "encrypted-test-placeholder" not in payload.text
    authorize(app, "imap")
    choices = client.get("/api/mail/providers").json()["providers"]
    assert [item["id"] for item in choices if item["connected"]] == ["icloud"]


@pytest.mark.parametrize("provider", ["gmail", "outlook", "imap"])
def test_provider_supports_preview_activation_and_saved_real_briefing(workspace, monkeypatch, provider):
    app, client = workspace
    app.state.db.set_connection(provider, "connected", "studio@example.test")
    messages = [{"id": "request-1", "thread_id": "thread-1", "sender": CONTACT["email"],
                 "subject": "Preventivo urgente", "body": "Potete inviarmi un preventivo entro oggi?",
                 "received_at": NOW.isoformat(), "from_client": True}]
    monkeypatch.setattr("app.main.load_messages", lambda *_: messages)
    monkeypatch.setattr(service_module, "load_messages", lambda *_: messages)
    assert client.post("/api/service/preview", json=PREFERENCES).status_code == 200
    response = client.post("/api/service/activate", json={**PREFERENCES, "authorization": AUTHORIZATION})
    assert response.status_code == 200, response.text
    service = app.state.db.get_setting("service")
    service["activated_at"] = NOW.isoformat()
    app.state.db.set_setting("service", service)
    run = app.state.scheduler.enqueue(now=NOW)
    app.state.scheduler.tick(NOW)
    assert app.state.db.run(run["id"])["status"] == "succeeded"
    data = client.get("/api/bootstrap").json()
    assert data["briefing"]["counts"]["pending"] == 1
    assert data["briefing"]["coverage_complete"] is False
    assert "limitato" in data["briefing"]["coverage_note"]


@pytest.mark.parametrize("provider", ["outlook", "imap"])
def test_other_providers_poll_watches_without_open_browser(workspace, monkeypatch, provider):
    app, client = workspace
    authorize(app, provider)
    monkeypatch.setattr("app.watches.needs_watch_poll", lambda *_: True)
    calls = []
    monkeypatch.setattr(service_module, "load_messages", lambda selected, *_: calls.append(selected) or [])
    first = app.state.scheduler.tick(NOW)
    assert first[0]["trigger"] == "watch"
    assert first[0]["status"] == "succeeded"
    assert app.state.scheduler.tick(NOW + timedelta(minutes=4)) == []
    assert app.state.scheduler.tick(NOW + timedelta(minutes=5))[0]["status"] == "succeeded"
    assert calls == [provider, provider]


def test_unlink_erases_all_credentials_pending_consents_and_authorization(workspace):
    app, client = workspace
    authorize(app, "imap")
    for key in ("gmail_tokens", "outlook_tokens", "imap_credentials", "oauth:pending", "outlook_oauth:pending"):
        app.state.db.set_setting(key, "encrypted")
    app.state.scheduler.enqueue(now=NOW)
    previous = mailbox_scope(app.state.db, "imap")
    assert client.post("/api/mail/disconnect", json={}).status_code == 200
    assert app.state.db.get_connection()["status"] == "disconnected"
    assert app.state.db.get_setting("service")["mandate"] is None
    assert all(run["status"] == "failed" for run in app.state.db.list_runs())
    assert previous != mailbox_scope(app.state.db, "imap")
    for key in ("gmail_tokens", "outlook_tokens", "imap_credentials", "oauth:pending", "outlook_oauth:pending"):
        assert app.state.db.get_setting(key) is None


@pytest.mark.parametrize("expired", [False, True])
def test_check_cannot_save_old_mail_or_expire_a_replacement_account(workspace, monkeypatch, expired):
    app, _ = workspace
    authorize(app, "imap")

    def replacement(*_):
        app.state.db.set_setting("gmail_revision", 1)
        app.state.db.set_connection("imap", "connected", "replacement@example.test", mail_provider="yahoo")
        if expired:
            raise ConnectorError("Password precedente revocata", expired=True)
        return [{"id": "old-message", "sender": CONTACT["email"], "subject": "Preventivo",
                 "body": "Potete inviarmi un preventivo?", "received_at": NOW.isoformat()}]

    monkeypatch.setattr(service_module, "load_messages", replacement)
    run = app.state.scheduler.enqueue(now=NOW)
    app.state.scheduler.tick(NOW)
    assert app.state.db.run(run["id"])["status"] == "failed"
    assert app.state.db.get_connection()["status"] == "connected"
    assert app.state.db.get_connection()["mail_provider"] == "yahoo"
    assert app.state.db.pending_drafts() == []


def test_mail_routes_require_login_and_validation_never_echoes_password(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", "owner-test-secret")
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        assert client.get("/api/mail/providers").status_code == 401
        assert client.post("/api/mail/imap/connect", json={}).status_code == 401
        token = client.get("/api/auth/session").json()["csrf_token"]
        assert client.post("/api/auth/register", json={"name": "Alfa", "email": "alfa@example.test", "password": "Long#Account-Test42"}, headers={"X-CSRF-Token": token}).status_code == 200
        state = client.get("/api/bootstrap").json()
        assert client.get("/api/mail/providers").status_code == 200
        secret = "do-not-echo-this-secret" * 500
        response = client.post("/api/mail/imap/connect", json={"provider": "imap", "email": "alfa@example.test", "password": secret, "host": "example.test"}, headers={"X-CSRF-Token": state["csrf_token"]})
        assert response.status_code == 422
        assert "do-not-echo-this-secret" not in response.text


def test_preview_discards_messages_if_mailbox_changes_during_read(workspace, monkeypatch):
    app, client = workspace
    authorize(app, "imap")

    def switch(*_):
        app.state.db.set_setting("gmail_revision", 1)
        app.state.db.set_connection("imap", "connected", "replacement@example.test")
        return [{"id": "old-preview", "sender": CONTACT["email"], "subject": "Old private preview",
                 "body": "Potete inviarmi un preventivo?", "received_at": NOW.isoformat()}]

    monkeypatch.setattr("app.main.load_messages", switch)
    result = client.post("/api/service/preview", json=PREFERENCES)
    assert result.status_code == 409
    assert "Old private preview" not in result.text


def test_real_parent_routes_separate_connected_mailboxes_and_encryption_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", "owner-test-secret")

    @contextmanager
    def verified_session(_credentials):
        yield None

    monkeypatch.setattr("app.imap_mail._session", verified_session)
    app = create_app(tmp_path, start_worker=False)
    with TestClient(app) as alice, TestClient(app) as bob:
        for client, email, secret in [(alice, "alice@example.test", "alice-app-password"), (bob, "bob@example.test", "bob-app-password")]:
            csrf = client.get("/api/auth/session").json()["csrf_token"]
            result = client.post("/api/auth/register", json={"name": email, "email": email, "password": "Long#Account-Test42"}, headers={"X-CSRF-Token": csrf})
            assert result.status_code == 200
            state = client.get("/api/bootstrap").json()
            result = client.post("/api/mail/imap/connect", json={"provider": "yahoo", "email": email, "password": secret}, headers={"X-CSRF-Token": state["csrf_token"]})
            assert result.status_code == 200, result.text
            assert secret not in result.text
        assert alice.get("/api/mail/providers").json()["connection"]["label"] == "alice@example.test"
        assert bob.get("/api/mail/providers").json()["connection"]["label"] == "bob@example.test"
        alice_state = alice.get("/api/bootstrap").json()
        assert alice.post("/api/mail/disconnect", json={}, headers={"X-CSRF-Token": alice_state["csrf_token"]}).status_code == 200
        assert bob.get("/api/mail/providers").json()["connection"]["status"] == "connected"
        member_keys = list((tmp_path / "workspaces").glob("*/gmail.key"))
        assert len(member_keys) == 2
        assert member_keys[0].read_bytes() != member_keys[1].read_bytes()
