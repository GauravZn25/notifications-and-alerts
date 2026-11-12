"""Where notifications, reminders and the delivery log live — four tables in the host
module's own database, in its schema, under a prefix (default `notify_`):

    notify_notifications          one row per in-app notification per user
    notify_reminders              a scheduled reminder: what, to whom, how often, next run
    notify_reminder_attachments   the files a reminder sends each time (kept with it)
    notify_deliveries             every send attempt: channel, recipients, result, error

SQLAlchemy Core with portable types, so it runs on Postgres (production) and SQLite
(tests) alike. Create the tables with `Store.create_tables()` at startup, or hand
`Store.metadata` to the host's Alembic env (see README).

DUE REMINDERS AND SEVERAL WORKERS. `claim_due` locks the rows it takes with
`FOR UPDATE SKIP LOCKED` (Postgres), so two workers never send the same occurrence;
the lock is held until the caller's transaction commits.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (JSON, Column, DateTime, ForeignKey, Index, Integer, LargeBinary,
                        MetaData, String, Table, Text, UniqueConstraint, and_, create_engine, func,
                        or_, select, update)
from sqlalchemy.engine import Connection, Engine


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_metadata(schema: str | None = None, prefix: str = "notify_") -> tuple[MetaData, dict[str, Table]]:
    md = MetaData(schema=schema or None)
    ts = lambda name, **kw: Column(name, DateTime(timezone=True), **kw)  # noqa: E731

    notifications = Table(
        f"{prefix}notifications", md,
        Column("id", String(36), primary_key=True),
        Column("user_id", String(128), nullable=False),
        Column("title", String(300), nullable=False),
        Column("body", Text, nullable=False, default=""),
        Column("level", String(10), nullable=False, default="info"),
        Column("link", String(1000)),
        Column("category", String(60)),
        Column("source", String(40)),
        Column("entity_type", String(60)),
        Column("entity_id", String(128)),
        Column("data", JSON, nullable=False, default=dict),
        Column("tenant_urn", String(512)),
        Column("project_id", String(64)),
        Column("reminder_id", String(36)),
        ts("created_at", nullable=False, default=utcnow),
        ts("read_at"),
        ts("dismissed_at"),
        ts("expires_at"),
        Index(f"ix_{prefix}notifications_inbox", "user_id", "dismissed_at", "created_at"),
        Index(f"ix_{prefix}notifications_unread", "user_id", "read_at"),
        Index(f"ix_{prefix}notifications_entity", "entity_type", "entity_id"),
    )
    reminders = Table(
        f"{prefix}reminders", md,
        Column("id", String(36), primary_key=True),
        #: The host's handle — e.g. "letter:42:reply-due" — so it can replace or cancel
        #: a reminder without keeping our id. Unique while it exists.
        Column("key", String(200)),
        Column("source", String(40)),
        Column("title", String(300), nullable=False),
        Column("body_text", Text),
        Column("body_html", Text),
        Column("channels", JSON, nullable=False),
        Column("email_to", JSON, nullable=False, default=list),
        Column("email_cc", JSON, nullable=False, default=list),
        Column("email_bcc", JSON, nullable=False, default=list),
        Column("reply_to", JSON, nullable=False, default=list),
        Column("from_address", String(320)),
        Column("from_name", String(200)),
        Column("user_ids", JSON, nullable=False, default=list),
        #: level, link, category, entity_type, entity_id, data — the in-app half.
        Column("in_app", JSON, nullable=False, default=dict),
        #: Placeholder values for title and body.
        Column("context", JSON, nullable=False, default=dict),
        Column("frequency", JSON, nullable=False),
        Column("status", String(12), nullable=False, default="active"),
        ts("next_run_at"),
        #: While an occurrence is being retried: the time it was originally due.
        ts("occurrence_due_at"),
        ts("last_run_at"),
        Column("runs_done", Integer, nullable=False, default=0),
        Column("attempts", Integer, nullable=False, default=0),
        Column("last_error", Text),
        Column("tenant_urn", String(512)),
        Column("project_id", String(64)),
        Column("created_by", String(128)),
        ts("created_at", nullable=False, default=utcnow),
        ts("updated_at", nullable=False, default=utcnow, onupdate=utcnow),
        UniqueConstraint("key", name=f"uq_{prefix}reminders_key"),
        Index(f"ix_{prefix}reminders_due", "status", "next_run_at"),
        Index(f"ix_{prefix}reminders_creator", "created_by", "status"),
    )
    attachments = Table(
        f"{prefix}reminder_attachments", md,
        Column("id", String(36), primary_key=True),
        Column("reminder_id", String(36),
               ForeignKey(reminders.c.id, ondelete="CASCADE"), nullable=False, index=True),
        Column("filename", String(255), nullable=False),
        Column("content_type", String(150), nullable=False),
        Column("content", LargeBinary, nullable=False),
        Column("size", Integer, nullable=False),
    )
    deliveries = Table(
        f"{prefix}deliveries", md,
        Column("id", String(36), primary_key=True),
        Column("channel", String(10), nullable=False),
        Column("status", String(12), nullable=False),
        Column("reminder_id", String(36), index=True),
        Column("occurrence", Integer),
        Column("subject", String(300)),
        #: {"to": [...], "cc": [...], "bcc": [...]} or {"user_ids": [...]}
        Column("recipients", JSON, nullable=False, default=dict),
        Column("attachments", JSON, nullable=False, default=list),
        Column("transport", String(20)),
        Column("provider_ref", String(300)),
        Column("error", Text),
        Column("source", String(40)),
        ts("created_at", nullable=False, default=utcnow),
        Index(f"ix_{prefix}deliveries_created", "created_at"),
    )
    return md, {"notifications": notifications, "reminders": reminders,
                "attachments": attachments, "deliveries": deliveries}


class Store:
    def __init__(self, engine: Engine | str, *, schema: str | None = None,
                 prefix: str = "notify_") -> None:
        self.engine = create_engine(engine, future=True) if isinstance(engine, str) else engine
        self.metadata, t = build_metadata(schema, prefix)
        self.notifications = t["notifications"]
        self.reminders = t["reminders"]
        self.attachments = t["attachments"]
        self.deliveries = t["deliveries"]

    def create_tables(self) -> None:
        """Create the four tables if they are missing (safe to call at every start)."""
        self.metadata.create_all(self.engine)

    def begin(self):
        return self.engine.begin()

    # --- in-app notifications ---------------------------------------------------------
    def add_notifications(self, conn: Connection, rows: list[dict[str, Any]]) -> list[str]:
        now = utcnow()
        prepared = [{"id": new_id(), "created_at": now, **row} for row in rows]
        conn.execute(self.notifications.insert(), prepared)
        return [row["id"] for row in prepared]

    def _visible(self, user_id: str, now: datetime):
        n = self.notifications
        return and_(n.c.user_id == user_id, n.c.dismissed_at.is_(None),
                    or_(n.c.expires_at.is_(None), n.c.expires_at > now))

    def list_notifications(self, user_id: str, *, unread_only: bool = False, limit: int = 20,
                           before: datetime | None = None, category: str | None = None) -> list[dict]:
        n = self.notifications
        stmt = select(n).where(self._visible(user_id, utcnow()))
        if unread_only:
            stmt = stmt.where(n.c.read_at.is_(None))
        if before is not None:
            if before.tzinfo is not None:  # compare in UTC, as stored
                before = before.astimezone(timezone.utc).replace(tzinfo=None) \
                    if self.engine.dialect.name == "sqlite" else before
            stmt = stmt.where(n.c.created_at < before)
        if category:
            stmt = stmt.where(n.c.category == category)
        with self.engine.connect() as conn:
            return [dict(r._mapping) for r in conn.execute(
                stmt.order_by(n.c.created_at.desc(), n.c.id.desc()).limit(limit))]

    def unread_summary(self, user_id: str) -> dict[str, Any]:
        n = self.notifications
        with self.engine.connect() as conn:
            rows = conn.execute(select(n.c.level, func.count()).where(
                self._visible(user_id, utcnow()), n.c.read_at.is_(None)).group_by(n.c.level)).all()
        by_level = {level: count for level, count in rows}
        return {"count": sum(by_level.values()), "by_level": by_level,
                "urgent": bool(by_level.get("danger") or by_level.get("warning"))}

    def mark_read(self, user_id: str, ids: list[str] | None = None) -> int:
        """Mark `ids` (or everything unread) read for `user_id` — never another user's."""
        n = self.notifications
        stmt = update(n).where(n.c.user_id == user_id, n.c.read_at.is_(None)).values(read_at=utcnow())
        if ids is not None:
            stmt = stmt.where(n.c.id.in_(ids))
        with self.begin() as conn:
            return conn.execute(stmt).rowcount

    def dismiss(self, user_id: str, notification_id: str) -> bool:
        n = self.notifications
        with self.begin() as conn:
            return conn.execute(update(n).where(n.c.user_id == user_id, n.c.id == notification_id)
                                .values(dismissed_at=utcnow(),
                                        read_at=func.coalesce(n.c.read_at, utcnow()))).rowcount > 0

    # --- reminders ----------------------------------------------------------------------
    def get_reminder(self, conn: Connection, id_or_key: str) -> dict | None:
        r = self.reminders
        row = conn.execute(select(r).where(or_(r.c.id == id_or_key, r.c.key == id_or_key))).first()
        return dict(row._mapping) if row else None

    def claim_due(self, conn: Connection, now: datetime, limit: int) -> list[dict]:
        r = self.reminders
        stmt = (select(r).where(r.c.status == "active", r.c.next_run_at.is_not(None),
                                r.c.next_run_at <= now)
                .order_by(r.c.next_run_at).limit(limit).with_for_update(skip_locked=True))
        return [dict(row._mapping) for row in conn.execute(stmt)]

    def reminder_attachments(self, conn: Connection, reminder_id: str) -> list[dict]:
        a = self.attachments
        return [dict(row._mapping) for row in conn.execute(
            select(a).where(a.c.reminder_id == reminder_id).order_by(a.c.filename))]

    def log_delivery(self, conn: Connection, **row) -> str:
        row = {"id": new_id(), "created_at": utcnow(), **row}
        conn.execute(self.deliveries.insert().values(**row))
        return row["id"]

    def list_reminders(self, *, created_by: str | None = None, status: str | None = None,
                       source: str | None = None, limit: int = 100) -> list[dict]:
        r = self.reminders
        stmt = select(r)
        if created_by is not None:
            stmt = stmt.where(r.c.created_by == created_by)
        if status is not None:
            stmt = stmt.where(r.c.status == status)
        if source is not None:
            stmt = stmt.where(r.c.source == source)
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(
                stmt.order_by(r.c.next_run_at.asc().nulls_last(), r.c.created_at).limit(limit))]

    def deliveries_for(self, reminder_id: str) -> list[dict]:
        d = self.deliveries
        with self.engine.connect() as conn:
            return [dict(row._mapping) for row in conn.execute(
                select(d).where(d.c.reminder_id == reminder_id).order_by(d.c.created_at))]
