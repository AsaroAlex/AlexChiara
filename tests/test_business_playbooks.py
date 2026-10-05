"""Manual service workflows, entered-date priorities and recorded cash amounts."""
from datetime import datetime, timezone
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.business import business_services, business_summary, create_business_router
from app.business_catalog import get_service, list_services
from app.business_playbooks import get_playbook, list_playbooks
from app.db import Database


def client_for(db):
    app = FastAPI()
    app.include_router(create_business_router(db))
    return TestClient(app)


@pytest.fixture
def business(tmp_path):
    db = Database(tmp_path / "playbooks.sqlite3")
    with client_for(db) as client:
        yield db, client


def required_details(service_id):
    result = {}
    for field in get_service(service_id)["fields"]:
        if not field.get("required"):
            continue
        kind = field["type"]
        if kind == "date":
            result[field["id"]] = "2026-10-05"
        elif kind == "select":
            result[field["id"]] = field["options"][0]
        elif kind == "money":
            result[field["id"]] = "0.10"
        elif kind == "number":
            result[field["id"]] = max(1, field.get("min", 0))
        else:
            result[field["id"]] = "Dato inserito dall’utente"
    return result


def add(client, service_id="quotes", **changes):
    payload = {"service_id": service_id, "title": "Attività registrata", "details": required_details(service_id), **changes}
    response = client.post("/api/business/records", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["item"]


@pytest.mark.parametrize("service_id", list(list_playbooks()))
def test_every_module_advances_only_the_steps_explicitly_checked_and_exports_them(business, service_id):
    db, client = business
    item = add(client, service_id)
    definition = get_playbook(service_id)
    assert item["steps"] == {}
    assert item["playbook"]["completed"] == 0
    assert item["playbook"]["total"] == len(definition["steps"])
    assert not any(step["checked"] for step in item["playbook"]["steps"])
    assert item["next_action"] == definition["steps"][0]["label"]
    first, second = definition["steps"][:2]
    route = f'/api/business/records/{item["id"]}'
    updated = client.patch(route, json={"steps": {first["id"]: True}}).json()["item"]
    assert updated["steps"] == {first["id"]: True}
    assert updated["playbook"]["next_step"] == second
    assert updated["next_action"] == second["label"]
    assert updated["status"] == "todo"
    later = client.patch(route, json={"steps": {second["id"]: True}, "status": "done"}).json()["item"]
    assert later["playbook"]["completed"] == 2
    assert later["steps"] == {first["id"]: True, second["id"]: True}
    assert not later["playbook"]["steps"][2]["checked"]
    restored = client.patch(route, json={"steps": {first["id"]: False}, "status": "todo"}).json()["item"]
    assert restored["playbook"]["completed"] == 1
    assert restored["next_action"] == first["label"]
    text = client.get(route + "/export").text
    assert "stato segnato manualmente da te" in text
    assert "[ ] " + first["label"] in text
    assert "[x] " + second["label"] in text
    with client_for(Database(db.path)) as reopened:
        assert reopened.get(route).json()["item"] == restored


@pytest.mark.parametrize("steps", [{"scope": 1}, {"scope": "true"}, {"scope": None}, {"unknown": True},
                                    {"scope": []}, {str(i): True for i in range(7)}])
def test_invalid_or_other_service_steps_are_rejected_without_writes(business, steps):
    _, client = business
    response = client.post("/api/business/records", json={"service_id": "quotes", "title": "Test", "details": required_details("quotes"), "steps": steps})
    assert response.status_code == 422
    item = add(client)
    before = client.get(f'/api/business/records/{item["id"]}').json()["item"]
    response = client.patch(f'/api/business/records/{item["id"]}', json={"steps": steps, "title": "Non salvare"})
    assert response.status_code == 422
    assert client.get(f'/api/business/records/{item["id"]}').json()["item"] == before


def test_done_status_never_claims_the_steps_were_checked(business):
    _, client = business
    item = add(client, status="done")
    assert item["playbook"]["completed"] == 0
    assert not any(step["checked"] for step in item["playbook"]["steps"])
    updated = client.patch(f'/api/business/records/{item["id"]}', json={"status": "todo"}).json()["item"]
    assert updated["next_action"] == get_playbook("quotes")["steps"][0]["label"]


def test_all_checked_steps_do_not_auto_complete_and_closed_records_offer_no_operational_action(business):
    _, client = business
    item = add(client, steps={step["id"]: True for step in get_playbook("quotes")["steps"]})
    assert item["status"] == "todo" and item["playbook"]["next_step"] is None
    assert item["next_action"] == "Hai segnato tutti i passaggi. Se hai finito, completa l’attività."
    route = f'/api/business/records/{item["id"]}'
    done = client.patch(route, json={"status": "done"}).json()["item"]
    assert done["next_action"] == "Attività completata. Puoi consultare il documento o ripartire con una nuova attività."
    cancelled = client.patch(route, json={"status": "cancelled"}).json()["item"]
    assert cancelled["next_action"] == "Attività annullata. Puoi consultarla o riaprirla."
    stock = add(client, "inventory", status="done", details={**required_details("inventory"), "quantity": 1, "reorder_level": 5})
    assert stock["attention"] is False and "riordino" not in stock["next_action"]


def test_old_database_migrates_without_checking_or_changing_any_record(tmp_path):
    db = Database(tmp_path / "legacy.sqlite3")
    details = json.dumps(required_details("quotes"))
    with db.connection() as conn:
        conn.execute("""CREATE TABLE business_records (id TEXT PRIMARY KEY,service_id TEXT NOT NULL,title TEXT NOT NULL,
            contact TEXT NOT NULL DEFAULT '',due_date TEXT,status TEXT NOT NULL DEFAULT 'todo',priority TEXT NOT NULL DEFAULT 'normal',
            notes TEXT NOT NULL DEFAULT '',details TEXT NOT NULL DEFAULT '{}',saved_minutes INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'manual',created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
        conn.execute("INSERT INTO business_records(id,service_id,title,details,status,saved_minutes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     ("a" * 32, "quotes", "Preventivo precedente", details, "done", 12, "2026-10-01", "2026-10-01"))
    with client_for(db) as client:
        item = client.get("/api/business/records/" + "a" * 32).json()["item"]
        assert item["title"] == "Preventivo precedente" and item["status"] == "done"
        assert item["steps"] == {} and item["playbook"]["completed"] == 0
        assert item["saved_minutes"] == 12 and item["updated_at"] == "2026-10-01"
    with client_for(db) as client:
        assert client.get("/api/business/records").json()["total"] == 1
    with db.connection() as conn:
        assert conn.execute("SELECT steps FROM business_records").fetchone()[0] == "{}"
        assert conn.execute("SELECT COUNT(*) FROM business_repeats").fetchone()[0] == 0


@pytest.mark.parametrize("service_id,field_id", [(sid, field_id) for sid, playbook in list_playbooks().items() for field_id in playbook["due_fields"]])
def test_service_date_prioritizes_without_copying_it_into_common_due_date_and_override_wins(business, service_id, field_id):
    db, client = business
    details = {**required_details(service_id), field_id: "2026-10-04"}
    item = add(client, service_id, details=details)
    assert item["due_date"] is None and item["effective_due_date"] == "2026-10-04"
    label = next(field["label"] for field in get_service(service_id)["fields"] if field["id"] == field_id)
    assert item["due_source"] == {"type": "field", "label": label}
    now = datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
    summary = business_summary(db, now)
    assert summary["totals"]["overdue"] == 1 and summary["next_actions"][0]["urgency"] == "overdue"
    service = next(service for service in business_services(db, now)["services"] if service["id"] == service_id)
    assert service["overdue_count"] == 1
    override = client.patch(f'/api/business/records/{item["id"]}', json={"due_date": "2026-10-10"}).json()["item"]
    assert override["effective_due_date"] == "2026-10-10" and override["due_source"]["type"] == "record"
    assert override["details"][field_id] == "2026-10-04"
    assert business_summary(db, now)["totals"]["overdue"] == 0
    cleared = client.patch(f'/api/business/records/{item["id"]}', json={"due_date": None}).json()["item"]
    assert cleared["effective_due_date"] == "2026-10-04"


def test_historical_expense_date_is_not_a_deadline_and_rome_day_applies(business):
    db, client = business
    historical = add(client, "expense-claims", details={**required_details("expense-claims"), "expense_date": "2026-01-01"})
    today = add(client, "receivables", details={**required_details("receivables"), "expected_date": "2026-10-05"})
    summary = business_summary(db, datetime(2026, 10, 4, 22, tzinfo=timezone.utc))
    assert historical["effective_due_date"] is None and historical["due_source"] is None
    assert summary["totals"]["today"] == 1 and summary["totals"]["overdue"] == 0
    assert summary["next_actions"][0]["id"] == today["id"]


def test_financial_overview_sums_only_open_recorded_cash_amounts_with_decimal_and_effective_dates(business):
    db, client = business
    add(client, "receivables", details={**required_details("receivables"), "amount": "0.10", "expected_date": "2026-10-04"})
    add(client, "receivables", details={**required_details("receivables"), "amount": "0.20", "expected_date": "2026-10-01"}, due_date="2026-10-05")
    add(client, "receivables", details={**required_details("receivables"), "amount": "10.00"}, status="waiting")
    add(client, "receivables", details={**required_details("receivables"), "amount": "90.00"}, status="done")
    add(client, "receivables", details={**required_details("receivables"), "amount": "80.00"}, status="cancelled")
    add(client, "expenses", details={**required_details("expenses"), "amount": "123.45", "payment_date": "2026-10-05"})
    add(client, "expenses", details={**required_details("expenses"), "amount": "1.00", "payment_date": "2026-10-04"})
    add(client, "expenses", details={**required_details("expenses"), "amount": "99.00"}, status="done")
    add(client, "quotes", details={**required_details("quotes"), "net_amount": "999.00"})
    add(client, "invoices", details={**required_details("invoices"), "net_amount": "999.00"})
    add(client, "expense-claims", details={**required_details("expense-claims"), "amount": "888.00"})
    summary = business_summary(db, datetime(2026, 10, 5, 10, tzinfo=timezone.utc))["financial_summary"]
    assert summary["currency"] == "EUR"
    assert summary["receivables"] == {"open_amount": "10.30", "overdue_amount": "0.10", "today_amount": "0.20", "count": 3}
    assert summary["payables"] == {"open_amount": "124.45", "overdue_amount": "1.00", "today_amount": "123.45", "count": 2}
    assert "inseriti dall’utente" in summary["source"] and "non collegati alla banca" in summary["source"]


def test_summary_projects_only_financial_metadata_and_does_not_render_all_documents(business, monkeypatch):
    db, client = business
    for number in range(15):
        add(client, "receivables", title=f"Incasso {number}", details={**required_details("receivables"), "amount": "0.01"})
    import app.business as module
    original_item = module._item
    calls = []

    def tracked_item(row, **kwargs):
        calls.append(row["id"])
        return original_item(row, **kwargs)

    monkeypatch.setattr(module, "_item", tracked_item)
    summary = business_summary(db)
    assert len(calls) == len(summary["next_actions"]) == 12
    assert summary["financial_summary"]["receivables"]["open_amount"] == "0.15"


@pytest.mark.parametrize("quantity,minimum,suggested", [(3, 5, "2"), (5, 5, "0"), (8, 5, "0"), (0, 0, "0")])
def test_stock_reorder_is_a_nonnegative_read_only_calculation(business, quantity, minimum, suggested):
    _, client = business
    item = add(client, "inventory", details={**required_details("inventory"), "quantity": quantity, "reorder_level": minimum})
    assert item["stock_reorder"] == {"quantity": str(quantity), "minimum": str(minimum), "suggested_quantity": suggested}
    assert item["details"]["quantity"] == quantity
    assert "nessun movimento di magazzino o acquisto eseguito" in item["document"]
    if quantity < minimum:
        assert item["attention_reason"] == "stock_below_minimum"
        step = get_playbook("inventory")["steps"][0]["id"]
        updated = client.patch(f'/api/business/records/{item["id"]}', json={"steps": {step: True}}).json()["item"]
        assert updated["next_action"] == item["next_action"]
        assert updated["details"]["quantity"] == quantity


def test_playbook_metadata_copies_cannot_change_the_contract():
    first = get_playbook("quotes")
    first["steps"][0]["id"] = "corrupt"
    first["due_fields"].clear()
    assert get_playbook("quotes")["steps"][0]["id"] == "scope"
    assert get_playbook("quotes")["due_fields"] == ["valid_until"]
    assert len(list_playbooks()) == sum(service["kind"] == "business" for service in list_services())
