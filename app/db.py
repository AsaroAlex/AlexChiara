"""SQLite persistence. Every write is transactional and survives browser closure."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import sqlite3

DEFAULT_CONTACTS = [
    {"name": "Giulia Conti", "email": "giulia.conti@example.com"},
    {"name": "Marco Bianchi", "email": "marco.bianchi@example.com"},
    {"name": "Sara Rossi", "email": "sara.rossi@example.com"},
]
SESSION_LIFETIME = timedelta(days=7)
DEFAULT_COMPANY = {"name": "Studio Riva", "sector": "Studio di architettura", "description": "Studio fittizio per la dimostrazione. Progettiamo spazi residenziali e commerciali.", "signature": "Il team di Studio Riva", "demo": True}


def iso_now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.initialize()
        self.path.chmod(0o600)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self):
        with self.connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, slot_key TEXT UNIQUE, trigger TEXT NOT NULL,
                    provider TEXT NOT NULL, status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT,
                    summary TEXT NOT NULL DEFAULT '', error TEXT, error_step TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0, demo INTEGER NOT NULL DEFAULT 0,
                    analysis_mode TEXT NOT NULL DEFAULT 'deterministic',
                    preferences TEXT NOT NULL, company TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    source_id TEXT NOT NULL, client TEXT NOT NULL, subject TEXT NOT NULL,
                    reason TEXT NOT NULL, draft TEXT NOT NULL, thread_id TEXT,
                    source_excerpt TEXT NOT NULL DEFAULT '',
                    received_at TEXT, email TEXT, source_body TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL,
                    approved_at TEXT, UNIQUE(run_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, action TEXT NOT NULL,
                    target_id TEXT, created_at TEXT NOT NULL, details TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY, csrf TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS run_queue ON runs(status, next_attempt_at, created_at);
                CREATE TABLE IF NOT EXISTS briefing_snapshots (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id), provider TEXT NOT NULL,
                    scope_key TEXT NOT NULL, checked_at TEXT NOT NULL,
                    coverage_start TEXT NOT NULL, coverage_end TEXT NOT NULL,
                    coverage_complete INTEGER NOT NULL DEFAULT 0,
                    contacts TEXT NOT NULL, messages TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS briefing_latest ON briefing_snapshots(provider,scope_key,checked_at);
            """)
            # Additive migration keeps existing local drafts and approvals intact.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(drafts)")}
            if "source_excerpt" not in columns:
                conn.execute("ALTER TABLE drafts ADD COLUMN source_excerpt TEXT NOT NULL DEFAULT ''")
            for name, definition in (("received_at", "TEXT"), ("email", "TEXT"),
                                     ("source_body", "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    conn.execute(f"ALTER TABLE drafts ADD COLUMN {name} {definition}")
            defaults = {
                "company": DEFAULT_COMPANY,
                "service": {"id": "priority-email", "status": "inactive", "provider": None,
                            "priority_contacts": DEFAULT_CONTACTS, "hour": 9, "minute": 0,
                            "timezone": "Europe/Rome", "activated_at": None,
                            "last_run_at": None, "mandate": None},
                "connection": {"status": "disconnected", "provider": None, "label": "Nessun account collegato"},
                "demo_failure": None,
            }
            for key, value in defaults.items():
                conn.execute("INSERT OR IGNORE INTO kv(key,value) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
            from .briefing import migrate_scope_keys
            migrate_scope_keys(conn, "briefing_snapshots")

    def get_setting(self, key, default=None):
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        with self.connection() as conn:
            conn.execute("INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def get_connection(self):
        return self.get_setting("connection")

    def set_connection(self, provider=None, status="disconnected", label=None, **extra):
        value = {"provider": provider, "status": status, "label": label or ("Posta dimostrativa" if provider == "demo" else "Gmail" if provider == "gmail" else "Nessun account collegato")}
        value.update(extra)
        self.set_setting("connection", value)
        return value

    def log_action(self, action, target_id=None, details=None):
        with self.connection() as conn:
            conn.execute("INSERT INTO actions(action,target_id,created_at,details) VALUES (?,?,?,?)", (action, target_id, iso_now(), json.dumps(details or {}, ensure_ascii=False)))

    @staticmethod
    def _session_cutoff():
        # Stored timestamps are ISO strings, so compare with the same format.
        return (datetime.now(timezone.utc) - SESSION_LIFETIME).isoformat()

    def session(self, session_id=None):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id=? AND created_at>=?", (session_id, self._session_cutoff())).fetchone() if session_id else None
            if row:
                return dict(row)
            session = {"id": secrets.token_urlsafe(32), "csrf": secrets.token_urlsafe(32), "created_at": iso_now()}
            conn.execute("DELETE FROM sessions WHERE created_at<?", (self._session_cutoff(),))
            conn.execute("INSERT INTO sessions(id,csrf,created_at) VALUES (:id,:csrf,:created_at)", session)
            return session

    def csrf_valid(self, session_id, csrf):
        if not isinstance(session_id, str) or not isinstance(csrf, str) or not session_id or not csrf or not csrf.isascii():
            return False
        with self.connection() as conn:
            row = conn.execute("SELECT csrf FROM sessions WHERE id=? AND created_at>=?", (session_id, self._session_cutoff())).fetchone()
        return bool(row and secrets.compare_digest(row["csrf"], csrf))

    def draft(self, draft_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return dict(row) if row else None

    def run(self, run_id):
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["items"] = [dict(item) for item in conn.execute("SELECT * FROM drafts WHERE run_id=? ORDER BY created_at,id", (run_id,))]
        result["retryable"] = bool(result["retryable"])
        result["demo"] = bool(result["demo"])
        result.pop("preferences", None)
        result.pop("company", None)
        return result

    def list_runs(self, limit=50):
        with self.connection() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM runs ORDER BY created_at DESC,id DESC LIMIT ?", (limit,))]
        return [self.run(run_id) for run_id in ids]

    def pending_drafts(self):
        from .briefing import latest_pending_drafts
        with self.connection() as conn:
            rows = [dict(row) for row in conn.execute("""
                SELECT d.*, r.provider AS _provider, s.scope_key AS _scope_key
                FROM drafts d JOIN runs r ON r.id=d.run_id
                LEFT JOIN briefing_snapshots s ON s.run_id=r.id
                ORDER BY d.created_at DESC,d.id DESC
            """)]
            snapshots = [dict(row) for row in conn.execute("SELECT * FROM briefing_snapshots")]
        items = latest_pending_drafts(rows, snapshots)
        return [{key: value for key, value in item.items() if not key.startswith("_")}
                for item in items[:100]]

    def list_actions(self, limit=30):
        with self.connection() as conn:
            items = [dict(row) for row in conn.execute("SELECT * FROM actions ORDER BY id DESC LIMIT ?", (limit,))]
        for item in items:
            item["details"] = json.loads(item["details"])
        return items
