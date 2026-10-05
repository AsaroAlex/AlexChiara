"""A read-only morning briefing from persisted, explicitly bounded checks.

The working day ends at 18:00 in Europe/Rome. Before that hour, Monday's
briefing starts on Friday at 18:00; Saturday and Sunday use the same weekend
window. Other weekdays start at 18:00 the previous day. After 18:00 on a
weekday the next evening window begins. A recent briefing ends at its last
check, so an absence never purports to cover time that was not read.

Gmail currently returns the latest message of a bounded set of recent
conversations, rather than a complete inbox. Its missing contacts are always
"not_verified"; observed outgoing replies can close an older suggestion.
"""
from datetime import datetime, time, timedelta, timezone
import json
import re
from zoneinfo import ZoneInfo

from . import ai

ROME = ZoneInfo("Europe/Rome")
FRESH_FOR = timedelta(hours=1)


def _utc(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _stamp(value):
    try:
        return _utc(value) if value else None
    except (ValueError, TypeError, OverflowError):
        return None


def _email(value):
    return ai.sender_address(value).casefold()


def briefing_period(now=None):
    local = _utc(now).astimezone(ROME)
    day = local.date()
    weekday = local.weekday()
    if weekday in (5, 6) or (weekday == 0 and local.hour < 18):
        day -= timedelta(days=(weekday - 4) % 7)
        label = "Dal weekend"
    elif local.hour >= 18:
        label = "Da questa sera"
    else:
        day -= timedelta(days=1)
        label = "Da ieri sera"
    start = datetime.combine(day, time(18), tzinfo=ROME)
    return {"label": label, "start": start.isoformat(), "end": local.isoformat(),
            "timezone": "Europe/Rome"}


_REVISION_SCOPE = re.compile(r"(gmail|outlook|imap):[0-9]+:(.*)", re.S)


def mailbox_scope(db, provider):
    """Do not mix a former mailbox's checks with its replacement.

    The scope follows the connected account (its address label), not the
    connection revision: renewing consent for the same mailbox keeps reviewed
    suggestions and reminders, while a different account starts clean.
    """
    if provider in {"gmail", "outlook", "imap"}:
        connection = db.get_connection()
        return provider + ":mailbox:" + str(connection.get("label", ""))
    return str(provider or "disconnected")


def canonical_scope(key):
    """Map a scope saved with a connection revision to the account scope."""
    found = _REVISION_SCOPE.fullmatch(key) if isinstance(key, str) else None
    return f"{found[1]}:mailbox:{found[2]}" if found else key


def migrate_scope_keys(conn, table):
    """Additive migration of saved scopes; table names come from this code only."""
    assert table in {"briefing_snapshots", "watch_requests"}
    for (key,) in conn.execute(f"SELECT DISTINCT scope_key FROM {table}").fetchall():
        if canonical_scope(key) != key:
            conn.execute(f"UPDATE {table} SET scope_key=? WHERE scope_key=?", (canonical_scope(key), key))


def capture_snapshot(messages, contacts, provider, checked_at, scope_key):
    """Capture source observations, including messages that produce no draft.

    The synthetic mailbox exposes every generated message. The present Gmail
    adapter bounds its listing and retains only a thread's latest message, so
    absence cannot be verified there. Observed outgoing replies remain useful
    for invalidating older suggestions. No analysis result proves coverage.
    """
    checked = _utc(checked_at)
    observed = []
    for message in messages:
        sender = _email(ai._field(message, "sender"))
        received = _stamp(ai._field(message, "received_at"))
        observed.append({
            "id": str(ai._field(message, "id")),
            "thread_id": str(ai._field(message, "thread_id") or ai._field(message, "id")),
            "email": sender,
            "received_at": received.isoformat() if received else None,
            "from_client": ai._field(message, "from_client", True) is not False,
        })
    return {
        "provider": provider, "scope_key": scope_key, "checked_at": checked.isoformat(),
        "coverage_start": (checked - timedelta(days=7)).isoformat(),
        "coverage_end": checked.isoformat(), "coverage_complete": provider == "demo",
        "contacts": [{"name": str(ai._field(contact, "name")),
                      "email": _email(ai._field(contact, "email"))} for contact in contacts],
        "messages": observed,
    }


def persist_snapshot(conn, run_id, snapshot):
    conn.execute("""
        INSERT OR REPLACE INTO briefing_snapshots
        (run_id,provider,scope_key,checked_at,coverage_start,coverage_end,coverage_complete,contacts,messages)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (run_id, snapshot["provider"], snapshot["scope_key"], snapshot["checked_at"],
          snapshot["coverage_start"], snapshot["coverage_end"], int(snapshot["coverage_complete"]),
          json.dumps(snapshot["contacts"], ensure_ascii=False),
          json.dumps(snapshot["messages"], ensure_ascii=False)))


def _draft_order(item):
    minimum = datetime.min.replace(tzinfo=timezone.utc)
    created = _stamp(item.get("created_at")) or minimum
    return (_stamp(item.get("received_at")) or minimum, created, str(item.get("id", "")))


def latest_pending_drafts(rows, snapshots=()):
    """One latest revision per thread; a reviewed source never reappears.

    All statuses are considered before filtering pending work. Filtering first
    would resurrect an older draft when its newer revision was reviewed.
    """
    source_threads = {}
    for item in sorted(rows, key=_draft_order):
        provider = item.get("_provider")
        scope = (provider, item.get("_scope_key") or (provider if provider == "demo" else None))
        if item.get("thread_id"):
            source_threads[(*scope, item["source_id"])] = item["thread_id"]
    grouped = {}
    for item in rows:
        provider = item.get("_provider")
        scope = (provider, item.get("_scope_key") or (provider if provider == "demo" else None))
        key = (*scope, source_threads.get((*scope, item["source_id"]), item.get("thread_id") or item["source_id"]))
        grouped.setdefault(key, []).append(item)
    latest_sources = {}
    for snapshot in snapshots:
        for message in json.loads(snapshot["messages"]):
            received = _stamp(message.get("received_at"))
            key = (snapshot["provider"], snapshot["scope_key"],
                   message.get("thread_id") or message.get("id"))
            previous = latest_sources.get(key)
            if received and (previous is None or received > _stamp(previous.get("received_at"))):
                latest_sources[key] = message
    result = []
    for key, revisions in grouped.items():
        latest = max(revisions, key=_draft_order)
        reviewed_sources = {item["source_id"] for item in revisions if item["status"] == "approved"}
        if latest["status"] == "pending" and latest["source_id"] not in reviewed_sources:
            source = latest_sources.get(key)
            if source and source["id"] != latest["source_id"]:
                received = _stamp(latest.get("received_at"))
                if received is None or _stamp(source["received_at"]) >= received:
                    continue
            result.append(latest)
    return sorted(result, key=_draft_order, reverse=True)


def _priority(item, now):
    source = item.get("source_body") or item.get("source_excerpt", "")
    text = ai._business_text(item.get("subject", ""), source)
    urgent = re.search(r"\b(urgente|urgenti|urgent|urgency|asap|entro oggi|entro domani|"
                       r"entro stasera|entro questa sera|entro le \d|il prima possibile|"
                       r"as soon as possible)\b", text)
    if urgent and re.search(r"\b(non (?:e |è |sono )?|not )$", text[max(0, urgent.start() - 15):urgent.start()]):
        urgent = None
    received = _stamp(item.get("received_at"))
    waiting = max(timedelta(), now - received) if received else None
    old = waiting is not None and waiting >= timedelta(hours=48)
    if urgent:
        reason = f'Il messaggio contiene una richiesta esplicita: “{urgent.group(0)}”.'
    elif old:
        days = max(2, waiting.days)
        reason = f"La richiesta è in attesa da almeno {days} giorni."
    elif received:
        reason = "La richiesta è in attesa; dopo le urgenze, rispondi in ordine di arrivo."
    else:
        reason = "Richiesta in attesa; la data del messaggio non è disponibile."
    priority = "high" if urgent or old else "normal"
    kind = ai._pending_kind(text)
    next_step = ("Controlla il brief e completa la bozza con i dettagli del preventivo." if kind == "quote"
                 else "Verifica lo stato del progetto e aggiungi un aggiornamento alla bozza." if kind == "project"
                 else "Rileggi la richiesta e verifica la risposta già preparata.")
    return {
        "draft_id": item.get("id"), "run_id": item.get("run_id"),
        "client": item["client"], "email": item.get("email"), "subject": item["subject"],
        "source_excerpt": item.get("source_excerpt", ""), "draft": item["draft"],
        "received_at": item.get("received_at"), "priority": priority,
        "priority_label": "Da vedere per prima" if priority == "high" else "A seguire",
        "reason": reason, "next_step": next_step,
        "_urgent": bool(urgent), "_received": received,
    }


def _priorities(rows, now):
    values = [_priority(row, now) for row in rows]
    values.sort(key=lambda value: (value["priority"] != "high", not value["_urgent"],
                                  value["_received"] or datetime.max.replace(tzinfo=timezone.utc)))
    return [{key: value for key, value in item.items() if not key.startswith("_")}
            for item in values]


def _contact_statuses(contacts, snapshots, period, fresh):
    start, end = _utc(period["start"]), _utc(period["end"])
    latest = snapshots[0] if snapshots else None
    result = []
    for contact in contacts:
        email = _email(contact.get("email"))
        received = []
        last_known = []
        for snapshot in snapshots:
            for message in json.loads(snapshot["messages"]):
                stamp = _stamp(message.get("received_at"))
                if message.get("from_client") and message.get("email") == email and stamp and stamp <= end:
                    last_known.append(stamp)
                    if start <= stamp:
                        received.append(stamp)
        covered_contacts = {_email(value.get("email")) for value in json.loads(latest["contacts"])} if latest else set()
        complete = bool(fresh and latest and latest["coverage_complete"] and email in covered_contacts
                        and _utc(latest["coverage_start"]) <= start
                        and _utc(latest["coverage_end"]) >= end)
        status = "wrote" if received else "no_messages" if complete else "not_verified"
        result.append({"name": contact.get("name") or email, "email": email, "status": status,
                       "last_received_at": max(last_known).isoformat() if last_known else None})
    return result


def build_briefing(db, now=None, *, service=None):
    """Read existing checks only; never contact a provider or enqueue work."""
    now = _utc(now)
    service = db.get_setting("service") if service is None else service
    connection = db.get_connection()
    provider = connection.get("provider") or service.get("provider")
    scope = mailbox_scope(db, provider)
    with db.connection() as conn:
        snapshots = [dict(row) for row in conn.execute("""
            SELECT * FROM briefing_snapshots WHERE provider=? AND scope_key=?
            ORDER BY checked_at DESC,run_id DESC
        """, (provider, scope))]
        rows = [dict(row) for row in conn.execute("""
            SELECT d.*,r.provider AS _provider,s.scope_key AS _scope_key
            FROM drafts d JOIN runs r ON r.id=d.run_id
            LEFT JOIN briefing_snapshots s ON s.run_id=r.id
            WHERE r.provider=? AND (s.scope_key=? OR (s.scope_key IS NULL AND r.provider='demo'))
        """, (provider, scope))]
        # Pre-upgrade Gmail drafts have no mailbox identity. Preserve their
        # existence without presenting them as observations of this account.
        legacy_rows = [dict(row) for row in conn.execute("""
            SELECT d.*,r.provider AS _provider,NULL AS _scope_key
            FROM drafts d JOIN runs r ON r.id=d.run_id
            LEFT JOIN briefing_snapshots s ON s.run_id=r.id
            WHERE r.provider='gmail' AND s.run_id IS NULL
        """)] if provider == "gmail" else []
        # Runs without snapshots are valid during a first check or after an old
        # installation is upgraded. Older account activity must not override a
        # newer account's empty state.
        activation = service.get("activated_at") or now.isoformat()
        run = conn.execute("""
            SELECT r.* FROM runs r LEFT JOIN briefing_snapshots s ON s.run_id=r.id
            WHERE r.provider=? AND (s.scope_key=? OR (s.scope_key IS NULL AND
                  (r.provider='demo' OR r.created_at>=?)))
            ORDER BY r.created_at DESC,r.id DESC LIMIT 1
        """, (provider, scope, activation)).fetchone()
    latest = snapshots[0] if snapshots else None
    legacy_pending_count = len(latest_pending_drafts(legacy_rows))
    legacy_verification = bool(not latest and legacy_pending_count)
    checked = _stamp(latest["checked_at"]) if latest else None
    period = briefing_period(now)
    fresh = bool(checked and timedelta() <= now - checked <= FRESH_FOR
                 and checked >= _utc(period["start"]))
    if fresh:
        period["end"] = checked.astimezone(ROME).isoformat()
    stale = bool((checked and not fresh) or legacy_verification)
    contacts = _contact_statuses(service.get("priority_contacts", []), snapshots, period, fresh)
    allowed = {_email(contact.get("email")) for contact in service.get("priority_contacts", [])}
    names = {contact.get("name"): _email(contact.get("email")) for contact in service.get("priority_contacts", [])}
    pending = latest_pending_drafts(rows, snapshots)
    visible = []
    for item in pending:
        email = item.get("email") or names.get(item.get("client"))
        if email not in allowed:
            continue
        visible.append({**item, "email": email})
    priorities = _priorities(visible, now)
    counts = {"pending": len(priorities), "high_priority": sum(item["priority"] == "high" for item in priorities),
              "contacts_wrote": sum(item["status"] == "wrote" for item in contacts),
              "contacts_unverified": sum(item["status"] == "not_verified" for item in contacts)}
    active = run and run["status"] in ("queued", "running", "retry_wait") and service.get("status") == "active"
    failed = run and run["status"] == "failed" and (checked is None or _utc(run["created_at"]) >= checked)
    status = "checking" if active else "error" if failed else "stale" if stale else "ready" if latest else "not_started"
    if status == "not_started":
        summary = "Il tuo riepilogo è pronto a partire. Configura i contatti e attiva il primo controllo."
    elif status == "checking":
        summary = "Sto controllando la posta dei tuoi contatti. Il riepilogo si aggiornerà appena il controllo termina."
    elif status == "error":
        summary = "L'ultimo controllo non è riuscito. Il riepilogo conserva solo i risultati già verificati."
    elif legacy_verification:
        summary = ("Ci sono bozze dei controlli precedenti nello storico. Aggiorna il riepilogo "
                   "per verificare i messaggi della tua casella: le bozze precedenti non sono "
                   "ancora associate a questo controllo.")
    elif stale:
        summary = "Il riepilogo va aggiornato: le richieste sono salvate, ma gli ultimi arrivi non sono ancora verificati."
    elif priorities:
        summary = f"{len(priorities)} risposte da verificare. Parti da {priorities[0]['client']}: la bozza è già pronta."
    else:
        summary = "Nessuna richiesta da verificare nei messaggi analizzati. Qui trovi chi ha scritto fino all'ultimo controllo."
    if connection.get("status") != "connected":
        suggestion = {"label": "Collega la tua posta", "action": "reconnect", "reason": "Serve una casella collegata per preparare il tuo riepilogo."}
    elif service.get("status") == "paused":
        suggestion = {"label": "Riprendi i controlli della posta", "action": "resume", "reason": "Il servizio è in pausa: riprendilo per tornare a controllare la posta."}
    elif service.get("status") != "active":
        suggestion = {"label": "Attiva la segreteria email", "action": "open-wizard", "reason": "Scegli i contatti da seguire e autorizza il controllo."}
    elif status in ("stale", "error", "not_started"):
        suggestion = {"label": "Aggiorna il riepilogo", "action": "run", "reason": "Un nuovo controllo verifica i messaggi più recenti."}
    elif active:
        suggestion = {"label": "Controllo in corso", "action": "wait", "reason": "Ti mostrerò il riepilogo appena pronto."}
    elif priorities:
        suggestion = {"label": f"Verifica la risposta per {priorities[0]['client']}", "action": "review-priority", "reason": priorities[0]["reason"], "draft_id": priorities[0]["draft_id"]}
    else:
        suggestion = {"label": "La posta è sotto controllo", "action": "none", "reason": "Il prossimo controllo segue l'orario che hai scelto."}
    return {"generated_at": now.isoformat(), "period": period,
            "last_checked_at": checked.isoformat() if checked else None, "stale": stale,
            "status": status, "summary": summary, "priorities": priorities, "contacts": contacts,
            "counts": counts, "suggested_action": suggestion,
            "legacy_pending_count": legacy_pending_count,
            "coverage_complete": bool(latest and latest["coverage_complete"]),
            "coverage_note": ("Casella dimostrativa: tutti i messaggi fittizi sono inclusi." if provider == "demo"
                              else "Posta: controllo limitato ai messaggi recenti. L'assenza di messaggi non è verificata." if provider in {"gmail", "outlook", "imap"}
                              else "Collega una casella per verificare i contatti."),
            "latest_error": run["error"] if failed else None}


def build_example_briefing(db, now=None):
    """An explicitly fictional preview, separate from actual stored activity."""
    from .demo import generate_messages

    now = _utc(now)
    contacts = [{"name": "Giulia Conti", "email": "giulia.conti@example.com"},
                {"name": "Marco Bianchi", "email": "marco.bianchi@example.com"},
                {"name": "Sara Rossi", "email": "sara.rossi@example.com"}]
    messages = generate_messages(contacts)
    # Rebase only this fictional preview to its supplied clock; never redate a
    # persisted real or demo check.
    period = briefing_period(now)
    window = now - _utc(period["start"])
    for index, message in enumerate(messages):
        message["received_at"] = (now - window * ((index + 1) / (len(messages) + 1))).isoformat()
    messages[0]["body"] += "\nLa richiesta è urgente."
    analyzed = ai.analyze_messages(messages, db.get_setting("company"), contacts)
    sources = {message["id"]: message for message in messages}
    items = [{**item, "id": None, "run_id": None,
              "email": sources[item["source_id"]]["sender"],
              "received_at": sources[item["source_id"]]["received_at"],
              "source_body": sources[item["source_id"]]["body"]} for item in analyzed["items"]]
    priorities = _priorities(items, now)
    example_contacts = [{**contact, "status": "wrote", "last_received_at": messages[index]["received_at"]}
                        for index, contact in enumerate(contacts)]
    example_contacts.append({"name": "Paolo Ferri", "email": "paolo.ferri@example.com",
                             "status": "no_messages", "last_received_at": None})
    return {"preview": True, "generated_at": now.isoformat(), "period": period,
            "last_checked_at": None, "stale": False, "status": "example",
            "summary": "Un esempio del tuo riepilogo: 3 risposte pronte da verificare, in ordine di priorità.",
            "priorities": priorities,
            "contacts": example_contacts,
            "counts": {"pending": len(priorities), "high_priority": sum(item["priority"] == "high" for item in priorities),
                       "contacts_wrote": len(contacts), "contacts_unverified": 0},
            "legacy_pending_count": 0,
            "suggested_action": {"label": "Prepara il tuo riepilogo", "action": "open-wizard",
                                 "reason": "Collega la posta e scegli i contatti da seguire."},
            "coverage_complete": False, "coverage_note": "Esempio con dati fittizi. Nessun controllo della tua posta.",
            "latest_error": None}
