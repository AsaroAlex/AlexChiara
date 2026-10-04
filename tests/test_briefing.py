"""Verify the morning briefing against source evidence and existing databases."""
from datetime import datetime, timedelta, timezone
import pytest

from app.briefing import briefing_period, build_briefing, build_example_briefing
from app.db import Database
from app.service import Scheduler
import app.service as service_module

UTC = timezone.utc
NOW = datetime(2026, 10, 5, 8, tzinfo=UTC)  # Monday, 10:00 in Rome.
CONTACTS = [{"name": "Cliente Alfa", "email": "alfa@example.test"},
            {"name": "Cliente Beta", "email": "beta@example.test"}]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    value = Database(tmp_path / "filo.db")
    service = value.get_setting("service")
    service.update(provider="demo", status="active", priority_contacts=CONTACTS,
                   activated_at=NOW.isoformat(),
                   mandate={"read": True, "draft": True, "send": False})
    value.set_setting("service", service)
    value.set_connection("demo", "connected")
    return value


def message(source="request-1", thread="thread-1", sender="alfa@example.test", *,
            received=NOW - timedelta(hours=2), body="Restiamo in attesa del preventivo.",
            subject="Preventivo per il progetto", from_client=True):
    return {"id": source, "thread_id": thread, "sender": sender,
            "received_at": received.isoformat(), "body": body, "subject": subject,
            "from_client": from_client}


def check(db, monkeypatch, messages, now=NOW):
    monkeypatch.setattr(service_module, "load_messages", lambda *_: messages)
    scheduler = Scheduler(db)
    run = scheduler.enqueue(now=now)
    scheduler.tick(now=now)
    assert db.run(run["id"])["status"] == "succeeded"
    return db.run(run["id"])


def test_every_successful_empty_check_has_a_durable_snapshot(db, monkeypatch):
    run = check(db, monkeypatch, [])
    assert run["items"] == []
    restarted = Database(db.path)
    briefing = build_briefing(restarted, NOW + timedelta(minutes=15))
    assert briefing["status"] == "ready"
    assert briefing["last_checked_at"] == NOW.isoformat()
    assert briefing["counts"]["pending"] == 0
    assert {contact["status"] for contact in briefing["contacts"]} == {"no_messages"}
    assert datetime.fromisoformat(briefing["period"]["end"]) == NOW
    with restarted.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM briefing_snapshots").fetchone()[0] == 1


def test_source_observation_without_a_draft_proves_contact_wrote(db, monkeypatch):
    run = check(db, monkeypatch, [message(body="Grazie, il materiale va bene.")])
    assert run["items"] == []
    briefing = build_briefing(db, NOW)
    assert briefing["contacts"][0]["status"] == "wrote"
    assert briefing["contacts"][0]["last_received_at"] == message()["received_at"]
    assert briefing["contacts"][1]["status"] == "no_messages"
    assert briefing["counts"] == {"pending": 0, "high_priority": 0,
                                   "contacts_wrote": 1, "contacts_unverified": 0}


def test_stale_snapshot_never_claims_an_absence(db, monkeypatch):
    check(db, monkeypatch, [message(body="Grazie per il materiale.")])
    briefing = build_briefing(db, NOW + timedelta(hours=2))
    assert briefing["stale"] is True
    assert briefing["status"] == "stale"
    assert [item["status"] for item in briefing["contacts"]] == ["wrote", "not_verified"]
    assert briefing["suggested_action"]["action"] == "run"


def test_incomplete_snapshot_and_new_contacts_do_not_prove_absence(db, monkeypatch):
    check(db, monkeypatch, [])
    with db.connection() as conn:
        conn.execute("UPDATE briefing_snapshots SET coverage_complete=0")
    assert {item["status"] for item in build_briefing(db, NOW)["contacts"]} == {"not_verified"}
    with db.connection() as conn:
        conn.execute("UPDATE briefing_snapshots SET coverage_complete=1")
    service = db.get_setting("service")
    service["priority_contacts"] = [*CONTACTS, {"name": "Nuovo cliente", "email": "new@example.test"}]
    db.set_setting("service", service)
    assert [item["status"] for item in build_briefing(db, NOW)["contacts"]] == ["no_messages", "no_messages", "not_verified"]


def test_gmail_bounded_unanswered_results_never_prove_absence(db, monkeypatch):
    service = db.get_setting("service")
    service["provider"] = "gmail"
    db.set_setting("service", service)
    db.set_connection("gmail", "connected", "studio@example.test")
    check(db, monkeypatch, [message(body="Grazie.")])
    briefing = build_briefing(db, NOW)
    assert briefing["coverage_complete"] is False
    assert [item["status"] for item in briefing["contacts"]] == ["wrote", "not_verified"]


def test_getter_does_not_read_mail_enqueue_work_or_write(db, monkeypatch):
    check(db, monkeypatch, [message()])
    def forbidden(*_):
        pytest.fail("A dashboard read must never access the mailbox")
    monkeypatch.setattr(service_module, "load_messages", forbidden)
    with db.connection() as conn:
        before = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in ("runs", "drafts", "briefing_snapshots", "actions")}
    for _ in range(3):
        assert build_briefing(db, NOW)["counts"]["pending"] == 1
    with db.connection() as conn:
        assert before == {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                          for table in before}


def test_priority_is_explicit_and_received_at_survives_restart(db, monkeypatch):
    messages = [
        message(source="ordinary", thread="ordinary", received=NOW - timedelta(hours=1)),
        message(source="old", thread="old", received=NOW - timedelta(days=3)),
        message(source="urgent", thread="urgent", sender="beta@example.test",
                body="Il preventivo è urgente, restiamo in attesa.", received=NOW - timedelta(minutes=10)),
    ]
    run = check(db, monkeypatch, messages)
    sources = {item["id"]: item for item in messages}
    for draft in run["items"]:
        assert draft["received_at"] == sources[draft["source_id"]]["received_at"]
        assert draft["source_body"] == sources[draft["source_id"]]["body"]
        assert draft["email"] == sources[draft["source_id"]]["sender"]
    items = build_briefing(Database(db.path), NOW)["priorities"]
    assert [item["subject"] for item in items] == [messages[2]["subject"], messages[1]["subject"], messages[0]["subject"]]
    assert items[0]["email"] == "beta@example.test"
    assert "urgente" in items[0]["reason"]
    assert "3 giorni" in items[1]["reason"]
    assert [item["priority"] for item in items] == ["high", "high", "normal"]
    assert build_briefing(db, NOW)["suggested_action"]["draft_id"] == items[0]["draft_id"]


def test_next_steps_follow_the_source_request_type(db, monkeypatch):
    check(db, monkeypatch, [
        message(source="quote", thread="quote"),
        message(source="project", thread="project", subject="Aggiornamento progetto",
                body="Restiamo in attesa di un aggiornamento sul progetto."),
    ])
    priorities = build_briefing(db, NOW)["priorities"]
    by_subject = {item["subject"]: item["next_step"] for item in priorities}
    assert "brief" in by_subject["Preventivo per il progetto"]
    assert "preventivo" in by_subject["Preventivo per il progetto"]
    assert "stato del progetto" in by_subject["Aggiornamento progetto"]


def test_coverage_must_include_the_report_window_and_last_known_arrival_is_retained(db, monkeypatch):
    older = message(received=NOW - timedelta(days=4), body="Grazie.")
    check(db, monkeypatch, [older])
    briefing = build_briefing(db, NOW)
    assert briefing["contacts"][0]["status"] == "no_messages"
    assert briefing["contacts"][0]["last_received_at"] == older["received_at"]
    with db.connection() as conn:
        conn.execute("UPDATE briefing_snapshots SET coverage_start=?", ((NOW - timedelta(hours=1)).isoformat(),))
    assert {item["status"] for item in build_briefing(db, NOW)["contacts"]} == {"not_verified"}


def test_unconnected_briefing_does_not_describe_a_gmail_check(tmp_path):
    briefing = build_briefing(Database(tmp_path / "new.db"), NOW)
    assert briefing["status"] == "not_started"
    assert "Gmail" not in briefing["coverage_note"]
    assert briefing["suggested_action"]["action"] == "reconnect"


def test_priority_does_not_treat_ai_draft_or_quoted_history_as_source_urgency(db, monkeypatch):
    run = check(db, monkeypatch, [message(body="Restiamo in attesa del preventivo.\n> urgente entro oggi")])
    with db.connection() as conn:
        conn.execute("UPDATE drafts SET draft='Urgente entro oggi',reason='Urgentissimo' WHERE id=?", (run["items"][0]["id"],))
    assert build_briefing(db, NOW)["priorities"][0]["priority"] == "normal"
    check(db, monkeypatch, [message(source="not-urgent", thread="not-urgent", body="Il preventivo non è urgente, ma restiamo in attesa.")], NOW + timedelta(minutes=10))
    assert all(item["priority"] == "normal" for item in build_briefing(db, NOW + timedelta(minutes=10))["priorities"])


def test_repeated_checks_keep_latest_revision_and_do_not_resurrect_reviewed_source(db, monkeypatch):
    first = check(db, monkeypatch, [message()])
    second = check(db, monkeypatch, [message()], NOW + timedelta(minutes=10))
    assert len(db.pending_drafts()) == 1
    assert db.pending_drafts()[0]["id"] == second["items"][0]["id"]
    with db.connection() as conn:
        conn.execute("UPDATE drafts SET status='approved',approved_at=? WHERE id=?", (NOW.isoformat(), first["items"][0]["id"]))
    assert db.pending_drafts() == []
    check(db, monkeypatch, [message()], NOW + timedelta(minutes=20))
    assert build_briefing(db, NOW + timedelta(minutes=20))["priorities"] == []
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM briefing_snapshots").fetchone()[0] == 3


def test_reviewed_latest_message_hides_older_thread_but_new_message_is_pending(db, monkeypatch):
    check(db, monkeypatch, [message(source="older", received=NOW - timedelta(hours=2))])
    newer = check(db, monkeypatch, [message(source="newer", received=NOW - timedelta(hours=1))], NOW + timedelta(minutes=10))
    assert len(db.pending_drafts()) == 1
    assert db.pending_drafts()[0]["source_id"] == "newer"
    with db.connection() as conn:
        conn.execute("UPDATE drafts SET status='approved' WHERE id=?", (newer["items"][0]["id"],))
    assert db.pending_drafts() == []
    check(db, monkeypatch, [message(source="new-request", received=NOW + timedelta(minutes=15))], NOW + timedelta(minutes=20))
    assert len(db.pending_drafts()) == 1
    assert db.pending_drafts()[0]["source_id"] == "new-request"


def test_a_known_later_thread_message_invalidates_an_old_suggestion(db, monkeypatch):
    check(db, monkeypatch, [message(source="request")])
    check(db, monkeypatch, [message(source="answered", sender="studio@example.test", from_client=False,
                                    received=NOW + timedelta(minutes=5), body="Ecco il preventivo.")],
          NOW + timedelta(minutes=10))
    assert db.pending_drafts() == []
    assert build_briefing(db, NOW + timedelta(minutes=10))["priorities"] == []


def test_additive_migration_preserves_old_approvals_and_unknown_dates(db, monkeypatch):
    run = check(db, monkeypatch, [message()])
    draft_id = run["items"][0]["id"]
    with db.connection() as conn:
        conn.execute("UPDATE drafts SET status='approved',approved_at=? WHERE id=?", (NOW.isoformat(), draft_id))
        previous = dict(conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone())
        conn.execute("DROP TABLE briefing_snapshots")
        for column in ("received_at", "email", "source_body"):
            conn.execute(f"ALTER TABLE drafts DROP COLUMN {column}")
    migrated = Database(db.path)
    saved = migrated.draft(draft_id)
    for key in set(previous) - {"received_at", "email", "source_body"}:
        assert saved[key] == previous[key]
    assert saved["received_at"] is None
    assert saved["email"] is None
    assert saved["source_body"] == ""
    assert migrated.pending_drafts() == []
    briefing = build_briefing(migrated, NOW)
    assert briefing["status"] == "not_started"
    assert briefing["last_checked_at"] is None
    assert {item["status"] for item in briefing["contacts"]} == {"not_verified"}


def test_unknown_thread_migration_deduplicates_same_source(db, monkeypatch):
    first = check(db, monkeypatch, [message()])
    with db.connection() as conn:
        conn.execute("UPDATE drafts SET thread_id=NULL,received_at=NULL WHERE id=?", (first["items"][0]["id"],))
        conn.execute("DELETE FROM briefing_snapshots")
    latest = check(db, monkeypatch, [message()], NOW + timedelta(minutes=10))
    assert len(db.pending_drafts()) == 1
    assert db.pending_drafts()[0]["id"] == latest["items"][0]["id"]


def test_mailbox_replacement_does_not_reuse_previous_mailbox_evidence(db, monkeypatch):
    service = db.get_setting("service")
    service["provider"] = "gmail"
    db.set_setting("service", service)
    db.set_connection("gmail", "connected", "previous@example.test")
    check(db, monkeypatch, [message()])
    db.set_setting("gmail_revision", 1)
    db.set_connection("gmail", "connected", "new@example.test")
    service.update(status="inactive", activated_at=None, mandate=None)
    db.set_setting("service", service)
    briefing = build_briefing(db, NOW + timedelta(minutes=10))
    assert briefing["status"] == "not_started"
    assert briefing["priorities"] == []
    assert briefing["last_checked_at"] is None
    assert {item["status"] for item in briefing["contacts"]} == {"not_verified"}


def test_legacy_gmail_pending_drafts_require_verification_without_fiction_or_invented_observations(db, monkeypatch):
    service = db.get_setting("service")
    service["provider"] = "gmail"
    db.set_setting("service", service)
    db.set_connection("gmail", "connected", "studio@example.test")
    run = check(db, monkeypatch, [message()])
    draft_id = run["items"][0]["id"]
    with db.connection() as conn:
        conn.execute("DROP TABLE briefing_snapshots")
        for column in ("received_at", "email", "source_body"):
            conn.execute(f"ALTER TABLE drafts DROP COLUMN {column}")
    migrated = Database(db.path)
    before = migrated.draft(draft_id)
    briefing = build_briefing(migrated, NOW)
    assert briefing["status"] == "stale"
    assert briefing["stale"] is True
    assert briefing["legacy_pending_count"] == 1
    assert "bozze dei controlli precedenti nello storico" in briefing["summary"]
    assert briefing["suggested_action"]["action"] == "run"
    assert briefing["priorities"] == []
    assert briefing["counts"]["pending"] == 0
    assert briefing["last_checked_at"] is None
    assert {item["status"] for item in briefing["contacts"]} == {"not_verified"}
    assert {item["last_received_at"] for item in briefing["contacts"]} == {None}
    assert migrated.draft(draft_id) == before
    assert before["received_at"] is None
    assert before["email"] is None


def test_legacy_gmail_drafts_are_not_assigned_to_a_replacement_mailbox(db, monkeypatch):
    service = db.get_setting("service")
    service["provider"] = "gmail"
    db.set_setting("service", service)
    db.set_connection("gmail", "connected", "old@example.test")
    check(db, monkeypatch, [message()])
    with db.connection() as conn:
        conn.execute("DELETE FROM briefing_snapshots")
    db.set_setting("gmail_revision", 1)
    db.set_connection("gmail", "connected", "replacement@example.test")
    briefing = build_briefing(db, NOW + timedelta(minutes=5))
    assert briefing["status"] == "stale"
    assert briefing["legacy_pending_count"] == 1
    assert briefing["priorities"] == []
    assert briefing["counts"]["contacts_wrote"] == 0
    db.set_connection("gmail", "disconnected", "replacement@example.test")
    assert build_briefing(db, NOW + timedelta(minutes=5))["suggested_action"]["action"] == "reconnect"


def test_current_gmail_snapshot_precedes_legacy_warning_and_empty_gmail_starts_normally(db, monkeypatch):
    service = db.get_setting("service")
    service["provider"] = "gmail"
    db.set_setting("service", service)
    db.set_connection("gmail", "connected", "studio@example.test")
    empty = build_briefing(db, NOW)
    assert empty["status"] == "not_started"
    assert empty["legacy_pending_count"] == 0
    check(db, monkeypatch, [message(source="legacy", thread="legacy")])
    with db.connection() as conn:
        conn.execute("DELETE FROM briefing_snapshots")
    check(db, monkeypatch, [message(source="current", thread="current")], NOW + timedelta(minutes=10))
    briefing = build_briefing(db, NOW + timedelta(minutes=10))
    assert briefing["status"] == "ready"
    assert briefing["stale"] is False
    assert briefing["legacy_pending_count"] == 1
    assert [item["draft_id"] for item in briefing["priorities"]] == [db.pending_drafts()[0]["id"]]
    assert len(briefing["priorities"]) == 1
    assert briefing["priorities"][0]["run_id"] != db.list_runs()[-1]["id"]


@pytest.mark.parametrize("clock,label,start", [
    ("2026-10-05T08:00:00+00:00", "Dal weekend", "2026-10-02T18:00:00+02:00"),
    ("2026-10-04T08:00:00+00:00", "Dal weekend", "2026-10-02T18:00:00+02:00"),
    ("2026-10-06T07:00:00+00:00", "Da ieri sera", "2026-10-05T18:00:00+02:00"),
    ("2026-10-06T18:00:00+00:00", "Da questa sera", "2026-10-06T18:00:00+02:00"),
    ("2026-10-26T08:00:00+00:00", "Dal weekend", "2026-10-23T18:00:00+02:00"),
])
def test_rome_workday_and_weekend_windows_include_dst(clock, label, start):
    period = briefing_period(clock)
    assert period["timezone"] == "Europe/Rome"
    assert period["label"] == label
    assert period["start"] == start
    assert datetime.fromisoformat(period["end"]) == datetime.fromisoformat(clock)


def test_example_is_fictional_separate_and_does_not_become_a_successful_check(db, monkeypatch):
    def forbidden(*_):
        pytest.fail("The fictional example must not contact a mailbox")
    monkeypatch.setattr(service_module, "load_messages", forbidden)
    actual = build_briefing(db, NOW)
    example = build_example_briefing(db, NOW)
    assert actual["status"] == "not_started"
    assert example["preview"] is True
    assert example["last_checked_at"] is None
    assert example["priorities"]
    assert [item["status"] for item in example["contacts"]] == ["wrote", "wrote", "wrote", "no_messages"]
    assert example["counts"]["contacts_wrote"] == 3
    assert all(item["draft_id"] is None and item["run_id"] is None for item in example["priorities"])
    assert "fittizi" in example["coverage_note"]
    assert build_briefing(db, NOW) == actual
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM briefing_snapshots").fetchone()[0] == 0


def test_queued_and_failed_checks_have_explicit_status_without_false_absences(db):
    scheduler = Scheduler(db)
    queued = scheduler.enqueue(now=NOW)
    assert build_briefing(db, NOW)["status"] == "checking"
    with db.connection() as conn:
        conn.execute("UPDATE runs SET status='failed',error='Connessione non disponibile' WHERE id=?", (queued["id"],))
    failed = build_briefing(db, NOW)
    assert failed["status"] == "error"
    assert failed["latest_error"] == "Connessione non disponibile"
    assert {item["status"] for item in failed["contacts"]} == {"not_verified"}
