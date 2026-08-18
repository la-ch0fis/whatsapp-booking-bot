"""
calendar_utils.py — Everything that touches Google Calendar.
ZERO platform-specific code. "user_id" is an opaque string: Telegram's
numeric ID in the old bot, the wa_id (phone) in WhatsApp.

The calendar IS the database: events carry
extendedProperties.private = {"user_id": ..., "bot": BOT_EVENT_TAG}
which is how they're found/cancelled later. No SQLite, no local state.
"""
import logging
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import (
    TZ, SESSION_MINUTES, CALENDAR_ID, GOOGLE_TOKEN_FILE, GOOGLE_SCOPES,
    BOT_EVENT_TAG, BUSINESS_OPEN_HOUR, BUSINESS_CLOSE_HOUR,
)
from parsing_utils import validate_slot_dt

log = logging.getLogger(__name__)

_service = None


def _load_credentials() -> Credentials:
    """
    Local: reads token.json (same file your Telegram version uses).
    Lambda: no persistent disk, so the CONTENT of token.json lives in SSM
    Parameter Store (SecureString), pointed at by env var GOOGLE_TOKEN_SSM.
    The client library refreshes the access token in memory on each cold
    start using the refresh_token — nothing needs writing back as long as
    Google doesn't rotate the refresh_token.
    """
    import os
    ssm_param = os.environ.get("GOOGLE_TOKEN_SSM")
    if ssm_param:
        import json as _json
        import boto3
        resp = boto3.client("ssm").get_parameter(Name=ssm_param, WithDecryption=True)
        info = _json.loads(resp["Parameter"]["Value"])
        return Credentials.from_authorized_user_info(info, GOOGLE_SCOPES)
    return Credentials.from_authorized_user_file(GOOGLE_TOKEN_FILE, GOOGLE_SCOPES)


def _svc():
    """Cached service per process (in Lambda: per warm container)."""
    global _service
    if _service is None:
        _service = build(
            "calendar", "v3", credentials=_load_credentials(), cache_discovery=False
        )
    return _service


class CalendarError(Exception):
    """External failure: the caller decides the friendly user message."""


def _day_busy_ranges(day: datetime):
    """ONE freebusy call for the whole day; everything else computed locally."""
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    try:
        resp = _svc().freebusy().query(body={
            "timeMin": day_start.isoformat(),
            "timeMax": day_end.isoformat(),
            "timeZone": str(TZ),
            "items": [{"id": CALENDAR_ID}],
        }).execute()
    except HttpError as exc:
        log.error("freebusy failed: %s", exc)
        raise CalendarError("freebusy") from exc

    busy = []
    for rng in resp["calendars"][CALENDAR_ID].get("busy", []):
        busy.append((
            datetime.fromisoformat(rng["start"]).astimezone(TZ),
            datetime.fromisoformat(rng["end"]).astimezone(TZ),
        ))
    return busy


def _overlaps(start, end, busy_ranges):
    return any(start < b_end and end > b_start for b_start, b_end in busy_ranges)


def is_slot_free(start: datetime) -> bool:
    end = start + timedelta(minutes=SESSION_MINUTES)
    return not _overlaps(start, end, _day_busy_ranges(start))


def find_alternatives(requested: datetime, max_alts: int = 3):
    """
    Requested slot is taken: sweep the SAME day in 30-min steps and return
    up to max_alts open slots that pass every rule (hours, lunch, not past).
    Reuses a single freebusy call for the day.
    """
    busy = _day_busy_ranges(requested)
    alts = []
    cursor = requested.replace(
        hour=BUSINESS_OPEN_HOUR, minute=0, second=0, microsecond=0
    )
    last_start = requested.replace(
        hour=BUSINESS_CLOSE_HOUR, minute=0, second=0, microsecond=0
    ) - timedelta(minutes=SESSION_MINUTES)

    while cursor <= last_start and len(alts) < max_alts:
        if cursor != requested and validate_slot_dt(cursor) is None:
            end = cursor + timedelta(minutes=SESSION_MINUTES)
            if not _overlaps(cursor, end, busy):
                alts.append(cursor)
        cursor += timedelta(minutes=30)
    return alts


def create_booking(name: str, user_id: str, start: datetime) -> dict:
    end = start + timedelta(minutes=SESSION_MINUTES)
    body = {
        "summary": f"Sesión — {name}",
        "description": f"Reservado vía bot de WhatsApp.\nCliente: {name}",
        "start": {"dateTime": start.isoformat(), "timeZone": str(TZ)},
        "end": {"dateTime": end.isoformat(), "timeZone": str(TZ)},
        "extendedProperties": {
            "private": {"user_id": str(user_id), "bot": BOT_EVENT_TAG}
        },
    }
    try:
        return _svc().events().insert(calendarId=CALENDAR_ID, body=body).execute()
    except HttpError as exc:
        log.error("event insert failed: %s", exc)
        raise CalendarError("insert") from exc


def get_user_bookings(user_id: str):
    """
    ONLY this user's future bookings. The privateExtendedProperty filter
    guarantees nobody ever sees or cancels someone else's appointment.
    """
    now = datetime.now(TZ)
    try:
        resp = _svc().events().list(
            calendarId=CALENDAR_ID,
            privateExtendedProperty=[f"user_id={user_id}", f"bot={BOT_EVENT_TAG}"],
            timeMin=now.isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=10,
        ).execute()
    except HttpError as exc:
        log.error("event list failed: %s", exc)
        raise CalendarError("list") from exc
    return resp.get("items", [])


def delete_booking(event_id: str, user_id: str) -> bool:
    """
    Deletes only if the event belongs to the requester (double-checked
    server-side even though the flow only offers the user's own IDs) and
    only if it hasn't already happened.
    """
    try:
        ev = _svc().events().get(calendarId=CALENDAR_ID, eventId=event_id).execute()
        props = ev.get("extendedProperties", {}).get("private", {})
        if props.get("user_id") != str(user_id):
            log.warning("Attempt to cancel foreign event: %s by %s", event_id, user_id)
            return False
        start = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
        if start <= datetime.now(TZ):
            return False
        _svc().events().delete(calendarId=CALENDAR_ID, eventId=event_id).execute()
        return True
    except HttpError as exc:
        log.error("event delete failed: %s", exc)
        raise CalendarError("delete") from exc


def get_events_needing_reminder(window_start: datetime, window_end: datetime):
    """Bot-tagged events inside the window that haven't been reminded yet."""
    try:
        resp = _svc().events().list(
            calendarId=CALENDAR_ID,
            privateExtendedProperty=[f"bot={BOT_EVENT_TAG}"],
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except HttpError as exc:
        log.error("reminder list failed: %s", exc)
        raise CalendarError("list_reminders") from exc

    pending = []
    for ev in resp.get("items", []):
        props = ev.get("extendedProperties", {}).get("private", {})
        if props.get("reminded") != "1" and props.get("user_id"):
            pending.append(ev)
    return pending


def mark_reminded(event_id: str):
    """The flag lives on the event itself — calendar stays the only DB."""
    try:
        _svc().events().patch(
            calendarId=CALENDAR_ID,
            eventId=event_id,
            body={"extendedProperties": {"private": {"reminded": "1"}}},
        ).execute()
    except HttpError as exc:
        # Non-critical: worst case, one duplicate reminder. Log and move on.
        log.error("reminded patch failed: %s", exc)
