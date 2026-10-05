"""Exercise the public account boundary and real private workspace isolation.

All accounts, sessions and agenda entries are created in a temporary directory.
These checks use the actual parent application, never external mail or Stripe.
"""
import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient
import pytest

from app.db import Database
from app.main import create_app


PASSWORD = "Correct#Horse-42"
OWNER_PASSWORD = "owner-secret-long"
DAY = "2026-10-05"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield app, client, tmp_path


def csrf(client):
    response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def register(client, email, name="Nuovo studio"):
    response = client.post(
        "/api/auth/register",
        json={"name": name, "email": email, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code in (200, 201), response.text
    return bootstrap(client)


def login(client, email, password=PASSWORD):
    response = client.post(
        "/api/auth/login", json={"identifier": email, "password": password},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 200, response.text
    return bootstrap(client)


def bootstrap(client):
    response = client.get("/api/bootstrap")
    assert response.status_code == 200, response.text
    return response.json()


def mutate(client, state, method, path, payload=None):
    return client.request(
        method, path, json=payload,
        headers={"X-CSRF-Token": state["csrf_token"]},
    )


def test_public_pages_do_not_open_browser_basic_auth_and_private_data_require_login(site):
    app, client, _ = site
    for path in ("/", "/login", "/register", "/api/auth/session", "/api/health"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert "www-authenticate" not in response.headers
    for path in ("/api/bootstrap", "/api/agenda", "/api/agenda/export", "/api/foo", "/api/billing/status"):
        response = client.get(path)
        assert response.status_code == 401, (path, response.text)
        assert "www-authenticate" not in response.headers
        assert response.headers.get("cache-control") == "no-store"
    assert client.post("/api/billing/checkout").status_code == 401
    for path in ("/app", "/account"):
        response = client.get(path, follow_redirects=False)
        assert response.status_code in (302, 303, 307)
        assert response.headers["location"].split("?")[0] == "/login"


def test_old_basic_credentials_and_workspace_cookie_do_not_authenticate_accounts(site):
    app, client, _ = site
    old_session = app.state.db.session()
    client.cookies.set("alexchiara_session", old_session["id"])
    response = client.get("/api/bootstrap", auth=("filo", OWNER_PASSWORD))
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers
    # The retained credentials work in the explicit form, which owns the old DB.
    state = login(client, "filo", OWNER_PASSWORD)
    assert state["csrf_token"] != old_session["csrf"]
    assert app.state.workspaces["owner"].state.db.path == app.state.db.path


def test_registered_workspaces_are_blank_and_isolate_company_agenda_and_export(site):
    app, alice, _ = site
    alice_state = register(alice, "alice@example.test", "Alice")
    bob = TestClient(app)
    try:
        bob_state = register(bob, "bob@example.test", "Bob")
        for state in (alice_state, bob_state):
            assert state["company"]["name"] == ""
            assert not state["company"].get("demo")
            assert state["service"]["priority_contacts"] == []
            assert state["connection"]["status"] == "disconnected"
            assert state["runs"] == []
            assert state["agenda"]["items"] == []
        # Verify the persisted defaults too: real-mode masking must not hide
        # fictitious company/contact records in a newly registered workspace.
        for client in (alice, bob):
            account_id = client.get("/api/auth/session").json()["user"]["id"]
            db = app.state.workspaces[account_id].state.db
            assert db.get_setting("company")["name"] == ""
            assert db.get_setting("service")["priority_contacts"] == []

        for client, state, name in ((alice, alice_state, "Studio Alice"), (bob, bob_state, "Studio Bob")):
            response = mutate(client, state, "PUT", "/api/company", {"name": name})
            assert response.status_code == 200, response.text
        response = mutate(alice, alice_state, "POST", "/api/agenda", {
            "title": "Appuntamento privato Alice", "starts_at": DAY + "T09:00:00+02:00",
            "notes": "Documento riservato Alice",
        })
        assert response.status_code == 201, response.text
        alice_event = response.json()["item"]["id"]
        response = mutate(bob, bob_state, "POST", "/api/agenda", {
            "title": "Appuntamento privato Bob", "starts_at": DAY + "T10:00:00+02:00",
        })
        assert response.status_code == 201, response.text
        bob_event = response.json()["item"]["id"]

        assert bootstrap(alice)["company"]["name"] == "Studio Alice"
        assert bootstrap(bob)["company"]["name"] == "Studio Bob"
        assert [event["id"] for event in alice.get("/api/agenda?date=" + DAY).json()["items"]] == [alice_event]
        assert [event["id"] for event in bob.get("/api/agenda?date=" + DAY).json()["items"]] == [bob_event]
        alice_export = alice.get("/api/agenda/export?date=" + DAY).text
        bob_export = bob.get("/api/agenda/export?date=" + DAY).text
        assert "Appuntamento privato Alice" in alice_export and "Appuntamento privato Bob" not in alice_export
        assert "Appuntamento privato Bob" in bob_export and "Documento riservato Alice" not in bob_export
        assert mutate(bob, bob_state, "DELETE", "/api/agenda/" + alice_event).status_code == 404
        assert mutate(alice, alice_state, "DELETE", "/api/agenda/" + bob_event).status_code == 404
        # Another account's valid CSRF token cannot authorize this browser.
        response = mutate(bob, alice_state, "PUT", "/api/company", {"name": "Intrusione"})
        assert response.status_code == 403
        assert bootstrap(bob)["company"]["name"] == "Studio Bob"
    finally:
        bob.close()


def test_logout_revokes_previous_session_and_login_keeps_workspace(site):
    app, client, _ = site
    state = register(client, "returning@example.test")
    assert mutate(client, state, "PUT", "/api/company", {"name": "Studio persistente"}).status_code == 200
    replay = TestClient(app)
    replay.cookies.update(client.cookies)
    try:
        response = mutate(client, state, "POST", "/api/auth/logout", {})
        assert response.status_code in (200, 204), response.text
        assert client.get("/api/bootstrap").status_code == 401
        assert replay.get("/api/bootstrap").status_code == 401
        response = client.get("/app", follow_redirects=False)
        assert response.status_code in (302, 303, 307)
        state = login(client, "returning@example.test")
        assert state["company"]["name"] == "Studio persistente"
    finally:
        replay.close()


def test_owner_login_retains_legacy_data_and_new_account_cannot_read_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    legacy = Database(tmp_path / "alexchiara.sqlite3")
    legacy.set_setting("company", {"name": "Studio originale", "sector": "", "description": "Solo owner", "signature": "", "demo": False})
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as owner:
        owner_state = login(owner, "filo", OWNER_PASSWORD)
        assert owner_state["company"]["name"] == "Studio originale"
        assert app.state.db.get_setting("company")["description"] == "Solo owner"
        other = TestClient(app)
        try:
            other_state = register(other, "separate@example.test")
            assert other_state["company"]["name"] == ""
            assert "Solo owner" not in str(other_state)
            assert app.state.db.get_setting("company")["name"] == "Studio originale"
        finally:
            other.close()


def test_browser_cookie_and_private_data_survive_server_restart(site):
    app, client, runtime = site
    state = register(client, "persistent@example.test")
    assert mutate(client, state, "PUT", "/api/company", {"name": "Studio dopo il riavvio"}).status_code == 200
    before = client.get("/api/auth/session").json()
    restarted = create_app(data_dir=runtime, start_worker=False)
    with TestClient(restarted) as returning:
        returning.cookies.update(client.cookies)
        after = returning.get("/api/auth/session").json()
        assert after["authenticated"] is True
        assert after["user"]["id"] == before["user"]["id"]
        assert after["expires_at"] == before["expires_at"]
        assert bootstrap(returning)["company"]["name"] == "Studio dopo il riavvio"


def test_auth_csrf_host_origin_and_private_security_headers(site):
    app, client, _ = site
    # Login CSRF must be checked before any account can be created.
    response = client.post("/api/auth/register", json={"name": "x", "email": "csrf@example.test", "password": PASSWORD})
    assert response.status_code == 403
    token = csrf(client)
    response = client.post("/api/auth/register", json={"name": "x", "email": "csrf@example.test", "password": PASSWORD}, headers={"X-CSRF-Token": token, "Origin": "https://foreign.example"})
    assert response.status_code == 403
    assert client.get("/", headers={"Host": "attacker.example"}).status_code == 400
    state = register(client, "headers@example.test")
    response = client.get("/api/bootstrap")
    assert response.headers.get("cache-control") == "no-store"
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "DENY"
    assert "www-authenticate" not in response.headers
    response = mutate(client, {"csrf_token": token}, "PUT", "/api/company", {"name": "Old token"})
    assert response.status_code == 403
    assert bootstrap(client)["company"]["name"] == ""


def test_registration_validation_never_echoes_submitted_password(site):
    _, client, _ = site
    secret = "SECRETSHORT"
    response = client.post(
        "/api/auth/register",
        json={"name": "Studio", "email": "short@example.test", "password": secret},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in error for error in response.json()["detail"])


def test_parent_preserves_signed_webhook_body_without_browser_session_or_csrf(site, monkeypatch):
    from app import billing

    app, client, _ = site
    secret = "whsec_isolated_parent_boundary"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)

    async def no_provider_calls(*args, **kwargs):
        raise AssertionError("This boundary test must not contact Stripe")

    monkeypatch.setattr(billing, "_stripe_request", no_provider_calls)
    app.state.billing.save_customer("owner", "cus_owner")
    stamp = int(time.time())
    payload = json.dumps({
        "id": "evt_parent_boundary", "type": "customer.subscription.updated", "created": stamp,
        "data": {"object": {"id": "sub_owner", "customer": "cus_owner", "status": "active", "metadata": {"user_id": "owner"}}},
    }, separators=(",", ":")).encode()
    signature = hmac.new(secret.encode(), str(stamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    assert client.cookies.get("filo_session") is None
    invalid = client.post("/api/billing/webhook", content=payload)
    assert invalid.status_code == 400
    assert "www-authenticate" not in invalid.headers
    signed_headers = {"Stripe-Signature": f"t={stamp},v1={signature}"}
    # Equivalent JSON with a different raw byte stream has a different MAC.
    tampered = client.post("/api/billing/webhook", content=payload + b" ", headers=signed_headers)
    assert tampered.status_code == 400
    assert not app.state.billing.event_seen("evt_parent_boundary")
    valid = client.post("/api/billing/webhook", content=payload, headers=signed_headers)
    assert valid.status_code == 200, valid.text
    assert app.state.billing.subscription_for_user("owner")["status"] == "active"
    assert app.state.billing.event_seen("evt_parent_boundary")
    # Signed provider webhooks are public; ordinary billing data stays private.
    assert client.get("/api/billing/status").status_code == 401


def test_parent_requires_billing_csrf_and_disables_unconfigured_payments(site):
    _, client, _ = site
    state = register(client, "billing-boundary@example.test")
    assert client.post("/api/billing/checkout").status_code == 403
    response = client.get("/api/billing/status")
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is False
    assert response.json()["plan"]["price_label"] is None
    for path in ("/api/billing/checkout", "/api/billing/portal"):
        response = mutate(client, state, "POST", path, {})
        assert response.status_code == 503, response.text
        assert response.headers.get("cache-control") == "no-store"
