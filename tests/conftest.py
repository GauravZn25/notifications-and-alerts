from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from notifications_and_alerts import MemoryTransport, Notifier, NotifySettings, Store


class Clock:
    """A clock the test moves by hand."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def set(self, value: datetime) -> None:
        self.now = value


@pytest.fixture
def clock() -> Clock:
    # 10:00 IST on Wednesday 11 November 2026.
    return Clock(datetime(2026, 11, 11, 4, 30, tzinfo=timezone.utc))


@pytest.fixture
def store() -> Store:
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    s = Store(engine)
    s.create_tables()
    return s


@pytest.fixture
def mail() -> MemoryTransport:
    return MemoryTransport()


@pytest.fixture
def notifier(store, mail, clock) -> Notifier:
    settings = NotifySettings(email_transport="memory", from_address="alerts@rodic.in",
                              from_name="AIPMS Alerts", retry_backoff_minutes=[5, 15])
    return Notifier(settings=settings, store=store, email=mail, source="M14", clock=clock)
