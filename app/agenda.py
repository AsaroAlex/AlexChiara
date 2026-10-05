"""A durable, manual agenda, independent of any external calendar account."""
from datetime import date as calendar_date, datetime, time, timedelta, timezone
import re
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .company import has_hidden_characters


ROME = ZoneInfo("Europe/Rome")
UTC = timezone.utc
SOURCE = "Appuntamenti aggiunti da te"
MAX_EVENTS = 5000


class AgendaInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    notes: str = Field(default="", max_length=500)

    @field_validator("starts_at", "ends_at", mode="before")
    @classmethod
    def iso_datetime(cls, value):
        # Numeric strings would otherwise be read as Unix timestamps.
        if value is not None and (not isinstance(value, str) or not re.match(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", value)):
            raise ValueError("Indica data e ora ISO con fuso orario.")
        return value

    @field_validator("starts_at", "ends_at")
    @classmethod
    def convertible_datetime(cls, value):
        if value is not None:
            try:
                value.astimezone(UTC)
                value.astimezone(ROME)
            except (OverflowError, ValueError) as exc:
                raise ValueError("Data fuori intervallo.") from exc
        return value

    @field_validator("title", "notes")
    @classmethod
    def readable_text(cls, value, info):
        allowed = "\n\t" if info.field_name == "notes" else ""
        if has_hidden_characters(value, allowed):
            raise ValueError("Il testo contiene caratteri non consentiti.")
        return value

    @model_validator(mode="after")
    def end_after_start(self):
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("La fine deve essere successiva all'inizio dell'appuntamento.")
        return self


def _initialize(db):
    with db.connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agenda_events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                starts_at TEXT NOT NULL,
                ends_at TEXT,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS agenda_events_start ON agenda_events(starts_at, id);
        """)


def _utc_stamp(value):
    # Fixed precision makes SQLite's text ordering match chronological ordering.
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _day_bounds(day):
    try:
        start = datetime.combine(day, time.min, tzinfo=ROME)
        end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=ROME)
        return _utc_stamp(start), _utc_stamp(end)
    except (OverflowError, ValueError) as exc:
        raise ValueError("Data fuori intervallo.") from exc


def _item(row):
    start = datetime.fromisoformat(row["starts_at"]).astimezone(ROME)
    end = datetime.fromisoformat(row["ends_at"]).astimezone(ROME) if row["ends_at"] else None
    label = start.strftime("%H:%M")
    if end:
        label += "–" + (end.strftime("%H:%M") if end.date() == start.date() else end.strftime("%d/%m %H:%M"))
    return {
        "id": row["id"], "title": row["title"],
        "starts_at": start.isoformat(), "ends_at": end.isoformat() if end else None,
        "notes": row["notes"], "time_label": label,
    }


def agenda_summary(db, now=None, day=None):
    """Summarize a Rome calendar day from saved appointments only.

    The router initializes the table before this helper is used by bootstrap.
    ``now`` is injectable for checks and must include a timezone. ``day`` may
    select a past or future date without changing the source of the events.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now deve includere il fuso orario.")
    local_now = now.astimezone(ROME)
    selected_day = day or local_now.date()
    lower, upper = _day_bounds(selected_day)
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM agenda_events WHERE starts_at >= ? AND starts_at < ? ORDER BY starts_at, id",
            (lower, upper),
        ).fetchall()
    items = [_item(row) for row in rows]
    next_event = next(
        (item for item in items if datetime.fromisoformat(item["starts_at"]) >= now), None
    )
    return {
        "date": selected_day.isoformat(), "timezone": "Europe/Rome",
        "items": items, "next_event": next_event,
        "calendar_connected": False, "source": SOURCE,
    }


def _export_text(summary):
    day = calendar_date.fromisoformat(summary["date"])
    lines = [
        "Spazelia — Ordine del giorno", day.strftime("%d/%m/%Y"),
        "Fuso orario: Europe/Rome", SOURCE,
        "Calendario esterno non collegato.", "",
    ]
    if not summary["items"]:
        lines.append("Nessun appuntamento aggiunto per questa giornata.")
    for item in summary["items"]:
        lines.append(f'{item["time_label"]} — {item["title"]}')
        if item["notes"]:
            lines.extend("  " + line for line in item["notes"].splitlines())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def create_agenda_router(db):
    """Create API routes protected by the application's existing middleware."""
    _initialize(db)
    router = APIRouter(prefix="/api/agenda", tags=["agenda"])

    def summary_for(day):
        try:
            return agenda_summary(db, day=day)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("")
    def list_events(day: calendar_date | None = Query(default=None, alias="date")):
        return summary_for(day)

    @router.post("", status_code=201)
    def add_event(payload: AgendaInput):
        event_id = uuid4().hex
        with db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT COUNT(*) FROM agenda_events").fetchone()[0] >= MAX_EVENTS:
                raise HTTPException(status_code=409, detail="Hai raggiunto il limite di appuntamenti salvati. Elimina quelli passati non più necessari.")
            conn.execute(
                "INSERT INTO agenda_events(id,title,starts_at,ends_at,notes,created_at) VALUES (?,?,?,?,?,?)",
                (event_id, payload.title, _utc_stamp(payload.starts_at),
                 _utc_stamp(payload.ends_at) if payload.ends_at else None,
                 payload.notes, _utc_stamp(datetime.now(UTC))),
            )
            item = _item(conn.execute("SELECT * FROM agenda_events WHERE id=?", (event_id,)).fetchone())
        return {"item": item}

    @router.get("/export")
    def export_events(day: calendar_date | None = Query(default=None, alias="date")):
        summary = summary_for(day)
        return Response(
            content=_export_text(summary).encode("utf-8"), media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="ordine-del-giorno-{summary["date"]}.txt"'},
        )

    @router.delete("/{event_id}")
    def delete_event(event_id: str = Path(min_length=1, max_length=64)):
        with db.connection() as conn:
            deleted = conn.execute("DELETE FROM agenda_events WHERE id=?", (event_id,)).rowcount
        if not deleted:
            raise HTTPException(status_code=404, detail="Appuntamento non trovato.")
        return {"deleted": True, "id": event_id}

    return router
