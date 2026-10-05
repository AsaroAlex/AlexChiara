"""Production shows saved real work and never offers invented first results."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app import service as service_module
from app.db import DEFAULT_COMPANY, DEFAULT_CONTACTS
from app.main import create_workspace_app as create_app


CONTACT = {"name": "Cliente effettivo", "email": "cliente@example.test"}
PREFERENCES = {"priority_contacts": [CONTACT], "hour": 9, "minute": 0, "timezone": "Europe/Rome"}
AUTHORIZATION = {"read": True, "draft": True, "send": False}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    monkeypatch.delenv("FILO_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("FILO_GOOGLE_CLIENT_SECRET", raising=False)
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        token = client.get("/api/bootstrap").json()["csrf_token"]
        client.headers["X-CSRF-Token"] = token
        yield app, client


def test_fresh_workspace_is_empty_without_mutating_saved_demo_defaults(workspace):
    app, client = workspace
    data = client.get("/api/bootstrap").json()
    assert data["real_data_only"] and data["mode"] == "real-mail"
    assert data["company"]["name"] == "" and data["company"]["needs_setup"]
    assert data["service"]["priority_contacts"] == []
    assert data["briefing_example"] is None
    assert data["briefing"]["priorities"] == data["briefing"]["contacts"] == []
    assert data["runs"] == data["approvals"] == data["actions"] == []
    assert app.state.db.get_setting("company") == DEFAULT_COMPANY
    assert app.state.db.get_setting("service")["priority_contacts"] == DEFAULT_CONTACTS


def test_first_gmail_connection_does_not_resurrect_sample_contacts(workspace):
    app, client = workspace
    app.state.db.set_connection("gmail", "connected", "studio@example.test")
    data = client.get("/api/bootstrap").json()
    assert data["service"]["priority_contacts"] == []
    assert data["briefing"]["contacts"] == []
    assert data["briefing"]["counts"]["contacts_unverified"] == 0


def test_chat_and_service_actions_do_not_offer_sample_contacts(workspace):
    _, client = workspace
    chat = client.post("/api/chat", json={"message": "Controlla le email alle 9"}).json()
    assert chat["preferences"]["priority_contacts"] == []
    data = client.post("/api/service/action", json={"action": "deactivate"}).json()
    assert data["service"]["priority_contacts"] == []


def test_demo_entry_points_are_closed_even_with_a_real_profile(workspace):
    app, client = workspace
    assert client.put("/api/company", json={"name": "Il mio studio"}).status_code == 200
    app.state.db.set_connection("demo", "connected")
    assert client.post("/api/connection/demo", json={}).status_code == 403
    assert client.post("/api/demo/failure", json={"kind": "temporary"}).status_code == 403
    assert client.post("/api/service/preview", json=PREFERENCES).status_code == 409
    assert client.post("/api/service/activate", json={**PREFERENCES, "authorization": AUTHORIZATION}).status_code == 409
    settings = app.state.db.get_setting("service")
    settings.update(status="paused", provider="demo", mandate=AUTHORIZATION)
    app.state.db.set_setting("service", settings)
    assert client.post("/api/service/action", json={"action": "resume"}).status_code == 409


def test_previous_demo_results_are_preserved_but_not_served(workspace, monkeypatch):
    app, client = workspace
    monkeypatch.delenv("FILO_REAL_DATA_ONLY")
    assert client.post("/api/connection/demo", json={}).status_code == 200
    assert client.post("/api/service/activate", json={**PREFERENCES, "authorization": AUTHORIZATION}).status_code == 200
    run = app.state.scheduler.enqueue()
    app.state.scheduler.tick()
    draft_id = app.state.db.run(run["id"])["items"][0]["id"]
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    data = client.get("/api/bootstrap").json()
    assert data["runs"] == data["approvals"] == []
    assert data["service"]["status"] == "inactive"
    assert data["connection"]["provider"] is None
    assert client.get(f"/api/runs/{run['id']}").status_code == 404
    assert client.post(f"/api/runs/{run['id']}/retry", json={}).status_code == 404
    assert client.post(f"/api/drafts/{draft_id}/approve", json={}).status_code == 404
    assert app.state.db.run(run["id"])["items"]


def test_real_email_results_and_profile_remain_visible(workspace, monkeypatch):
    app, client = workspace
    now = datetime.now(timezone.utc)
    assert client.put("/api/company", json={"name": "Il mio studio", "signature": "Chiara"}).status_code == 200
    app.state.db.set_connection("gmail", "connected", "studio@example.test")
    assert client.post("/api/service/activate", json={**PREFERENCES, "authorization": AUTHORIZATION}).status_code == 200
    monkeypatch.setattr(service_module, "load_messages", lambda *args: [{
        "id": "actual-inbox-1", "thread_id": "actual-thread-1", "sender": CONTACT["email"],
        "subject": "Preventivo urgente", "body": "Sono in attesa del preventivo entro oggi.",
        "received_at": now.isoformat(), "from_client": True,
    }])
    run = app.state.scheduler.enqueue(now=now)
    app.state.scheduler.tick(now=now)
    assert app.state.db.run(run["id"])["status"] == "succeeded"
    data = client.get("/api/bootstrap").json()
    assert data["company"]["name"] == "Il mio studio"
    assert data["service"]["priority_contacts"] == [CONTACT]
    assert data["briefing"]["priorities"][0]["client"] == CONTACT["name"]
    assert data["briefing"]["contacts"][0]["name"] == CONTACT["name"]
    assert data["runs"][0]["id"] == run["id"]


def test_missing_google_configuration_is_actionable_and_secrets_are_private(workspace, monkeypatch):
    _, client = workspace
    callback = "https://filo-production-65a1.up.railway.app/api/gmail/oauth/callback"
    monkeypatch.setenv("FILO_GOOGLE_REDIRECT_URI", callback)
    status = client.get("/api/gmail/status").json()
    assert not status["configured"] and status["redirect_uri"] == callback
    assert client.post("/api/gmail/oauth/start", json={}).status_code == 409
    monkeypatch.setenv("FILO_GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("FILO_GOOGLE_CLIENT_SECRET", "test-secret-value")
    response = client.get("/api/gmail/status")
    assert response.json()["configured"]
    assert "test-secret-value" not in response.text and "test-client-id" not in response.text
