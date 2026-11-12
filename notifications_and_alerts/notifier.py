"""The one object a module talks to.

    notifier = Notifier.from_settings(engine=engine, source="M14")

    notifier.send_notification(                       # now, or on a schedule
        title="Reply due: {ref}", body_text="…", context={"ref": "NHAI/1142"},
        to=["pm@knr.example"], cc=[…], bcc=[…], attachments=[Attachment.from_path(p)],
        user_ids=["u-17"],                            # in-app too
        frequency=Frequency.daily(at="09:00", max_runs=5),   # omit = send once, now
        key="letter:42:reply-due")                   # replace / cancel it later by key

    notifier.send_email(...)        email only, now
    notifier.notify(...)            in-app only, now
    notifier.schedule_reminder(...) / cancel_reminder(key) / pause / resume
    notifier.run_due()              send what is due — the worker calls this

DELIVERY GUARANTEES. Each due reminder is processed in its own transaction: its send,
its delivery-log row and its next occurrence commit together. A crash after the mail
left but before the commit sends that occurrence again (at-least-once) — never zero
times. A failing email is retried with backoff (`retry_backoff_minutes`) up to
`max_attempts`, then the reminder moves on to its next occurrence; the in-app half of
an occurrence is written once, on its first attempt, never per retry.
"""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from sqlalchemy import delete, insert, update

from .email import EmailTransport, SendResult, get_transport
from .frequency import Frequency
from .messages import (Attachment, Channel, EmailMessage, InAppMessage, InvalidMessage, Level,
                       check_attachments, render)
from .settings import NotifySettings
from .store import Store, new_id, utcnow

log = logging.getLogger("notifications_and_alerts")


class NotConfigured(RuntimeError):
    """A feature needs something the notifier was built without (e.g. a database)."""


class ReminderExists(ValueError):
    """`schedule_reminder(key=…, replace=False)` for a key that is already scheduled."""


@dataclass(frozen=True)
class Reminder:
    id: str
    key: str | None
    title: str
    status: str
    channels: list[str]
    frequency: Frequency
    next_run_at: datetime | None
    last_run_at: datetime | None
    runs_done: int
    last_error: str | None
    source: str | None
    created_by: str | None

    @property
    def schedule(self) -> str:
        return self.frequency.describe()


@dataclass
class Outcome:
    """What `send_notification` did when it sent at once."""

    email: SendResult | None = None
    notification_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.email is None or self.email.ok


@dataclass
class RunReport:
    """What one `run_due` pass did."""

    processed: int = 0
    sent: int = 0
    retrying: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    completed: list[str] = field(default_factory=list)


class Notifier:
    def __init__(self, *, settings: NotifySettings | None = None, store: Store | None = None,
                 email: EmailTransport | None = None, source: str | None = None,
                 on_in_app: Callable[[list[dict]], None] | None = None,
                 clock: Callable[[], datetime] | None = None) -> None:
        """`on_in_app` is called with the new in-app rows after they commit — the place
        to push them to open browsers (SSE / websocket) if the host does that."""
        self.settings = settings or NotifySettings()
        self.store = store
        self.email = email or get_transport(self.settings)
        self.source = source
        self._on_in_app = on_in_app
        self._clock = clock or utcnow

    def _now(self) -> datetime:
        """The current instant in UTC — everything stored is UTC (SQLite keeps no offset)."""
        return _utc(self._clock()).astimezone(timezone.utc)

    @classmethod
    def from_settings(cls, settings: NotifySettings | None = None, *, engine=None,
                      source: str | None = None, create_tables: bool = False,
                      **kwargs) -> "Notifier":
        """Build from `NOTIFY_*` settings. Pass the host's SQLAlchemy `engine` (or set
        NOTIFY_DATABASE_URL) for in-app notifications and reminders; without either,
        only `send_email` works."""
        s = settings or NotifySettings()
        store = None
        if engine is not None or s.database_url:
            store = Store(engine if engine is not None else s.database_url,
                          schema=s.db_schema or None, prefix=s.table_prefix)
            if create_tables:
                store.create_tables()
        return cls(settings=s, store=store, source=source, **kwargs)

    # =================================================================== send now
    def send_email(self, *, subject: str, to: Iterable[str] | str | None = None,
                   cc: Iterable[str] | str | None = None, bcc: Iterable[str] | str | None = None,
                   body_text: str | None = None, body_html: str | None = None,
                   attachments: Iterable[Attachment] = (), reply_to: Iterable[str] | str | None = None,
                   from_address: str | None = None, from_name: str | None = None,
                   headers: dict[str, str] | None = None, context: dict[str, Any] | None = None,
                   ) -> SendResult:
        """Send one email now. `{placeholders}` in the title and bodies are filled from
        `context` (values HTML-escaped in `body_html`). Raises `InvalidMessage` for a
        message that cannot be sent as given; a provider failure is the result's status."""
        message = self._email(subject=subject, to=to, cc=cc, bcc=bcc, body_text=body_text,
                              body_html=body_html, attachments=attachments, reply_to=reply_to,
                              from_address=from_address, from_name=from_name, headers=headers,
                              context=context)
        result = self._deliver(message)
        if self.store is not None:
            with self.store.begin() as conn:
                self._log_email(conn, message, result)
        return result

    def notify(self, *, user_ids: Iterable[str] | str, title: str, body: str = "",
               level: Level = "info", link: str | None = None, category: str | None = None,
               entity_type: str | None = None, entity_id: str | None = None,
               data: dict[str, Any] | None = None, tenant_urn: str | None = None,
               project_id: str | None = None, expires_at: datetime | None = None,
               context: dict[str, Any] | None = None) -> list[str]:
        """Show an in-app notification to each user now. Returns the new ids."""
        message = InAppMessage(
            user_ids=user_ids, title=render(title, context), body=render(body, context) or "",
            level=level, link=link, category=category, source=self.source,
            entity_type=entity_type, entity_id=entity_id, data=data or {},
            tenant_urn=tenant_urn, project_id=project_id, expires_at=expires_at)
        store = self._need_store("in-app notifications")
        with store.begin() as conn:
            rows = self._in_app_rows(message)
            ids = store.add_notifications(conn, rows)
            store.log_delivery(conn, channel="in_app", status="sent", subject=message.title,
                               recipients={"user_ids": message.user_ids}, source=self.source)
        self._announce(rows, ids)
        return ids

    def send_notification(
        self, *, title: str, body_text: str | None = None, body_html: str | None = None,
        to: Iterable[str] | str | None = None, cc: Iterable[str] | str | None = None,
        bcc: Iterable[str] | str | None = None, attachments: Iterable[Attachment] = (),
        user_ids: Iterable[str] | str | None = None, channels: Iterable[Channel] | None = None,
        frequency: Frequency | None = None, key: str | None = None,
        level: Level = "info", link: str | None = None, category: str | None = None,
        entity_type: str | None = None, entity_id: str | None = None,
        data: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
        reply_to: Iterable[str] | str | None = None, from_address: str | None = None,
        from_name: str | None = None, tenant_urn: str | None = None,
        project_id: str | None = None, created_by: str | None = None, replace: bool = True,
    ) -> Outcome | Reminder:
        """THE entry point. Email to `to`/`cc`/`bcc` and/or in-app to `user_ids`
        (`channels` defaults to whichever recipients are given). With `frequency` it
        is scheduled as a reminder and returned as a `Reminder`; without, it is sent
        now and an `Outcome` says how it went."""
        chosen = _channels(channels, to=to, cc=cc, bcc=bcc, user_ids=user_ids)
        common = dict(title=title, body_text=body_text, body_html=body_html, to=to, cc=cc, bcc=bcc,
                      attachments=attachments, user_ids=user_ids, level=level, link=link,
                      category=category, entity_type=entity_type, entity_id=entity_id, data=data,
                      context=context, reply_to=reply_to, from_address=from_address,
                      from_name=from_name, tenant_urn=tenant_urn, project_id=project_id)
        if frequency is not None:
            return self.schedule_reminder(frequency=frequency, key=key, channels=chosen,
                                          created_by=created_by, replace=replace, **common)
        outcome = Outcome()
        if "email" in chosen:
            outcome.email = self.send_email(
                subject=title, to=to, cc=cc, bcc=bcc, body_text=body_text, body_html=body_html,
                attachments=attachments, reply_to=reply_to, from_address=from_address,
                from_name=from_name, context=context)
        if "in_app" in chosen:
            outcome.notification_ids = self.notify(
                user_ids=user_ids or [], title=title, body=_plain(body_text, body_html),
                level=level, link=link, category=category, entity_type=entity_type,
                entity_id=entity_id, data=data, tenant_urn=tenant_urn, project_id=project_id,
                context=context)
        return outcome

    # ================================================================== reminders
    def schedule_reminder(
        self, *, title: str, frequency: Frequency, key: str | None = None,
        body_text: str | None = None, body_html: str | None = None,
        channels: Iterable[Channel] | None = None, to: Iterable[str] | str | None = None,
        cc: Iterable[str] | str | None = None, bcc: Iterable[str] | str | None = None,
        attachments: Iterable[Attachment] = (), user_ids: Iterable[str] | str | None = None,
        level: Level = "info", link: str | None = None, category: str | None = None,
        entity_type: str | None = None, entity_id: str | None = None,
        data: dict[str, Any] | None = None, context: dict[str, Any] | None = None,
        reply_to: Iterable[str] | str | None = None, from_address: str | None = None,
        from_name: str | None = None, tenant_urn: str | None = None,
        project_id: str | None = None, created_by: str | None = None, replace: bool = True,
    ) -> Reminder:
        """Schedule a reminder. The whole message is validated NOW (addresses, body,
        attachment sizes), so a mistake is the caller's exception, not a worker's log
        line. With a `key` that is already scheduled, `replace=True` swaps it (the old
        one and its attachments go); `replace=False` raises `ReminderExists`."""
        store = self._need_store("reminders")
        chosen = _channels(channels, to=to, cc=cc, bcc=bcc, user_ids=user_ids)
        files = list(attachments)
        email = (self._email(subject=title, to=to, cc=cc, bcc=bcc, body_text=body_text,
                             body_html=body_html, attachments=files, reply_to=reply_to,
                             from_address=from_address, from_name=from_name, context=context)
                 if "email" in chosen else None)
        in_app = (InAppMessage(user_ids=user_ids or [], title=title)
                  if "in_app" in chosen else None)
        now = self._now()
        first = frequency.first(now)
        if first is None:
            raise InvalidMessage("that frequency has no occurrence from now on "
                                 "(every time is in the past, or past `until`)")
        row = {
            "id": new_id(), "key": key, "source": self.source, "title": title,
            "body_text": body_text, "body_html": body_html, "channels": chosen,
            "email_to": email.to if email else [], "email_cc": email.cc if email else [],
            "email_bcc": email.bcc if email else [], "reply_to": email.reply_to if email else [],
            "from_address": from_address, "from_name": from_name,
            "user_ids": in_app.user_ids if in_app else [],
            "in_app": {"level": level, "link": link, "category": category,
                       "entity_type": entity_type, "entity_id": entity_id, "data": data or {}},
            "context": dict(context or {}), "frequency": frequency.model_dump(mode="json"),
            "status": "active", "next_run_at": first, "runs_done": 0, "attempts": 0,
            "tenant_urn": tenant_urn, "project_id": project_id, "created_by": created_by,
            "created_at": now, "updated_at": now,
        }
        with store.begin() as conn:
            if key is not None:
                existing = store.get_reminder(conn, key)
                if existing is not None and existing["key"] == key:
                    if not replace:
                        raise ReminderExists(f"a reminder with key {key!r} is already scheduled")
                    conn.execute(delete(store.attachments)
                                 .where(store.attachments.c.reminder_id == existing["id"]))
                    conn.execute(delete(store.reminders).where(store.reminders.c.id == existing["id"]))
            conn.execute(insert(store.reminders).values(**row))
            if files:
                conn.execute(insert(store.attachments), [
                    {"id": new_id(), "reminder_id": row["id"], "filename": a.filename,
                     "content_type": a.content_type, "content": a.content, "size": a.size}
                    for a in files])
            return _reminder(store.get_reminder(conn, row["id"]))

    def cancel_reminder(self, id_or_key: str) -> bool:
        """Stop a reminder for good (it stays listed, as cancelled). False if unknown."""
        return self._set_status(id_or_key, "cancelled", next_run_at=None)

    def pause_reminder(self, id_or_key: str) -> bool:
        return self._set_status(id_or_key, "paused")

    def resume_reminder(self, id_or_key: str) -> bool:
        """Carry on from now: the next occurrence is the first one after this moment."""
        store = self._need_store("reminders")
        with store.begin() as conn:
            row = store.get_reminder(conn, id_or_key)
            if row is None or row["status"] != "paused":
                return False
            frequency = Frequency.model_validate(row["frequency"])
            nxt = (frequency.next_after(_utc(row["last_run_at"]), row["runs_done"], now=self._now())
                   if row["last_run_at"] else frequency.first(self._now()))
            conn.execute(update(store.reminders).where(store.reminders.c.id == row["id"]).values(
                status="active" if nxt else "completed", next_run_at=nxt, updated_at=self._now()))
            return True

    def get_reminder(self, id_or_key: str) -> Reminder | None:
        store = self._need_store("reminders")
        with store.engine.connect() as conn:
            row = store.get_reminder(conn, id_or_key)
        return _reminder(row) if row else None

    def list_reminders(self, *, created_by: str | None = None, status: str | None = None,
                       limit: int = 100) -> list[Reminder]:
        store = self._need_store("reminders")
        return [_reminder(r) for r in store.list_reminders(created_by=created_by, status=status,
                                                           source=self.source, limit=limit)]

    # ================================================================ the worker
    def run_due(self, *, now: datetime | None = None, limit: int = 50) -> RunReport:
        """Send every reminder that is due — each in its own transaction (see module
        docstring). Safe to run from several processes at once."""
        store = self._need_store("reminders")
        report = RunReport()
        for _ in range(limit):
            moment = _utc(now).astimezone(timezone.utc) if now else self._now()
            with store.begin() as conn:
                due = store.claim_due(conn, moment, 1)
                if not due:
                    break
                fresh = self._run_one(conn, due[0], moment, report)
            # Pushed only once committed: a rolled-back occurrence announces nothing.
            self._announce(*fresh)
        return report

    def _run_one(self, conn, row: dict, now: datetime, report: RunReport
                 ) -> tuple[list[dict], list[str]]:
        store = self.store
        report.processed += 1
        occurrence = row["runs_done"] + 1
        context = {**(row["context"] or {}), "occurrence": occurrence}
        channels = row["channels"] or []
        email_result: SendResult | None = None

        if "email" in channels:
            files = [Attachment(filename=a["filename"], content=a["content"],
                                content_type=a["content_type"])
                     for a in store.reminder_attachments(conn, row["id"])]
            try:
                message = self._email(subject=row["title"], to=row["email_to"], cc=row["email_cc"],
                                      bcc=row["email_bcc"], body_text=row["body_text"],
                                      body_html=row["body_html"], attachments=files,
                                      reply_to=row["reply_to"], from_address=row["from_address"],
                                      from_name=row["from_name"], context=context)
            except InvalidMessage as exc:
                email_result = SendResult("rejected", error=str(exc))
                store.log_delivery(conn, channel="email", status="rejected", reminder_id=row["id"],
                                   occurrence=occurrence, subject=row["title"], error=str(exc),
                                   source=row["source"])
            else:
                email_result = self._deliver(message)
                self._log_email(conn, message, email_result, reminder_id=row["id"],
                                occurrence=occurrence, source=row["source"])

        new_rows: list[dict] = []
        new_ids: list[str] = []
        if "in_app" in channels and row["attempts"] == 0 and row["user_ids"]:
            extra = row["in_app"] or {}
            message = InAppMessage(
                user_ids=row["user_ids"], title=render(row["title"], context),
                body=render(_plain(row["body_text"], row["body_html"]), context) or "",
                level=extra.get("level") or "info", link=extra.get("link"),
                category=extra.get("category"), source=row["source"],
                entity_type=extra.get("entity_type"), entity_id=extra.get("entity_id"),
                data=extra.get("data") or {}, tenant_urn=row["tenant_urn"],
                project_id=row["project_id"])
            new_rows = [{**r, "reminder_id": row["id"]} for r in self._in_app_rows(message)]
            new_ids = store.add_notifications(conn, new_rows)
            store.log_delivery(conn, channel="in_app", status="sent", reminder_id=row["id"],
                               occurrence=occurrence, subject=message.title,
                               recipients={"user_ids": message.user_ids}, source=row["source"])

        r = store.reminders
        # When this occurrence was DUE — kept across its retries, so the schedule carries
        # on from the slot, not from whenever a retry finally got through.
        due_at = _utc(row["occurrence_due_at"]) or _utc(row["next_run_at"])
        if email_result is not None and email_result.retryable \
                and row["attempts"] + 1 < self.settings.max_attempts:
            backoff = self.settings.retry_backoff_minutes or [5]
            wait = backoff[min(row["attempts"], len(backoff) - 1)]
            conn.execute(update(r).where(r.c.id == row["id"]).values(
                attempts=row["attempts"] + 1, last_error=email_result.error,
                occurrence_due_at=due_at, next_run_at=now + timedelta(minutes=wait),
                updated_at=now))
            report.retrying.append(row["id"])
            return new_rows, new_ids

        frequency = Frequency.model_validate(row["frequency"])
        nxt = frequency.next_after(due_at, occurrence, now=now)
        failed = email_result is not None and not email_result.ok
        conn.execute(update(r).where(r.c.id == row["id"]).values(
            runs_done=occurrence, attempts=0, occurrence_due_at=None, last_run_at=now,
            last_error=email_result.error if failed else None,
            next_run_at=nxt, status="active" if nxt else "completed", updated_at=now))
        if failed:
            report.failed.append(row["id"])
        else:
            report.sent += 1
        if nxt is None:
            report.completed.append(row["id"])
        return new_rows, new_ids

    # ================================================================== internals
    def _email(self, *, subject, to, cc, bcc, body_text, body_html, attachments, reply_to,
               from_address, from_name, context, headers=None) -> EmailMessage:
        files = list(attachments or [])
        check_attachments(files, max_mb=self.settings.max_attachment_mb,
                          max_total_mb=self.settings.max_total_attachments_mb)
        message = EmailMessage(
            to=to, cc=cc, bcc=bcc, reply_to=reply_to, subject=render(subject, context),
            body_text=render(body_text, context), body_html=render(body_html, context, escape=True),
            attachments=files, from_address=from_address, from_name=from_name,
            headers=headers or {})
        if len(message.recipients) > self.settings.max_recipients:
            raise InvalidMessage(f"{len(message.recipients)} recipients; the limit is "
                                 f"{self.settings.max_recipients} per message")
        return message

    def _deliver(self, message: EmailMessage) -> SendResult:
        try:
            return self.email.send(message,
                                   from_address=message.from_address or self.settings.from_address,
                                   from_name=message.from_name or self.settings.from_name)
        except Exception as exc:  # noqa: BLE001 — a transport bug must not lose the log
            log.exception("email transport %s raised", getattr(self.email, "name", "?"))
            return SendResult("failed", error=f"{type(exc).__name__}: {exc}")

    def _log_email(self, conn, message: EmailMessage, result: SendResult, *,
                   reminder_id: str | None = None, occurrence: int | None = None,
                   source: str | None = None) -> None:
        self.store.log_delivery(
            conn, channel="email", status=result.status, reminder_id=reminder_id,
            occurrence=occurrence, subject=message.subject,
            recipients={"to": message.to, "cc": message.cc, "bcc": message.bcc},
            attachments=[{"filename": a.filename, "size": a.size} for a in message.attachments],
            transport=getattr(self.email, "name", None), provider_ref=result.provider_ref,
            error=result.error, source=source or self.source)

    @staticmethod
    def _in_app_rows(message: InAppMessage) -> list[dict]:
        return [{"user_id": user, "title": message.title, "body": message.body,
                 "level": message.level, "link": message.link, "category": message.category,
                 "source": message.source, "entity_type": message.entity_type,
                 "entity_id": message.entity_id, "data": message.data,
                 "tenant_urn": message.tenant_urn, "project_id": message.project_id,
                 "expires_at": _utc(message.expires_at).astimezone(timezone.utc)
                 if message.expires_at else None} for user in message.user_ids]

    def _announce(self, rows: list[dict], ids: list[str]) -> None:
        if self._on_in_app is None:
            return
        try:
            self._on_in_app([{**row, "id": i} for row, i in zip(rows, ids)])
        except Exception:  # noqa: BLE001 — a push failing never undoes the notification
            log.exception("on_in_app hook raised")

    def _set_status(self, id_or_key: str, status: str, **values) -> bool:
        store = self._need_store("reminders")
        with store.begin() as conn:
            row = store.get_reminder(conn, id_or_key)
            if row is None or row["status"] in ("cancelled", "completed"):
                return False
            conn.execute(update(store.reminders).where(store.reminders.c.id == row["id"])
                         .values(status=status, updated_at=self._now(), **values))
            return True

    def _need_store(self, feature: str) -> Store:
        if self.store is None:
            raise NotConfigured(f"{feature} need a database: pass engine= to Notifier.from_settings "
                                "or set NOTIFY_DATABASE_URL")
        return self.store


# --- helpers ----------------------------------------------------------------------------
def _channels(channels, *, to, cc, bcc, user_ids) -> list[str]:
    if channels is not None:
        chosen = list(dict.fromkeys(channels))
        unknown = set(chosen) - {"email", "in_app"}
        if unknown:
            raise InvalidMessage(f"unknown channel(s) {sorted(unknown)}: use email, in_app")
    else:
        chosen = (["email"] if (to or cc or bcc) else []) + (["in_app"] if user_ids else [])
    if not chosen:
        raise InvalidMessage("nobody to notify: give to/cc/bcc for email or user_ids for in-app")
    return chosen


_TAGS = re.compile(r"<[^>]+>")


def _plain(body_text: str | None, body_html: str | None) -> str:
    """The in-app text: the plain body, or the HTML body with its tags taken out."""
    if body_text:
        return body_text
    if not body_html:
        return ""
    text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", body_html, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(_TAGS.sub("", text))).strip()


def _utc(value: datetime | None) -> datetime | None:
    """A stored instant, aware. SQLite hands back naive datetimes; they are UTC here."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _reminder(row: dict) -> Reminder:
    return Reminder(id=row["id"], key=row["key"], title=row["title"], status=row["status"],
                    channels=list(row["channels"] or []),
                    frequency=Frequency.model_validate(row["frequency"]),
                    next_run_at=_utc(row["next_run_at"]), last_run_at=_utc(row["last_run_at"]),
                    runs_done=row["runs_done"], last_error=row["last_error"], source=row["source"],
                    created_by=row["created_by"])
