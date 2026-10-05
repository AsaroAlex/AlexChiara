"""Literal, paginated search over privately entered business data."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.business import create_business_router
from app.db import Database


def client_for(db):
    app = FastAPI()
    app.include_router(create_business_router(db))
    return TestClient(app)


@pytest.fixture
def business(tmp_path):
    db = Database(tmp_path / "search.sqlite3")
    with client_for(db) as client:
        yield db, client


def add(client, **changes):
    payload = {"service_id": "clients", "title": "Cliente registrato", "contact": "Ada",
               "notes": "Nota interna", "details": {"company": "Studio Aurora", "request": "Consulenza"}}
    payload.update(changes)
    response = client.post("/api/business/records", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["item"]


@pytest.mark.parametrize("changes,query", [
    ({"title": "Offerta progettazione"}, "progettazione"),
    ({"contact": "Persona Moretti"}, "MORETTI"),
    ({"notes": "Chiedere conferma a settembre"}, "settembre"),
    ({"details": {"company": "Cliente professionale", "request": "Sviluppo web"}}, "Sviluppo web"),
    ({"details": {"company": "Città studio", "request": "Attività di qualità"}}, "qualità"),
])
def test_search_matches_common_fields_and_detail_values(business, changes, query):
    _, client = business
    item = add(client, **changes)
    add(client, title="Altra scheda")
    response = client.get("/api/business/records", params={"q": query})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert [record["id"] for record in response.json()["items"]] == [item["id"]]


@pytest.mark.parametrize("literal", ["%", "_", "\\", "' OR 1=1 --", '"speciale"'])
def test_sql_wildcards_quotes_and_json_escapes_are_literal(business, literal):
    _, client = business
    item = add(client, title="Scheda con caratteri", details={"company": "Cliente", "request": "Contiene " + literal + " qui"})
    add(client, title="Scheda ordinaria", details={"company": "Cliente", "request": "Contiene qualcosa qui"})
    response = client.get("/api/business/records", params={"q": literal})
    assert response.status_code == 200, response.text
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == item["id"]


def test_search_filters_before_pagination_and_keeps_service_and_status_filters(business):
    _, client = business
    matching = [add(client, title=f"Progetto speciale {index}", status="waiting") for index in range(5)]
    add(client, title="Progetto speciale completato", status="done")
    add(client, service_id="procedures", title="Progetto speciale procedura", details={"process": "Progetto", "steps": "Verifica"})
    # Newer unrelated rows fill the first unfiltered page: search must still find old rows.
    for index in range(8):
        add(client, title=f"Non correlato {index}")
    params = {"q": "speciale", "service_id": "clients", "status": "open", "limit": 2, "offset": 2}
    response = client.get("/api/business/records", params=params)
    assert response.status_code == 200, response.text
    page = response.json()
    assert page["total"] == 5 and page["limit"] == 2 and page["offset"] == 2
    assert [record["id"] for record in page["items"]] == [item["id"] for item in reversed(matching)][2:4]
    assert all(item["service_id"] == "clients" and item["status"] == "waiting" for item in page["items"])
    assert client.get("/api/business/records", params={"q": "speciale"}).json()["total"] == 7


def test_empty_search_is_all_records_and_keys_are_not_searched_as_entered_values(business):
    _, client = business
    add(client)
    assert client.get("/api/business/records", params={"q": "   "}).json()["total"] == 1
    assert client.get("/api/business/records", params={"q": "company"}).json()["total"] == 0
    assert client.get("/api/business/records", params={"q": "irreperibile"}).json()["items"] == []
    assert client.get("/api/business/records", params={"q": "x" * 121}).status_code == 422
    assert client.get("/api/business/records", params={"q": "nuova\nriga"}).status_code == 422


def test_search_cannot_cross_private_workspace_and_survives_restart(tmp_path):
    db1 = Database(tmp_path / "one.sqlite3")
    db2 = Database(tmp_path / "two.sqlite3")
    with client_for(db1) as alice, client_for(db2) as bob:
        private = add(alice, notes="Parola riservata Aurora-299")
        add(bob, notes="Dati diversi")
        assert bob.get("/api/business/records", params={"q": "Aurora-299"}).json()["total"] == 0
        assert alice.get("/api/business/records", params={"q": "Aurora-299"}).json()["items"][0]["id"] == private["id"]
    with client_for(Database(db1.path)) as reopened:
        assert reopened.get("/api/business/records", params={"q": "Aurora-299"}).json()["items"][0]["id"] == private["id"]
