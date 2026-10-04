"""Personal reminders require actual, scoped, newly received source evidence."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.briefing import capture_snapshot, mailbox_scope, persist_snapshot
from app.db import Database
from app.watches import build_watch_summary, create_watches_router, needs_watch_poll, propose_watch


UTC = timezone.utc
NOW = datetime(2026, 10, 4, 20, 30, tzinfo=UTC)
CONTACTS = [{"name": "Caio Rossi", "email": "caio@studio.test"},
            {"name": "Sempronio Bianchi", "email": "sempronio@studio.test"}]


@pytest.fixture
def watches(tmp_path):
    db = Database(tmp_path / "watches.sqlite3")
    service = db.get_setting("service")
    service.update(provider="gmail", status="active", priority_contacts=CONTACTS,
                   mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", service)
    db.set_connection("gmail", "connected", "studio@studio.test")
    clock = [NOW]
    app = FastAPI()
    app.include_router(create_watches_router(db, clock=lambda: clock[0]))
    with TestClient(app) as client:
        yield db, client, clock


def add(client, day="2026-10-05", email="caio@studio.test", name="Caio Rossi"):
    response = client.post("/api/watches", json={"name": name, "email": email, "day": day})
    assert response.status_code == 201, response.text
    return response.json()["item"]


def observe(db, received, *, checked=None, sender="caio@studio.test", from_client=True,
            source="response-1", thread="thread-1", provider="gmail", scope=None):
    run_id = uuid4().hex
    checked = checked or received + timedelta(minutes=1)
    snapshot = capture_snapshot([{"id": source, "thread_id": thread, "sender": sender,
                                  "received_at": received.isoformat(), "from_client": from_client}],
                                CONTACTS, provider, checked, scope or mailbox_scope(db, provider))
    with db.connection() as conn:
        conn.execute("""
            INSERT INTO runs(id,trigger,provider,status,created_at,preferences,company)
            VALUES (?,'manual',?,'succeeded',?,'{}','{}')
        """, (run_id, provider, checked.isoformat()))
        persist_snapshot(conn, run_id, snapshot)
    return snapshot


def test_empty_read_neither_seeds_a_reminder_nor_initializes_table(tmp_path):
    db = Database(tmp_path / "empty.db")
    assert build_watch_summary(db, NOW)["items"] == []
    assert needs_watch_poll(db, NOW) is False
    with db.connection() as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='watch_requests'").fetchone()


def test_creation_uses_configured_identity_and_preserves_service_authorization(watches):
    db, client, _ = watches
    service = db.get_setting("service")
    item = add(client, name="Nome inventato", email="CAIO@STUDIO.TEST")
    assert item["name"] == "Caio Rossi"
    assert item["email"] == "caio@studio.test"
    assert item["status"] == "waiting"
    assert item["label"] == "In attesa del prossimo controllo"
    assert "non invio email" in item["next_step"]
    assert db.get_setting("service") == service
    assert add(client)["id"] == item["id"]
    assert len(client.get("/api/watches").json()["items"]) == 1


@pytest.mark.parametrize("payload", [
    {"name": "Caio", "email": "bad", "day": "2026-10-05"},
    {"name": "Caio", "email": "a..b@studio.test", "day": "2026-10-05"},
    {"name": "Caio", "email": "a@-studio.test", "day": "2026-10-05"},
    {"name": "Caio", "email": "a@studio..test", "day": "2026-10-05"},
    {"name": "Caio\nRossi", "email": "caio@studio.test", "day": "2026-10-05"},
    {"name": "Caio", "email": "caio@studio.test", "day": "2026-10-03"},
    {"name": "Caio", "email": "caio@studio.test", "day": "2026-11-04"},
    {"name": "Caio", "email": "caio@studio.test", "day": 1791093600},
    {"name": "Caio", "email": "caio@studio.test", "day": "2026-10-05T00:00:00Z"},
    {"name": "Caio", "email": "caio@studio.test", "day": "2026-02-30"},
    {"name": "Caio", "email": "caio@studio.test", "day": "2026-10-05", "send": True},
])
def test_invalid_input_does_not_persist(watches, payload):
    db, client, _ = watches
    assert client.post("/api/watches", json=payload).status_code == 422
    assert build_watch_summary(db, NOW)["items"] == []


def test_unknown_contact_and_fictional_or_disconnected_provider_are_rejected(watches):
    db, client, _ = watches
    payload = {"name": "Altro", "email": "new@studio.test", "day": "2026-10-05"}
    assert client.post("/api/watches", json=payload).status_code == 400
    payload.update(name="Caio", email="caio@studio.test")
    for provider, status in [("demo", "connected"), ("gmail", "disconnected"), (None, "disconnected")]:
        db.set_connection(provider, status)
        assert client.post("/api/watches", json=payload).status_code == 409
    assert db.get_setting("service")["priority_contacts"] == CONTACTS


def test_match_can_be_source_only_and_survives_restart_without_provider_calls(watches, monkeypatch):
    db, client, clock = watches
    item = add(client)
    received = NOW + timedelta(hours=3)
    checked = received + timedelta(minutes=5)
    observe(db, received, checked=checked)
    clock[0] = checked
    def forbidden(*_):
        pytest.fail("A watch read must never request mailbox data")
    monkeypatch.setattr("app.service.load_messages", forbidden)
    first = client.get("/api/watches").json()
    assert first["matched"][0]["id"] == item["id"]
    assert first["matched"][0]["match"] == {"source_id": "response-1", "thread_id": "thread-1",
                                           "received_at": received.isoformat()}
    with db.connection() as conn:
        counts = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("runs", "drafts", "briefing_snapshots", "watch_requests", "actions")}
    reopened = Database(db.path)
    later = build_watch_summary(reopened, checked + timedelta(days=3))
    assert later["matched"] == first["matched"]
    assert needs_watch_poll(reopened, checked) is False
    with db.connection() as conn:
        assert counts == {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in counts}


@pytest.mark.parametrize("reason", ["previous_day", "before_creation", "old_observation", "future_observation", "outgoing", "different_sender", "different_scope", "different_provider", "future_received"])
def test_only_new_correct_day_incoming_current_mailbox_sources_match(watches, reason):
    db, client, clock = watches
    received = NOW + timedelta(hours=3)
    # Test the same-day-before-creation case separately from a prior-day source.
    if reason == "before_creation":
        clock[0] = received + timedelta(minutes=5)
    add(client)
    options = {}
    checked = received + timedelta(minutes=10)
    if reason == "previous_day":
        received = NOW
    elif reason == "old_observation":
        checked = NOW - timedelta(minutes=1)
    elif reason == "future_observation":
        checked = NOW + timedelta(days=2)
    elif reason == "outgoing":
        options["from_client"] = False
    elif reason == "different_sender":
        options["sender"] = "sempronio@studio.test"
    elif reason == "different_scope":
        options["scope"] = "gmail:99:another@studio.test"
    elif reason == "different_provider":
        options["provider"] = "imported"
    elif reason == "future_received":
        checked = received - timedelta(seconds=1)
    observe(db, received, checked=checked, **options)
    result = build_watch_summary(db, NOW + timedelta(hours=4))
    assert result["matched"] == []
    assert result["items"][0]["status"] == "waiting"


def test_reconnecting_another_gmail_mailbox_hides_previous_watches_and_sources(watches):
    db, client, _ = watches
    old = add(client)
    received = NOW + timedelta(hours=3)
    observe(db, received)
    assert build_watch_summary(db, received + timedelta(minutes=2))["matched"][0]["id"] == old["id"]
    db.set_setting("gmail_revision", 1)
    db.set_connection("gmail", "connected", "another@studio.test")
    assert build_watch_summary(db, received + timedelta(minutes=2))["items"] == []
    assert client.delete("/api/watches/" + old["id"]).status_code == 404
    new = add(client)
    assert new["id"] != old["id"]
    assert build_watch_summary(db, received + timedelta(minutes=2))["matched"] == []


def test_matched_first_waiting_next_expired_last_and_no_claim_of_absence(watches):
    db, client, clock = watches
    expired = add(client, day="2026-10-04", email="sempronio@studio.test", name="Sempronio")
    matched = add(client)
    waiting = add(client, day="2026-10-06", email="sempronio@studio.test", name="Sempronio")
    clock[0] = NOW + timedelta(hours=4)
    observe(db, NOW + timedelta(hours=3))
    items = client.get("/api/watches").json()["items"]
    assert [item["id"] for item in items] == [matched["id"], waiting["id"], expired["id"]]
    assert [item["status"] for item in items] == ["matched", "waiting", "expired"]
    assert items[-1]["label"] == "Periodo concluso, esito non verificato"


def test_dismissal_persists_and_sources_do_not_resurrect_watch(watches):
    db, client, clock = watches
    item = add(client)
    response = client.post("/api/watches/" + item["id"] + "/dismiss")
    assert response.json() == {"dismissed": True, "id": item["id"]}
    observe(db, NOW + timedelta(hours=3))
    assert build_watch_summary(Database(db.path), NOW + timedelta(hours=4))["items"] == []
    assert client.delete("/api/watches/" + item["id"]).status_code == 404
    with db.connection() as conn:
        assert conn.execute("SELECT done_at FROM watch_requests WHERE id=?", (item["id"],)).fetchone()["done_at"] == NOW.isoformat()


@pytest.mark.parametrize("created,received,expected", [
    ("2026-03-28T12:00:00Z", "2026-03-28T22:59:59Z", False),
    ("2026-03-28T12:00:00Z", "2026-03-28T23:00:00Z", True),
    ("2026-03-28T12:00:00Z", "2026-03-29T21:59:59Z", True),
    ("2026-03-28T12:00:00Z", "2026-03-29T22:00:00Z", False),
    ("2026-10-24T12:00:00Z", "2026-10-24T22:00:00Z", True),
    ("2026-10-24T12:00:00Z", "2026-10-25T00:30:00Z", True),
    ("2026-10-24T12:00:00Z", "2026-10-25T01:30:00Z", True),
    ("2026-10-24T12:00:00Z", "2026-10-25T22:59:59Z", True),
    ("2026-10-24T12:00:00Z", "2026-10-25T23:00:00Z", False),
])
def test_day_matches_rome_calendar_across_dst(watches, created, received, expected):
    db, client, clock = watches
    clock[0] = datetime.fromisoformat(created.replace("Z", "+00:00"))
    suggestion = propose_watch(db, "Domani se risponde Caio dimmelo subito", now=clock[0])["watch_suggestion"]
    add(client, day=suggestion["day"])
    received = datetime.fromisoformat(received.replace("Z", "+00:00"))
    observe(db, received)
    assert bool(build_watch_summary(db, received + timedelta(minutes=2))["matched"]) is expected


@pytest.mark.parametrize("message", [
    "Domani se risponde Caio dimmelo subito",
    "Se Caio Rossi risponde domani avvisami",
    "Domani, se Sempronio scrive, fammi sapere",
    "Se CAIO@STUDIO.TEST scrive domani dimmelo",
])
def test_proposals_resolve_only_existing_contacts_without_changing_settings(watches, message):
    db, client, _ = watches
    before = db.get_setting("service")
    proposal = propose_watch(db, message, now=NOW)
    assert proposal["watch_suggestion"]["day"] == "2026-10-05"
    assert proposal["watch_suggestion"]["email"] in {contact["email"] for contact in CONTACTS}
    assert "Conferma" in proposal["reply"]
    assert "nell'app" in proposal["reply"]
    assert db.get_setting("service") == before
    assert client.get("/api/watches").json()["items"] == []


def test_unknown_or_ambiguous_person_never_invents_email(watches):
    db, _, _ = watches
    assert "watch_suggestion" not in propose_watch(db, "Domani se Mario risponde avvisami", NOW)
    assert "watch_suggestion" not in propose_watch(db, "Domani se new@studio.test risponde avvisami", NOW)
    service = db.get_setting("service")
    service["priority_contacts"] = [*CONTACTS, {"name": "Caio Verdi", "email": "other@studio.test"}]
    db.set_setting("service", service)
    assert "watch_suggestion" not in propose_watch(db, "Se Caio risponde domani avvisami", NOW)
    assert propose_watch(db, "Se Caio Rossi risponde domani avvisami", NOW)["watch_suggestion"]["email"] == "caio@studio.test"
    assert propose_watch(db, "Prepara le risposte ai clienti domani", NOW) is None


def test_only_active_authorized_today_waiting_watch_requests_poll(watches):
    db, client, clock = watches
    item = add(client)
    assert needs_watch_poll(db, NOW) is False
    clock[0] = NOW + timedelta(hours=3)
    assert needs_watch_poll(db, clock[0]) is True
    service = db.get_setting("service")
    service.update(status="paused")
    db.set_setting("service", service)
    assert needs_watch_poll(db, clock[0]) is False
    assert build_watch_summary(db, clock[0])["next_check_at"] is None
    service.update(status="active", mandate={"read": True, "draft": True, "send": True})
    db.set_setting("service", service)
    assert needs_watch_poll(db, clock[0]) is False
    service["mandate"]["send"] = False
    db.set_setting("service", service)
    assert needs_watch_poll(db, clock[0]) is True
    assert client.delete("/api/watches/" + item["id"]).status_code == 200
    assert needs_watch_poll(db, clock[0]) is False


def test_next_check_eta_follows_poll_cooldown_and_avoids_double_counting_inflight(watches):
    db, client, clock = watches
    add(client)
    clock[0] = NOW + timedelta(hours=3)
    finished = clock[0] - timedelta(minutes=2)
    observe(db, finished - timedelta(minutes=2), checked=finished, sender="sempronio@studio.test")
    with db.connection() as conn:
        conn.execute("UPDATE runs SET finished_at=?", (finished.isoformat(),))
    summary = build_watch_summary(db, clock[0])
    assert summary["next_check_at"] == (finished + timedelta(minutes=5)).isoformat()
    with db.connection() as conn:
        conn.execute("UPDATE runs SET status='queued',finished_at=NULL")
    assert build_watch_summary(db, clock[0])["next_check_at"] is None


def test_creating_watch_does_not_activate_inactive_service(watches):
    db, client, clock = watches
    clock[0] = NOW + timedelta(hours=3)
    service = db.get_setting("service")
    service.update(status="inactive", mandate=None)
    db.set_setting("service", service)
    add(client)
    assert db.get_setting("service") == service
    assert needs_watch_poll(db, clock[0]) is False
    assert build_watch_summary(db, clock[0])["monitoring_active"] is False
    assert build_watch_summary(db, clock[0])["next_check_at"] is None


def test_removed_contact_stops_watch_poll_but_keeps_saved_arrival_evidence(watches):
    db, client, clock = watches
    clock[0] = NOW + timedelta(hours=3)
    item = add(client)
    assert needs_watch_poll(db, clock[0]) is True
    service = db.get_setting("service")
    service["priority_contacts"] = [CONTACTS[1]]
    db.set_setting("service", service)
    summary = build_watch_summary(db, clock[0])
    assert summary["items"][0]["id"] == item["id"]
    assert summary["items"][0]["status"] == "waiting"
    assert summary["items"][0]["contact_active"] is False
    assert needs_watch_poll(db, clock[0]) is False
    assert datetime.fromisoformat(summary["next_check_at"]) > clock[0] + timedelta(minutes=5)

    # A previously observed actual arrival remains useful history even after
    # the user removes the sender from future authorized checks.
    received = clock[0] + timedelta(minutes=1)
    observe(db, received)
    summary = build_watch_summary(db, received + timedelta(minutes=2))
    assert summary["matched"][0]["id"] == item["id"]
    assert summary["matched"][0]["contact_active"] is False
    assert needs_watch_poll(db, received + timedelta(minutes=2)) is False


def test_ambiguous_naive_clock_is_rejected(watches):
    db, _, _ = watches
    with pytest.raises(ValueError, match="fuso orario"):
        build_watch_summary(db, datetime(2026, 10, 4, 10))
