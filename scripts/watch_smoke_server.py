"""Disposable localhost fixture for scripts/watch-smoke.cjs; never deploy this.

Start: .venv/bin/python scripts/watch_smoke_server.py
Then: FILO_PORT=8016 node scripts/watch-smoke.cjs
"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
os.environ["FILO_REAL_DATA_ONLY"] = "1"
os.environ["FILO_ACCESS_PASSWORD"] = ""
for variable in ("FILO_GOOGLE_CLIENT_ID", "FILO_GOOGLE_CLIENT_SECRET", "FILO_GOOGLE_REDIRECT_URI", "FILO_AI_ENABLED"):
    os.environ.pop(variable, None)

import app.main as main_module
import app.service as service_module
import app.connectors as connector_module
from app.briefing import capture_snapshot, mailbox_scope, persist_snapshot
import uvicorn

provider_calls = []
def forbidden_provider(*args, **kwargs):
    provider_calls.append("attempted")
    raise RuntimeError("External mailbox calls are forbidden in the smoke fixture")
main_module.load_messages = forbidden_provider
service_module.load_messages = forbidden_provider
connector_module.load_messages = forbidden_provider
connector_module._gmail_messages = forbidden_provider

scratch_root = PROJECT_ROOT / ".runtime"
scratch_root.mkdir(parents=True, exist_ok=True, mode=0o700)
scratch = tempfile.TemporaryDirectory(prefix="watch-smoke-fixture-", dir=scratch_root)
runtime = scratch.name
app = main_module.create_app(data_dir=runtime, start_worker=False)
db = app.state.db
contacts = [{"name": "Elena Prova", "email": "elena@watch-smoke.test"},
            {"name": "Luca Prova", "email": "luca@watch-smoke.test"}]
db.set_setting("company", {"name": "Filo Watch Smoke Fixture", "sector": "Test locale", "description": "Dati fixture isolati", "signature": "Prova", "demo": False})
service = db.get_setting("service")
service.update(provider="gmail", status="active", priority_contacts=contacts,
               activated_at=datetime.now(timezone.utc).isoformat(),
               mandate={"read": True, "draft": True, "send": False})
db.set_setting("service", service)
db.set_connection("gmail", "connected", "casella@watch-smoke.test")

@app.get("/__fixture__/status")
def fixture_status():
    return {"fixture": "Filo Watch Smoke Fixture", "provider_calls": provider_calls, "worker": "disabled", "data_dir": runtime}

@app.post("/__fixture__/incoming")
def fixture_incoming():
    now = datetime.now(timezone.utc)
    messages = [{"id": "watch-incoming", "thread_id": "watch-thread/fixture", "sender": "elena@watch-smoke.test",
                 "received_at": now.isoformat(), "from_client": True},
                {"id": "priority-source", "thread_id": "priority-thread", "sender": "luca@watch-smoke.test",
                 "received_at": (now-timedelta(minutes=2)).isoformat(), "from_client": True}]
    run_id = uuid4().hex
    snapshot = capture_snapshot(messages, contacts, "gmail", now, mailbox_scope(db, "gmail"))
    with db.connection() as conn:
        conn.execute("""INSERT INTO runs(id,trigger,provider,status,created_at,started_at,finished_at,preferences,company)
                        VALUES (?,'manual','gmail','succeeded',?,?,?,'{}','{}')""", (run_id,now.isoformat(),now.isoformat(),now.isoformat()))
        persist_snapshot(conn, run_id, snapshot)
        conn.execute("""INSERT INTO drafts(id,run_id,source_id,client,subject,reason,draft,thread_id,source_excerpt,received_at,email,source_body,created_at)
                        VALUES (?,?,'priority-source','Luca Prova','Richiesta urgente','Il messaggio contiene urgente','Bozza fixture','priority-thread','Preventivo urgente',?,'luca@watch-smoke.test','Preventivo urgente',?)""",
                     (uuid4().hex,run_id,messages[1]["received_at"],now.isoformat()))
    return {"source_id": "watch-incoming", "thread_id": "watch-thread/fixture", "received_at": now.isoformat()}

if __name__ == "__main__":
    print("Watch smoke fixture:", runtime, flush=True)
    try:
        uvicorn.run(app, host="127.0.0.1", port=8016, log_level="warning")
    finally:
        scratch.cleanup()
