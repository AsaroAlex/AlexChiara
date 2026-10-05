"""Present saved real work without inventing a workspace or showing demo data."""
import os

from .briefing import briefing_period, build_briefing
from .db import DEFAULT_CONTACTS
from .service import public_service
from .mail_providers import REAL_MAIL_PROVIDERS


def real_data_only():
    return os.environ.get("FILO_REAL_DATA_ONLY") == "1"


def visible_service(db):
    service = public_service(db)
    if not real_data_only():
        return service
    sample_addresses = {contact["email"] for contact in DEFAULT_CONTACTS}
    service["priority_contacts"] = [contact for contact in service["priority_contacts"] if contact["email"] not in sample_addresses]
    if service.get("provider") == "demo":
        service.update(status="inactive", provider=None, mandate=None, activated_at=None, last_run_at=None, next_run_at=None)
    service["preferences"]["priority_contacts"] = service["priority_contacts"]
    return service


def workspace_data(db):
    company = db.get_setting("company")
    service = visible_service(db)
    connection = db.get_connection()
    runs = db.list_runs()
    approvals = db.pending_drafts()
    if not real_data_only():
        return company, service, connection, runs, approvals, build_briefing(db)
    if company.get("demo"):
        company = {"name": "", "sector": "", "description": "", "signature": "", "demo": False, "needs_setup": True}
    if connection.get("provider") == "demo":
        connection = {"provider": None, "status": "disconnected", "label": "Collega la tua casella email"}
    runs = [run for run in runs if not run["demo"]]
    with db.connection() as conn:
        real_runs = {row["id"] for row in conn.execute("SELECT id FROM runs WHERE demo=0")}
    approvals = [draft for draft in approvals if draft["run_id"] in real_runs]
    if connection.get("provider") in REAL_MAIL_PROVIDERS:
        briefing = build_briefing(db, service=service)
    else:
        briefing = {
            "status": "not_started", "period": briefing_period(), "last_checked_at": None,
            "stale": False, "summary": "Il riepilogo arriverà dopo il primo controllo della tua posta.",
            "priorities": [], "contacts": [],
            "counts": {"pending": 0, "high_priority": 0, "contacts_wrote": 0, "contacts_unverified": 0},
            "suggested_action": {"label": "Collega la posta", "action": "reconnect", "reason": "Collega la tua casella per vedere le richieste vere, in ordine di priorità."},
            "coverage_note": "Nessuna casella è stata letta.", "coverage_complete": False,
        }
    return company, service, connection, runs, approvals, briefing
