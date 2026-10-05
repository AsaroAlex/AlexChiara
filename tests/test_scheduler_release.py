"""Daily retry window, history pruning and same-mailbox reconnection."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from app import service as service_module, watches
from app.briefing import build_briefing, capture_snapshot, mailbox_scope, persist_snapshot
from app.connectors import ConnectorError
from app.db import Database
from app.service import SCHEDULED_RETRY_DELAYS, Scheduler
from app.watches import build_watch_summary, create_watches_router


NOW = datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc)
CONTACT = {"name": "Cliente Alfa", "email": "alfa@example.test"}


def authorize(db, hour=9, minute=0):
    db.set_setting("company", {"name": "Attività reale", "sector": "", "description": "", "signature": "Team", "demo": False})
    settings = db.get_setting("service")
    settings.update(provider="gmail", status="active", priority_contacts=[CONTACT], hour=hour, minute=minute,
                    timezone="Europe/Rome", activated_at=(NOW - timedelta(days=1)).isoformat(),
                    mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", settings)
    db.set_connection("gmail", "connected", "studio@example.test")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    database = Database(tmp_path / "filo.sqlite3")
    authorize(database)
    return database


def test_daily_check_survives_a_short_outage(db, monkeypatch):
    outage = {"until": NOW + timedelta(minutes=10)}
    calls = []

    def read(provider, contacts, database):
        calls.append(provider)
        if current["now"] < outage["until"]:
            raise ConnectorError("Google non risponde.", "connessione Google", True)
        return []

    current = {"now": NOW}
    monkeypatch.setattr(service_module, "load_messages", read)
    monkeypatch.setattr(watches, "needs_watch_poll", lambda *_: False)
    scheduler = Scheduler(db)
    # 09:00 Europe/Rome is 07:00 UTC in October.
    [first] = scheduler.tick(NOW)
    assert first["trigger"] == "scheduled" and first["status"] == "retry_wait"
    elapsed = timedelta()
    for delay in SCHEDULED_RETRY_DELAYS:
        elapsed += timedelta(seconds=delay)
        current["now"] = NOW + elapsed
        [run] = scheduler.tick(current["now"])
        if run["status"] == "succeeded":
            break
    assert run["id"] == first["id"]
    assert run["status"] == "succeeded"
    assert len(calls) >= 3


def test_daily_retries_are_bounded(db, monkeypatch):
    def unavailable(*_):
        raise ConnectorError("Google non risponde.", "connessione Google", True)
    monkeypatch.setattr(service_module, "load_messages", unavailable)
    monkeypatch.setattr(watches, "needs_watch_poll", lambda *_: False)
    scheduler = Scheduler(db)
    now = NOW
    [run] = scheduler.tick(now)
    while run["status"] == "retry_wait":
        now = datetime.fromisoformat(run["next_attempt_at"])
        [run] = scheduler.tick(now)
    assert run["status"] == "failed"
    assert run["attempts"] == len(SCHEDULED_RETRY_DELAYS) + 1
    assert now - NOW < timedelta(hours=3)
    assert scheduler.tick(now + timedelta(hours=1)) == []


def watch_run(db, run_id, created, messages):
    with db.connection() as conn:
        conn.execute("""INSERT INTO runs(id,trigger,provider,status,created_at,started_at,finished_at,preferences,company)
                        VALUES (?,'watch','gmail','succeeded',?,?,?,'{}','{}')""", (run_id, created.isoformat(), created.isoformat(), created.isoformat()))
        snapshot = capture_snapshot(messages, [CONTACT], "gmail", created, mailbox_scope(db, "gmail"))
        persist_snapshot(conn, run_id, snapshot)


def message(source_id, received):
    return {"id": source_id, "thread_id": source_id, "sender": "alfa@example.test", "received_at": received.isoformat(), "from_client": True}


def test_pruning_removes_only_superseded_reminder_checks(db):
    create_watches_router(db)
    early = message("early", NOW - timedelta(days=3, hours=1))
    late = message("late", NOW - timedelta(days=2))
    watch_run(db, "old-same", NOW - timedelta(days=3), [early])
    watch_run(db, "old-distinct", NOW - timedelta(days=2, hours=12), [message("gone", NOW - timedelta(days=2, hours=13))])
    watch_run(db, "newer", NOW - timedelta(days=2), [early, late])
    watch_run(db, "recent", NOW - timedelta(hours=2), [early, late])
    with db.connection() as conn:
        conn.execute("""INSERT INTO runs(id,trigger,provider,status,created_at,preferences,company,error)
                        VALUES ('old-failed','watch','gmail','failed',?,'{}','{}','Non ci sono risposte')""", ((NOW - timedelta(days=4)).isoformat(),))
    before = build_briefing(db, NOW)
    removed = Scheduler(db).prune_history(NOW)
    with db.connection() as conn:
        remaining = {row["id"] for row in conn.execute("SELECT id FROM runs")}
    # "newer" is superseded by "recent"; "old-distinct" saw a message nobody else saw.
    assert remaining == {"old-distinct", "recent"}
    assert removed == 3
    after = build_briefing(db, NOW)
    assert after["contacts"] == before["contacts"]
    assert after["last_checked_at"] == before["last_checked_at"]


def test_pruning_never_removes_checks_with_drafts_or_daily_checks(db):
    watch_run(db, "with-draft", NOW - timedelta(days=3), [message("a", NOW - timedelta(days=3, hours=1))])
    watch_run(db, "later", NOW - timedelta(days=2), [message("a", NOW - timedelta(days=3, hours=1))])
    with db.connection() as conn:
        conn.execute("""INSERT INTO drafts(id,run_id,source_id,client,subject,reason,draft,created_at)
                        VALUES ('d1','with-draft','a','Cliente','Oggetto','Motivo','Bozza',?)""", (NOW.isoformat(),))
        conn.execute("UPDATE runs SET trigger='scheduled' WHERE id='later'")
    assert Scheduler(db).prune_history(NOW) == 0


def test_reconnecting_the_same_mailbox_keeps_reviewed_state_and_reminders(db):
    create_watches_router(db)
    scope_before = mailbox_scope(db, "gmail")
    db.set_setting("gmail_revision", db.get_setting("gmail_revision", 0) + 1)
    assert mailbox_scope(db, "gmail") == scope_before
    db.set_connection("gmail", "connected", "another@example.test")
    assert mailbox_scope(db, "gmail") != scope_before


def test_saved_revision_scopes_are_migrated_on_startup(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = Database(path)
    create_watches_router(db)
    with db.connection() as conn:
        conn.execute("""INSERT INTO runs(id,trigger,provider,status,created_at,preferences,company)
                        VALUES ('r1','manual','gmail','succeeded',?,'{}','{}')""", (NOW.isoformat(),))
        conn.execute("""INSERT INTO briefing_snapshots(run_id,provider,scope_key,checked_at,coverage_start,coverage_end,contacts,messages)
                        VALUES ('r1','gmail','gmail:3:studio@example.test',?,?,?,'[]','[]')""", (NOW.isoformat(),) * 3)
        conn.execute("""INSERT INTO watch_requests(id,name,email,day,created_at,provider,scope_key)
                        VALUES ('w1','Alfa','alfa@example.test','2026-10-05',?,'gmail','gmail:3:studio@example.test')""", (NOW.isoformat(),))
    migrated = Database(path)
    create_watches_router(migrated)
    with migrated.connection() as conn:
        assert conn.execute("SELECT scope_key FROM briefing_snapshots").fetchone()[0] == "gmail:mailbox:studio@example.test"
        assert conn.execute("SELECT scope_key FROM watch_requests").fetchone()[0] == "gmail:mailbox:studio@example.test"
    migrated.set_connection("gmail", "connected", "studio@example.test")
    assert mailbox_scope(migrated, "gmail") == "gmail:mailbox:studio@example.test"
    assert build_watch_summary(migrated, NOW)["items"][0]["id"] == "w1"
