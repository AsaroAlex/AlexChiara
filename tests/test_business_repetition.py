"""Reviewed repeat proposals, reset progress and private idempotent successors."""
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.business import create_business_router
from app.business_catalog import get_service
from app.business_playbooks import get_playbook, list_playbooks
from app.db import Database
from app.main import create_app


TODAY = "2026-10-05"
NEXT_DATE = "2026-11-10"


def client_for(db):
    app = FastAPI()
    app.include_router(create_business_router(db))
    return TestClient(app)


@pytest.fixture(autouse=True)
def fixed_italian_day(monkeypatch):
    monkeypatch.setattr("app.business._today", lambda now=None: TODAY)


@pytest.fixture
def business(tmp_path):
    db = Database(tmp_path / "repeats.sqlite3")
    with client_for(db) as client:
        yield db, client


def details_for(service_id):
    values = {}
    for field in get_service(service_id)["fields"]:
        if field["type"] == "date":
            values[field["id"]] = "2026-10-01"
        elif field.get("required"):
            if field["type"] == "money":
                values[field["id"]] = "123.45"
            elif field["type"] == "number":
                values[field["id"]] = max(1, field.get("min", 0))
            elif field["type"] == "select":
                values[field["id"]] = field["options"][0]
            else:
                values[field["id"]] = "Dato originale"
    return values


def add(client, service_id="quotes", **changes):
    payload = {"service_id": service_id, "title": "Attività originale", "contact": "Ada", "notes": "Note annotate",
               "details": details_for(service_id), "status": "done", "saved_minutes": 18,
               "due_date": "2026-10-01", "priority": "high",
               "steps": {step["id"]: True for step in get_playbook(service_id)["steps"]}, **changes}
    response = client.post("/api/business/records", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["item"]


def route(item):
    return f'/api/business/records/{item["id"]}'


@pytest.mark.parametrize("service_id", list(list_playbooks()))
def test_every_module_has_read_only_repeat_proposal_and_explicit_successor_without_progress(business, service_id):
    db, client = business
    source = add(client, service_id)
    response = client.get(route(source) + "/repetition", params={"next_date": NEXT_DATE})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["existing"] is None
    assert data["source"] == {"id": source["id"], "title": source["title"], "service_id": service_id}
    assert client.get("/api/business/records").json()["total"] == 1
    assert client.get(route(source)).json()["item"] == source
    proposal = data["proposal"]
    assert proposal["service_id"] == service_id and proposal["status"] == "todo"
    assert proposal["saved_minutes"] == 0 and proposal["steps"] == {}
    assert proposal["due_date"] == NEXT_DATE
    due_fields = get_playbook(service_id)["due_fields"]
    for field in get_service(service_id)["fields"]:
        if field["type"] == "date":
            if due_fields and field["id"] == due_fields[0]:
                assert proposal["details"][field["id"]] == NEXT_DATE
            else:
                assert field["id"] not in proposal["details"]
    # Required historical/period dates must be reviewed again, never copied.
    edits = {field["id"]: NEXT_DATE for field in get_service(service_id)["fields"]
             if field["required"] and field["type"] == "date" and field["id"] not in proposal["details"]}
    result = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE, "details": edits})
    assert result.status_code == 200, result.text
    item = result.json()["item"]
    assert result.json()["created"] is True and item["id"] != source["id"]
    assert item["source"] == "repeat" and item["service_id"] == service_id
    assert item["status"] == "todo" and item["saved_minutes"] == 0 and item["steps"] == {}
    assert item["playbook"]["completed"] == 0
    assert item["due_date"] == item["effective_due_date"] == NEXT_DATE
    assert item["links"]["source"] == {"id": source["id"], "title": source["title"], "service_id": service_id, "kind": "repeat"}
    original = client.get(route(source)).json()["item"]
    for key in ("status", "saved_minutes", "steps", "details", "due_date", "updated_at"):
        assert original[key] == source[key]
    assert original["links"]["targets"] == [{"id": item["id"], "title": item["title"], "service_id": service_id, "kind": "repeat"}]
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 0


def test_review_edits_clear_optional_values_and_do_not_change_original(business):
    _, client = business
    source = add(client, details={**details_for("quotes"), "payment_terms": "30 giorni"})
    response = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE, "title": "Proposta successiva",
        "contact": None, "notes": None, "priority": "normal", "details": {"net_amount": "0.10", "vat_rate": "5", "payment_terms": ""}})
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["title"] == "Proposta successiva" and item["contact"] == item["notes"] == ""
    assert item["totals"]["gross_amount"] == "0.11" and item["priority"] == "normal"
    assert "payment_terms" not in item["details"]
    original = client.get(route(source)).json()["item"]
    assert original["details"] == source["details"]


@pytest.mark.parametrize("changes", [{"status": "done"}, {"saved_minutes": 18}, {"steps": {"scope": True}},
    {"service_id": "invoices"}, {"due_date": "2026-12-10"}, {"source": "automatic"}, {"user_id": "owner"},
    {"title": None}, {"details": None}, {"priority": None}, {"title": "bad\x00value"},
    {"details": {"net_amount": "1.001"}}, {"details": {"unrecognized": "value"}}])
def test_repeat_rejects_authority_progress_and_invalid_review_fields_without_partial_writes(business, changes):
    db, client = business
    source = add(client)
    response = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE, **changes})
    assert response.status_code == 422, response.text
    assert client.get("/api/business/records").json()["total"] == 1
    assert client.get(route(source)).json()["item"] == source
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_repeats").fetchone()[0] == 0


@pytest.mark.parametrize("next_date", ["2026-10-05", "2026-10-04", "2026-02-30", "", "2026-11-10T12:00:00Z", None, 20261110])
def test_repeat_date_must_be_a_future_calendar_date_and_bad_proposals_do_not_write(business, next_date):
    _, client = business
    source = add(client)
    assert client.post(route(source) + "/repeat", json={"next_date": next_date}).status_code == 422
    assert client.get(route(source) + "/repetition", params={"next_date": next_date}).status_code == 422
    assert client.get("/api/business/records").json()["total"] == 1


def test_required_end_of_leave_and_expense_date_require_a_new_review(business):
    _, client = business
    leave = add(client, "leave")
    response = client.post(route(leave) + "/repeat", json={"next_date": NEXT_DATE})
    assert response.status_code == 422 and "Fine del periodo" in response.text
    response = client.post(route(leave) + "/repeat", json={"next_date": NEXT_DATE, "details": {"ends_on": "2026-11-09"}})
    assert response.status_code == 422
    expense = add(client, "expense-claims")
    assert client.post(route(expense) + "/repeat", json={"next_date": NEXT_DATE}).status_code == 422
    assert client.get("/api/business/records").json()["total"] == 2


def test_duplicate_clicks_return_original_successor_without_overwriting_reviewed_edits(business):
    _, client = business
    source = add(client)
    first = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE}).json()["item"]
    revised = client.patch(route(first), json={"title": "Già rivista", "steps": {"scope": True}, "details": {"net_amount": "200.00"}}).json()["item"]
    client.patch(route(source), json={"title": "Originale cambiato", "details": {"net_amount": "900.00"}})
    # The reference title follows the source; the successor's editable data does not.
    revised = client.get(route(first)).json()["item"]
    preview = client.get(route(source) + "/repetition", params={"next_date": NEXT_DATE}).json()
    assert preview["existing"] == revised
    response = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE, "title": "Non sovrascrivere"}).json()
    assert response["created"] is False and response["item"] == revised
    assert client.get("/api/business/records").json()["total"] == 2
    later = client.post(route(source) + "/repeat", json={"next_date": "2026-12-10"}).json()
    assert later["created"] is True and later["item"]["id"] != first["id"]


def test_parallel_repeat_requests_create_exactly_one_successor(business):
    db, client = business
    source = add(client)

    def repeat():
        with client_for(db) as independent:
            response = independent.post(route(source) + "/repeat", json={"next_date": NEXT_DATE})
            assert response.status_code == 200, response.text
            return response.json()

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _: repeat(), range(6)))
    assert sum(result["created"] for result in results) == 1
    assert len({result["item"]["id"] for result in results}) == 1
    assert client.get("/api/business/records").json()["total"] == 2


def test_deleting_source_removes_only_link_and_deleting_successor_allows_reviewed_replacement(business):
    db, client = business
    source = add(client)
    first = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE}).json()["item"]
    assert client.delete(route(first)).status_code == 200
    assert client.get(route(source)).json()["item"]["links"]["targets"] == []
    replacement = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE}).json()["item"]
    assert replacement["id"] != first["id"]
    assert client.delete(route(source)).status_code == 200
    survivor = client.get(route(replacement)).json()["item"]
    assert survivor["title"] == replacement["title"] and survivor["links"]["source"] is None
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM business_repeats").fetchone()[0] == 0


def test_record_capacity_failure_leaves_no_successor_or_relationship(business, monkeypatch):
    _, client = business
    source = add(client)
    monkeypatch.setattr("app.business.MAX_RECORDS", 1)
    response = client.post(route(source) + "/repeat", json={"next_date": NEXT_DATE})
    assert response.status_code == 409
    assert client.get(route(source)).json()["item"] == source
    assert client.get("/api/business/records").json()["total"] == 1


def test_conversion_also_resets_manual_steps_and_keeps_existing_conversion_link_shape(business):
    _, client = business
    source = add(client)
    response = client.post(route(source) + "/convert", json={"target_service_id": "invoices"})
    assert response.status_code == 200, response.text
    invoice = response.json()["item"]
    assert invoice["steps"] == {} and invoice["playbook"]["completed"] == 0
    assert invoice["links"]["source"] == {"id": source["id"], "title": source["title"], "service_id": "quotes"}


def test_repeat_account_boundary_authentication_csrf_origin_and_finance_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_ACCESS_PASSWORD", "Owner-test-password-42")
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "0")
    app = create_app(data_dir=tmp_path, start_worker=False)
    with TestClient(app) as alice, TestClient(app) as bob:
        foreign_route = "/api/business/records/" + "a" * 32
        assert alice.get(foreign_route + "/repetition?next_date=" + NEXT_DATE).status_code == 401
        assert alice.post(foreign_route + "/repeat", json={"next_date": NEXT_DATE}).status_code == 401
        accounts = []
        for client, email in ((alice, "repeat-alice@example.test"), (bob, "repeat-bob@example.test")):
            csrf = client.get("/api/auth/session").json()["csrf_token"]
            response = client.post("/api/auth/register", json={"name": "Studio", "email": email, "password": "Private-Account-42!"}, headers={"X-CSRF-Token": csrf})
            assert response.status_code == 200, response.text
            accounts.append(response.json())
        source_response = alice.post("/api/business/records", json={"service_id": "receivables", "title": "Incasso privato Alice", "details": details_for("receivables")}, headers={"X-CSRF-Token": accounts[0]["csrf_token"]})
        assert source_response.status_code == 201, source_response.text
        source = source_response.json()["item"]
        assert alice.post(route(source) + "/repeat", json={"next_date": NEXT_DATE}).status_code == 403
        assert alice.post(route(source) + "/repeat", json={"next_date": NEXT_DATE}, headers={"X-CSRF-Token": accounts[0]["csrf_token"], "Origin": "https://foreign.example"}).status_code == 403
        assert bob.get(route(source) + "/repetition?next_date=" + NEXT_DATE).status_code == 404
        assert bob.post(route(source) + "/repeat", json={"next_date": NEXT_DATE}, headers={"X-CSRF-Token": accounts[1]["csrf_token"]}).status_code == 404
        result = alice.post(route(source) + "/repeat", json={"next_date": NEXT_DATE}, headers={"X-CSRF-Token": accounts[0]["csrf_token"]})
        assert result.status_code == 200, result.text
        assert bob.get(route(result.json()["item"])).status_code == 404
        assert bob.get("/api/business/summary").json()["financial_summary"]["receivables"]["open_amount"] == "0.00"
        assert alice.get("/api/business/summary").json()["financial_summary"]["receivables"]["open_amount"] == "246.90"
