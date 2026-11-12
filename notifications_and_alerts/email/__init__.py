"""Email transports. `get_transport(settings)` picks one by `NOTIFY_EMAIL_TRANSPORT`.

    smtp     any SMTP server (smtp.py)
    graph    Microsoft Graph, app-only (graph.py)
    console  logs, sends nothing — development
    memory   keeps messages in `.sent` — tests

A host can also pass any object with `send(message, from_address=, from_name=)`.
"""
from __future__ import annotations

import logging

from ..messages import EmailMessage
from ..settings import NotifySettings
from .base import EmailTransport, SendResult
from .graph import GraphNotConfigured, GraphTransport
from .smtp import SmtpNotConfigured, SmtpTransport

log = logging.getLogger("notifications_and_alerts.email")


class ConsoleTransport:
    """Development: log who would have got what. Nothing leaves the machine."""

    name = "console"

    def send(self, message: EmailMessage, *, from_address: str, from_name: str | None) -> SendResult:
        log.info("EMAIL (console, not sent) from=%s to=%s cc=%s bcc=%s subject=%r attachments=%s",
                 from_address, message.to, message.cc, message.bcc, message.subject,
                 [a.filename for a in message.attachments])
        return SendResult("logged_only")


class MemoryTransport:
    """Tests: keep every message, with the sender it would have gone from."""

    name = "memory"

    def __init__(self) -> None:
        self.sent: list[tuple[str, EmailMessage]] = []
        #: Set to a SendResult to make the next sends answer it (e.g. a failure).
        self.next_result: SendResult | None = None

    def send(self, message: EmailMessage, *, from_address: str, from_name: str | None) -> SendResult:
        if self.next_result is not None:
            return self.next_result
        self.sent.append((from_address, message))
        return SendResult("sent", provider_ref=f"memory-{len(self.sent)}")


def get_transport(settings: NotifySettings) -> EmailTransport:
    kind = (settings.email_transport or "console").strip().lower()
    if kind == "smtp":
        return SmtpTransport(settings)
    if kind == "graph":
        return GraphTransport(settings)
    if kind == "memory":
        return MemoryTransport()
    if kind == "console":
        return ConsoleTransport()
    raise ValueError(f"NOTIFY_EMAIL_TRANSPORT={kind!r}: use smtp, graph, console or memory")


__all__ = ["ConsoleTransport", "EmailTransport", "GraphNotConfigured", "GraphTransport",
           "MemoryTransport", "SendResult", "SmtpNotConfigured", "SmtpTransport", "get_transport"]
