"""Chat can suggest a workflow without creating work or expanding permissions."""
from fastapi.testclient import TestClient
import pytest

from app.main import create_workspace_app


@pytest.fixture
def chat_workspace(tmp_path):
    app = create_workspace_app(tmp_path, start_worker=False)
    with TestClient(app) as client:
        client.headers["X-CSRF-Token"] = client.get("/api/bootstrap").json()["csrf_token"]
        yield app, client


@pytest.mark.parametrize("message,expected", [
    ("Prepara un preventivo per il cliente Alfa", "quotes"),
    ("Organizza le fatture non pagate e i solleciti", "receivables"),
    ("Prepara una bozza fattura", "invoices"),
    ("Voglio una nota spese", "expense-claims"),
    ("Gestisci ferie e permessi", "leave"),
    ("Organizza il magazzino", "inventory"),
    ("Prepara il verbale della riunione", "meetings"),
    ("Scrivi un post per Instagram", "content"),
])
def test_local_service_proposal_never_starts_work_or_writes_records(chat_workspace, message, expected):
    app, client = chat_workspace
    before = app.state.db.get_setting("service")
    response = client.post("/api/chat", json={"message": message})
    assert response.status_code == 200
    result = response.json()
    assert result["supported"] is True
    assert result["business_service_id"] == expected
    assert result["service_id"] is None
    assert app.state.db.get_setting("service") == before
    assert client.get("/api/business/records").json()["items"] == []


@pytest.mark.parametrize("message", ["Controlla la posta alle 11:15", "Segui le email con i preventivi alle 9"])
def test_business_catalog_does_not_hijack_email_monitoring(chat_workspace, message):
    _, client = chat_workspace
    result = client.post("/api/chat", json={"message": message}).json()
    assert result["service_id"] == "priority-email"
    assert result.get("business_service_id") is None


@pytest.mark.parametrize("message", [
    "Pubblica una campagna pubblicitaria e spendi 500 euro",
    "Invia una PEC al cliente", "Esegui un bonifico al fornitore", "Trasmetti fattura allo SDI",
])
def test_external_or_financial_actions_remain_unavailable(chat_workspace, message):
    app, client = chat_workspace
    result = client.post("/api/chat", json={"message": message}).json()
    assert result["supported"] is False
    assert result["service_id"] is None
    assert app.state.db.get_setting("service")["status"] == "inactive"
    assert client.get("/api/business/records").json()["items"] == []


def test_chat_does_not_choose_employees_for_dismissal(chat_workspace):
    _, client = chat_workspace
    result = client.post("/api/chat", json={"message": "Chi licenziare fra Luca e Marta?"}).json()
    assert result["supported"] is False
    assert "valutazioni umane" in result["reply"]
    assert result.get("business_service_id") is None
