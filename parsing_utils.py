"""
parsing_utils.py — Spanish date/time parsing and validation.
ZERO platform-specific code. Reusable in Telegram, WhatsApp, web, etc.

Contract: parse_and_validate(text) -> (aware datetime | None, error_code | None)
Error codes are keys for messages.py, never user-facing text.
"""
import re
import logging
import unicodedata
from datetime import datetime, timedelta

from dateutil import parser as duparser

from config import (
    TZ, SESSION_MINUTES, BUSINESS_OPEN_HOUR, BUSINESS_CLOSE_HOUR,
    LUNCH_START, LUNCH_END, HOLIDAYS,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error codes (friendly Spanish messages live in messages.py)
# ---------------------------------------------------------------------------
ERR_JUNK = "junk"                 # unrecognized text mixed in ("Pringles, 5pm")
ERR_NO_TIME_MARKER = "no_time"    # no am/pm and no colon-time
ERR_INVALID_DATE = "invalid"      # impossible date ("July 32nd")
ERR_MISSING_PARTS = "missing"     # date or time missing (ambiguous)
ERR_PAST = "past"                 # date/time already happened
ERR_SUNDAY = "sunday"
ERR_HOLIDAY = "holiday"
ERR_LUNCH = "lunch"
ERR_OUTSIDE_HOURS = "outside_hours"
ERR_UNPARSEABLE = "unparseable"   # dateutil couldn't handle the text
ERR_RUDE_WORD = "rude"

# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------
def _normalize(text: str) -> str:
    """lowercase, strip accents (n-tilde -> n on purpose), collapse spaces.
       Then the function strips the accent because unicodedata.category(c) != "Mn" removes all
       Why NFD instead of NFC?
       Form	               What it does	                                   Example
       NFC                 Composed — accent on the letter itself          á (single char)
       NFD                 Decomposed — letter + separate accent           a + ´ (two chars)
    """
    text = text.lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[,;]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# Common filler phrases REMOVED before strict parsing.
# Ordered longest-first so no partial leftovers remain.
FILLER_PHRASES = [
    "quisiera agendar para", "quiero agendar para", "agendame para",
    "agendame una cita para", "quiero una cita para", "una cita para",
    "reservame para", "book me for", "agendar para",
    "quiero agendar", "me gustaria", "por favor", "porfavor", "please",
    "para el dia", "el dia",
    # bare verb forms — last, so the longer phrases get cleaned first
    "reservame", "agendame", "resérvame", "agendar", "reservar", "quiero",
]

# Relative words -> day offset. LONGEST FIRST:
# "manana" would partial-match inside "pasado manana" if checked earlier.
RELATIVE_DATES = [
    ("pasado manana", 2),
    ("manana", 1),
    ("hoy", 0),
]

MONTHS_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
    # common abbreviations
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "jun": 6, "jul": 7,
    "ago": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dic": 12,
}
# This is for later, Cochise. Once we're international, yeah boy!
MONTHS_EN = {
    "january": 1, "february":2, "march": 3, "april": 4, "may":5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "novermber": 11, "december": 12
}

# Connector tokens allowed through (dateutil skips them)
CONNECTOR_TOKENS = {"de", "del", "el", "a", "las", "la", "y", "para", "at"}

TIME_COLON_RE = re.compile(r"\b\d{1,2}:\d{2}\b")
AMPM_RE = re.compile(r"\b\d{1,2}(:\d{2})?\s*(am|pm|a\.?m\.?|p\.?m\.?)\b")
DATE_SLASH_RE = re.compile(r"^\d{1,2}/\d{1,2}(/\d{2,4})?$")
NUM_RE = re.compile(r"^\d{1,4}$")
AMPM_TOKEN_RE = re.compile(r"^(am|pm|a\.?m\.?|p\.?m\.?)$")
TIME_TOKEN_RE = re.compile(r"^\d{1,2}:\d{2}(am|pm)?$")


def _now() -> datetime:
    return datetime.now(TZ)


def _strip_fillers(text: str) -> str:
    for phrase in FILLER_PHRASES:
        text = text.replace(phrase, " ")
    return re.sub(r"\s+", " ", text).strip()


def _resolve_relative(text: str) -> str:
    """Replace hoy/manana/pasado manana with an explicit DD/MM/YYYY date."""
    for word, offset in RELATIVE_DATES:  # already ordered longest-first
        if word in text:
            target = (_now() + timedelta(days=offset)).strftime("%d/%m/%Y")
            text = text.replace(word, target)
            break  # only one relative word per message
    return re.sub(r"\s+", " ", text).strip()


def _tokens_are_clean(text: str) -> bool:
    """
    STRICT validation: every token must be recognizable.
    "Pringles 5pm" -> False. We reject; we never half-parse.
    """
    for tok in text.split():
        if (
            tok in CONNECTOR_TOKENS
            or tok in MONTHS_ES
            or NUM_RE.match(tok)
            or DATE_SLASH_RE.match(tok)
            or AMPM_TOKEN_RE.match(tok)
            or TIME_TOKEN_RE.match(tok)
        ):
            continue
        # number glued to am/pm: "3pm"
        m = re.match(r"^(\d{1,2})(am|pm|a\.?m\.?|p\.?m\.?)$", tok)
        if m:
            continue
        return False
    return True


def _has_impossible_date(text: str) -> bool:
    """
    Catch impossible dates BEFORE the double parse, because dateutil
    sometimes doesn't raise: in "32 de julio" it reads 32 as a YEAR and
    happily moves on. Explicit rejection, never silent correction.
    """
    import calendar as _cal
    year = _now().year

    # "27 de julio" / "27 julio" style
    for m in re.finditer(r"\b(\d{1,2})\s+(?:de\s+)?([a-z]{3,})\b", text):
        day, month_word = int(m.group(1)), m.group(2)
        month = MONTHS_ES.get(month_word)
        if month and not (1 <= day <= _cal.monthrange(year, month)[1]):
            return True

    # DD/MM or DD/MM/YYYY (dayfirst)
    for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text):
        day, month = int(m.group(1)), int(m.group(2))
        yr = int(m.group(3)) if m.group(3) else year
        if yr < 100:
            yr += 2000
        if not (1 <= month <= 12):
            return True
        if not (1 <= day <= _cal.monthrange(yr, month)[1]):
            return True
    return False


class _SpanishParserInfo(duparser.parserinfo):
    """dateutil parserinfo with Spanish month names and dayfirst DD/MM."""
    MONTHS = [
        ("ene", "enero"), ("feb", "febrero"), ("mar", "marzo"),
        ("abr", "abril"), ("may", "mayo"), ("jun", "junio"),
        ("jul", "julio"), ("ago", "agosto"), ("sep", "sept", "septiembre", "setiembre"),
        ("oct", "octubre"), ("nov", "noviembre"), ("dic", "diciembre"),
    ]
    JUMP = [" ", ".", ",", ";", "-", "/", "'", "de", "del", "el",
            "a", "las", "la", "y", "para", "at", "on", "and"]
    AMPM = [("am", "a.m", "a.m."), ("pm", "p.m", "p.m.")]

    def __init__(self):
        super().__init__(dayfirst=True)  # 05/07 = July 5th, NOT May 7th


_PARSER_INFO = _SpanishParserInfo()


def _parse_strict(text: str):
    """
    Double-parse trick with two different defaults:
    - Both defaults have minute=0, second=0, microsecond=0 so "9am" lands
      at 9:00 sharp instead of inheriting the wall clock's real minutes
      (classic bug: booking "9am" at 9:37).
    - The defaults differ in DAY and MONTH but share the YEAR. If the user
      omitted the day or month, the two results differ -> reject as
      incomplete instead of inventing the missing part. The year IS
      inherited from the default (nobody types the year for next month's
      appointment).
    - An impossible date makes dateutil raise ValueError -> specific
      error, never a silent correction to a nearby valid date.
    """
    year = _now().year
    d1 = datetime(year, 3, 7, 0, 0, 0, 0)
    d2 = datetime(year, 10, 21, 0, 0, 0, 0)
    try:
        r1 = duparser.parse(text, parserinfo=_PARSER_INFO, default=d1, fuzzy=False)
        r2 = duparser.parse(text, parserinfo=_PARSER_INFO, default=d2, fuzzy=False)
    except (ValueError, OverflowError) as exc:
        log.info("Parse rejected (%r): %s", text, exc)
        # dateutil raises ValueError both for "can't read this" and for
        # "day out of range". If there's a clear date pattern, treat it
        # as an invalid date.
        if re.search(r"\d{1,2}\s*(de\s+)?[a-z]+|\d{1,2}/\d{1,2}", text):
            return None, ERR_INVALID_DATE
        return None, ERR_UNPARSEABLE

    if r1 != r2:
        return None, ERR_MISSING_PARTS
    return r1, None


def _check_business_rules(dt: datetime):
    """Business rules. dt arrives timezone-aware in TZ."""
    end = dt + timedelta(minutes=SESSION_MINUTES)

    if dt <= _now():
        return ERR_PAST
    if dt.weekday() == 6:  # Sunday
        return ERR_SUNDAY
    if (dt.month, dt.day) in HOLIDAYS:
        return ERR_HOLIDAY

    open_t = dt.replace(hour=BUSINESS_OPEN_HOUR, minute=0, second=0, microsecond=0)
    close_t = dt.replace(hour=BUSINESS_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    if dt < open_t or end > close_t:
        # the FULL 90-min session must fit inside business hours
        return ERR_OUTSIDE_HOURS

    lunch_start = dt.replace(hour=LUNCH_START[0], minute=LUNCH_START[1], second=0, microsecond=0)
    lunch_end = dt.replace(hour=LUNCH_END[0], minute=LUNCH_END[1], second=0, microsecond=0)
    # any overlap of [dt, end) with lunch is rejected:
    # a 12:00 booking ends 13:30 and also invades lunch.
    if dt < lunch_end and end > lunch_start:
        return ERR_LUNCH

    return None


def parse_and_validate(raw_text: str):
    """
    Single entry point. Returns (aware datetime in TZ, None) on success,
    or (None, error_code) with the specific code of the FIRST failure.
    """
    text = _normalize(raw_text)
    text = _strip_fillers(text)
    text = _resolve_relative(text)

    if not text:
        return None, ERR_UNPARSEABLE

    if not _tokens_are_clean(text):
        return None, ERR_JUNK

    # Explicit time required: am/pm or a colon format.
    # "el 27 de julio a las 5" is ambiguous (5am? 5pm?) -> rejected.
    if not (TIME_COLON_RE.search(text) or AMPM_RE.search(text)):
        return None, ERR_NO_TIME_MARKER

    if _has_impossible_date(text):
        return None, ERR_INVALID_DATE

    parsed, err = _parse_strict(text)
    if err:
        return None, err

    dt = parsed.replace(tzinfo=TZ)

    rule_err = _check_business_rules(dt)
    if rule_err:
        return None, rule_err

    return dt, None


def validate_slot_dt(dt: datetime):
    """Re-validate an already-built datetime (e.g. a chosen alternative)."""
    return _check_business_rules(dt)
