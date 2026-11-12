"""Configuration, from the environment (prefix `NOTIFY_`) or passed in code.

A host module either lets `Notifier.from_settings()` read these, or builds a
`NotifySettings(...)` itself — e.g. from its own settings object, so a module keeps
one env namespace (M14 can map `DOCSHUB_SMTP_*` onto these fields).
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class NotifySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOTIFY_", env_file=".env", extra="ignore")

    #: Which email transport sends: smtp | graph | console | memory.
    #:   console — logs the message and sends nothing (development)
    #:   memory  — keeps messages in a list (tests)
    email_transport: str = "console"
    #: The sender when a message does not name one.
    from_address: str = "no-reply@example.com"
    from_name: str = "AIPMS"

    # --- SMTP ---------------------------------------------------------------------
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    #: STARTTLS on a plain connection (587). Turn off with `smtp_use_ssl` for 465.
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False
    smtp_timeout_seconds: float = 30.0

    # --- Microsoft Graph, app-only (client credentials, Mail.Send application) -----
    graph_tenant_id: str = ""
    graph_client_id: str = ""
    graph_client_secret: str = ""
    #: The mailbox the app sends AS (needs Mail.Send for it).
    graph_sender: str = ""
    graph_save_to_sent_items: bool = True
    graph_timeout_seconds: float = 30.0

    # --- limits -------------------------------------------------------------------
    #: Per attachment, and for all of a message's attachments together.
    max_attachment_mb: float = 20.0
    max_total_attachments_mb: float = 25.0
    #: Recipients across to + cc + bcc in one message.
    max_recipients: int = 100

    # --- storage (in-app notifications, reminders, the delivery log) ----------------
    #: SQLAlchemy URL. Blank: the host passes its own engine to `Notifier`.
    database_url: str = ""
    #: The schema the tables live in (Postgres). Blank = the default schema.
    db_schema: str = ""
    #: Table name prefix, so the tables sit beside a module's own without clashing.
    table_prefix: str = "notify_"

    # --- reminders ----------------------------------------------------------------
    #: Default time zone for daily/weekly/monthly reminders.
    timezone: str = "Asia/Kolkata"
    #: How often the worker looks for due reminders.
    worker_interval_seconds: float = 30.0
    #: Attempts per occurrence before a failing reminder moves on to its next one.
    max_attempts: int = 4
    #: Minutes to wait before attempt 2, 3, 4… (the last value repeats).
    retry_backoff_minutes: list[int] = [5, 15, 60]
