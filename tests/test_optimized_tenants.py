"""Account isolation for linked business documents and literal record search.

The real public application is used with temporary storage; no provider, SDI,
banking or payment operation runs during these checks.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


PASSWORD = "Private#Workflow-42"
OWNER_PASSWORD = "owner-workflow-password"
BASE = "/api/business/records"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "0")
    for key in ("STRIPE_SECRET_KEY", "STRIPE_PRICE_ID", "STRIPE_WEBHOOK_SECRET"):
        monkeypatch.delenv(key, raising=False)
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield app, client


def csrf(client):
    return client.get("/api/auth/session").json()["csrf_token"]


def register(client, email):
    response = client.post(
        "/api/auth/register", json={"name": "Studio privato", "email": email, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def mutation(client, token, method, path, payload):
    return client.request(method, path, json=payload, headers={"X-CSRF-Token": token})


def save_profile(client, token, name, vat):
    response = mutation(client, token, "PUT", "/api/company", {
        "name": name, "legal_name": name, "vat_number": vat, "address": "Via " + name + " 1",
    })
    assert response.status_code == 200, response.text


def add_quote(client, token, title):
    response = mutation(client, token, "POST", BASE, {
        "service_id": "quotes", "title": title, "status": "waiting", "saved_minutes": 9,
        "details": {"client": "Cliente " + title, "scope": "Servizi descritti dall’utente",
                    "net_amount": "123.45", "vat_rate": "22", "payment_terms": "30 giorni"},
    })
    assert response.status_code == 201, response.text
    return response.json()["item"]


def convert(client, token, source, target):
    response = mutation(client, token, "POST", BASE + "/" + source["id"] + "/convert", {"target_service_id": target})
    assert response.status_code == 200, response.text
    assert response.json()["created"] is True
    return response.json()["item"]


def test_conversion_boundary_requires_login_csrf_and_same_origin(site):
    _, client = site
    unknown = "a" * 32
    proposal = BASE + "/" + unknown + "/conversion?target_service_id=invoices"
    response = client.get(proposal)
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers
    assert client.get(BASE, params={"q": "riservato"}).status_code == 401
    assert client.post(BASE + "/" + unknown + "/convert", json={"target_service_id": "invoices"}).status_code == 401

    token = register(client, "workflow-csrf@example.test")
    source = add_quote(client, token, "Preventivo protetto")
    endpoint = BASE + "/" + source["id"] + "/convert"
    assert client.post(endpoint, json={"target_service_id": "invoices"}).status_code == 403
    response = client.post(endpoint, json={"target_service_id": "invoices"},
                           headers={"X-CSRF-Token": token, "Origin": "https://foreign.example"})
    assert response.status_code == 403
    response = client.get(BASE + "/" + source["id"] + "/conversion", params={"target_service_id": "invoices"})
    assert response.status_code == 200, response.text
    assert response.json()["existing"] is None
    assert client.get(BASE).json()["total"] == 1
    # Reading the proposal never implies payment or completion of the source.
    assert client.get(BASE + "/" + source["id"]).json()["item"]["status"] == "waiting"


def test_conversion_search_links_and_exports_stay_in_the_signed_in_account(site):
    app, alice = site
    alice_token = register(alice, "workflow-alice@example.test")
    save_profile(alice, alice_token, "Emittente Alice", "12345678903")
    source = add_quote(alice, alice_token, "Offerta Alice privata %_")
    invoice = convert(alice, alice_token, source, "invoices")
    receivable = convert(alice, alice_token, invoice, "receivables")
    assert receivable["details"]["amount"] == "150.61"
    for item in (invoice, receivable):
        assert item["status"] == "todo" and item["saved_minutes"] == 0
        assert item["links"]["source"]["id"] in (source["id"], invoice["id"])
    assert alice.get(BASE + "/" + source["id"]).json()["item"]["status"] == "waiting"

    with TestClient(app) as bob:
        bob_token = register(bob, "workflow-bob@example.test")
        save_profile(bob, bob_token, "Emittente Bob", "98765432103")
        bob_source = add_quote(bob, bob_token, "Offerta Bob privata")
        bob_invoice = convert(bob, bob_token, bob_source, "invoices")
        for foreign in (source, invoice, receivable):
            assert bob.get(BASE + "/" + foreign["id"]).status_code == 404
            assert bob.get(BASE + "/" + foreign["id"] + "/export").status_code == 404
        assert bob.get(BASE + "/" + source["id"] + "/conversion", params={"target_service_id": "invoices"}).status_code == 404
        assert mutation(bob, bob_token, "POST", BASE + "/" + source["id"] + "/convert", {"target_service_id": "invoices"}).status_code == 404
        assert mutation(bob, alice_token, "POST", BASE + "/" + bob_invoice["id"] + "/convert", {"target_service_id": "receivables"}).status_code == 403

        alice_search = alice.get(BASE, params={"q": "Alice privata %_"})
        bob_search = bob.get(BASE, params={"q": "Alice privata %_"})
        assert alice_search.status_code == bob_search.status_code == 200
        assert alice_search.json()["total"] == 3
        assert bob_search.json()["items"] == []
        assert "Emittente Alice" not in str(bob.get(BASE).json())
        assert alice.get(BASE, params={"q": "Bob privata"}).json()["items"] == []
        bob_current = bob.get(BASE + "/" + bob_source["id"]).json()["item"]
        assert [item["id"] for item in bob_current["links"]["targets"]] == [bob_invoice["id"]]
        assert "Alice" not in str(bob_current["links"])

        repeated = mutation(alice, alice_token, "POST", BASE + "/" + source["id"] + "/convert", {"target_service_id": "invoices"})
        assert repeated.status_code == 200
        assert repeated.json()["created"] is False and repeated.json()["item"]["id"] == invoice["id"]
        assert alice.get(BASE).json()["total"] == 3
        exported = alice.get(BASE + "/" + invoice["id"] + "/export")
        assert exported.status_code == 200
        assert "Emittente Alice" in exported.text and "12345678903" in exported.text
        assert "Emittente Bob" not in exported.text and "98765432103" not in exported.text
        assert "non è una fattura fiscale" in exported.text
        assert "Nessuna numerazione fiscale assegnata o trasmissione allo SDI" in exported.text
    with TestClient(app) as owner:
        response = owner.post("/api/auth/login", json={"identifier": "filo", "password": OWNER_PASSWORD},
                              headers={"X-CSRF-Token": csrf(owner)})
        assert response.status_code == 200, response.text
        assert owner.get(BASE, params={"q": "Alice privata"}).json()["items"] == []
        assert owner.get(BASE + "/" + source["id"] + "/conversion", params={"target_service_id": "invoices"}).status_code == 404


def test_exports_use_current_private_company_profile_and_omit_demo_identity(site):
    app, client = site
    token = register(client, "current-issuer@example.test")
    save_profile(client, token, "Emittente iniziale", "12345678903")
    source = add_quote(client, token, "Offerta corrente")
    invoice = convert(client, token, source, "invoices")
    assert "Emittente iniziale" in invoice["document"]
    save_profile(client, token, "Emittente aggiornato", "98765432103")
    for item in (source, invoice):
        response = client.get(BASE + "/" + item["id"] + "/export")
        assert response.status_code == 200
        assert "Emittente aggiornato" in response.text and "98765432103" in response.text
        assert "Emittente iniziale" not in response.text and "12345678903" not in response.text

    with TestClient(app) as owner:
        response = owner.post("/api/auth/login", json={"identifier": "filo", "password": OWNER_PASSWORD},
                              headers={"X-CSRF-Token": csrf(owner)})
        assert response.status_code == 200, response.text
        owner_token = response.json()["csrf_token"]
        app.state.db.set_setting("company", {"name": "Azienda dimostrativa segreta", "legal_name": "Identità fittizia", "vat_number": "11111111115", "demo": True})
        demo_source = add_quote(owner, owner_token, "Offerta senza emittente demo")
        demo_invoice = convert(owner, owner_token, demo_source, "invoices")
        for item in (demo_source, demo_invoice):
            exported = owner.get(BASE + "/" + item["id"] + "/export").text
            assert "Azienda dimostrativa segreta" not in exported
            assert "Identità fittizia" not in exported and "11111111115" not in exported
            assert "Emittente aggiornato" not in exported
