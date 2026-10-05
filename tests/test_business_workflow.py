"""Reviewable internal document flows, without fiscal issuance or external actions."""
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.business import create_business_router
from app.db import Database
from app.main import create_app


def client_for(db):
    app = FastAPI()
    app.include_router(create_business_router(db))
    return TestClient(app)


@pytest.fixture
def business(tmp_path):
    db = Database(tmp_path / "workflow.sqlite3")
    with client_for(db) as client:
        yield db, client


def quote(client, **changes):
    payload = {
        "service_id": "quotes", "title": "Consulenza Studio Aurora", "contact": "Ada",
        "priority": "high", "status": "waiting", "saved_minutes": 48,
        "due_date": "2027-02-10", "notes": "Da verificare con il cliente",
        "details": {"client": "Studio Aurora", "scope": "Analisi e consegna",
                    "net_amount": "123.45", "vat_rate": "22", "valid_until": "2027-02-08",
                    "payment_terms": "Bonifico entro 30 giorni"},
    }
    payload.update(changes)
    response = client.post("/api/business/records", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["item"]


def convert(client, item, target_service_id, **changes):
    response = client.post(f'/api/business/records/{item["id"]}/convert', json={"target_service_id": target_service_id, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def test_proposals_are_read_only_and_copy_selected_data_without_dates_or_completion(business):
    _, client = business
    source = quote(client)
    proposal = client.get(f'/api/business/records/{source["id"]}/conversion', params={"target_service_id": "invoices"})
    assert proposal.status_code == 200, proposal.text
    data = proposal.json()
    assert data["existing"] is None
    assert data["source"] == {"id": source["id"], "title": source["title"], "service_id": "quotes"}
    assert data["proposal"] == {
        "service_id": "invoices", "title": "Bozza fattura — Consulenza Studio Aurora",
        "contact": "Ada", "due_date": None, "status": "todo", "priority": "high",
        "notes": "Da verificare con il cliente", "saved_minutes": 0,
        "details": {"client": "Studio Aurora", "reference": "Bozza da Consulenza Studio Aurora",
                    "description": "Analisi e consegna", "net_amount": "123.45", "vat_rate": "22",
                    "payment_terms": "Bonifico entro 30 giorni"},
    }
    assert client.get("/api/business/records").json()["total"] == 1
    assert client.get(f'/api/business/records/{source["id"]}').json()["item"] == source


def test_quote_to_draft_to_receivable_keeps_decimal_vat_and_manual_reference(business):
    db, client = business
    source = quote(client)
    invoice_result = convert(client, source, "invoices", details={"reference": "Riferimento concordato AC/26"})
    invoice = invoice_result["item"]
    assert invoice_result["created"] is True
    assert invoice["totals"]["vat_amount"] == "27.16"
    assert invoice["totals"]["gross_amount"] == "150.61"
    assert invoice["status"] == "todo" and invoice["saved_minutes"] == 0 and invoice["due_date"] is None
    assert invoice["details"]["reference"] == "Riferimento concordato AC/26"
    assert "BOZZA INTERNA — non è una fattura fiscale" in invoice["document"]
    assert "Nessuna numerazione fiscale assegnata o trasmissione allo SDI" in invoice["document"]
    receivable = convert(client, invoice, "receivables")["item"]
    assert receivable["details"] == {"client": "Studio Aurora", "reference": "Riferimento concordato AC/26", "amount": "150.61", "payment_context": "Bonifico entro 30 giorni"}
    assert receivable["due_date"] is None and "expected_date" not in receivable["details"]
    assert receivable["links"]["source"]["id"] == invoice["id"]
    invoice_now = client.get(f'/api/business/records/{invoice["id"]}').json()["item"]
    assert invoice_now["links"]["source"]["id"] == source["id"]
    assert invoice_now["links"]["targets"][0]["id"] == receivable["id"]
    source_now = client.get(f'/api/business/records/{source["id"]}').json()["item"]
    assert source_now["status"] == "waiting" and source_now["saved_minutes"] == 48
    assert source_now["links"]["targets"][0]["id"] == invoice["id"]
    assert client.get("/api/business/summary").json()["declared_saved_minutes"] == 0
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_review_edits_are_validated_and_do_not_change_source_or_infer_due_date(business):
    _, client = business
    source = quote(client)
    invoice = convert(client, source, "invoices", title="Bozza rivista", due_date="2027-03-20", priority="normal",
                      contact="Luca", notes="", details={"net_amount": "0.10", "vat_rate": "5", "payment_terms": ""})["item"]
    assert invoice["title"] == "Bozza rivista" and invoice["due_date"] == "2027-03-20"
    assert invoice["contact"] == "Luca" and invoice["notes"] == "" and invoice["priority"] == "normal"
    assert invoice["totals"]["gross_amount"] == "0.11" and invoice["totals"]["vat_amount"] == "0.01"
    assert "payment_terms" not in invoice["details"]
    unchanged = client.get(f'/api/business/records/{source["id"]}').json()["item"]
    assert unchanged["details"] == source["details"] and unchanged["due_date"] == source["due_date"]


def test_later_source_changes_and_duplicate_clicks_do_not_overwrite_existing_draft(business):
    _, client = business
    source = quote(client)
    invoice = convert(client, source, "invoices")["item"]
    client.patch(f'/api/business/records/{invoice["id"]}', json={"title": "Bozza già corretta", "details": {"net_amount": "200.00"}})
    client.patch(f'/api/business/records/{source["id"]}', json={"title": "Preventivo aggiornato", "details": {"client": "Altro cliente", "net_amount": "900.00"}})
    existing = client.get(f'/api/business/records/{invoice["id"]}').json()["item"]
    assert existing["details"]["client"] == "Studio Aurora" and existing["totals"]["gross_amount"] == "244.00"
    proposal = client.get(f'/api/business/records/{source["id"]}/conversion', params={"target_service_id": "invoices"}).json()
    assert proposal["existing"] == existing
    assert proposal["proposal"]["details"]["client"] == "Altro cliente"
    repeated = convert(client, source, "invoices", title="Tentativo di sovrascrittura", details={"net_amount": "1.00"})
    assert repeated["created"] is False and repeated["item"] == existing
    assert client.get("/api/business/records").json()["total"] == 2


def test_concurrent_conversion_retries_create_one_target_and_keep_relationship_after_restart(business):
    db, client = business
    source = quote(client)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: convert(client, source, "invoices"), range(16)))
    assert sum(result["created"] for result in results) == 1
    assert len({result["item"]["id"] for result in results}) == 1
    target = results[0]["item"]
    with client_for(Database(db.path)) as reopened:
        assert reopened.get(f'/api/business/records/{target["id"]}').json()["item"]["links"]["source"]["id"] == source["id"]
        assert convert(reopened, source, "invoices")["created"] is False
        assert reopened.get("/api/business/records").json()["total"] == 2


def test_deleting_source_or_target_removes_only_links_and_keeps_other_documents_usable(business):
    _, client = business
    source = quote(client)
    invoice = convert(client, source, "invoices")["item"]
    receivable = convert(client, invoice, "receivables")["item"]
    assert client.delete(f'/api/business/records/{invoice["id"]}').status_code == 200
    assert client.get(f'/api/business/records/{source["id"]}').json()["item"]["links"] == {"source": None, "targets": []}
    child = client.get(f'/api/business/records/{receivable["id"]}').json()["item"]
    assert child["links"] == {"source": None, "targets": []}
    assert child["details"]["amount"] == "150.61"
    assert client.get(f'/api/business/records/{receivable["id"]}/export').status_code == 200
    replacement = convert(client, source, "invoices")["item"]
    assert replacement["id"] != invoice["id"]
    assert client.delete(f'/api/business/records/{source["id"]}').status_code == 200
    assert client.get(f'/api/business/records/{replacement["id"]}').json()["item"]["links"]["source"] is None


@pytest.mark.parametrize("changes", [
    {"status": "done"}, {"saved_minutes": 200}, {"source": "fiscal"}, {"service_id": "invoices"},
    {"user_id": "owner"}, {"title": None}, {"details": None}, {"details": {"vat_rate": "99"}},
    {"details": {"invoice_number": "2026/001"}}, {"due_date": "2026-02-30"},
    {"details": {f"unknown-{index}": "x" for index in range(24)}},
])
def test_conversion_rejects_fiscal_or_workspace_controls_and_invalid_edits(business, changes):
    _, client = business
    source = quote(client)
    response = client.post(f'/api/business/records/{source["id"]}/convert', json={"target_service_id": "invoices", **changes})
    assert response.status_code == 422, response.text
    assert client.get("/api/business/records").json()["total"] == 1
    assert client.get(f'/api/business/records/{source["id"]}').json()["item"]["links"]["targets"] == []


def test_wrong_flow_and_missing_source_never_create_targets(business):
    _, client = business
    source = quote(client)
    for suffix, method in (("conversion?target_service_id=receivables", "GET"), ("convert", "POST")):
        response = client.request(method, f'/api/business/records/{source["id"]}/{suffix}', json={"target_service_id": "receivables"} if method == "POST" else None)
        assert response.status_code == 422
    for suffix, method in (("conversion?target_service_id=invoices", "GET"), ("convert", "POST")):
        response = client.request(method, f'/api/business/records/{"a" * 32}/{suffix}', json={"target_service_id": "invoices"} if method == "POST" else None)
        assert response.status_code == 404
    assert client.get("/api/business/records").json()["total"] == 1


def test_capacity_failure_rolls_back_target_and_link_but_retries_for_existing_still_work(business, monkeypatch):
    import app.business as module
    _, client = business
    source = quote(client)
    monkeypatch.setattr(module, "MAX_RECORDS", 2)
    invoice = convert(client, source, "invoices")["item"]
    response = client.post(f'/api/business/records/{invoice["id"]}/convert', json={"target_service_id": "receivables"})
    assert response.status_code == 409
    assert client.get("/api/business/records").json()["total"] == 2
    assert client.get(f'/api/business/records/{invoice["id"]}').json()["item"]["links"]["targets"] == []
    assert convert(client, source, "invoices")["created"] is False


def test_documents_reuse_current_private_issuer_and_hide_demo_or_missing_identity(business):
    db, client = business
    source = quote(client)
    assert "Studio Riva" not in source["document"] and "Azienda emittente:" not in source["document"]
    db.set_setting("company", {"name": "Studio visibile", "legal_name": "Aurora SRL", "vat_number": "IT12345678903",
                              "address": "Via Roma 12", "postal_code": "20100", "city": "Milano", "province": "mi"})
    invoice = convert(client, source, "invoices")["item"]
    receivable = convert(client, invoice, "receivables")["item"]
    listing = client.get("/api/business/records").json()["items"]
    summary = client.get("/api/business/summary").json()["next_actions"]
    for item in [*listing, *summary]:
        assert "Azienda emittente: Aurora SRL" in item["document"]
        assert "Partita IVA: 12345678903" in item["document"] and "Provincia: MI" in item["document"]
        assert "Via Roma 12" in item["document"] and "None" not in item["document"]
    db.set_setting("company", {"name": "Nuovo studio"})
    for item in (source, invoice, receivable):
        text = client.get(f'/api/business/records/{item["id"]}/export').text
        assert "Azienda emittente: Nuovo studio" in text and "Aurora SRL" not in text
    db.set_setting("company", {"name": "Finta azienda", "vat_number": "12345678903", "demo": True})
    assert "Finta azienda" not in client.get(f'/api/business/records/{invoice["id"]}/export').text


def test_conversion_preserves_email_mandate_credentials_and_runs(business):
    db, client = business
    mandate = db.get_setting("service")
    connection = {"provider": "gmail", "mailbox": "private@example.test"}
    credentials = {"encrypted": "local-test-only"}
    db.set_setting("connection", connection)
    db.set_setting("google_credentials", credentials)
    source = quote(client)
    invoice = convert(client, source, "invoices")["item"]
    convert(client, invoice, "receivables")
    assert db.get_setting("service") == mandate
    assert db.get_setting("connection") == connection
    assert db.get_setting("google_credentials") == credentials
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_conversion_account_routes_require_csrf_and_cannot_access_foreign_source(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", "Owner-test-password-42")
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "0")
    app = create_app(data_dir=tmp_path, start_worker=False)
    path = f'/api/business/records/{"a" * 32}/conversion?target_service_id=invoices'
    with TestClient(app) as alice, TestClient(app) as bob:
        assert alice.get(path).status_code == 401
        accounts = []
        for client, email in ((alice, "flow-alice@example.test"), (bob, "flow-bob@example.test")):
            csrf = client.get("/api/auth/session").json()["csrf_token"]
            response = client.post("/api/auth/register", json={"name": "Studio", "email": email, "password": "Private-Account-42!"}, headers={"X-CSRF-Token": csrf})
            assert response.status_code == 200, response.text
            accounts.append(response.json())
        payload = {"service_id": "quotes", "title": "Solo Alice", "details": {"client": "Alice", "scope": "Lavoro", "net_amount": "123.45", "vat_rate": "22"}}
        source_response = alice.post("/api/business/records", json=payload, headers={"X-CSRF-Token": accounts[0]["csrf_token"]})
        assert source_response.status_code == 201, source_response.text
        source = source_response.json()["item"]
        route = f'/api/business/records/{source["id"]}'
        assert alice.post(route + "/convert", json={"target_service_id": "invoices"}).status_code == 403
        assert alice.post(route + "/convert", json={"target_service_id": "invoices"}, headers={"X-CSRF-Token": accounts[0]["csrf_token"], "Origin": "https://foreign.example"}).status_code == 403
        assert bob.get(route + "/conversion?target_service_id=invoices").status_code == 404
        assert bob.post(route + "/convert", json={"target_service_id": "invoices"}, headers={"X-CSRF-Token": accounts[1]["csrf_token"]}).status_code == 404
        created = alice.post(route + "/convert", json={"target_service_id": "invoices"}, headers={"X-CSRF-Token": accounts[0]["csrf_token"]})
        assert created.status_code == 200, created.text
        assert bob.get("/api/business/records").json()["items"] == []
        target = created.json()["item"]
        assert bob.get(f'/api/business/records/{target["id"]}').status_code == 404
        assert target["links"]["source"]["id"] == source["id"]
