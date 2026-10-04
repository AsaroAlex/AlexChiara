"""Verify that observed Gmail replies retire earlier dashboard suggestions."""
import base64
from datetime import datetime, timedelta, timezone
import json
import time

import httpx

from app import connectors
from app.briefing import build_briefing
from app.db import Database
from app.service import Scheduler


def test_observed_gmail_reply_retires_pending_briefing_without_extra_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("FILO_AI_ENABLED", "")
    db = Database(tmp_path / "filo.sqlite3")
    now = datetime(2026, 10, 5, 8, tzinfo=timezone.utc)
    contacts = [{"name": "Cliente Alfa", "email": "alfa@example.test"}]
    service = db.get_setting("service")
    service.update(provider="gmail", status="active", priority_contacts=contacts,
                   activated_at=now.isoformat(),
                   mandate={"read": True, "draft": True, "send": False})
    db.set_setting("service", service)
    db.set_connection("gmail", "connected", "studio@example.test")
    connectors._save_tokens(db, {"access_token": "fake-test-token", "expires_at": time.time() + 3600})

    def message(source_id, sender, body, received):
        return {
            "id": source_id, "internalDate": str(int(received.timestamp() * 1000)),
            "payload": {
                "mimeType": "text/plain",
                "headers": [{"name": "From", "value": sender},
                            {"name": "Subject", "value": "Preventivo per il progetto"}],
                "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()},
            },
        }

    inbound = message("request-1", "Cliente Alfa <alfa@example.test>",
                      "Restiamo in attesa del preventivo.", now - timedelta(hours=1))
    reply = message("reply-1", "Studio <studio@example.test>",
                    "Ecco il preventivo richiesto.", now + timedelta(minutes=5))
    newer_request = message("request-2", "Cliente Alfa <alfa@example.test>",
                            "Potete inviare un preventivo aggiornato?", now + timedelta(minutes=12))
    observed = [inbound]
    requests = []

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer fake-test-token"
        if request.url.path.endswith("/messages"):
            assert request.url.params["q"] == "newer_than:7d {from:alfa@example.test}"
            assert request.url.params["maxResults"] == "25"
            return httpx.Response(200, json={"messages": [{"id": "request-1", "threadId": "thread-1"}]})
        assert request.url.path.endswith("/threads/thread-1")
        return httpx.Response(200, json={"messages": list(observed)})

    monkeypatch.setattr(connectors, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    scheduler = Scheduler(db)

    def check(when):
        queued = scheduler.enqueue(now=when)
        scheduler.tick(now=when)
        result = db.run(queued["id"])
        assert result["status"] == "succeeded"
        return result

    first = check(now)
    assert [item["source_id"] for item in first["items"]] == ["request-1"]
    assert build_briefing(db, now)["counts"]["pending"] == 1
    assert len(db.pending_drafts()) == 1

    observed.append(reply)
    second_time = now + timedelta(minutes=10)
    second = check(second_time)
    assert second["items"] == []
    briefing = build_briefing(db, second_time)
    assert briefing["status"] == "ready"
    assert briefing["priorities"] == []
    assert briefing["counts"]["pending"] == 0
    assert db.pending_drafts() == []
    # Observed outgoing content can close a suggestion without claiming that
    # the bounded Gmail sample proves any contact's absence.
    assert briefing["coverage_complete"] is False
    with db.connection() as conn:
        snapshot = conn.execute("SELECT messages FROM briefing_snapshots WHERE run_id=?", (second["id"],)).fetchone()
    assert json.loads(snapshot["messages"])[0]["from_client"] is False
    assert db.draft(first["items"][0]["id"])["status"] == "pending"

    observed.append(newer_request)
    third_time = now + timedelta(minutes=20)
    third = check(third_time)
    assert [item["source_id"] for item in third["items"]] == ["request-2"]
    assert [item["draft_id"] for item in build_briefing(db, third_time)["priorities"]] == [third["items"][0]["id"]]
    expected_pair = [("GET", "/gmail/v1/users/me/messages"), ("GET", "/gmail/v1/users/me/threads/thread-1")]
    assert requests == expected_pair * 3
