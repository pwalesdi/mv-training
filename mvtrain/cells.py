"""Turning raw spreadsheet cell values into numbers."""
import datetime as dt
import re

CHURCH_LAP_M = 1725  # one lap of the church loop (Patrick)


def blank(v):
    return v is None or (isinstance(v, str) and not v.strip())


def to_seconds(v):
    """Split/rest cell -> seconds, or None if it isn't a time.

    Handles datetime.time (how most splits are stored), timedelta, Excel day
    fractions, and strings like '3:00.0', '2:04.5', '45s', '1:30'.
    """
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return None  # a date, not a duration
    if isinstance(v, dt.time):
        return v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6
    if isinstance(v, dt.timedelta):
        return v.total_seconds()
    if isinstance(v, (int, float)):
        # Excel stores durations as a fraction of a day
        if 0 < v < 1:
            return round(v * 86400, 2)
        return None
    s = str(v).strip().lower()
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", s)
    if m:
        h = int(m.group(1) or 0)
        return h * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:s|sec|secs)", s)
    if m:
        return float(m.group(1))
    return None


def to_distance(v):
    """Header cell -> rep distance in metres, or None."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) if 50 <= v <= 30000 else None
    s = str(v).strip().lower().replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*m?", s)
    if m:
        d = float(m.group(1))
        return d if 50 <= d <= 30000 else None
    if s in ("lap", "church", "church loop"):
        return float(CHURCH_LAP_M)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:mi|mile|miles)", s)
    if m:
        return round(float(m.group(1)) * 1609.34)
    return None


def to_miles(v):
    """'0.5 miles' / 0.5 / '6.0 miles' -> float miles."""
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    m = re.match(r"\s*(\d+(?:\.\d+)?)", str(v))
    return float(m.group(1)) if m else None


def text(v):
    if v is None:
        return ""
    if isinstance(v, dt.time):
        return v.strftime("%H:%M")
    return str(v).strip()


def duration_text(v):
    """Rest/duration cell -> text the way the sheet reads: '0:45', '1:30', '3:00', '0:56.4'.

    Excel stores these as times of day, so text() would print 0:45 as '00:00'.
    Tenths are kept only when the sheet has them (timed rests); anything that
    isn't a time ('200j + 1:30') goes through text() unchanged.
    """
    if not isinstance(v, (dt.time, dt.timedelta)):
        return text(v)
    tenths = round(to_seconds(v) * 10)
    secs, tenth = divmod(tenths, 10)
    mins, s = divmod(secs, 60)
    h, m = divmod(mins, 60)
    out = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return out + (f".{tenth}" if tenth else "")
