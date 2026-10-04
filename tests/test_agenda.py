"""Verify manual agenda persistence, local calendar boundaries and downloads."""
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agenda import agenda_summary, create_agenda_router
from app.db import Database


UTC = timezone.utc


@pytest.fixture
def agenda(tmp_path):
    db = Database(tmp_path / "agenda.sqlite3")
    app = FastAPI()
    app.include_router(create_agenda_router(db))
    with TestClient(app) as client:
        yield db, client


def add(client, starts_at, title="Incontro con il cliente", **extra):
    response = client.post("/api/agenda", json={"title": title, "starts_at": starts_at, **extra})
    assert response.status_code == 201, response.text
    return response.json()["item"]


def test_empty_agenda_is_honest_and_does_not_seed_events(agenda):
    db, client = agenda
    summary = agenda_summary(db, now=datetime(2026, 10, 4, 8, tzinfo=UTC))
    assert summary == {
        "date": "2026-10-04", "timezone": "Europe/Rome", "items": [],
        "next_event": None, "calendar_connected": False, "source": "Appuntamenti aggiunti da te",
    }
    response = client.get("/api/agenda/export?date=2026-10-04")
    assert response.status_code == 200
    assert "Nessun appuntamento aggiunto per questa giornata." in response.text
    assert "Calendario esterno non collegato." in response.text


@pytest.mark.parametrize("payload", [
    {"title": "   ", "starts_at": "2026-10-04T09:00:00+02:00"},
    {"title": "x" * 161, "starts_at": "2026-10-04T09:00:00+02:00"},
    {"title": "Titolo\nche imita un'altra riga", "starts_at": "2026-10-04T09:00:00+02:00"},
    {"title": "Incontro", "starts_at": "2026-10-04T09:00:00"},
    {"title": "Incontro", "starts_at": "2026-10-04"},
    {"title": "Incontro", "starts_at": 1791097200},
    {"title": "Incontro", "starts_at": "2026-10-04T09:00:00+02:00", "ends_at": "2026-10-04T10:00:00"},
    {"title": "Incontro", "starts_at": "2026-10-04T09:00:00+02:00", "ends_at": "2026-10-04T09:00:00+02:00"},
    {"title": "Incontro", "starts_at": "2026-10-04T09:00:00+02:00", "ends_at": "2026-10-04T08:00:00+02:00"},
    {"title": "Incontro", "starts_at": "2026-10-04T09:00:00+02:00", "notes": "x" * 501},
    {"title": "Incontro", "starts_at": "2026-10-04T09:00:00+02:00", "calendar_connected": True},
])
def test_invalid_input_is_rejected_without_persisting(agenda, payload):
    db, client = agenda
    assert client.post("/api/agenda", json=payload).status_code == 422
    assert agenda_summary(db, now=datetime(2026, 10, 4, tzinfo=UTC))["items"] == []


def test_date_filter_and_next_event_use_rome_midnight(agenda):
    db, client = agenda
    before = add(client, "2026-10-03T21:59:59Z", "Prima di mezzanotte")
    first = add(client, "2026-10-03T22:00:00Z", "Inizio giornata")
    late = add(client, "2026-10-04T21:59:59Z", "Fine giornata")
    after = add(client, "2026-10-04T22:00:00Z", "Domani")
    morning = add(client, "2026-10-04T07:30:00Z", "Primo incontro", ends_at="2026-10-04T08:15:00Z")
    summary = agenda_summary(db, now=datetime(2026, 10, 4, 7, tzinfo=UTC))
    assert [item["id"] for item in summary["items"]] == [first["id"], morning["id"], late["id"]]
    assert summary["next_event"]["id"] == morning["id"]
    assert morning["starts_at"] == "2026-10-04T09:30:00+02:00"
    assert morning["ends_at"] == "2026-10-04T10:15:00+02:00"
    assert morning["time_label"] == "09:30–10:15"
    assert client.get("/api/agenda?date=2026-10-03").json()["items"][0]["id"] == before["id"]
    assert client.get("/api/agenda?date=2026-10-05").json()["items"][0]["id"] == after["id"]


def test_spring_transition_has_23_hour_calendar_day(agenda):
    db, client = agenda
    add(client, "2026-03-28T22:59:59Z", "Giorno precedente")
    first = add(client, "2026-03-28T23:00:00Z", "Mezzanotte")
    last = add(client, "2026-03-29T21:59:59Z", "Fine del giorno")
    add(client, "2026-03-29T22:00:00Z", "Giorno seguente")
    summary = agenda_summary(db, now=datetime(2026, 3, 29, 10, tzinfo=UTC))
    assert [item["id"] for item in summary["items"]] == [first["id"], last["id"]]
    assert first["starts_at"].endswith("+01:00")
    assert last["starts_at"].endswith("+02:00")


def test_autumn_transition_has_25_hour_calendar_day(agenda):
    db, client = agenda
    add(client, "2026-10-24T21:59:59Z", "Giorno precedente")
    first = add(client, "2026-10-24T22:00:00Z", "Mezzanotte")
    summer = add(client, "2026-10-25T00:30:00Z", "Prima delle due e trenta")
    winter = add(client, "2026-10-25T01:30:00Z", "Seconda delle due e trenta")
    last = add(client, "2026-10-25T22:59:59Z", "Fine del giorno")
    add(client, "2026-10-25T23:00:00Z", "Giorno seguente")
    summary = agenda_summary(db, now=datetime(2026, 10, 25, tzinfo=UTC))
    assert [item["id"] for item in summary["items"]] == [first["id"], summer["id"], winter["id"], last["id"]]
    assert summer["starts_at"] == "2026-10-25T02:30:00+02:00"
    assert winter["starts_at"] == "2026-10-25T02:30:00+01:00"


def test_export_contains_saved_appointments_notes_and_utf8_download_header(agenda):
    _, client = agenda
    add(client, "2026-10-04T12:00:00+02:00", "Pranzo più tardi")
    add(client, "2026-10-04T09:00:00+02:00", "Caffè con Chiara", notes="Portare il preventivo.\nConfermare i materiali.")
    add(client, "2026-10-05T09:00:00+02:00", "Appuntamento escluso")
    response = client.get("/api/agenda/export?date=2026-10-04")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-disposition"] == 'attachment; filename="ordine-del-giorno-2026-10-04.txt"'
    assert "04/10/2026" in response.text
    assert "09:00 — Caffè con Chiara" in response.text
    assert "  Portare il preventivo.\n  Confermare i materiali." in response.text
    assert response.text.index("Caffè") < response.text.index("Pranzo")
    assert "Appuntamento escluso" not in response.text
    assert response.content.decode("utf-8") == response.text


def test_appointments_survive_reopening_and_delete_persists(agenda):
    db, client = agenda
    item = add(client, "2026-10-04T09:00:00+02:00", notes="Note salvate")
    reopened = Database(db.path)
    fresh_app = FastAPI()
    fresh_app.include_router(create_agenda_router(reopened))
    with TestClient(fresh_app) as fresh_client:
        assert fresh_client.get("/api/agenda?date=2026-10-04").json()["items"] == [item]
        response = fresh_client.delete("/api/agenda/" + item["id"])
        assert response.status_code == 200
        assert response.json() == {"deleted": True, "id": item["id"]}
        assert fresh_client.delete("/api/agenda/" + item["id"]).status_code == 404
    assert client.get("/api/agenda?date=2026-10-04").json()["items"] == []


def test_equal_starts_are_ordered_deterministically(agenda):
    _, client = agenda
    one = add(client, "2026-10-04T09:00:00+02:00", "Uno")
    two = add(client, "2026-10-04T09:00:00+02:00", "Due")
    expected = sorted([one["id"], two["id"]])
    assert [item["id"] for item in client.get("/api/agenda?date=2026-10-04").json()["items"]] == expected


@pytest.mark.parametrize("url", ["/api/agenda?date=not-a-date", "/api/agenda/export?date=2026-02-30", "/api/agenda?date=9999-12-31"])
def test_invalid_date_queries_return_validation_error(agenda, url):
    _, client = agenda
    assert client.get(url).status_code == 422


def test_summary_rejects_ambiguous_naive_now(agenda):
    db, _ = agenda
    with pytest.raises(ValueError, match="fuso orario"):
        agenda_summary(db, now=datetime(2026, 10, 4, 9))
