"""What can be sent: an email (to/cc/bcc, title, text and/or HTML body, attachments)
and an in-app notification (for signed-in users, shown in the bell and as a toast).

Both validate on construction — addresses, at least one body, attachment sizes —
so a mistake fails where the host code makes it, not later inside a worker.
"""
from __future__ import annotations

import html
import mimetypes
import re
import string
from datetime import datetime
from email.utils import formataddr, parseaddr
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_ADDRESS = re.compile(r"^[^@\s<>\"']+@[^@\s<>\"']+\.[^@\s<>\"']+$")

Level = Literal["info", "success", "warning", "danger"]
Channel = Literal["email", "in_app"]


class InvalidMessage(ValueError):
    """The message cannot be sent as given — the text says what to fix."""


# --- addresses ------------------------------------------------------------------------
def normalise_address(value: str) -> str:
    """`"Name <a@b.c>"` or `"a@b.c"` → the same, cleaned; raises on anything else."""
    name, address = parseaddr((value or "").strip())
    if not _ADDRESS.match(address or ""):
        raise InvalidMessage(f"{value!r} is not an email address")
    return formataddr((name, address.lower())) if name else address.lower()


def bare_address(value: str) -> str:
    """The address part of `"Name <a@b.c>"`."""
    return parseaddr(value)[1]


def _addresses(values) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in re.split(r"[;,]", values) if v.strip()]
    seen, out = set(), []
    for value in values:
        address = normalise_address(value)
        key = bare_address(address)
        if key not in seen:
            seen.add(key)
            out.append(address)
    return out


# --- attachments ----------------------------------------------------------------------
class Attachment(BaseModel):
    """One file to attach. Build with `from_path` or `from_bytes`."""

    model_config = ConfigDict(frozen=True)

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"

    @classmethod
    def from_bytes(cls, filename: str, content: bytes, content_type: str | None = None) -> "Attachment":
        return cls(filename=Path(filename).name or "attachment", content=content,
                   content_type=content_type or _guess_type(filename))

    @classmethod
    def from_path(cls, path: str | Path, *, filename: str | None = None,
                  content_type: str | None = None) -> "Attachment":
        path = Path(path)
        name = filename or path.name
        return cls(filename=name, content=path.read_bytes(),
                   content_type=content_type or _guess_type(name))

    @property
    def size(self) -> int:
        return len(self.content)


def _guess_type(name: str) -> str:
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def check_attachments(attachments: list[Attachment], *, max_mb: float, max_total_mb: float) -> None:
    total = 0
    for a in attachments:
        if a.size > max_mb * 1024 * 1024:
            raise InvalidMessage(f"{a.filename} is {a.size / 1048576:.1f} MB; "
                                 f"the limit per attachment is {max_mb:g} MB")
        total += a.size
    if total > max_total_mb * 1024 * 1024:
        raise InvalidMessage(f"the attachments come to {total / 1048576:.1f} MB; "
                             f"the limit per message is {max_total_mb:g} MB")


# --- placeholders ---------------------------------------------------------------------
class _Keep(dict):
    def __missing__(self, key):  # an unknown {placeholder} stays as written
        return "{" + key + "}"


def render(template: str | None, context: dict[str, Any] | None, *, escape: bool = False) -> str | None:
    """`"Reply to {ref} by {due}"` with `context` filled in. Unknown placeholders stay;
    `escape` HTML-escapes the VALUES (for an HTML body), never the template."""
    if template is None or not context:
        return template
    values = {k: html.escape(str(v)) if escape else v for k, v in context.items()}
    try:
        return string.Formatter().vformat(template, (), _Keep(values))
    except (ValueError, IndexError):  # a stray "{" in the text: leave it untouched
        return template


# --- the two messages -----------------------------------------------------------------
class _Checked(BaseModel):
    """Validation problems surface as ONE exception type, `InvalidMessage`, with plain
    reasons — never pydantic's ValidationError, which a host would have to know about."""

    def __init__(self, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise InvalidMessage("; ".join(_reason(e) for e in exc.errors())) from None


def _reason(error: dict) -> str:
    message = str(error.get("msg", "")).removeprefix("Value error, ")
    where = ".".join(str(p) for p in error.get("loc", ()) if not isinstance(p, int))
    return message if not where or where in message else f"{where}: {message}"


class EmailMessage(_Checked):
    """An email. `to` + `cc` + `bcc` need at least one address; `bcc` never appears in
    the headers. At least one of `body_text` / `body_html` is required — give both and
    mail clients pick."""

    to: list[str] = Field(default_factory=list)
    cc: list[str] = Field(default_factory=list)
    bcc: list[str] = Field(default_factory=list)
    subject: str
    body_text: str | None = None
    body_html: str | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    reply_to: list[str] = Field(default_factory=list)
    #: Override the configured sender.
    from_address: str | None = None
    from_name: str | None = None
    #: Extra headers (e.g. a module's own reference). Never Bcc / From / To.
    headers: dict[str, str] = Field(default_factory=dict)

    @field_validator("to", "cc", "bcc", "reply_to", mode="before")
    @classmethod
    def _clean(cls, value):
        return _addresses(value)

    @field_validator("from_address")
    @classmethod
    def _sender(cls, value):
        return normalise_address(value) if value else value

    @field_validator("headers")
    @classmethod
    def _no_routing_headers(cls, value: dict[str, str]):
        banned = {"bcc", "cc", "to", "from", "subject", "reply-to"}
        for key, item in value.items():
            if key.lower() in banned:
                raise InvalidMessage(f"set {key} through its own field, not headers")
            if "\n" in item or "\r" in item or "\n" in key:
                raise InvalidMessage(f"header {key!r} has a line break")
        return value

    @model_validator(mode="after")
    def _complete(self):
        if not (self.to or self.cc or self.bcc):
            raise InvalidMessage("the email has no recipients (to, cc or bcc)")
        if not (self.subject or "").strip():
            raise InvalidMessage("the email needs a title (subject)")
        if "\n" in self.subject or "\r" in self.subject:
            raise InvalidMessage("the title (subject) cannot contain a line break")
        if not ((self.body_text or "").strip() or (self.body_html or "").strip()):
            raise InvalidMessage("the email needs a body (body_text or body_html)")
        return self

    @property
    def recipients(self) -> list[str]:
        """Every envelope recipient, bcc included — what the SMTP server is told."""
        return [bare_address(a) for a in (*self.to, *self.cc, *self.bcc)]


class InAppMessage(_Checked):
    """An in-app notification for signed-in users: the bell's list, and a toast while
    they are on a screen. `link` is where clicking it goes (an app path or URL)."""

    user_ids: list[str]
    title: str
    body: str = ""
    level: Level = "info"
    link: str | None = None
    #: A grouping the UI can filter on, e.g. "deadline", "approval", "mention".
    category: str | None = None
    #: Which module raised it, e.g. "M14".
    source: str | None = None
    entity_type: str | None = None
    entity_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    tenant_urn: str | None = None
    project_id: str | None = None
    #: After this it is no longer listed.
    expires_at: datetime | None = None

    @field_validator("user_ids", mode="before")
    @classmethod
    def _users(cls, value):
        if isinstance(value, str):
            value = [value]
        out = list(dict.fromkeys(str(v).strip() for v in (value or []) if str(v).strip()))
        if not out:
            raise InvalidMessage("an in-app notification needs at least one user id")
        return out

    @model_validator(mode="after")
    def _titled(self):
        if not (self.title or "").strip():
            raise InvalidMessage("an in-app notification needs a title")
        return self
