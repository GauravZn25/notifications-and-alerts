"""AIPMS notifications & alerts — email, in-app, and scheduled reminders.

Either build a `Notifier` and keep it, or `configure()` one once and use the
module-level functions anywhere in the host code:

    import notifications_and_alerts as na

    na.configure(na.Notifier.from_settings(engine=engine, source="M14", create_tables=True))

    na.send_notification(title="Reply due: {ref}", body_text="…", context={"ref": "1142"},
                         to=["pm@knr.example"], cc=["tl@rodic.in"], bcc=["audit@rodic.in"],
                         attachments=[na.Attachment.from_path("letter.pdf")],
                         user_ids=["u-17"], frequency=na.Frequency.daily(at="09:00", max_runs=3),
                         key="letter:42:reply-due")
    na.cancel_reminder("letter:42:reply-due")
"""
from __future__ import annotations

from .email import ConsoleTransport, EmailTransport, MemoryTransport, SendResult, get_transport
from .frequency import Frequency
from .messages import Attachment, EmailMessage, InAppMessage, InvalidMessage, render
from .notifier import NotConfigured, Notifier, Outcome, Reminder, ReminderExists, RunReport
from .settings import NotifySettings
from .store import Store
from .worker import ReminderWorker

_default: Notifier | None = None


def configure(notifier: Notifier) -> Notifier:
    """Make `notifier` the one the module-level functions use."""
    global _default
    _default = notifier
    return notifier


def get_notifier() -> Notifier:
    if _default is None:
        raise NotConfigured("call notifications_and_alerts.configure(Notifier.from_settings(...)) first")
    return _default


def send_notification(**kwargs) -> Outcome | Reminder:
    """See `Notifier.send_notification`: email and/or in-app, now or on a `frequency`."""
    return get_notifier().send_notification(**kwargs)


def send_email(**kwargs) -> SendResult:
    return get_notifier().send_email(**kwargs)


def notify(**kwargs) -> list[str]:
    return get_notifier().notify(**kwargs)


def schedule_reminder(**kwargs) -> Reminder:
    return get_notifier().schedule_reminder(**kwargs)


def cancel_reminder(id_or_key: str) -> bool:
    return get_notifier().cancel_reminder(id_or_key)


__all__ = [
    "Attachment", "ConsoleTransport", "EmailMessage", "EmailTransport", "Frequency",
    "InAppMessage", "InvalidMessage", "MemoryTransport", "NotConfigured", "Notifier",
    "NotifySettings", "Outcome", "Reminder", "ReminderExists", "ReminderWorker", "RunReport",
    "SendResult", "Store", "cancel_reminder", "configure", "get_notifier", "get_transport",
    "notify", "render", "schedule_reminder", "send_email", "send_notification",
]
