"""Release checks for amounts, text, search and conversions of business records."""
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agenda import create_agenda_router
from app.business import create_business_router
from app.company import normalize_company_field
from app.db import Database


@pytest.fixture
def business(tmp_path):
    db = Database(tmp_path / "release.sqlite3")
    app = FastAPI()
    app.include_router(create_business_router(db))
    app.include_router(create_agenda_router(db))
    with TestClient(app) as client:
        yield db, client


def quote(client, net="100.00", **changes):
    payload = {"service_id": "quotes", "title": "Preventivo Società Élite", "contact": "Ada",
               "details": {"client": "SOCIETÀ ÉLITE", "scope": "Analisi", "net_amount": net, "vat_rate": "22"}}
    payload.update(changes)
    response = client.post("/api/business/records", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["item"]


@pytest.mark.parametrize("value", ["-0", "-0.00", "-0.0"])
def test_negative_zero_is_stored_and_printed_as_zero(business, value):
    _, client = business
    item = quote(client, net=value)
    assert item["details"]["net_amount"] == "0.00"
    assert item["totals"]["gross_amount"] == "0.00"
    assert "−0,00" not in item["document"] and "-0,00" not in item["document"]


def test_negative_zero_saved_by_an_older_version_is_printed_as_zero(business):
    db, client = business
    item = quote(client, net="0.00")
    with db.connection() as conn:
        details = {"client": "Cliente", "scope": "Analisi", "net_amount": "-0.00", "vat_rate": "22"}
        conn.execute("UPDATE business_records SET details=? WHERE id=?", (json.dumps(details), item["id"]))
    stored = client.get(f'/api/business/records/{item["id"]}').json()["item"]
    assert stored["totals"]["net_amount"] == "0.00"
    assert "−0,00" not in stored["document"]


def test_largest_invoice_converts_to_a_receivable_with_its_gross_amount(business):
    _, client = business
    invoice = client.post("/api/business/records", json={
        "service_id": "invoices", "title": "Fattura massima",
        "details": {"client": "Cliente", "reference": "F-1", "description": "Opera", "net_amount": "1000000000.00", "vat_rate": "22"},
    })
    assert invoice.status_code == 201, invoice.text
    item = invoice.json()["item"]
    response = client.post(f'/api/business/records/{item["id"]}/convert', json={"target_service_id": "receivables"})
    assert response.status_code == 200, response.text
    assert response.json()["item"]["details"]["amount"] == "1220000000.00"


@pytest.mark.parametrize("text", ["Totale ‮00,001", "Titolo​invisibile", "Riga\u0085nascosta", "⁦isolato⁩"])
def test_direction_overrides_and_invisible_characters_are_rejected_everywhere(business, text):
    _, client = business
    response = client.post("/api/business/records", json={"service_id": "documents", "title": text, "details": {}})
    assert response.status_code == 422
    agenda = client.post("/api/agenda", json={"title": text, "starts_at": "2027-01-10T09:00:00+01:00"})
    assert agenda.status_code == 422
    with pytest.raises(ValueError):
        normalize_company_field("name", text)


def test_emoji_sequences_remain_valid_text(business):
    _, client = business
    response = client.post("/api/business/records", json={"service_id": "documents", "title": "Team 👩‍💻 pronto", "details": {}})
    assert response.status_code in (201, 422)
    if response.status_code == 422:
        # Only required details may be missing; the title itself is accepted.
        assert "caratteri" not in response.text
    assert normalize_company_field("name", "Studio 👩‍💻") == "Studio 👩‍💻"


@pytest.mark.parametrize("query", ["società", "SOCIETA", "elite", "ÉLITE", "Élite"])
def test_search_ignores_case_and_accents(business, query):
    _, client = business
    item = quote(client)
    found = client.get("/api/business/records", params={"q": query}).json()
    assert [record["id"] for record in found["items"]] == [item["id"]]


@pytest.mark.parametrize("query", ["1.250,50", "1250,50", "1250.50"])
def test_search_finds_amounts_written_in_italian_format(business, query):
    _, client = business
    item = quote(client, net="1250.50")
    quote(client, net="99.00", title="Altro preventivo")
    found = client.get("/api/business/records", params={"q": query}).json()
    assert [record["id"] for record in found["items"]] == [item["id"]]


def test_search_wildcards_remain_literal(business):
    _, client = business
    quote(client, title="Sconto 100% cliente")
    quote(client, title="Sconto 1000 cliente")
    found = client.get("/api/business/records", params={"q": "100%"}).json()
    assert [record["title"] for record in found["items"]] == ["Sconto 100% cliente"]


def test_cancelled_records_cannot_start_a_conversion(business):
    _, client = business
    item = quote(client, status="cancelled")
    proposal = client.get(f'/api/business/records/{item["id"]}/conversion', params={"target_service_id": "invoices"})
    assert proposal.status_code == 409
    response = client.post(f'/api/business/records/{item["id"]}/convert', json={"target_service_id": "invoices"})
    assert response.status_code == 409
    assert "annullata" in response.json()["detail"]
    assert client.get("/api/business/records", params={"service_id": "invoices"}).json()["items"] == []
    reopened = client.patch(f'/api/business/records/{item["id"]}', json={"status": "todo"})
    assert reopened.status_code == 200
    assert client.post(f'/api/business/records/{item["id"]}/convert', json={"target_service_id": "invoices"}).status_code == 200


def test_existing_conversion_stays_visible_after_the_source_is_cancelled(business):
    _, client = business
    item = quote(client)
    created = client.post(f'/api/business/records/{item["id"]}/convert', json={"target_service_id": "invoices"}).json()["item"]
    client.patch(f'/api/business/records/{item["id"]}', json={"status": "cancelled"})
    proposal = client.get(f'/api/business/records/{item["id"]}/conversion', params={"target_service_id": "invoices"})
    assert proposal.status_code == 200
    assert proposal.json()["existing"]["id"] == created["id"]


def test_record_of_a_module_removed_from_the_catalog_does_not_break_the_workspace(business):
    db, client = business
    item = quote(client)
    with db.connection() as conn:
        conn.execute("UPDATE business_records SET service_id='retired-module' WHERE id=?", (item["id"],))
    listing = client.get("/api/business/records")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["service_name"] == "Modulo non più disponibile"
    assert client.get("/api/business/summary").status_code == 200
    assert client.get(f'/api/business/records/{item["id"]}/export').status_code == 200


@pytest.mark.parametrize("value", ["1700000000", "1700000000.5", "20270110"])
def test_agenda_rejects_numeric_timestamps(business, value):
    _, client = business
    response = client.post("/api/agenda", json={"title": "Riunione", "starts_at": value})
    assert response.status_code == 422


@pytest.mark.parametrize("field,value", [
    ("vat_number", "12345678901"), ("vat_number", "IT01234567890"),
    ("tax_code", "RSSMRA80A01H501X"), ("tax_code", "12345678901"), ("tax_code", "ABCDEFGHIJKLMNOP"),
])
def test_company_codes_with_a_wrong_check_character_are_rejected(field, value):
    with pytest.raises(ValueError):
        normalize_company_field(field, value)


@pytest.mark.parametrize("field,value,expected", [
    ("vat_number", "IT12345678903", "12345678903"), ("vat_number", "01234567897", "01234567897"),
    ("tax_code", "rssmra80a01h501u", "RSSMRA80A01H501U"), ("tax_code", "MRTMTT25D09F205Z", "MRTMTT25D09F205Z"),
    ("tax_code", "12345678903", "12345678903"),
])
def test_company_codes_with_valid_check_characters_are_accepted(field, value, expected):
    assert normalize_company_field(field, value) == expected
