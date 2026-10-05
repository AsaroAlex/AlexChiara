"""Checklist, repetition and cash summaries respect the real account boundary.

These tests exercise the public app with private temporary workspaces. All
amounts and dates are entered by the test; no provider or payment is contacted.
"""
from datetime import datetime, timedelta
import os
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


BASE = "/api/business/records"
PASSWORD = "Private#Playbook-42"
OWNER_PASSWORD = "owner-playbook-password"


@pytest.fixture
def site(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith(("FILO_GOOGLE_", "FILO_MICROSOFT_", "STRIPE_", "OPENAI_")):
            monkeypatch.delenv(key, raising=False)
    for key in ("FILO_PUBLIC_URL", "RAILWAY_PUBLIC_DOMAIN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("FILO_ACCESS_USERNAME", "filo")
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", OWNER_PASSWORD)
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "0")
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as client:
        yield app, client, tmp_path


def calendar_dates():
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    return tuple((today + timedelta(days=offset)).isoformat() for offset in (-1, 0, 7))


def csrf(client):
    response = client.get("/api/auth/session")
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def register(client, email):
    response = client.post(
        "/api/auth/register", json={"name": "Studio privato", "email": email, "password": PASSWORD},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def mutate(client, token, method, path, payload):
    return client.request(method, path, json=payload, headers={"X-CSRF-Token": token})


def add_record(client, token, service_id, title, details, **values):
    response = mutate(client, token, "POST", BASE, {
        "service_id": service_id, "title": title, "details": details, **values,
    })
    assert response.status_code == 201, response.text
    return response.json()["item"]


def add_quote(client, token, title, **values):
    return add_record(client, token, "quotes", title, {
        "client": "Cliente " + title, "scope": "Prestazioni concordate dal cliente",
        "net_amount": "1000.00", "vat_rate": "22",
    }, **values)


def save_profile(client, token, name, vat):
    response = mutate(client, token, "PUT", "/api/company", {
        "name": name, "legal_name": name, "vat_number": vat,
    })
    assert response.status_code == 200, response.text


def workspace_counts(app, client):
    user_id = client.get("/api/auth/session").json()["user"]["id"]
    with app.state.workspaces[user_id].state.db.connection() as conn:
        return tuple(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                     for table in ("business_records", "business_repeats"))


def repeat(client, token, source, next_date, **changes):
    response = mutate(client, token, "POST", BASE + "/" + source["id"] + "/repeat", {
        "next_date": next_date, **changes,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_new_boundaries_require_login_csrf_and_same_origin_and_reads_do_not_write(site):
    app, client, _ = site
    _, _, next_date = calendar_dates()
    unknown = "a" * 32
    proposal_url = BASE + "/" + unknown + "/repetition"
    response = client.get(proposal_url, params={"next_date": next_date})
    assert response.status_code == 401
    assert "www-authenticate" not in response.headers
    assert response.headers.get("cache-control") == "no-store"
    assert client.post(BASE + "/" + unknown + "/repeat", json={"next_date": next_date}).status_code == 401
    assert client.patch(BASE + "/" + unknown, json={"steps": {"scope": True}}).status_code == 401

    token = register(client, "playbook-boundary@example.test")
    source = add_quote(client, token, "Attività da rivedere", status="waiting", saved_minutes=7)
    step = source["playbook"]["steps"][0]["id"]
    requests = (("PATCH", BASE + "/" + source["id"], {"steps": {step: True}}),
                ("POST", BASE + "/" + source["id"] + "/repeat", {"next_date": next_date}))
    for method, path, payload in requests:
        assert client.request(method, path, json=payload).status_code == 403
        response = client.request(method, path, json=payload,
                                  headers={"X-CSRF-Token": token, "Origin": "https://foreign.example"})
        assert response.status_code == 403
    assert workspace_counts(app, client) == (1, 0)
    for _ in range(2):
        response = client.get(BASE + "/" + source["id"] + "/repetition", params={"next_date": next_date})
        assert response.status_code == 200, response.text
        assert response.json()["existing"] is None
        assert response.json()["proposal"]["steps"] == {}
        assert response.json()["proposal"]["saved_minutes"] == 0
    assert workspace_counts(app, client) == (1, 0)
    unchanged = client.get(BASE + "/" + source["id"]).json()["item"]
    assert unchanged["updated_at"] == source["updated_at"]
    assert unchanged["status"] == "waiting" and unchanged["playbook"]["completed"] == 0

    response = client.patch(BASE + "/" + source["id"], json={"steps": {step: True}},
                            headers={"X-CSRF-Token": token, "Origin": "http://testserver"})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["playbook"]["completed"] == 1


def test_checklists_repetitions_cash_and_company_exports_are_private_for_members_and_owner(site):
    app, alice, _ = site
    yesterday, today, next_date = calendar_dates()
    alice_token = register(alice, "playbook-alice@example.test")
    save_profile(alice, alice_token, "Emittente Alice privata", "12345678903")
    alice_source = add_quote(alice, alice_token, "Preventivo Alice riservato", status="done", saved_minutes=9)
    alice_step = alice_source["playbook"]["steps"][0]["id"]
    response = mutate(alice, alice_token, "PATCH", BASE + "/" + alice_source["id"], {"steps": {alice_step: True}})
    assert response.status_code == 200, response.text
    alice_receivable = add_record(alice, alice_token, "receivables", "Incasso Alice", {
        "client": "Cliente Alice", "reference": "A-1", "amount": "123.45", "expected_date": yesterday,
    }, status="waiting")
    add_record(alice, alice_token, "expenses", "Pagamento Alice", {
        "supplier": "Fornitore Alice", "reference": "A-2", "amount": "34.56", "payment_date": today,
    })
    alice_repeat = repeat(alice, alice_token, alice_source, next_date, title="Prossima attività Alice")
    alice_child = alice_repeat["item"]
    assert alice_repeat["created"] is True
    assert alice_child["status"] == "todo" and alice_child["saved_minutes"] == 0
    assert alice_child["playbook"]["completed"] == 0
    assert alice_child["links"]["source"] == {
        "id": alice_source["id"], "title": alice_source["title"], "service_id": "quotes", "kind": "repeat",
    }
    assert mutate(alice, alice_token, "PATCH", BASE + "/" + alice_child["id"], {"steps": {alice_step: True}}).status_code == 200

    with TestClient(app) as bob:
        bob_token = register(bob, "playbook-bob@example.test")
        save_profile(bob, bob_token, "Emittente Bob privata", "98765432103")
        bob_source = add_quote(bob, bob_token, "Preventivo Bob riservato")
        bob_child = repeat(bob, bob_token, bob_source, next_date, title="Prossima attività Bob")["item"]
        add_record(bob, bob_token, "receivables", "Incasso Bob", {
            "client": "Cliente Bob", "reference": "B-1", "amount": "200.01", "expected_date": next_date,
        })
        add_record(bob, bob_token, "expenses", "Pagamento Bob", {
            "supplier": "Fornitore Bob", "reference": "B-2", "amount": "20.10", "payment_date": today,
        })
        for client, token, mine, foreign in ((alice, alice_token, alice_source, bob_source),
                                           (bob, bob_token, bob_source, alice_source)):
            foreign_path = BASE + "/" + foreign["id"]
            assert client.get(foreign_path).status_code == 404
            assert client.get(foreign_path + "/export").status_code == 404
            assert client.get(foreign_path + "/repetition", params={"next_date": next_date}).status_code == 404
            assert mutate(client, token, "POST", foreign_path + "/repeat", {"next_date": next_date}).status_code == 404
            assert mutate(client, token, "PATCH", foreign_path, {"steps": {alice_step: True}}).status_code == 404
            current = client.get(BASE + "/" + mine["id"]).json()["item"]
            assert foreign["title"] not in str(current["links"])
            summary = client.get("/api/business/summary").json()
            assert foreign["title"] not in str(summary)
        assert mutate(bob, alice_token, "PATCH", BASE + "/" + bob_child["id"], {"steps": {alice_step: True}}).status_code == 403
        assert mutate(bob, alice_token, "POST", BASE + "/" + bob_source["id"] + "/repeat", {"next_date": next_date}).status_code == 403
        for key in ("workspace_id", "user_id", "source", "steps"):
            payload = {"next_date": next_date, key: {} if key == "steps" else "owner"}
            assert mutate(bob, bob_token, "POST", BASE + "/" + bob_source["id"] + "/repeat", payload).status_code == 422

        alice_summary = alice.get("/api/business/summary").json()
        bob_summary = bob.get("/api/business/summary").json()
        assert alice_summary["financial_summary"]["receivables"] == {
            "open_amount": "123.45", "overdue_amount": "123.45", "today_amount": "0.00", "count": 1,
        }
        assert alice_summary["financial_summary"]["payables"] == {
            "open_amount": "34.56", "overdue_amount": "0.00", "today_amount": "34.56", "count": 1,
        }
        assert bob_summary["financial_summary"]["receivables"] == {
            "open_amount": "200.01", "overdue_amount": "0.00", "today_amount": "0.00", "count": 1,
        }
        assert bob_summary["financial_summary"]["payables"]["open_amount"] == "20.10"
        assert alice_summary["next_actions"][0]["id"] == alice_receivable["id"]
        assert alice_summary["next_actions"][0]["due_source"]["type"] == "field"
        assert bob.get(BASE + "/" + bob_child["id"]).json()["item"]["playbook"]["completed"] == 0

        before = workspace_counts(app, alice)
        proposal = alice.get(BASE + "/" + alice_source["id"] + "/repetition", params={"next_date": next_date}).json()
        assert proposal["existing"]["id"] == alice_child["id"]
        retry = repeat(alice, alice_token, alice_source, next_date, title="Non sovrascrivere")
        assert retry["created"] is False and retry["item"]["title"] == "Prossima attività Alice"
        assert retry["item"]["playbook"]["completed"] == 1
        assert workspace_counts(app, alice) == before
        for client, child, own_name, own_vat, foreign_name, foreign_vat in (
            (alice, alice_child, "Emittente Alice privata", "12345678903", "Emittente Bob privata", "98765432103"),
            (bob, bob_child, "Emittente Bob privata", "98765432103", "Emittente Alice privata", "12345678903"),
        ):
            exported = client.get(BASE + "/" + child["id"] + "/export")
            assert exported.status_code == 200, exported.text
            assert own_name in exported.text and own_vat in exported.text
            assert foreign_name not in exported.text and foreign_vat not in exported.text
            assert "stato segnato manualmente da te" in exported.text
        assert "[x]" in alice.get(BASE + "/" + alice_child["id"] + "/export").text
        assert "[x]" not in bob.get(BASE + "/" + bob_child["id"] + "/export").text

    with TestClient(app) as owner:
        response = owner.post("/api/auth/login", json={"identifier": "filo", "password": OWNER_PASSWORD},
                              headers={"X-CSRF-Token": csrf(owner)})
        assert response.status_code == 200, response.text
        owner_token = response.json()["csrf_token"]
        assert owner.get("/api/business/summary").json()["financial_summary"]["receivables"]["open_amount"] == "0.00"
        add_record(owner, owner_token, "receivables", "Incasso proprietario", {
            "client": "Cliente proprietario", "reference": "P-1", "amount": "7.77", "expected_date": today,
        })
        assert owner.get("/api/business/summary").json()["financial_summary"]["receivables"]["open_amount"] == "7.77"
        assert "Alice" not in str(owner.get("/api/business/summary").json())
        assert owner.get(BASE + "/" + alice_child["id"] + "/export").status_code == 404
        assert owner.get(BASE + "/" + alice_source["id"] + "/repetition", params={"next_date": next_date}).status_code == 404
        assert mutate(owner, owner_token, "POST", BASE + "/" + alice_source["id"] + "/repeat", {"next_date": next_date}).status_code == 404


def test_repetition_checklist_and_private_origin_survive_application_restart(site):
    _, client, runtime = site
    _, _, next_date = calendar_dates()
    token = register(client, "playbook-restart@example.test")
    source = add_quote(client, token, "Attività privata persistente")
    child = repeat(client, token, source, next_date)["item"]
    step_id = child["playbook"]["steps"][0]["id"]
    assert mutate(client, token, "PATCH", BASE + "/" + child["id"], {"steps": {step_id: True}}).status_code == 200

    restarted = create_app(data_dir=runtime, start_worker=False)
    with TestClient(restarted) as returning:
        returning.cookies.update(client.cookies)
        item = returning.get(BASE + "/" + child["id"]).json()["item"]
        assert item["playbook"]["completed"] == 1 and item["steps"][step_id] is True
        assert item["links"]["source"]["id"] == source["id"]
        assert item["effective_due_date"] == next_date
        existing = returning.get(BASE + "/" + source["id"] + "/repetition", params={"next_date": next_date}).json()["existing"]
        assert existing["id"] == child["id"] and existing["playbook"]["completed"] == 1
        assert workspace_counts(restarted, returning) == (2, 1)
