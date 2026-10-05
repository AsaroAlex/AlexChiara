"""Recorded operational work, local documents and Rome calendar priorities."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.business import business_services, business_summary, create_business_router
from app.business_catalog import get_service, list_services
from app.business_playbooks import get_playbook
from app.db import Database


UTC = timezone.utc


def client_for(db):
    app = FastAPI()
    app.include_router(create_business_router(db))
    return TestClient(app)


@pytest.fixture
def business(tmp_path):
    db = Database(tmp_path / "business.sqlite3")
    with client_for(db) as client:
        yield db, client


QUOTE = {"client": "Studio Aurora", "scope": "Progetto e consegna", "net_amount": "100.00", "vat_rate": "22"}


def add(client, service_id="quotes", *, details=None, **extra):
    payload = {"service_id": service_id, "title": "Proposta Studio Aurora", "details": details if details is not None else QUOTE, **extra}
    response = client.post("/api/business/records", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["item"]


def required_fields(service):
    values = {}
    for field in service["fields"]:
        if not field.get("required"):
            continue
        if field["type"] == "date":
            values[field["id"]] = "2026-10-05"
        elif field["type"] == "money":
            values[field["id"]] = "100.00"
        elif field["type"] == "number":
            values[field["id"]] = max(1, field.get("min", 0))
        elif field["type"] == "select":
            values[field["id"]] = field["options"][0]
        else:
            values[field["id"]] = "Dati inseriti nella scheda"
    return values


def test_empty_workspace_never_seeds_business_data(business):
    db, client = business
    assert client.get("/api/business/records").json()["items"] == []
    summary = business_summary(db, now=datetime(2026, 10, 4, 22, tzinfo=UTC))
    assert summary["date"] == "2026-10-05"
    assert summary["timezone"] == "Europe/Rome"
    assert summary["source"] == "Attività aggiunte da te"
    assert summary["next_actions"] == []
    assert summary["declared_saved_minutes"] == 0
    assert summary["totals"] == {"total": 0, "open": 0, "overdue": 0, "today": 0, "done": 0, "waiting": 0}
    catalog = client.get("/api/business/services").json()
    assert all(item["record_count"] == item["open_count"] == item["overdue_count"] == 0 for item in catalog["services"])


@pytest.mark.parametrize("service", [service for service in list_services() if service["kind"] == "business"], ids=lambda service: service["id"])
def test_each_available_module_has_functional_persistent_record_and_download(business, service):
    db, client = business
    item = add(client, service["id"], details=required_fields(service), title="Scheda compilata")
    assert item["service_name"] == service["name"]
    assert item["source"] == "manual"
    assert item["document"].startswith("Spazelia — " + service["output_title"])
    assert "Scheda compilata" in item["document"]
    assert item["next_action"] == get_playbook(service["id"])["steps"][0]["label"]
    assert client.get("/api/business/records/" + item["id"]).json()["item"] == item
    download = client.get("/api/business/records/" + item["id"] + "/export")
    assert download.status_code == 200
    assert download.text == item["document"]
    assert download.headers["content-type"] == "text/plain; charset=utf-8"
    assert download.headers["x-content-type-options"] == "nosniff"
    assert item["id"] in download.headers["content-disposition"]
    fresh = Database(db.path)
    with client_for(fresh) as reopened:
        assert reopened.get("/api/business/records/" + item["id"]).json()["item"] == item


@pytest.mark.parametrize("amount, rate, vat, gross", [
    ("99.99", "22", "22.00", "121.99"),
    ("0.10", "5", "0.01", "0.11"),
    ("0.00", "0", "0.00", "0.00"),
    ("1000000000.00", "22", "220000000.00", "1220000000.00"),
])
def test_quote_money_uses_decimal_half_up_and_user_selected_vat(business, amount, rate, vat, gross):
    _, client = business
    item = add(client, details={**QUOTE, "net_amount": amount, "vat_rate": rate})
    assert item["totals"] == {"currency": "EUR", "net_amount": amount, "vat_rate": rate, "vat_amount": vat, "gross_amount": gross}
    assert "Aliquota indicata dall’utente" in item["document"]
    assert "non viene trasmessa allo SDI" in item["document"]


def test_invoice_is_only_an_internal_draft_with_no_fiscal_number_or_submission(business):
    _, client = business
    item = add(client, "invoices", details={"client": "Cliente", "reference": "Riferimento inserito", "description": "Lavoro effettuato", "net_amount": "250.00", "vat_rate": "10"})
    assert item["totals"]["gross_amount"] == "275.00"
    assert "BOZZA INTERNA — non è una fattura fiscale" in item["document"]
    assert "Nessuna numerazione fiscale assegnata o trasmissione allo SDI" in item["document"]
    assert "invoice_number" not in item


@pytest.mark.parametrize("details", [
    {**QUOTE, "net_amount": "NaN"}, {**QUOTE, "net_amount": "Infinity"},
    {**QUOTE, "net_amount": "-0.01"}, {**QUOTE, "net_amount": "1000000000.01"},
    {**QUOTE, "net_amount": "1.001"}, {**QUOTE, "net_amount": "1e-9999999999999"},
    {**QUOTE, "net_amount": True}, {**QUOTE, "net_amount": {"value": 50}},
    {**QUOTE, "vat_rate": 22}, {**QUOTE, "vat_rate": "100"},
    {**QUOTE, "vat_rate": None}, {**QUOTE, "client": "   "},
    {**QUOTE, "scope": "x" * 2001}, {**QUOTE, "unknown_field": "unexpected"},
    {**QUOTE, "valid_until": "2026-02-30"}, {**QUOTE, "valid_until": 123},
])
def test_invalid_typed_details_never_persist(business, details):
    _, client = business
    response = client.post("/api/business/records", json={"service_id": "quotes", "title": "Scheda", "details": details})
    assert response.status_code == 422
    assert client.get("/api/business/records").json()["total"] == 0


@pytest.mark.parametrize("changes", [
    {"title": " "}, {"title": "x" * 161}, {"title": "Prima\nSeconda"},
    {"contact": "x" * 201}, {"notes": "x" * 2001}, {"notes": "bad\x00text"},
    {"due_date": "2026-02-30"}, {"due_date": "2026-10-05T09:00:00Z"},
    {"due_date": "1800-01-01"}, {"due_date": 20261005},
    {"status": "paid"}, {"priority": "critical"}, {"saved_minutes": -1},
    {"saved_minutes": 1.5}, {"saved_minutes": True}, {"saved_minutes": 100001},
    {"account_id": "another-account"}, {"source": "gmail"},
])
def test_invalid_common_fields_do_not_write_record(business, changes):
    _, client = business
    response = client.post("/api/business/records", json={"service_id": "quotes", "title": "Scheda", "details": QUOTE, **changes})
    assert response.status_code == 422
    assert client.get("/api/business/records").json()["items"] == []


@pytest.mark.parametrize("service_id", ["priority-email", "agenda", "sdi", "bank", "sync-calendar", "does-not-exist"])
def test_existing_and_future_connections_do_not_claim_business_record_support(business, service_id):
    _, client = business
    response = client.post("/api/business/records", json={"service_id": service_id, "title": "Test", "details": {}})
    assert response.status_code == 422


def test_patch_merges_details_and_clears_optional_fields_and_delete_is_durable(business):
    db, client = business
    item = add(client, contact="Referente", due_date="2026-10-05", notes="Note", details={**QUOTE, "valid_until": "2026-10-10"})
    response = client.patch("/api/business/records/" + item["id"], json={"status": "done", "contact": None, "due_date": None, "notes": None, "details": {"payment_terms": "30 giorni", "valid_until": None}, "saved_minutes": 15})
    assert response.status_code == 200, response.text
    updated = response.json()["item"]
    assert updated["contact"] == updated["notes"] == ""
    assert updated["due_date"] is None
    assert updated["details"] == {**QUOTE, "payment_terms": "30 giorni"}
    assert updated["created_at"] == item["created_at"]
    assert updated["updated_at"] >= updated["created_at"]
    assert business_summary(db)["declared_saved_minutes"] == 15
    assert client.delete("/api/business/records/" + item["id"]).json() == {"deleted": True, "id": item["id"]}
    assert client.get("/api/business/records/" + item["id"]).status_code == 404
    with client_for(Database(db.path)) as reopened:
        assert reopened.get("/api/business/records").json()["total"] == 0


@pytest.mark.parametrize("changes", [{}, {"title": None}, {"status": None}, {"details": None}, {"saved_minutes": None}, {"service_id": "clients"}, {"details": {"client": None}}, {"details": {"net_amount": "-1"}}])
def test_invalid_patch_leaves_original_unchanged(business, changes):
    _, client = business
    item = add(client)
    assert client.patch("/api/business/records/" + item["id"], json=changes).status_code == 422
    assert client.get("/api/business/records/" + item["id"]).json()["item"] == item


def test_summary_orders_overdue_today_future_then_undated_and_ignores_closed(business):
    db, client = business
    future = add(client, title="Domani urgente", due_date="2026-10-06", priority="urgent")
    overdue = add(client, title="Ieri", due_date="2026-10-04", priority="low")
    today = add(client, title="Oggi", due_date="2026-10-05", priority="high")
    undated = add(client, title="Senza data", priority="urgent")
    today_urgent = add(client, title="Oggi prima", due_date="2026-10-05", priority="urgent", status="waiting")
    add(client, title="Conclusa", due_date="2026-10-01", status="done", saved_minutes=7)
    add(client, title="Annullata", due_date="2026-10-01", status="cancelled", saved_minutes=100)
    add(client, title="Da fare e minuti non ancora validi", status="todo", saved_minutes=50, priority="low")
    summary = business_summary(db, now=datetime(2026, 10, 4, 22, tzinfo=UTC))
    assert [item["id"] for item in summary["next_actions"][:5]] == [overdue["id"], today_urgent["id"], today["id"], future["id"], undated["id"]]
    assert [item["urgency"] for item in summary["next_actions"][:5]] == ["overdue", "today", "today", "attention", "attention"]
    assert summary["totals"] == {"total": 8, "open": 6, "overdue": 1, "today": 2, "done": 1, "waiting": 1}
    assert summary["declared_saved_minutes"] == 7
    assert "non misurati automaticamente" in summary["savings_source"]
    service = next(service for service in business_services(db, now=datetime(2026, 10, 4, 22, tzinfo=UTC))["services"] if service["id"] == "quotes")
    assert service["record_count"] == 8
    assert service["open_count"] == 6
    assert service["overdue_count"] == 1


@pytest.mark.parametrize("utc_time, today", [
    (datetime(2026, 3, 28, 23, tzinfo=UTC), "2026-03-29"),
    (datetime(2026, 3, 29, 22, tzinfo=UTC), "2026-03-30"),
    (datetime(2026, 10, 24, 22, tzinfo=UTC), "2026-10-25"),
    (datetime(2026, 10, 25, 23, tzinfo=UTC), "2026-10-26"),
])
def test_summary_respects_local_midnight_on_daylight_saving_boundaries(business, utc_time, today):
    db, client = business
    item = add(client, due_date=today)
    summary = business_summary(db, now=utc_time)
    assert summary["date"] == today
    assert summary["next_actions"][0]["id"] == item["id"]
    assert summary["next_actions"][0]["urgency"] == "today"


def test_summary_rejects_ambiguous_naive_time(business):
    db, _ = business
    with pytest.raises(ValueError, match="fuso orario"):
        business_summary(db, now=datetime(2026, 10, 5, 9))


def test_filter_pagination_and_priority_summary_limit(business):
    db, client = business
    for index in range(15):
        add(client, title=f"Proposta {index}", status="waiting" if index % 2 else "todo")
    add(client, "clients", details={"company": "Studio", "request": "Richiesta"})
    page = client.get("/api/business/records?service_id=quotes&status=waiting&limit=3&offset=2").json()
    assert page["total"] == 7
    assert len(page["items"]) == 3
    assert page["limit"] == 3 and page["offset"] == 2
    assert all(item["service_id"] == "quotes" and item["status"] == "waiting" for item in page["items"])
    add(client, status="done")
    open_page = client.get("/api/business/records?service_id=quotes&status=open&limit=3&offset=2").json()
    assert open_page["total"] == 15
    assert len(open_page["items"]) == 3
    assert all(item["status"] in ("todo", "in_progress", "waiting") for item in open_page["items"])
    assert len(business_summary(db)["next_actions"]) == 12
    assert client.get("/api/business/records?limit=251").status_code == 422
    assert client.get("/api/business/records?service_id=unknown").status_code == 422
    assert client.get("/api/business/records?status=paid").status_code == 422


def test_two_workspace_databases_cannot_read_update_delete_or_export_each_others_id(tmp_path):
    first = Database(tmp_path / "first.sqlite3")
    second = Database(tmp_path / "second.sqlite3")
    with client_for(first) as one, client_for(second) as two:
        item = add(one)
        assert two.get("/api/business/records").json()["total"] == 0
        assert two.get("/api/business/records/" + item["id"]).status_code == 404
        assert two.get("/api/business/records/" + item["id"] + "/export").status_code == 404
        assert two.patch("/api/business/records/" + item["id"], json={"title": "Inaccessibile"}).status_code == 404
        assert two.delete("/api/business/records/" + item["id"]).status_code == 404
        assert one.get("/api/business/records/" + item["id"]).json()["item"] == item


def test_mail_credentials_and_existing_drafts_are_not_modified_by_business_work(business):
    db, client = business
    connection = {"status": "connected", "provider": "gmail", "mailbox": "test@example.test"}
    db.set_setting("connection", connection)
    db.set_setting("google_credentials", {"encrypted": "saved-test-only"})
    settings = db.get_setting("service")
    item = add(client)
    client.patch("/api/business/records/" + item["id"], json={"status": "done"})
    client.delete("/api/business/records/" + item["id"])
    assert db.get_setting("connection") == connection
    assert db.get_setting("google_credentials") == {"encrypted": "saved-test-only"}
    assert db.get_setting("service") == settings
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_documents_are_plain_text_and_preserve_user_input_without_execution(business):
    _, client = business
    malicious = '<script>alert("x")</script>'
    item = add(client, title=malicious, details={**QUOTE, "client": malicious}, notes="Nota\nSeconda riga")
    response = client.get("/api/business/records/" + item["id"] + "/export")
    assert response.headers["content-type"].startswith("text/plain")
    assert malicious in response.text
    assert item["id"] in response.headers["content-disposition"]
    assert malicious not in response.headers["content-disposition"]
    assert client.get("/api/business/records").headers["content-type"].startswith("application/json")


def test_receivable_generates_only_a_copyable_message_with_real_entered_reference(business):
    _, client = business
    item = add(client, "receivables", details={"client": "Cliente Aurora", "reference": "AC-2026-12", "amount": "1234.50"})
    assert "Buongiorno, Cliente Aurora," in item["document"]
    assert "AC-2026-12" in item["document"]
    assert "1.234,50 €" in item["document"]
    assert "il promemoria non viene inviato" in item["document"]


def test_leave_dates_validate_order_and_preserve_existing_record_on_invalid_patch(business):
    _, client = business
    details = {"person": "Referente", "starts_on": "2026-10-06", "ends_on": "2026-10-05"}
    assert client.post("/api/business/records", json={"service_id": "leave", "title": "Assenza", "details": details}).status_code == 422
    item = add(client, "leave", details={**details, "ends_on": "2026-10-07"})
    assert client.patch("/api/business/records/" + item["id"], json={"details": {"ends_on": "2026-10-05"}}).status_code == 422
    assert client.get("/api/business/records/" + item["id"]).json()["item"] == item


def test_time_measurement_inputs_do_not_imply_automatic_savings_or_personnel_scores(business):
    db, client = business
    details = {"activity": "Preparazione preventivo", "period": "Settimana", "minutes_before": 15, "minutes_after": 20, "session_count": 5}
    item = add(client, "time-measurement", details=details, status="done")
    assert business_summary(db)["declared_saved_minutes"] == 0
    assert item["totals"] is None
    assert item["measurement"]["difference_per_session"] == "-5"
    assert item["measurement"]["total_difference_minutes"] == "-25"
    assert "non misurato automaticamente" in item["measurement"]["source"]
    assert "Preparazione preventivo" in item["document"]
    assert "employee_score" not in item
    bad = client.patch("/api/business/records/" + item["id"], json={"details": {"session_count": 1.5}})
    assert bad.status_code == 422


def test_inventory_below_user_selected_minimum_generates_reorder_attention_without_stock_changes(business):
    db, client = business
    details = {"sku": "CARTA-A4", "item": "Carta per stampante", "quantity": 3, "reorder_level": 5, "unit": "risme"}
    item = add(client, "inventory", details=details, title="Scorta carta")
    assert item["attention"] is True
    assert item["attention_reason"] == "stock_below_minimum"
    assert "Carta per stampante (CARTA-A4)" in item["next_action"]
    assert "quantità inserita 3 risme, sotto la soglia di 5 risme" in item["next_action"]
    assert item["next_action"] in item["document"]
    summary = business_summary(db, now=datetime(2026, 10, 5, 8, tzinfo=UTC))
    assert summary["next_actions"][0]["urgency"] == "attention"
    assert summary["attention_count"] == 1
    assert client.get("/api/business/records/" + item["id"]).json()["item"]["details"] == details
    updated = client.patch("/api/business/records/" + item["id"], json={"details": {"quantity": 5}}).json()["item"]
    assert updated["attention"] is False
    assert updated["attention_reason"] is None
    assert updated["next_action"] == get_playbook("inventory")["steps"][0]["label"]
    assert business_summary(db)["attention_count"] == 0


@pytest.mark.parametrize("details", [
    {"quantity": 5, "reorder_level": 5}, {"quantity": 6, "reorder_level": 5},
    {"quantity": 0, "reorder_level": 0}, {"quantity": 0},
])
def test_inventory_at_or_above_threshold_or_without_threshold_never_has_false_stock_alert(business, details):
    _, client = business
    item = add(client, "inventory", details={"sku": "A-1", "item": "Articolo", **details})
    assert item["attention"] is False
    assert item["attention_reason"] is None
    assert item["next_action"] == get_playbook("inventory")["steps"][0]["label"]


def test_explicit_high_and_urgent_undated_work_outranks_ordinary_future_after_today_and_overdue(business):
    db, client = business
    future = add(client, due_date="2026-10-06", priority="low", title="Scadenza domani ordinaria")
    high = add(client, priority="high", title="Importante senza data")
    urgent = add(client, priority="urgent", title="Urgente senza data")
    today = add(client, priority="low", due_date="2026-10-05", title="Oggi")
    overdue = add(client, priority="low", due_date="2026-10-04", title="Già scaduta")
    add(client, priority="urgent", status="done", title="Urgente ma completata")
    summary = business_summary(db, now=datetime(2026, 10, 5, 8, tzinfo=UTC))
    assert [item["id"] for item in summary["next_actions"]] == [overdue["id"], today["id"], urgent["id"], high["id"], future["id"]]
    assert [item["urgency"] for item in summary["next_actions"]] == ["overdue", "today", "attention", "attention", "upcoming"]
    assert summary["next_actions"][2]["attention_reason"] == "urgent_priority"
    assert summary["next_actions"][3]["attention_reason"] == "high_priority"


def test_closed_inventory_record_does_not_propose_reorder_or_enter_open_priorities(business):
    db, client = business
    item = add(client, "inventory", details={"sku": "A-1", "item": "Articolo", "quantity": 0, "reorder_level": 10}, status="done")
    assert item["attention"] is False
    assert item["attention_reason"] is None
    assert business_summary(db)["next_actions"] == []


def test_read_summary_uses_snapshot_when_record_is_deleted_concurrently(business, monkeypatch):
    db, client = business
    item = add(client)
    original_connection = db.connection
    deleted = False

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor

        def fetchall(self):
            nonlocal deleted
            rows = self.cursor.fetchall()
            if not deleted:
                deleted = True
                with original_connection() as concurrent:
                    concurrent.execute("DELETE FROM business_records WHERE id=?", (item["id"],))
            return rows

    class Connection:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, statement, *parameters):
            result = self.connection.execute(statement, *parameters)
            return Cursor(result) if statement.startswith("SELECT id, status,") else result

    @contextmanager
    def hooked_connection():
        with original_connection() as connection:
            yield Connection(connection)

    monkeypatch.setattr(db, "connection", hooked_connection)
    summary = business_summary(db)
    assert summary["totals"]["total"] == 1
    assert summary["next_actions"][0]["id"] == item["id"]
    assert business_summary(db)["totals"]["total"] == 0


def test_concurrent_partial_detail_updates_preserve_independent_changes(business):
    db, client = business
    item = add(client)
    with ThreadPoolExecutor(max_workers=2) as executor:
        changes = [{"payment_terms": "Pagamento concordato"}, {"valid_until": "2026-11-01"}]
        responses = list(executor.map(lambda detail: client.patch("/api/business/records/" + item["id"], json={"details": detail}), changes))
    assert all(response.status_code == 200 for response in responses)
    saved = client.get("/api/business/records/" + item["id"]).json()["item"]
    assert saved["details"] == {**QUOTE, "payment_terms": "Pagamento concordato", "valid_until": "2026-11-01"}


@pytest.mark.parametrize("method,suffix", [("get", ""), ("get", "/export"), ("patch", ""), ("delete", "")])
def test_unknown_record_ids_have_no_cross_workspace_information(business, method, suffix):
    _, client = business
    request = getattr(client, method)
    arguments = {"json": {"status": "done"}} if method == "patch" else {}
    assert request("/api/business/records/" + "a" * 32 + suffix, **arguments).status_code == 404
    assert request("/api/business/records/bad-id" + suffix, **arguments).status_code == 422
