"""The email transport port: anything that can send an `EmailMessage`.

A host module can plug in its own (e.g. a module that already sends through its
platform's mail service) by implementing `send` — the rest of this package does not
care how mail leaves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ..messages import EmailMessage

Status = Literal["sent", "logged_only", "rejected", "failed"]


@dataclass(frozen=True)
class SendResult:
    """What happened to one email.

        sent         the provider accepted it
        logged_only  nothing went out — a console/test transport only logged it
        rejected     the provider refused THIS message (bad address, too large):
                     the same message would fail again, so it is not retried
        failed       the provider could not be reached / errored: worth retrying
    """

    status: Status
    provider_ref: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status in ("sent", "logged_only")

    @property
    def retryable(self) -> bool:
        return self.status == "failed"


class EmailTransport(Protocol):
    name: str

    def send(self, message: EmailMessage, *, from_address: str, from_name: str | None) -> SendResult:
        ...
