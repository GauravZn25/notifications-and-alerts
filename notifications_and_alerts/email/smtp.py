"""SMTP — works with any mail server (Office 365, Gmail, SES SMTP, a customer's relay).

BCC is put on the ENVELOPE only: the SMTP server is told every recipient, while the
message's headers name `To` and `Cc` alone, so no recipient can see who was blind-
copied. Text and HTML bodies go as `multipart/alternative`; attachments as parts.
"""
from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, formatdate, make_msgid

from ..messages import EmailMessage, bare_address
from ..settings import NotifySettings
from .base import SendResult


class SmtpNotConfigured(RuntimeError):
    pass


class SmtpTransport:
    name = "smtp"

    def __init__(self, settings: NotifySettings, *, smtp_factory=None) -> None:
        if not settings.smtp_host.strip():
            raise SmtpNotConfigured("NOTIFY_EMAIL_TRANSPORT=smtp but NOTIFY_SMTP_HOST is not set")
        self._s = settings
        # Tests pass a fake; production uses the standard library's client.
        self._factory = smtp_factory or (smtplib.SMTP_SSL if settings.smtp_use_ssl else smtplib.SMTP)

    def build(self, message: EmailMessage, *, from_address: str, from_name: str | None) -> MimeMessage:
        mime = MimeMessage()
        sender = bare_address(from_address)
        mime["From"] = formataddr((from_name, sender)) if from_name else sender
        if message.to:
            mime["To"] = ", ".join(message.to)
        if message.cc:
            mime["Cc"] = ", ".join(message.cc)
        if message.reply_to:
            mime["Reply-To"] = ", ".join(message.reply_to)
        mime["Subject"] = message.subject
        mime["Date"] = formatdate(localtime=False)
        mime["Message-ID"] = make_msgid(domain=sender.rsplit("@", 1)[-1])
        for key, value in message.headers.items():
            mime[key] = value
        if message.body_text and message.body_html:
            mime.set_content(message.body_text)
            mime.add_alternative(message.body_html, subtype="html")
        elif message.body_html:
            mime.set_content(message.body_html, subtype="html")
        else:
            mime.set_content(message.body_text or "")
        for a in message.attachments:
            maintype, _, subtype = a.content_type.partition("/")
            mime.add_attachment(a.content, maintype=maintype or "application",
                                subtype=subtype or "octet-stream", filename=a.filename)
        return mime

    def send(self, message: EmailMessage, *, from_address: str, from_name: str | None) -> SendResult:
        mime = self.build(message, from_address=from_address, from_name=from_name)
        s = self._s
        try:
            with self._factory(s.smtp_host, s.smtp_port, timeout=s.smtp_timeout_seconds) as smtp:
                if s.smtp_starttls and not s.smtp_use_ssl:
                    smtp.starttls(context=ssl.create_default_context())
                if s.smtp_username:
                    smtp.login(s.smtp_username, s.smtp_password)
                refused = smtp.send_message(mime, from_addr=bare_address(from_address),
                                            to_addrs=message.recipients)
        except smtplib.SMTPRecipientsRefused as exc:
            return SendResult("rejected", error=f"every recipient was refused: {list(exc.recipients)}")
        except (smtplib.SMTPSenderRefused, smtplib.SMTPDataError) as exc:
            code = getattr(exc, "smtp_code", 0) or 0
            status = "rejected" if 500 <= code < 600 else "failed"
            return SendResult(status, error=f"SMTP {code}: {_text(exc)}")
        except smtplib.SMTPAuthenticationError as exc:
            return SendResult("failed", error=f"SMTP login failed: {_text(exc)}")
        except (smtplib.SMTPException, OSError) as exc:
            return SendResult("failed", error=f"{type(exc).__name__}: {exc}")
        error = f"refused for {sorted(refused)}" if refused else None
        return SendResult("sent", provider_ref=mime["Message-ID"], error=error)


def _text(exc: Exception) -> str:
    error = getattr(exc, "smtp_error", b"")
    return error.decode(errors="replace") if isinstance(error, bytes) else str(error or exc)
