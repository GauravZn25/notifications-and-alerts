"""A module wired up end to end — the shape every AIPMS backend copies.

    pip install -e "..[fastapi]"
    NOTIFY_DATABASE_URL=sqlite:///./notify-demo.db uvicorn examples.fastapi_app:app --reload

Then:
    curl -X POST localhost:8000/api/v1/demo/letters/42/remind -H 'X-User: u-1'
    curl localhost:8000/api/v1/demo/notifications/unread-count -H 'X-User: u-1'
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Header

import notifications_and_alerts as na
from notifications_and_alerts.fastapi import create_router

# 1. One notifier for the process — the module's own engine, schema and source code.
settings = na.NotifySettings()  # NOTIFY_* from the environment
notifier = na.configure(na.Notifier.from_settings(
    settings, engine=settings.database_url or "sqlite:///./notify-demo.db",
    source="DEMO", create_tables=True))


# 2. The module's own "who is signed in" — here a header; in a real module, its JWT.
def current_user(x_user: str = Header(...)) -> str:
    return x_user


# 3. Due reminders are sent by a worker thread beside the API.
@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = na.ReminderWorker(notifier).start()
    try:
        yield
    finally:
        worker.stop()


app = FastAPI(lifespan=lifespan)
# 4. The bell's API, behind the module's auth.
app.include_router(create_router(notifier, current_user=current_user), prefix="/api/v1/demo")


# 5. Anywhere in the module's own code: notify now, or remind on a schedule.
@app.post("/api/v1/demo/letters/{letter_id}/remind")
def remind_reply_due(letter_id: str, x_user: str = Header(...)) -> dict:
    due = datetime.now(timezone.utc) + timedelta(days=3)
    reminder = na.send_notification(
        title="Reply due for letter {ref}",
        body_html="<p>The reply to <b>{ref}</b> is due on {due}.</p>",
        context={"ref": letter_id, "due": due.strftime("%d %b %Y")},
        to=["pm@knr.example"], cc=["tl@rodic.example"], bcc=["records@rodic.example"],
        user_ids=[x_user], level="warning", link=f"/letters/{letter_id}",
        entity_type="letter", entity_id=letter_id,
        frequency=na.Frequency.before(due, [timedelta(days=2), timedelta(days=1), timedelta(hours=2)]),
        key=f"letter:{letter_id}:reply-due", created_by=x_user)
    return {"reminder": reminder.id, "next_run_at": reminder.next_run_at, "schedule": reminder.schedule}


@app.post("/api/v1/demo/letters/{letter_id}/replied")
def replied(letter_id: str) -> dict:
    # The condition the reminder was about is met: stop it.
    return {"cancelled": na.cancel_reminder(f"letter:{letter_id}:reply-due")}
