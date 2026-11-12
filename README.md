# notifications-and-alerts

Email, in-app notifications and scheduled reminders for every AIPMS module — one
Python package for the backends, one React package for the screens.

| You want to… | Call |
|---|---|
| send an email now (to / cc / bcc, title, text or HTML body, attachments) | `send_email(...)` |
| show an in-app notification now (the bell + a toast) | `notify(...)` |
| do either or both, now **or on a schedule** | `send_notification(..., frequency=...)` |
| stop a reminder (the reply came in, the task was done) | `cancel_reminder(key)` |

It runs inside each module (its own database, its own schema) and matches the
platform's planned `svc-notifications` / `core.notifications.notify` /
`<NotificationCenter />` names, so it can become that shared service later without
the modules changing their calls.

```
notifications-and-alerts/
├── notifications_and_alerts/       the Python package
│   ├── notifier.py                 Notifier — every function below
│   ├── messages.py                 EmailMessage, InAppMessage, Attachment, placeholders
│   ├── frequency.py                Frequency — once / every / daily / weekly / monthly / before a deadline
│   ├── email/                      transports: smtp, graph (Microsoft 365), console, memory
│   ├── store.py                    the 4 tables (notifications, reminders, attachments, delivery log)
│   ├── worker.py                   ReminderWorker + `notifications-worker` CLI
│   ├── fastapi.py                  create_router() — the bell's API
│   └── settings.py                 NOTIFY_* settings
├── frontend/                       @rodichub/notifications-and-alerts (React)
├── examples/fastapi_app.py         a module wired up end to end
└── tests/                          47 tests (+1 against real Postgres)
```

---

## 1. Backend — five steps in any FastAPI module

**Install** (from a module's `backend/`):

```bash
pip install -e ../../notifications-and-alerts            # or add to requirements.txt:
# notifications-and-alerts @ file:///app/vendor/notifications-and-alerts   (vendored into the image)
```

A module's Docker image is built from its own folder, so for containers either copy
this folder into the build context (`vendor/`) or install it from its git repository
once it has one.

**Wire it up** — this is `examples/fastapi_app.py`:

```python
import notifications_and_alerts as na
from notifications_and_alerts.fastapi import create_router

# 1. one notifier, on the module's own engine and schema
notifier = na.configure(na.Notifier.from_settings(
    na.NotifySettings(db_schema="m14_docs", email_transport="smtp", smtp_host=..., from_address=...),
    engine=engine, source="M14", create_tables=True))

# 2. the bell's API, behind the module's own auth (a dependency returning the user id)
app.include_router(create_router(notifier, current_user=current_user_id),
                   prefix="/api/v1/docscomms")

# 3. due reminders: a worker thread beside the API (or the CLI as its own container)
worker = na.ReminderWorker(notifier).start()      # in the lifespan; worker.stop() on shutdown
```

Settings come from `NOTIFY_*` environment variables (see `.env.example`) or are passed
in code — a module can map its own (`DOCSHUB_SMTP_HOST` → `smtp_host`) so it keeps one
env namespace. `create_tables=True` creates the tables if missing; a module that keeps
its schema in Alembic can instead add `notifier.store.metadata` to its `env.py`.

**Use it** anywhere in the module:

```python
na.send_notification(
    title="Reply due for {ref}",
    body_html="<p>The reply to <b>{ref}</b> is due on {due}.</p>",   # and/or body_text
    context={"ref": "NHAI/PIU-HSN/2026/1142", "due": "16 Nov 2026"},
    to=["pm@knr.example"], cc=["tl@rodic.in"], bcc=["records@rodic.in"],
    attachments=[na.Attachment.from_path("letter.pdf")],
    user_ids=["u-17"],                       # in-app as well
    level="warning", link="/m14/register/letters/42",
    entity_type="letter", entity_id="42",
    frequency=na.Frequency.before(due_at, [timedelta(days=2), timedelta(days=1), timedelta(hours=2)]),
    key="letter:42:reply-due",               # to replace or cancel it later
    created_by=current_user.id)

na.cancel_reminder("letter:42:reply-due")    # e.g. when the reply is sent
```

Without `frequency` it sends at once and returns an `Outcome` (`.email` is the
`SendResult`, `.notification_ids` the in-app rows). With it, it is scheduled and
returns a `Reminder` (`.id`, `.next_run_at`, `.schedule`).

## 2. `send_notification` — every parameter

| Parameter | Meaning |
|---|---|
| `title` | The email subject and the notification title. `{placeholders}` allowed. |
| `body_text`, `body_html` | At least one (for email). HTML values from `context` are escaped. In-app shows the text, or the HTML with its tags removed. |
| `to`, `cc`, `bcc` | Lists or `"a@x.com; b@y.com"`. `"Name <a@x.com>"` is fine. BCC is never in the headers. |
| `attachments` | `Attachment.from_path(p)` / `Attachment.from_bytes(name, data)`. Up to 20 MB each, 25 MB per message (settings). Kept with a reminder and sent each time. |
| `user_ids` | In-app recipients — the module's user ids. |
| `channels` | `["email"]`, `["in_app"]` or both. Default: whichever recipients are given. |
| `frequency` | When to send. Omit = now. See below. |
| `key` | The caller's handle for a reminder (`"letter:42:reply-due"`). Scheduling the same key again replaces it (`replace=False` raises `ReminderExists`). |
| `level` | `info` · `success` · `warning` · `danger` (warning/danger turn the bell red and keep the toast open). |
| `link` | Where clicking the notification goes. |
| `category`, `entity_type`, `entity_id`, `data` | For filtering and for the screen to show context. |
| `context` | Placeholder values. Reminders also get `{occurrence}` (1, 2, 3 …). |
| `reply_to`, `from_address`, `from_name` | Override the configured sender. |
| `tenant_urn`, `project_id`, `created_by` | Scope and ownership (the API lists a user's own reminders by `created_by`). |

`send_email(...)` and `notify(...)` take the email-only and in-app-only subsets.
Every message is validated when the call is made — a bad address, no body or an
oversized attachment raises `InvalidMessage` in the caller, not later in a worker.

## 3. Frequency

```python
Frequency.once(at)                                         # one time (default: now)
Frequency.every(hours=6)                                   # minutes= / hours= / days=
Frequency.daily(at="09:00")                                # every=2 → every other day
Frequency.weekly(days=["mon", "thu"], at="10:00")          # every=2 → fortnightly
Frequency.monthly(day=31, at="09:00")                      # 31 → the last day of short months
Frequency.before(deadline, [timedelta(days=3), timedelta(hours=2)])   # offsets before a deadline
Frequency.at_times([t1, t2])                               # exact instants
```

All take `until=`, `max_runs=` and `timezone=` (default `Asia/Kolkata`). If the worker
was down, a due reminder is sent once and then moves to its next future occurrence —
no burst of catch-up mail.

## 4. Delivery, retries and the log

- Each due reminder is sent in its own transaction with its log row and next time:
  at-least-once (a crash between the send and the commit re-sends that occurrence),
  never lost.
- A failing email (`failed`: server down, timeout) is retried after 5, 15, 60 minutes,
  up to `max_attempts` (4); then that occurrence is given up and the next one stands.
  A `rejected` email (bad address, too large) is not retried. The in-app half of an
  occurrence is written once, never per retry.
- Several workers may run at once: due rows are taken with `FOR UPDATE SKIP LOCKED`.
- Every attempt is in `notify_deliveries`: channel, status, to/cc/bcc, attachments,
  transport, provider id, error.

## 5. Frontend — the bell, the drawer, live toasts

```bash
npm install ../notifications-and-alerts/frontend    # @rodichub/notifications-and-alerts
```

```tsx
import { createNotificationsClient, useNotifications, NotificationDrawer,
         NotificationToaster } from '@rodichub/notifications-and-alerts'
import '@rodichub/notifications-and-alerts/styles.css'

const client = createNotificationsClient({
  baseUrl: '/api/v1/docscomms/notifications',
  headers: () => ({ Authorization: `Bearer ${token()}`, 'X-Tenant-URN': tenant() }),
})

function App() {
  const bell = useNotifications(client)            // polls the unread count every 30 s
  const [open, setOpen] = useState(false)
  return (
    <ShellLayout topBar={{ notifications: { count: bell.count, urgent: bell.urgent,
                                            onOpen: () => setOpen(true) } }}>
      …
      <NotificationDrawer state={bell} open={open} onClose={() => setOpen(false)} onNavigate={navigate} />
      <NotificationToaster state={bell} onNavigate={navigate} />
    </ShellLayout>
  )
}
```

The shared shell's TopBar already draws the bell from `{ count, urgent, onOpen }`. The
drawer lists, marks read, dismisses and pages; the toaster pops up what arrives while
the user is on a screen. Colours are CSS variables (`--nna-brand`, `--nna-surface`, …)
defaulting to the v8 palette, with `.dark` support — map them onto your app's tokens.

## 6. The API `create_router` adds

| | |
|---|---|
| `GET /notifications` | the user's list, newest first (`unread_only`, `category`, `before`, `limit`) |
| `GET /notifications/unread-count` | `{count, urgent, by_level}` |
| `POST /notifications/{id}/read` · `/read-all` · `/{id}/dismiss` | |
| `GET /notifications/reminders` | reminders the user scheduled |
| `POST /notifications/reminders/{id}/cancel` | cancel one of them |

The router never authenticates — the module's `current_user` dependency does — and
every query is filtered to that user, so nobody reads or changes another user's
notifications.

## 7. Development

```bash
pip install -e ".[test]" && pytest                      # 47 tests on SQLite
NOTIFY_TEST_DATABASE_URL=postgresql+psycopg2://… pytest tests/test_postgres.py
cd frontend && npm install && npm run typecheck && npm test && npm run build
```

Email transports: `smtp` (any server; Office 365 = `smtp.office365.com:587`),
`graph` (Microsoft 365 app-only, Mail.Send application permission; inline attachments
up to 3 MB in total), `console` (logs, sends nothing), `memory` (tests). A module that
already has its own mail path can pass any object with
`send(message, from_address=, from_name=) -> SendResult` as `email=`.
