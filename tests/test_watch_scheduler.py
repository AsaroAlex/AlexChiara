"""Verify that Gmail reply watches run without a browser and stay bounded."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import service as service_module, watches
from app.connectors import ConnectorError
from app.db import DEFAULT_COMPANY, Database
from app.service import Scheduler


NOW = datetime(2026, 10, 5, 9, 1, tzinfo=timezone.utc)
CONTACT = {"name": "Cliente Alfa", "email": "alfa@example.test"}


def authorize(db, provider="gmail"):
    db.set_setting("company", {"name": "Attività reale", "sector": "Consulenza",
                               "description": "", "signature": "Il team Alfa", "demo": False})
    settings = db.get_setting("service")
    settings.update(provider=provider, status="active", priority_contacts=[CONTACT],
                    hour=23, minute=59, timezone="Europe/Rome", activated_at=NOW.isoformat(),
                    mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", settings)
    db.set_connection(provider, "connected", "studio@example.test")


@pytest.fixture
def scheduled(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    db = Database(tmp_path / "filo.sqlite3")
    authorize(db)
    scheduler = Scheduler(db)
    waiting = {"value": True}
    monkeypatch.setattr(watches, "needs_watch_poll", lambda _db, _now: waiting["value"])
    calls = []

    def read(provider, contacts, database):
        assert provider == "gmail"
        assert contacts == [CONTACT]
        assert database is db
        calls.append(provider)
        return []

    monkeypatch.setattr(service_module, "load_messages", read)
    return db, scheduler, waiting, calls


def test_watch_checks_are_five_minutes_apart_even_across_bucket_boundary(scheduled):
    db, scheduler, _, calls = scheduled
    first = scheduler.tick(NOW)
    assert first[0]["trigger"] == "watch"
    assert first[0]["status"] == "succeeded"
    for minutes in (0, 1, 3, 4):
        assert scheduler.tick(NOW + timedelta(minutes=minutes)) == []
    assert calls == ["gmail"]
    second = scheduler.tick(NOW + timedelta(minutes=5))
    assert second[0]["status"] == "succeeded"
    assert second[0]["id"] != first[0]["id"]
    assert first[0]["slot_key"] != second[0]["slot_key"]
    assert len(db.list_runs()) == 2
    assert calls == ["gmail", "gmail"]


@pytest.mark.parametrize("trigger", ["manual", "scheduled"])
def test_recent_daily_or_manual_check_delays_the_next_watch(scheduled, trigger):
    db, scheduler, _, calls = scheduled
    if trigger == "manual":
        scheduler.enqueue(now=NOW)
    else:
        settings = db.get_setting("service")
        settings.update(hour=11, minute=1)
        db.set_setting("service", settings)
    first = scheduler.tick(NOW)
    assert first[0]["trigger"] == trigger
    assert scheduler.tick(NOW + timedelta(minutes=4)) == []
    assert calls == ["gmail"]
    second = scheduler.tick(NOW + timedelta(minutes=5))
    assert second[0]["trigger"] == "watch"
    assert len(db.list_runs()) == 2
    assert calls == ["gmail", "gmail"]


@pytest.mark.parametrize("status", ["queued", "running", "retry_wait"])
def test_an_inflight_check_prevents_an_additional_watch_job(scheduled, status):
    db, scheduler, _, calls = scheduled
    run = scheduler.enqueue(now=NOW)
    with db.connection() as conn:
        conn.execute("UPDATE runs SET status=?,next_attempt_at=? WHERE id=?",
                     (status, (NOW + timedelta(minutes=30)).isoformat() if status == "retry_wait" else None,
                      run["id"]))
    scheduler.tick(NOW)
    assert len(db.list_runs()) == 1
    assert calls == (["gmail"] if status == "queued" else [])


@pytest.mark.parametrize("change", ["paused", "inactive", "read", "draft", "send", "disconnected", "expired", "other_provider"])
def test_polling_does_not_read_gmail_without_the_existing_authorization(scheduled, change):
    db, scheduler, _, calls = scheduled
    settings = db.get_setting("service")
    if change in {"paused", "inactive"}:
        settings["status"] = change
    elif change in {"read", "draft", "send"}:
        settings["mandate"][change] = change == "send"
    else:
        db.set_connection("demo" if change == "other_provider" else "gmail",
                          "connected" if change == "other_provider" else change)
    db.set_setting("service", settings)
    assert scheduler.tick(NOW) == []
    assert db.list_runs() == []
    assert calls == []


def test_no_waiting_watch_means_no_provider_calls(scheduled):
    db, scheduler, waiting, calls = scheduled
    waiting["value"] = False
    assert scheduler.tick(NOW) == []
    assert scheduler.tick(NOW + timedelta(minutes=20)) == []
    assert db.list_runs() == []
    assert calls == []


def test_real_mode_does_not_execute_an_old_active_demo_mandate(scheduled, monkeypatch):
    db, scheduler, _, calls = scheduled
    monkeypatch.delenv("FILO_REAL_DATA_ONLY")
    authorize(db, "demo")
    old = scheduler.enqueue(now=NOW)
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    assert scheduler.tick(NOW) == []
    assert db.run(old["id"])["status"] == "queued"
    with pytest.raises(ConnectorError, match="dimostrativa"):
        scheduler.enqueue(now=NOW)
    with pytest.raises(ConnectorError, match="dimostrativa"):
        scheduler._guard("demo")
    assert calls == []


@pytest.mark.parametrize("configured_after_queue", [False, True])
def test_real_mode_rejects_legacy_gmail_checks_with_a_demonstration_profile(scheduled, monkeypatch, configured_after_queue):
    db, scheduler, _, calls = scheduled
    monkeypatch.delenv("FILO_REAL_DATA_ONLY")
    db.set_setting("company", DEFAULT_COMPANY)
    queued = scheduler.enqueue(now=NOW)
    with db.connection() as conn:
        saved_company = conn.execute("SELECT company FROM runs WHERE id=?", (queued["id"],)).fetchone()["company"]
    if configured_after_queue:
        db.set_setting("company", {"name": "Attività reale", "signature": "Il team Alfa", "demo": False})
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    if not configured_after_queue:
        with pytest.raises(ConnectorError, match="profilo reale"):
            scheduler.enqueue(now=NOW)
    result = scheduler.tick(NOW)
    assert result[0]["id"] == queued["id"]
    assert result[0]["status"] == "failed"
    assert result[0]["error_step"] == "profilo"
    assert result[0]["retryable"] is False
    assert result[0]["items"] == []
    with db.connection() as conn:
        assert conn.execute("SELECT company FROM runs WHERE id=?", (queued["id"],)).fetchone()["company"] == saved_company
        assert conn.execute("SELECT COUNT(*) FROM briefing_snapshots").fetchone()[0] == 0
    assert calls == []


@pytest.mark.parametrize("former_provider", ["demo", "gmail"])
def test_a_former_mailbox_daily_check_does_not_block_the_current_gmail_mailbox(scheduled, monkeypatch, former_provider):
    db, scheduler, waiting, calls = scheduled
    waiting["value"] = False
    monkeypatch.delenv("FILO_REAL_DATA_ONLY")
    authorize(db, former_provider)
    monkeypatch.setattr(service_module, "load_messages", lambda provider, *_: calls.append(provider) or [])
    legacy_key = "priority-email:2026-10-05"
    old = scheduler.enqueue("scheduled", legacy_key, NOW)
    assert scheduler.tick(NOW)[0]["id"] == old["id"]
    authorize(db, "gmail")
    if former_provider == "gmail":
        db.set_setting("gmail_revision", 1)
        db.set_connection("gmail", "connected", "replacement@example.test")
    settings = db.get_setting("service")
    settings.update(hour=11, minute=1)
    db.set_setting("service", settings)
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    current = scheduler.tick(NOW + timedelta(minutes=1))[0]
    assert current["status"] == "succeeded"
    assert current["provider"] == "gmail"
    assert current["id"] != old["id"]
    assert current["slot_key"] != legacy_key
    assert current["slot_key"].startswith("priority-email:daily:")
    assert calls == [former_provider, "gmail"]


def test_a_successful_legacy_daily_check_of_the_same_mailbox_is_reused(scheduled):
    db, scheduler, waiting, calls = scheduled
    waiting["value"] = False
    previous = scheduler.enqueue("scheduled", "priority-email:2026-10-05", NOW)
    assert scheduler.tick(NOW)[0]["id"] == previous["id"]
    settings = db.get_setting("service")
    settings.update(hour=11, minute=1)
    db.set_setting("service", settings)
    assert scheduler.tick(NOW + timedelta(minutes=1)) == []
    assert len(db.list_runs()) == 1
    assert calls == ["gmail"]


def test_legacy_daily_using_a_demo_profile_is_replaced_by_a_real_profile_check(scheduled, monkeypatch):
    db, scheduler, waiting, calls = scheduled
    waiting["value"] = False
    monkeypatch.delenv("FILO_REAL_DATA_ONLY")
    db.set_setting("company", DEFAULT_COMPANY)
    previous = scheduler.enqueue("scheduled", "priority-email:2026-10-05", NOW)
    assert scheduler.tick(NOW)[0]["status"] == "succeeded"
    authorize(db)
    settings = db.get_setting("service")
    settings.update(hour=11, minute=1)
    db.set_setting("service", settings)
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    current = scheduler.tick(NOW + timedelta(minutes=1))[0]
    assert current["id"] != previous["id"]
    assert current["status"] == "succeeded"
    assert len(db.list_runs()) == 2
    assert calls == ["gmail", "gmail"]


def test_watch_keys_are_scoped_to_the_gmail_mailbox(scheduled):
    _, scheduler, _, calls = scheduled
    first = scheduler.tick(NOW)[0]
    db = scheduler.db
    db.set_setting("gmail_revision", 1)
    db.set_connection("gmail", "connected", "another@example.test")
    second = scheduler.tick(NOW + timedelta(minutes=5))[0]
    assert first["slot_key"].split(":")[-2] != second["slot_key"].split(":")[-2]
    assert "example.test" not in second["slot_key"]
    assert calls == ["gmail", "gmail"]


def test_multiple_schedulers_share_one_queued_watch(scheduled):
    db, scheduler, _, calls = scheduled
    other = Scheduler(db)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda worker: worker.enqueue("watch", now=NOW),
                                    (scheduler, other)))
    assert results[0]["id"] == results[1]["id"]
    assert len(db.list_runs()) == 1
    assert calls == []


def test_a_dismissed_watch_is_rechecked_before_reading_a_queued_job(scheduled):
    db, scheduler, waiting, calls = scheduled
    run = scheduler.enqueue("watch", now=NOW)
    waiting["value"] = False
    result = scheduler.tick(NOW)
    assert result[0]["id"] == run["id"]
    assert db.run(run["id"])["status"] == "failed"
    assert db.run(run["id"])["retryable"] is False
    assert calls == []


def test_temporary_watch_failure_waits_five_minutes_before_retry(scheduled, monkeypatch):
    db, scheduler, _, calls = scheduled

    def unavailable(*_):
        calls.append("gmail")
        raise ConnectorError("Temporaneamente non disponibile", "lettura", True)

    monkeypatch.setattr(service_module, "load_messages", unavailable)
    first = scheduler.tick(NOW)[0]
    assert first["status"] == "retry_wait"
    assert first["next_attempt_at"] == (NOW + timedelta(minutes=5)).isoformat()
    assert scheduler.tick(NOW + timedelta(minutes=4, seconds=59)) == []
    assert calls == ["gmail"]
    second = scheduler.tick(NOW + timedelta(minutes=5))[0]
    assert second["id"] == first["id"]
    assert second["attempts"] == 2
    assert len(db.list_runs()) == 1
    assert calls == ["gmail", "gmail"]


def test_an_observed_reply_stops_background_polling(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_REAL_DATA_ONLY", "1")
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    db = Database(tmp_path / "filo.sqlite3")
    authorize(db)
    scheduler = Scheduler(db)
    app = FastAPI()
    app.include_router(watches.create_watches_router(db, clock=lambda: NOW))
    with TestClient(app) as client:
        response = client.post("/api/watches", json={**CONTACT, "day": "2026-10-05"})
    assert response.status_code == 201
    calls = []

    def read(*_):
        calls.append("gmail")
        if len(calls) == 1:
            return []
        return [{"id": "real-observation", "thread_id": "observed-thread",
                 "sender": "Cliente Alfa <alfa@example.test>", "subject": "Conferma",
                 "body": "Confermo la ricezione.", "from_client": True,
                 "received_at": (NOW + timedelta(minutes=5)).isoformat()}]

    monkeypatch.setattr(service_module, "load_messages", read)
    assert scheduler.tick(NOW)[0]["status"] == "succeeded"
    assert scheduler.tick(NOW + timedelta(minutes=5))[0]["status"] == "succeeded"
    summary = watches.build_watch_summary(db, NOW + timedelta(minutes=5))
    assert summary["items"][0]["status"] == "matched"
    assert summary["items"][0]["match"]["source_id"] == "real-observation"
    assert scheduler.tick(NOW + timedelta(minutes=10)) == []
    assert scheduler.tick(NOW + timedelta(minutes=20)) == []
    assert calls == ["gmail", "gmail"]
