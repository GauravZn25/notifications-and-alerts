"""Against a real Postgres, when NOTIFY_TEST_DATABASE_URL is set: the tables in a
schema of their own, and two workers never taking the same due reminder.

    NOTIFY_TEST_DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/db python -m pytest
"""
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text

from notifications_and_alerts import Frequency, MemoryTransport, Notifier, NotifySettings, Store

URL = os.environ.get("NOTIFY_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="NOTIFY_TEST_DATABASE_URL is not set")


@pytest.fixture
def pg():
    engine = create_engine(URL)
    schema = f"notify_test_{uuid.uuid4().hex[:8]}"
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    store = Store(engine, schema=schema)
    store.create_tables()
    yield store
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def test_a_due_reminder_is_taken_by_one_worker_only(pg):
    now = datetime.now(timezone.utc)
    notifier = Notifier(settings=NotifySettings(), store=pg, email=MemoryTransport(),
                        clock=lambda: now)
    reminder = notifier.schedule_reminder(title="x", user_ids=["u-1"], frequency=Frequency.once())
    assert reminder.next_run_at is not None and reminder.next_run_at.tzinfo is not None

    with pg.engine.begin() as first:
        taken = pg.claim_due(first, now + timedelta(seconds=1), 10)
        with pg.engine.begin() as second:
            assert pg.claim_due(second, now + timedelta(seconds=1), 10) == []   # skipped, locked
        assert [r["id"] for r in taken] == [reminder.id]

    report = notifier.run_due(now=now + timedelta(seconds=1))
    assert report.sent == 1 and pg.unread_summary("u-1")["count"] == 1
