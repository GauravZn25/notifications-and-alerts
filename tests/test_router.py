"""The in-app API a module mounts: each user sees and changes only their own."""
import pytest
from fastapi import FastAPI, Header
from fastapi.testclient import TestClient

from notifications_and_alerts import Frequency
from notifications_and_alerts.fastapi import create_router


def current_user(x_user: str = Header(...)) -> str:
    """Stands in for a module's real auth dependency."""
    return x_user


@pytest.fixture
def api(notifier):
    app = FastAPI()
    app.include_router(create_router(notifier, current_user=current_user), prefix="/api/v1/m14")
    return TestClient(app)


BASE = "/api/v1/m14/notifications"


def test_the_bell_counts_lists_and_marks_read(api, notifier):
    notifier.notify(user_ids="u-1", title="Letter received", level="info")
    notifier.notify(user_ids="u-1", title="Reply overdue", level="danger", link="/m14/deadlines")
    notifier.notify(user_ids="u-2", title="Not yours")
    me = {"X-User": "u-1"}

    assert api.get(f"{BASE}/unread-count", headers=me).json() == {
        "count": 2, "urgent": True, "by_level": {"info": 1, "danger": 1}}
    items = api.get(BASE, headers=me).json()["items"]
    assert [i["title"] for i in items] == ["Reply overdue", "Letter received"]

    assert api.post(f"{BASE}/{items[0]['id']}/read", headers=me).json() == {"marked": 1}
    assert api.post(f"{BASE}/{items[0]['id']}/read", headers={"X-User": "u-2"}).json() == {"marked": 0}
    assert api.get(f"{BASE}/unread-count", headers=me).json()["count"] == 1
    assert api.get(BASE, headers=me, params={"unread_only": True}).json()["items"][0]["title"] == \
        "Letter received"
    assert api.post(f"{BASE}/read-all", headers=me).json() == {"marked": 1}
    assert api.post(f"{BASE}/{items[1]['id']}/dismiss", headers=me).status_code == 204
    assert api.post(f"{BASE}/{items[1]['id']}/dismiss", headers={"X-User": "u-2"}).status_code == 404


def test_paging(api, notifier):
    for i in range(5):
        notifier.notify(user_ids="u-1", title=f"n{i}")
    first = api.get(BASE, headers={"X-User": "u-1"}, params={"limit": 3}).json()
    assert len(first["items"]) == 3 and first["next_before"]
    rest = api.get(BASE, headers={"X-User": "u-1"},
                   params={"limit": 3, "before": first["next_before"]}).json()
    assert len(rest["items"]) == 2 and rest["next_before"] is None


def test_my_reminders_and_cancelling_only_my_own(api, notifier):
    mine = notifier.schedule_reminder(title="Weekly digest", user_ids=["u-1"], created_by="u-1",
                                      frequency=Frequency.weekly(days="mon"))
    notifier.schedule_reminder(title="Someone else's", user_ids=["u-2"], created_by="u-2",
                               frequency=Frequency.daily())
    listed = api.get(f"{BASE}/reminders", headers={"X-User": "u-1"}).json()
    assert [(r["title"], r["schedule"]) for r in listed] == [
        ("Weekly digest", "weekly on mon at 09:00 (Asia/Kolkata)")]
    assert api.post(f"{BASE}/reminders/{mine.id}/cancel", headers={"X-User": "u-2"}).status_code == 404
    assert api.post(f"{BASE}/reminders/{mine.id}/cancel", headers={"X-User": "u-1"}).json() == {
        "cancelled": True}
