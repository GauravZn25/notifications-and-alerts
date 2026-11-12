"""Sending now and on a schedule: email and in-app, the delivery log, retries, and the
reminder lifecycle (replace, pause, resume, cancel, complete)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from notifications_and_alerts import (Attachment, Frequency, InvalidMessage, NotConfigured,
                                      Notifier, NotifySettings, Reminder, ReminderExists,
                                      SendResult)

IST = ZoneInfo("Asia/Kolkata")
PDF = Attachment.from_bytes("GAD-rev2.pdf", b"%PDF-1.4 drawing")


def _addresses(message):
    return message.to, message.cc, message.bcc


# --- now ------------------------------------------------------------------------------
def test_one_call_sends_the_email_and_the_in_app_notification(notifier, mail, store):
    outcome = notifier.send_notification(
        title="Reply due for {ref}", body_html="<p>Reply to <b>{ref}</b> by {due}.</p>",
        context={"ref": "NHAI/1142", "due": "16 Nov"},
        to=["pm@knr.example"], cc=["tl@rodic.in"], bcc=["audit@rodic.in"], attachments=[PDF],
        user_ids=["u-17", "u-18"], level="warning", link="/m14/register/letters/42",
        entity_type="letter", entity_id="42")

    assert outcome.ok and outcome.email.status == "sent"
    [(sender, message)] = mail.sent
    assert sender == "alerts@rodic.in"
    assert message.subject == "Reply due for NHAI/1142"
    assert _addresses(message) == (["pm@knr.example"], ["tl@rodic.in"], ["audit@rodic.in"])
    assert message.attachments == [PDF]
    assert len(outcome.notification_ids) == 2

    [mine] = store.list_notifications("u-17")
    assert (mine["title"], mine["level"], mine["link"]) == (
        "Reply due for NHAI/1142", "warning", "/m14/register/letters/42")
    assert mine["body"] == "Reply to NHAI/1142 by 16 Nov."          # the HTML, as text
    assert store.unread_summary("u-17") == {"count": 1, "by_level": {"warning": 1}, "urgent": True}

    with store.engine.connect() as conn:
        rows = conn.execute(store.deliveries.select()).mappings().all()
    assert {(r["channel"], r["status"]) for r in rows} == {("email", "sent"), ("in_app", "sent")}
    email_row = next(r for r in rows if r["channel"] == "email")
    assert email_row["recipients"]["bcc"] == ["audit@rodic.in"]
    assert email_row["attachments"] == [{"filename": "GAD-rev2.pdf", "size": PDF.size}]


def test_channels_follow_the_recipients_given(notifier, mail, store):
    notifier.send_notification(title="Heads up", body_text="x", user_ids=["u-1"])
    assert mail.sent == [] and len(store.list_notifications("u-1")) == 1
    with pytest.raises(InvalidMessage, match="nobody to notify"):
        notifier.send_notification(title="To nobody", body_text="x")


def test_reading_and_dismissing_is_per_user(notifier, store):
    [a] = notifier.notify(user_ids="u-1", title="One")
    notifier.notify(user_ids=["u-1", "u-2"], title="Two")
    assert store.mark_read("u-2", [a]) == 0                       # not theirs
    assert store.mark_read("u-1", [a]) == 1
    assert store.unread_summary("u-1")["count"] == 1
    assert store.dismiss("u-1", a) and [n["title"] for n in store.list_notifications("u-1")] == ["Two"]
    assert store.mark_read("u-1") == 1 and store.unread_summary("u-1")["count"] == 0


def test_without_a_database_only_email_works(mail):
    plain = Notifier(settings=NotifySettings(), email=mail)
    assert plain.send_email(to="a@b.co", subject="x", body_text="y").ok
    with pytest.raises(NotConfigured):
        plain.notify(user_ids="u-1", title="x")


def test_attachment_limits(notifier):
    big = Attachment.from_bytes("huge.zip", b"x" * (21 * 1024 * 1024))
    with pytest.raises(InvalidMessage, match="limit per attachment"):
        notifier.send_email(to="a@b.co", subject="x", body_text="y", attachments=[big])


# --- reminders -------------------------------------------------------------------------
def _daily(notifier, **over) -> Reminder:
    return notifier.send_notification(**{
        "title": "Reply due: {ref} (reminder {occurrence})", "body_text": "Please reply to {ref}.",
        "context": {"ref": "NHAI/1142"}, "to": ["pm@knr.example"], "bcc": ["audit@rodic.in"],
        "attachments": [PDF], "user_ids": ["u-17"],
        "frequency": Frequency.daily(at="09:00", max_runs=3), "key": "letter:42:reply-due",
        "created_by": "u-1", **over})


def test_a_daily_reminder_sends_each_morning_then_completes(notifier, mail, store, clock):
    reminder = _daily(notifier)
    assert isinstance(reminder, Reminder)
    assert reminder.next_run_at == datetime(2026, 11, 12, 9, 0, tzinfo=IST)  # tomorrow 09:00
    assert notifier.run_due().processed == 0                               # not due yet

    for day, expected in ((12, 1), (13, 2), (14, 3)):
        clock.set(datetime(2026, 11, day, 9, 0, 30, tzinfo=IST))
        report = notifier.run_due()
        assert report.sent == 1, report
        assert len(mail.sent) == expected
    subjects = [m.subject for _, m in mail.sent]
    assert subjects == ["Reply due: NHAI/1142 (reminder 1)", "Reply due: NHAI/1142 (reminder 2)",
                        "Reply due: NHAI/1142 (reminder 3)"]
    assert all(m.attachments == [PDF] and m.bcc == ["audit@rodic.in"] for _, m in mail.sent)
    assert len(store.list_notifications("u-17")) == 3

    done = notifier.get_reminder("letter:42:reply-due")
    assert (done.status, done.runs_done, done.next_run_at) == ("completed", 3, None)
    clock.set(datetime(2026, 11, 20, 9, 0, tzinfo=IST))
    assert notifier.run_due().processed == 0


def test_a_failing_email_is_retried_with_backoff_then_moves_on(notifier, mail, store, clock):
    _daily(notifier)
    clock.set(datetime(2026, 11, 12, 9, 0, tzinfo=IST))
    mail.next_result = SendResult("failed", error="SMTP 421 try later")

    assert notifier.run_due().retrying
    r = notifier.get_reminder("letter:42:reply-due")
    assert r.next_run_at == datetime(2026, 11, 12, 9, 5, tzinfo=IST)       # +5 min
    assert (r.runs_done, r.last_error) == (0, "SMTP 421 try later")
    assert len(store.list_notifications("u-17")) == 1, "in-app once, not per retry"

    clock.set(datetime(2026, 11, 12, 9, 5, tzinfo=IST))
    notifier.run_due()
    r = notifier.get_reminder("letter:42:reply-due")
    assert r.next_run_at == datetime(2026, 11, 12, 9, 20, tzinfo=IST)      # +15 min

    mail.next_result = None                                               # back up
    clock.set(datetime(2026, 11, 12, 9, 20, tzinfo=IST))
    assert notifier.run_due().sent == 1
    r = notifier.get_reminder("letter:42:reply-due")
    # The schedule carries on from the 09:00 slot, not from the retry.
    assert (r.runs_done, r.next_run_at, r.last_error) == (
        1, datetime(2026, 11, 13, 9, 0, tzinfo=IST), None)
    assert len(store.list_notifications("u-17")) == 1
    statuses = [d["status"] for d in store.deliveries_for(r.id) if d["channel"] == "email"]
    assert statuses == ["failed", "failed", "sent"]


def test_after_the_last_attempt_the_occurrence_is_given_up(notifier, mail, clock):
    notifier.settings.max_attempts = 2
    _daily(notifier)
    mail.next_result = SendResult("failed", error="down")
    clock.set(datetime(2026, 11, 12, 9, 0, tzinfo=IST))
    notifier.run_due()
    clock.set(datetime(2026, 11, 12, 9, 6, tzinfo=IST))
    report = notifier.run_due()
    r = notifier.get_reminder("letter:42:reply-due")
    assert report.failed == [r.id]
    assert (r.runs_done, r.last_error, r.next_run_at) == (
        1, "down", datetime(2026, 11, 13, 9, 0, tzinfo=IST))


def test_a_rejected_email_is_not_retried(notifier, mail, clock):
    _daily(notifier)
    mail.next_result = SendResult("rejected", error="550 no such user")
    clock.set(datetime(2026, 11, 12, 9, 0, tzinfo=IST))
    assert notifier.run_due().failed
    assert notifier.get_reminder("letter:42:reply-due").runs_done == 1


def test_scheduling_the_same_key_replaces_it_unless_told_not_to(notifier, store):
    first = _daily(notifier)
    second = _daily(notifier, title="Final reminder", frequency=Frequency.once(
        datetime(2026, 11, 15, 10, 0, tzinfo=IST)), attachments=[])
    assert second.id != first.id and notifier.get_reminder(first.id) is None
    assert notifier.get_reminder("letter:42:reply-due").title == "Final reminder"
    with store.engine.connect() as conn:
        assert conn.execute(store.attachments.select()).all() == []
    with pytest.raises(ReminderExists):
        _daily(notifier, replace=False)


def test_pause_resume_and_cancel(notifier, mail, clock):
    _daily(notifier, frequency=Frequency.daily(at="09:00"))
    assert notifier.pause_reminder("letter:42:reply-due")
    clock.set(datetime(2026, 11, 14, 9, 0, tzinfo=IST))
    assert notifier.run_due().processed == 0
    assert notifier.resume_reminder("letter:42:reply-due")
    assert notifier.get_reminder("letter:42:reply-due").next_run_at == \
        datetime(2026, 11, 14, 9, 0, tzinfo=IST)
    assert notifier.cancel_reminder("letter:42:reply-due")
    assert notifier.run_due().processed == 0 and mail.sent == []
    assert notifier.get_reminder("letter:42:reply-due").status == "cancelled"
    assert not notifier.cancel_reminder("letter:42:reply-due")


def test_mistakes_fail_when_scheduling_not_in_the_worker(notifier):
    with pytest.raises(InvalidMessage, match="not an email address"):
        _daily(notifier, to=["nobody"])
    with pytest.raises(InvalidMessage, match="no occurrence"):
        _daily(notifier, frequency=Frequency.at_times([datetime(2020, 1, 1, tzinfo=timezone.utc)]))


def test_a_deadline_reminder_and_the_live_hook(store, mail, clock):
    pushed = []
    notifier = Notifier(settings=NotifySettings(), store=store, email=mail, source="M14",
                        clock=clock, on_in_app=pushed.extend)
    deadline = datetime(2026, 11, 13, 17, 0, tzinfo=IST)
    notifier.schedule_reminder(title="Due in a while", user_ids=["u-9"], level="danger",
                               frequency=Frequency.before(deadline, [timedelta(days=1),
                                                                    timedelta(hours=2)]))
    clock.set(datetime(2026, 11, 12, 17, 0, tzinfo=IST))
    notifier.run_due()
    clock.set(datetime(2026, 11, 13, 15, 0, tzinfo=IST))
    notifier.run_due()
    assert [p["user_id"] for p in pushed] == ["u-9", "u-9"]
    assert all(p["id"] and p["level"] == "danger" for p in pushed)
    assert notifier.list_reminders()[0].status == "completed"
