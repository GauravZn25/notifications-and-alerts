"""Run due reminders: in-process (a thread beside the API) or as its own process.

In-process, from a FastAPI lifespan:

    worker = ReminderWorker(notifier)
    worker.start()          # a daemon thread, every NOTIFY_WORKER_INTERVAL_SECONDS
    ...
    worker.stop()

As a process (a container next to the API, or a cron-like service):

    python -m notifications_and_alerts.worker            # forever
    python -m notifications_and_alerts.worker --once     # one pass, then exit

Several workers may run at once — `run_due` locks each reminder it takes.
"""
from __future__ import annotations

import argparse
import logging
import threading

from .notifier import Notifier
from .settings import NotifySettings

log = logging.getLogger("notifications_and_alerts.worker")


class ReminderWorker:
    def __init__(self, notifier: Notifier, *, interval_seconds: float | None = None) -> None:
        self.notifier = notifier
        self.interval = interval_seconds or notifier.settings.worker_interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> None:
        try:
            report = self.notifier.run_due()
            if report.processed:
                log.info("reminders: %d due, %d sent, %d retrying, %d failed, %d completed",
                         report.processed, report.sent, len(report.retrying),
                         len(report.failed), len(report.completed))
        except Exception:  # noqa: BLE001 — one bad pass must not kill the loop
            log.exception("reminder pass failed")

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self.interval)

    def start(self) -> "ReminderWorker":
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="reminder-worker", daemon=True)
            self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Send due reminders (NOTIFY_* settings).")
    parser.add_argument("--once", action="store_true", help="one pass, then exit")
    parser.add_argument("--interval", type=float, default=None, help="seconds between passes")
    parser.add_argument("--source", default=None, help="only this module's reminders")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = NotifySettings()
    if not settings.database_url:
        raise SystemExit("NOTIFY_DATABASE_URL is not set — the worker needs the reminders' database")
    notifier = Notifier.from_settings(settings, source=args.source)
    worker = ReminderWorker(notifier, interval_seconds=args.interval)
    if args.once:
        worker.run_once()
        return
    log.info("reminder worker every %ss", worker.interval)
    try:
        worker._loop()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
