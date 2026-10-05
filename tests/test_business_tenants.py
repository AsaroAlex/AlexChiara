"""PMI records use the public account boundary and its private workspace DB.

The tests create real accounts in temporary storage and do not contact any
provider, send a document, file a fiscal invoice, or run background workers.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


PASSWORD = "Private#Workspace-42"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", "owner-password-long")
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield app, client, tmp_path


def token(client):
    response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def register(client, email):
    response = client.post(
        "/api/auth/register",
        json={"name": "Studio privato", "email": email, "password": PASSWORD},
        headers={"X-CSRF-Token": token(client)},
    )
    assert response.status_code == 200, response.text
    return response.json()


def mutate(client, csrf, method, path, payload=None):
    return client.request(method, path, json=payload, headers={"X-CSRF-Token": csrf})


def quote(title, net_amount="123.45", *, status="todo", notes=""):
    return {
        "service_id": "quotes", "title": title, "status": status, "notes": notes,
        "details": {"client": title + " cliente", "scope": "Lavoro inserito dall’utente",
                    "net_amount": net_amount, "vat_rate": "22"},
    }


def add_quote(client, csrf, title, **kwargs):
    response = mutate(client, csrf, "POST", "/api/business/records", quote(title, **kwargs))
    assert response.status_code == 201, response.text
    return response.json()["item"]


def test_business_routes_are_private_and_mutations_require_account_csrf(site):
    _, client, _ = site
    record_id = "a" * 32
    for path in ("/api/business/services", "/api/business/summary", "/api/business/records",
                 "/api/business/records/" + record_id,
                 "/api/business/records/" + record_id + "/export"):
        response = client.get(path)
        assert response.status_code == 401, (path, response.text)
        assert "www-authenticate" not in response.headers
        assert response.headers.get("cache-control") == "no-store"
    assert client.post("/api/business/records", json=quote("Privata")).status_code == 401

    account = register(client, "csrf-business@example.test")
    assert client.post("/api/business/records", json=quote("Privata")).status_code == 403
    foreign_origin = client.post(
        "/api/business/records", json=quote("Privata"),
        headers={"X-CSRF-Token": account["csrf_token"], "Origin": "https://foreign.example"},
    )
    assert foreign_origin.status_code == 403
    assert client.get("/api/business/records").json()["items"] == []


def test_private_quotes_list_summary_catalog_and_exports_do_not_cross_accounts(site):
    app, alice, _ = site
    a = register(alice, "business-alice@example.test")
    with TestClient(app) as bob:
        b = register(bob, "business-bob@example.test")
        for client in (alice, bob):
            assert client.get("/api/business/records").json()["items"] == []
            assert client.get("/api/business/summary").json()["totals"]["total"] == 0
            assert client.get("/api/bootstrap").json()["business"]["next_actions"] == []
            assert all(service["record_count"] == 0 for service in client.get("/api/business/services").json()["services"])
            user_id = client.get("/api/auth/session").json()["user"]["id"]
            with app.state.workspaces[user_id].state.db.connection() as conn:
                assert conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0] == 0

        alice_quote = add_quote(alice, a["csrf_token"], "Offerta Alice riservata", notes="Solo Alice conosce questa nota")
        bob_quote = add_quote(bob, b["csrf_token"], "Offerta Bob riservata", net_amount="10.00", status="done")
        assert alice_quote["totals"] == {"currency": "EUR", "net_amount": "123.45", "vat_rate": "22", "vat_amount": "27.16", "gross_amount": "150.61"}
        assert bob_quote["totals"]["gross_amount"] == "12.20"
        # This is the actual default query used by the paginated browser list.
        alice_open = alice.get("/api/business/records?service_id=quotes&status=open&limit=100&offset=0")
        bob_open = bob.get("/api/business/records?service_id=quotes&status=open&limit=100&offset=0")
        assert alice_open.status_code == 200, alice_open.text
        assert bob_open.status_code == 200, bob_open.text
        assert [record["id"] for record in alice_open.json()["items"]] == [alice_quote["id"]]
        assert bob_open.json()["items"] == []

        for client, mine, other in ((alice, alice_quote, bob_quote), (bob, bob_quote, alice_quote)):
            listing = client.get("/api/business/records?service_id=quotes").json()
            assert listing["total"] == 1
            assert [item["id"] for item in listing["items"]] == [mine["id"]]
            summary = client.get("/api/business/summary").json()
            assert summary["totals"]["total"] == 1
            assert other["title"] not in str(summary)
            services = client.get("/api/business/services").json()["services"]
            assert next(service for service in services if service["id"] == "quotes")["record_count"] == 1
            exported = client.get("/api/business/records/" + mine["id"] + "/export")
            assert exported.status_code == 200
            assert mine["title"] in exported.text and other["title"] not in exported.text
            assert "Nessun invio, pagamento o adempimento eseguito da Spazelia." in exported.text
            assert "non viene trasmessa allo SDI" in exported.text
            for path in ("/api/business/records/" + other["id"], "/api/business/records/" + other["id"] + "/export"):
                assert client.get(path).status_code == 404

        assert mutate(bob, b["csrf_token"], "PATCH", "/api/business/records/" + alice_quote["id"], {"title": "Intrusione"}).status_code == 404
        assert mutate(bob, b["csrf_token"], "DELETE", "/api/business/records/" + alice_quote["id"]).status_code == 404
        assert mutate(bob, a["csrf_token"], "PATCH", "/api/business/records/" + bob_quote["id"], {"title": "Token di Alice"}).status_code == 403
        assert alice.get("/api/business/records/" + alice_quote["id"]).json()["item"]["title"] == alice_quote["title"]
        assert bob.get("/api/business/records/" + bob_quote["id"]).json()["item"]["title"] == bob_quote["title"]


def test_client_cannot_choose_owner_workspace_and_owner_does_not_see_member_records(site):
    app, member, _ = site
    account = register(member, "scope-business@example.test")
    for field, value in (("user_id", "owner"), ("workspace_id", "owner"), ("source", "automatic")):
        payload = quote("Spostamento non autorizzato")
        payload[field] = value
        assert mutate(member, account["csrf_token"], "POST", "/api/business/records", payload).status_code == 422
    mine = add_quote(member, account["csrf_token"], "Solo account membro")
    with TestClient(app) as owner:
        response = owner.post(
            "/api/auth/login", json={"identifier": "filo", "password": "owner-password-long"},
            headers={"X-CSRF-Token": token(owner)},
        )
        assert response.status_code == 200, response.text
        assert owner.get("/api/business/records").json()["items"] == []
        assert owner.get("/api/business/records/" + mine["id"]).status_code == 404
        with app.state.db.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM business_records").fetchone()[0] == 0


def test_business_record_persists_in_its_account_after_server_restart(site):
    _, client, runtime = site
    account = register(client, "persist-business@example.test")
    item = add_quote(client, account["csrf_token"], "Offerta dopo riavvio", notes="Nota ancora privata")
    restarted = create_app(data_dir=runtime, start_worker=False)
    with TestClient(restarted) as returning:
        returning.cookies.update(client.cookies)
        assert returning.get("/api/auth/session").json()["user"]["id"] == account["user"]["id"]
        response = returning.get("/api/business/records/" + item["id"])
        assert response.status_code == 200, response.text
        assert response.json()["item"]["notes"] == "Nota ancora privata"
        assert returning.get("/api/business/summary").json()["totals"]["total"] == 1
