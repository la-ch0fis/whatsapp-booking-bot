"""
reminders.py — Reminders ~24h before each session.
Local: run via cron every 30-60 min (KISS: no embedded scheduler, no queues):

    */30 * * * * cd /path/to/bot && /usr/bin/python3 reminders.py >> reminders.log 2>&1

In AWS: RemindersFunction on an EventBridge rate(30 minutes) schedule.

The 23-25h window + a 'reminded' flag stored on the event itself means
each booking gets exactly one reminder no matter how often this runs.

IMPORTANT (WhatsApp): at ~24h the service window has closed, so this MUST
go out as a pre-approved template (see whatsapp_api.send_reminder_template).
"""
import logging
from datetime import datetime, timedelta

import messages as M
import whatsapp_api as wa
import calendar_utils as cal
from config import TZ, REMINDER_WINDOW_HOURS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("reminders")


def run():
    now = datetime.now(TZ)
    start = now + timedelta(hours=REMINDER_WINDOW_HOURS[0])
    end = now + timedelta(hours=REMINDER_WINDOW_HOURS[1])

    try:
        pending = cal.get_events_needing_reminder(start, end)
    except cal.CalendarError:
        log.error("Could not query calendar; will retry on next run")
        return

    log.info("Bookings pending reminder: %d", len(pending))
    for ev in pending:
        props = ev["extendedProperties"]["private"]
        wa_id = props["user_id"]
        when = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
        if wa.send_reminder_template(wa_id, M.fmt_dt(when)):
            cal.mark_reminded(ev["id"])
            log.info("Reminder sent: %s -> %s", ev["id"], wa_id)
        else:
            # Flag NOT set: next run retries while still inside the window
            log.error("Reminder failed for %s; will retry", ev["id"])


if __name__ == "__main__":
    run()


def lambda_handler(event, context):
    """Entry point for the EventBridge rate(30 minutes) schedule in SAM."""
    run()
    return {"ok": True}
