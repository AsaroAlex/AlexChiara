"""Persistent, browser-independent daily scheduler with bounded attempts.

The server must run for timers to advance. Restarting it recovers one most-recent
missed daily slot and interrupted work; SQLite enforces slot uniqueness.
"""
from datetime import datetime, time as dt_time, timedelta, timezone
import hashlib
import json
import logging
import os
from queue import Empty, Queue
import secrets
import threading
from zoneinfo import ZoneInfo

from . import ai
from .briefing import capture_snapshot, mailbox_scope, persist_snapshot
from .connectors import ConnectorError, load_messages
from .mail_providers import REAL_MAIL_PROVIDERS
from .db import iso_now

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
WATCH_INTERVAL = timedelta(minutes=5)


def _real_data_only():
    return os.environ.get("FILO_REAL_DATA_ONLY") == "1"


def utc(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def next_slot(service, now=None):
    now = utc(now)
    tz = ZoneInfo(service.get("timezone", "Europe/Rome"))
    local = now.astimezone(tz)
    slot = datetime.combine(local.date(), dt_time(service["hour"], service["minute"]), tzinfo=tz)
    if slot.astimezone(timezone.utc) <= now:
        slot = datetime.combine(local.date() + timedelta(days=1), dt_time(service["hour"], service["minute"]), tzinfo=tz)
    return slot.isoformat()


def public_service(db, now=None):
    service = db.get_setting("service")
    service["next_run_at"] = next_slot(service, now) if service["status"] == "active" else None
    service["preferences"] = {key: service[key] for key in ("priority_contacts", "hour", "minute", "timezone")}
    return service


class Scheduler:
    def __init__(self, db, poll_interval=1.0, operation_timeout=25.0):
        self.db = db
        self.poll_interval = poll_interval
        self.operation_timeout = operation_timeout
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self.recover()

    def recover(self):
        now = iso_now()
        with self.db.connection() as conn:
            interrupted = conn.execute("SELECT id,attempts FROM runs WHERE status='running'").fetchall()
            for row in interrupted:
                can_retry = row["attempts"] < MAX_ATTEMPTS
                conn.execute("UPDATE runs SET status=?,next_attempt_at=?,error=?,error_step='ripristino',retryable=?,finished_at=? WHERE id=?", (
                    "retry_wait" if can_retry else "failed", now if can_retry else None,
                    "Il server si è interrotto durante il controllo. Recupero automatico avviato." if can_retry else "Il server si è interrotto. Raggiunto il limite di tre tentativi.",
                    int(can_retry), None if can_retry else now, row["id"]))
                self._log_in_transaction(conn, "run_recovered", row["id"], {"attempts": row["attempts"]})

    @staticmethod
    def _log_in_transaction(conn, action, target, details):
        conn.execute("INSERT INTO actions(action,target_id,created_at,details) VALUES (?,?,?,?)", (action, target, iso_now(), json.dumps(details, ensure_ascii=False)))

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="alexchiara-scheduler")
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=self.operation_timeout + 2)

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            self._stop.wait(self.poll_interval)

    def enqueue(self, trigger="manual", slot_key=None, now=None):
        observed_at = utc(now)
        now = observed_at.isoformat()
        run_id = secrets.token_hex(12)
        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            service = self.db.get_setting("service")
            self._guard(service["provider"])
            prefs = {key: service[key] for key in ("priority_contacts", "hour", "minute", "timezone")}
            # Manual clicks and background watches share one in-flight check.
            # The transaction also prevents multiple workers from polling the
            # same mailbox immediately after another check has completed.
            if trigger in ("manual", "watch"):
                existing = conn.execute("SELECT id FROM runs WHERE status IN ('queued','running','retry_wait') ORDER BY created_at LIMIT 1").fetchone()
                if existing:
                    return self.db.run(existing["id"])
            if trigger == "watch":
                from .watches import needs_watch_poll
                if service.get("provider") not in REAL_MAIL_PROVIDERS or not needs_watch_poll(self.db, observed_at):
                    return None
                # Bind deduplication to the mailbox still authorized inside
                # this transaction, even if it changed after scheduling.
                scope = hashlib.sha256(mailbox_scope(self.db, service["provider"]).encode()).hexdigest()[:24]
                bucket = int(observed_at.timestamp()) // int(WATCH_INTERVAL.total_seconds())
                slot_key = f"priority-email:watch:{scope}:{bucket}"
                recent = conn.execute("""
                    SELECT id,COALESCE(finished_at,started_at) AS observed_at
                    FROM runs WHERE provider=? AND started_at IS NOT NULL
                    ORDER BY julianday(COALESCE(finished_at,started_at)) DESC LIMIT 1
                """, (service["provider"],)).fetchone()
                if recent and observed_at - utc(recent["observed_at"]) < WATCH_INTERVAL:
                    return self.db.run(recent["id"])
            conn.execute("INSERT OR IGNORE INTO runs(id,slot_key,trigger,provider,status,created_at,demo,preferences,company) VALUES (?,?,?,?,'queued',?,?,?,?)", (
                run_id, slot_key, trigger, service["provider"], now, int(service["provider"] == "demo"),
                json.dumps(prefs, ensure_ascii=False), json.dumps(self.db.get_setting("company"), ensure_ascii=False)))
            if slot_key:
                row = conn.execute("SELECT id FROM runs WHERE slot_key=?", (slot_key,)).fetchone()
                run_id = row["id"]
        return self.db.run(run_id)

    def _guard(self, provider, preferences=None, company=None):
        if _real_data_only() and provider == "demo":
            raise ConnectorError("La posta dimostrativa non è disponibile: collega la tua casella per usare dati reali.", "collegamento", False)
        if _real_data_only():
            if self.db.get_setting("company", {}).get("demo"):
                raise ConnectorError("Configura il profilo reale della tua attività prima di controllare la posta e preparare bozze.", "profilo", False)
            if company is not None and company.get("demo"):
                raise ConnectorError("Questo controllo usa ancora un profilo dimostrativo. Avvia un nuovo controllo con il profilo reale.", "profilo", False)
        service = self.db.get_setting("service")
        mandate = service.get("mandate") or {}
        if service["status"] != "active" or not mandate.get("read") or not mandate.get("draft") or mandate.get("send"):
            raise ConnectorError("Il servizio non dispone di un mandato attivo per leggere e preparare bozze.", "mandato", False)
        if service["provider"] != provider:
            raise ConnectorError("La casella autorizzata è cambiata. Riattiva il servizio per la nuova connessione.", "mandato", False)
        if preferences is not None:
            expected = {contact["email"].lower() for contact in preferences["priority_contacts"]}
            current = {contact["email"].lower() for contact in service["priority_contacts"]}
            if expected != current:
                raise ConnectorError("I contatti prioritari sono cambiati. Avvia un nuovo controllo con le preferenze aggiornate.", "preferenze", False)
        connection = self.db.get_connection()
        if connection.get("provider") != provider or connection.get("status") != "connected":
            raise ConnectorError("La connessione email è scaduta o non è disponibile. Collega nuovamente la casella.", "autorizzazione", False, connection.get("status") == "expired")

    def _schedule_due(self, now):
        service = self.db.get_setting("service")
        if service["status"] != "active" or (_real_data_only() and service.get("provider") == "demo"):
            return
        # A revoked connection needs intervention, rather than repeated daily failures.
        connection = self.db.get_connection()
        if connection.get("status") != "connected" or connection.get("provider") != service.get("provider"):
            return
        try:
            self._guard(service["provider"])
        except ConnectorError:
            return
        tz = ZoneInfo(service["timezone"])
        local = now.astimezone(tz)
        slot = datetime.combine(local.date(), dt_time(service["hour"], service["minute"]), tzinfo=tz)
        if slot.astimezone(timezone.utc) > now:
            slot = datetime.combine(local.date() - timedelta(days=1), dt_time(service["hour"], service["minute"]), tzinfo=tz)
        activated_at = service.get("activated_at")
        if activated_at and slot.astimezone(timezone.utc) < utc(activated_at):
            return
        # The latest daily slot only. Long downtime never creates a backlog.
        scope_key = mailbox_scope(self.db, service["provider"])
        scope = hashlib.sha256(scope_key.encode()).hexdigest()[:24]
        # A pre-migration daily key is reusable only when its saved observation
        # proves that it belonged to this exact mailbox, rather than the demo
        # or a Gmail account that was subsequently replaced.
        legacy_key = f"priority-email:{slot.date().isoformat()}"
        with self.db.connection() as conn:
            existing = conn.execute("""
                SELECT r.company FROM runs r JOIN briefing_snapshots s ON s.run_id=r.id
                WHERE r.slot_key=? AND r.status='succeeded' AND s.provider=? AND s.scope_key=?
                LIMIT 1
            """, (legacy_key, service["provider"], scope_key)).fetchone()
        if existing and (not _real_data_only() or not json.loads(existing["company"]).get("demo")):
            return
        key = f"priority-email:daily:{scope}:{slot.date().isoformat()}"
        self.enqueue("scheduled", key, now)

    def _schedule_watch_due(self, now):
        service = self.db.get_setting("service")
        if service.get("provider") not in REAL_MAIL_PROVIDERS:
            return
        try:
            self._guard(service["provider"])
        except ConnectorError:
            return
        from .watches import needs_watch_poll
        if not needs_watch_poll(self.db, now):
            return
        scope = hashlib.sha256(mailbox_scope(self.db, service["provider"]).encode()).hexdigest()[:24]
        bucket = int(now.timestamp()) // int(WATCH_INTERVAL.total_seconds())
        key = f"priority-email:watch:{scope}:{bucket}"
        self.enqueue("watch", key, now)

    def tick(self, now=None):
        now = utc(now)
        if not self._lock.acquire(blocking=False):
            return []
        try:
            service = self.db.get_setting("service")
            if service["status"] != "active" or (_real_data_only() and service.get("provider") == "demo"):
                return []
            self._schedule_due(now)
            self._schedule_watch_due(now)
            with self.db.connection() as conn:
                row = conn.execute("SELECT * FROM runs WHERE status IN ('queued','retry_wait') AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at,id LIMIT 1", (now.isoformat(),)).fetchone()
                if not row:
                    return []
                conn.execute("UPDATE runs SET status='running',attempts=attempts+1,started_at=?,finished_at=NULL,error=NULL,error_step=NULL WHERE id=? AND status IN ('queued','retry_wait')", (now.isoformat(), row["id"]))
                run = dict(row)
                run["attempts"] += 1
            self._execute(run, now)
            return [self.db.run(run["id"])]
        finally:
            self._lock.release()

    def _bounded(self, callback):
        result = Queue(maxsize=1)
        def invoke():
            try:
                result.put((True, callback()))
            except Exception as exc:
                result.put((False, exc))
        thread = threading.Thread(target=invoke, daemon=True, name="alexchiara-read-only-operation")
        thread.start()
        try:
            succeeded, value = result.get(timeout=self.operation_timeout)
        except Empty:
            raise ConnectorError("Il controllo ha superato il tempo massimo. Verrà riprovato con un intervallo crescente.", "timeout", True)
        if succeeded:
            return value
        raise value

    def _execute(self, run, now):
        scope_key = mailbox_scope(self.db, run["provider"])
        # Any reconnection, even of the same mailbox, replaces the credentials
        # this run started with.
        revision = self.db.get_setting("gmail_revision", 0)

        def guard_current(preferences, company):
            self._guard(run["provider"], preferences, company)
            if mailbox_scope(self.db, run["provider"]) != scope_key or self.db.get_setting("gmail_revision", 0) != revision:
                raise ConnectorError("La casella è cambiata durante il controllo. Avvia un nuovo controllo per la connessione attuale.", "collegamento", False)

        try:
            preferences = json.loads(run["preferences"])
            company = json.loads(run["company"])
            self._guard(run["provider"], preferences, company)
            if run["trigger"] == "watch":
                from .watches import needs_watch_poll
                if not needs_watch_poll(self.db, now):
                    raise ConnectorError("Non ci sono risposte da monitorare per oggi.", "monitoraggio", False)
            failure = self.db.get_setting("demo_failure") if run["provider"] == "demo" else None
            if failure == "temporary":
                self.db.set_setting("demo_failure", None)
                raise ConnectorError("Il provider dimostrativo è temporaneamente indisponibile. Nuovo tentativo automatico in pochi secondi.", "lettura", True)
            if failure == "expired":
                self.db.set_connection("demo", "expired")
                raise ConnectorError("Autorizzazione dimostrativa scaduta. Ripristina la connessione per continuare.", "autorizzazione", False, True)
            def read_and_analyze():
                guard_current(preferences, company)
                messages = load_messages(run["provider"], preferences["priority_contacts"], self.db)
                guard_current(preferences, company)
                # Provider content never crosses the permission boundary above.
                analyze = getattr(ai, "analyze", None)
                if analyze:
                    result = analyze(messages, company, preferences["priority_contacts"], provider=run["provider"])
                else:
                    result = ai.analyze_messages(messages, company, preferences["priority_contacts"])
                snapshot = capture_snapshot(messages, preferences["priority_contacts"], run["provider"], now, scope_key)
                sources = {str(ai._field(message, "id")): message for message in messages}
                return result, snapshot, sources
            result, snapshot, sources = self._bounded(read_and_analyze)
            guard_current(preferences, company)
            finished = now.isoformat()
            with self.db.connection() as conn:
                conn.execute("BEGIN IMMEDIATE")
                guard_current(preferences, company)
                for item in result["items"]:
                    source = sources.get(item["source_id"], {})
                    observation = next((message for message in snapshot["messages"] if message["id"] == item["source_id"]), {})
                    conn.execute("INSERT OR IGNORE INTO drafts(id,run_id,source_id,client,subject,reason,draft,thread_id,source_excerpt,received_at,email,source_body,status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'pending',?)", (
                        secrets.token_hex(12), run["id"], item["source_id"], item["client"], item["subject"], item["reason"], item["draft"], item.get("thread_id"), str(item.get("source_excerpt", ""))[:4000],
                        observation.get("received_at"), observation.get("email"), str(ai._field(source, "body"))[:4000], finished))
                persist_snapshot(conn, run["id"], snapshot)
                conn.execute("UPDATE runs SET status='succeeded',finished_at=?,summary=?,error=NULL,error_step=NULL,retryable=0,next_attempt_at=NULL,analysis_mode=? WHERE id=?", (finished, result["summary"], result.get("analysis_mode", "deterministic"), run["id"]))
                self._log_in_transaction(conn, "run_succeeded", run["id"], {"items": len(result["items"]), "analysis_mode": result.get("analysis_mode", "deterministic")})
                service = json.loads(conn.execute("SELECT value FROM kv WHERE key='service'").fetchone()["value"])
                service["last_run_at"] = finished
                conn.execute("UPDATE kv SET value=? WHERE key='service'", (json.dumps(service),))
        except ConnectorError as exc:
            if exc.expired:
                with self.db.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    current = self.db.get_connection()
                    if current.get("provider") == run["provider"] and mailbox_scope(self.db, run["provider"]) == scope_key and self.db.get_setting("gmail_revision", 0) == revision:
                        current["status"] = "expired"
                        conn.execute("UPDATE kv SET value=? WHERE key='connection'", (json.dumps(current),))
            self._fail(run, now, str(exc), exc.step, exc.retryable)
        except Exception:
            logger.exception("Read-only analysis failed for run %s", run["id"])
            self._fail(run, now, "L'analisi non è riuscita. I dati non sono stati inviati né modificati. Riprova il controllo.", "analisi", True)

    def _fail(self, run, now, message, step, retryable):
        retry = bool(retryable and run["attempts"] < MAX_ATTEMPTS)
        delay = timedelta(seconds=2 ** run["attempts"])
        if run.get("trigger") == "watch":
            delay = max(delay, WATCH_INTERVAL)
        next_attempt = (now + delay).isoformat() if retry else None
        with self.db.connection() as conn:
            conn.execute("UPDATE runs SET status=?,error=?,error_step=?,retryable=?,next_attempt_at=?,finished_at=? WHERE id=?", (
                "retry_wait" if retry else "failed", message, step, int(retry), next_attempt,
                None if retry else now.isoformat(), run["id"]))
            self._log_in_transaction(conn, "run_retry_scheduled" if retry else "run_failed", run["id"], {"step": step, "attempts": run["attempts"]})

    def retry(self, run_id):
        run = self.db.run(run_id)
        if not run:
            raise LookupError("Controllo non trovato")
        if run["status"] not in ("failed", "retry_wait") or not run["retryable"] or run["attempts"] >= MAX_ATTEMPTS:
            raise ValueError("Questo controllo non può essere riprovato. Avvia un nuovo controllo dopo aver risolto il problema.")
        with self.db.connection() as conn:
            row = conn.execute("SELECT preferences,company FROM runs WHERE id=?", (run_id,)).fetchone()
        self._guard(run["provider"], json.loads(row["preferences"]), json.loads(row["company"]))
        with self.db.connection() as conn:
            conn.execute("UPDATE runs SET status='queued',next_attempt_at=NULL WHERE id=?", (run_id,))
        self.db.log_action("run_manual_retry", run_id)
        return self.db.run(run_id)
