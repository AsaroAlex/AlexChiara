"""A completed check cannot restore consent revoked by a mailbox callback."""
from datetime import datetime, timezone
import json
import threading

import pytest

from app import service as service_module
from app.connectors import ConnectorError, _invalidate_in_transaction
from app.db import Database
from app.service import Scheduler


NOW = datetime(2026, 10, 5, 9, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize("provider", ["gmail", "outlook", "imap"])
def test_finishing_check_does_not_restore_old_mandate_after_mailbox_replacement(tmp_path, monkeypatch, provider):
    db = Database(tmp_path / "mail.sqlite3")
    db.set_setting("company", {"name": "Studio reale", "demo": False})
    contact = {"name": "Cliente Alfa", "email": "alfa@example.test"}
    settings = db.get_setting("service")
    settings.update(provider=provider, status="active", priority_contacts=[contact],
                    hour=23, minute=59, timezone="Europe/Rome", activated_at=NOW.isoformat(),
                    mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", settings)
    db.set_connection(provider, "connected", "previous@example.test")
    monkeypatch.setattr(service_module, "load_messages", lambda *_: [])
    scheduler = Scheduler(db)

    success_pending = threading.Event()
    allow_replacement = threading.Event()
    replaced = threading.Event()
    failures = []

    def replacement_callback():
        if not allow_replacement.wait(timeout=3):
            failures.append("Replacement callback was never released")
            return
        try:
            with db.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                _invalidate_in_transaction(conn, "La casella è stata sostituita.")
                updates = {"gmail_revision": 1,
                           "connection": {"provider": provider, "status": "connected",
                                          "label": "replacement@example.test"}}
                for key, value in updates.items():
                    conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                                 (key, json.dumps(value)))
            replaced.set()
        except BaseException as exc:
            failures.append(exc)

    callback = threading.Thread(target=replacement_callback, daemon=True)
    callback.start()
    original_log = scheduler._log_in_transaction

    def completed_log(conn, action, *args):
        original_log(conn, action, *args)
        if action == "run_succeeded":
            success_pending.set()

    monkeypatch.setattr(scheduler, "_log_in_transaction", completed_log)
    original_read = db.get_setting

    def concurrent_service_read(key, default=None):
        value = original_read(key, default)
        # Reproduce the vulnerable interleaving if completion performs a later
        # service read/modify/write: the callback revokes consent after that
        # read, and must not be overwritten by the value already returned.
        if key == "service" and success_pending.is_set() and not replaced.is_set():
            allow_replacement.set()
            callback.join(timeout=3)
            assert replaced.is_set(), failures
        return value

    monkeypatch.setattr(db, "get_setting", concurrent_service_read)
    run = scheduler.enqueue(now=NOW)
    scheduler.tick(NOW)
    # With atomic completion there is no later service write. The callback can
    # take the SQLite lock after completion commits and revoke the old consent.
    allow_replacement.set()
    callback.join(timeout=3)
    assert not callback.is_alive()
    assert failures == []
    assert replaced.is_set()
    assert db.run(run["id"])["status"] == "succeeded"
    assert db.get_connection()["label"] == "replacement@example.test"
    settings = db.get_setting("service")
    assert settings["last_run_at"] == NOW.isoformat()
    assert settings["status"] == "inactive"
    assert settings["mandate"] is None
    with pytest.raises(ConnectorError, match="mandato attivo"):
        scheduler._guard(provider)
