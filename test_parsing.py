"""
Phase 0: pytest tests/ -v
Covers the whole parser without touching WhatsApp, AWS, or Google.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timedelta
import pytest

from config import TZ, HOLIDAYS
from parsing_utils import parse_and_validate


def _future_weekday(target_weekday: int) -> datetime:
    """Next date (>=14 days out) on that weekday that is not a holiday."""
    d = datetime.now(TZ) + timedelta(days=14)
    while d.weekday() != target_weekday or (d.month, d.day) in HOLIDAYS:
        d += timedelta(days=1)
    return d


MONTHS = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _es(d: datetime) -> str:
    return f"{d.day} de {MONTHS[d.month]}"


MON = _future_weekday(0)   # future working Monday
SUN = _future_weekday(6)   # future Sunday


@pytest.mark.parametrize("text,expected_err", [
    (f"{_es(MON)}, 3pm", None),
    (f"agéndame para el {_es(MON)}, 9am", None),          # filler + accented char
    (f"{MON.strftime('%d/%m/%Y')} 10:30am", None),        # DD/MM dayfirst
    (f"{_es(MON)}, 6:30pm", None),                        # ends exactly at 8pm
    ("Pringles, 5pm", "junk"),
    (f"{_es(MON)}, 9am jsdfkj", "junk"),
    (f"el {_es(MON)} a las 5", "no_time"),                # no am/pm, no colon
    ("32 de julio, 3pm", "invalid"),
    ("31 de septiembre, 3pm", "invalid"),
    ("31/02/2027 3pm", "invalid"),
    ("3pm", "missing"),                                   # date missing
    (f"{_es(SUN)}, 10am", "sunday"),
    (f"{_es(MON)}, 1pm", "lunch"),
    (f"{_es(MON)}, 12pm", "lunch"),                       # overlaps lunch at the tail end
    (f"{_es(MON)}, 7pm", "outside_hours"),                # would end 8:30pm
    (f"{_es(MON)}, 8am", "outside_hours"),
    ("01/01/2027 10am", "holiday"),
    ("ayer a las 3pm", "junk"),                           # unsupported word
])
def test_matrix(text, expected_err):
    dt, err = parse_and_validate(text)
    assert err == expected_err, f"{text!r}: err={err}, esperado {expected_err}"
    assert (dt is None) == (err is not None)


def test_relative_tomorrow_zeroed_minutes():
    dt, err = parse_and_validate("mañana a las 9am")
    if err is None:  # "mañana" may land on a Sunday/holiday depending on the real date
        assert (dt.minute, dt.second) == (0, 0)
        assert dt.hour == 9
    else:
        assert err in {"sunday", "holiday"}


def test_pasado_manana_beats_manana():
    """'pasado mañana' must NOT be read as 'mañana' + junk."""
    dt, err = parse_and_validate("pasado mañana a las 10:00am")
    expected_day = (datetime.now(TZ) + timedelta(days=2)).day
    if err is None:
        assert dt.day == expected_day
    else:
        assert err in {"sunday", "holiday"}
