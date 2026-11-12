"""The in-app notification API, as a router any FastAPI module mounts:

    from notifications_and_alerts.fastapi import create_router
    app.include_router(create_router(notifier, current_user=current_user_id),
                       prefix="/api/v1/<module>")

    GET    /notifications                     the bell's list (newest first, ?unread_only,
                                              ?category, ?before=<created_at> to page)
    GET    /notifications/unread-count        {count, urgent, by_level} — poll this
    POST   /notifications/{id}/read           mark one read
    POST   /notifications/read-all            mark all read
    POST   /notifications/{id}/dismiss        hide it from the list
    GET    /notifications/reminders           reminders the caller scheduled
    POST   /notifications/reminders/{id}/cancel   cancel one of the caller's reminders

`current_user` is the host's own dependency returning the signed-in user's id — this
router never authenticates anyone itself, and every query is filtered to that id, so
one user can never read or change another's notifications.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from .notifier import Notifier


class NotificationOut(BaseModel):
    id: str
    title: str
    body: str
    level: str
    link: str | None
    category: str | None
    source: str | None
    entity_type: str | None
    entity_id: str | None
    data: dict[str, Any]
    created_at: datetime
    read_at: datetime | None


class NotificationList(BaseModel):
    items: list[NotificationOut]
    #: Pass as `before` for the next (older) page; null on the last page.
    next_before: datetime | None


class UnreadCount(BaseModel):
    count: int
    urgent: bool
    by_level: dict[str, int]


class ReminderOut(BaseModel):
    id: str
    key: str | None
    title: str
    status: str
    channels: list[str]
    schedule: str
    next_run_at: datetime | None
    last_run_at: datetime | None
    runs_done: int
    last_error: str | None


def create_router(notifier: Notifier, *, current_user: Callable[..., str],
                  prefix: str = "/notifications", tags: list[str] | None = None) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=tags or ["notifications"])
    store = notifier.store
    if store is None:
        raise RuntimeError("the notifications router needs a Notifier with a database")

    @router.get("", response_model=NotificationList)
    def list_notifications(unread_only: bool = False, category: str | None = None,
                           before: datetime | None = None,
                           limit: int = Query(20, ge=1, le=100),
                           user: str = Depends(current_user)) -> NotificationList:
        rows = store.list_notifications(user, unread_only=unread_only, limit=limit + 1,
                                        before=before, category=category)
        page = rows[:limit]
        return NotificationList(items=[NotificationOut(**r) for r in page],
                                next_before=page[-1]["created_at"] if len(rows) > limit else None)

    @router.get("/unread-count", response_model=UnreadCount)
    def unread_count(user: str = Depends(current_user)) -> UnreadCount:
        return UnreadCount(**store.unread_summary(user))

    @router.post("/read-all")
    def read_all(user: str = Depends(current_user)) -> dict:
        return {"marked": store.mark_read(user)}

    @router.post("/{notification_id}/read")
    def read_one(notification_id: str, user: str = Depends(current_user)) -> dict:
        return {"marked": store.mark_read(user, [notification_id])}

    @router.post("/{notification_id}/dismiss", status_code=status.HTTP_204_NO_CONTENT)
    def dismiss(notification_id: str, user: str = Depends(current_user)) -> None:
        if not store.dismiss(user, notification_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such notification.")

    @router.get("/reminders", response_model=list[ReminderOut])
    def my_reminders(state: str | None = Query(None, alias="status"),
                     user: str = Depends(current_user)) -> list[ReminderOut]:
        return [ReminderOut(id=r.id, key=r.key, title=r.title, status=r.status,
                            channels=r.channels, schedule=r.schedule, next_run_at=r.next_run_at,
                            last_run_at=r.last_run_at, runs_done=r.runs_done,
                            last_error=r.last_error)
                for r in notifier.list_reminders(created_by=user, status=state)]

    @router.post("/reminders/{reminder_id}/cancel")
    def cancel(reminder_id: str, user: str = Depends(current_user)) -> dict:
        found = notifier.get_reminder(reminder_id)
        if found is None or found.created_by != user or found.id != reminder_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such reminder.")
        return {"cancelled": notifier.cancel_reminder(reminder_id)}

    return router
