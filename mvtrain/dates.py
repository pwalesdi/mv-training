"""Sheet dates.

Rule from Patrick: the tab name is the truth. The year comes from the
workbook's season (e.g. 2526 -> Jul-Dec 2025, Jan-May 2026). June can belong
to either end of a season, so it is placed by the tab's position relative to
the spring tabs (workbooks are kept newest-first). The date written inside the
sheet is only used to flag disagreements.
"""
import datetime as dt
import re

# '9-25', '9-25 (2)', and '9-26b' (a second group's sheet for the same day - same date, no session number)
TAB_RE = re.compile(r"^\s*(\d{1,2})-(\d{1,2})\s*(?:\((\w+)\)|([a-z]))?\s*$", re.I)
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_tab(name):
    """'9-25 (2)' -> (9, 25, '2'); '9-26b' -> (9, 26, 'b'); non-date tabs -> None."""
    m = TAB_RE.match(name)
    if not m:
        return None
    mo, d = int(m.group(1)), int(m.group(2))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return mo, d, m.group(3) or m.group(4)


def season_years(season):
    """'2526' -> (2025, 2026)."""
    return 2000 + int(season[:2]), 2000 + int(season[2:])


def assign_dates(tab_names, season):
    """Return {tab_name: date} for every date tab, in a workbook's tab order."""
    start, end = season_years(season)
    parsed = [(i, n, parse_tab(n)) for i, n in enumerate(tab_names)]
    parsed = [(i, n, p) for i, n, p in parsed if p]
    spring_idx = [i for i, _, p in parsed if 1 <= p[0] <= 5]
    out = {}
    for i, name, (mo, d, _) in parsed:
        if mo >= 7:
            yr = start
        elif mo <= 5:
            yr = end
        else:  # June: newest-first ordering -> before the spring tabs means end of season
            yr = end if spring_idx and i < max(spring_idx) else start
        try:
            out[name] = dt.date(yr, mo, d)
        except ValueError:
            out[name] = None
    return out


def parse_written(title):
    """Best-effort parse of the title cell: returns (date_or_None, weekday_or_None, month, day, year)."""
    if isinstance(title, dt.datetime):
        return title.date(), WEEKDAYS[title.weekday()], title.month, title.day, title.year
    if not isinstance(title, str):
        return None, None, None, None, None
    s = title.lower()
    wd = next((w for w in WEEKDAYS if w in s), None)
    mo = None
    for idx, m in enumerate(MONTHS):
        if re.search(r"\b" + m, s):
            mo = idx + 1
            break
    dm = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b(?!\s*:)", re.sub(r"\b(19|20)\d{2}\b", "", s))
    day = int(dm.group(1)) if dm else None
    ym = re.search(r"\b(20\d{2})\b", s)
    yr = int(ym.group(1)) if ym else None
    d = None
    if mo and day and yr:
        try:
            d = dt.date(yr, mo, day)
        except ValueError:
            d = None
    return d, wd, mo, day, yr


def compare(tab_date, title):
    """List of human-readable disagreements between the tab date and the written title."""
    if tab_date is None:
        return ["tab name is not a valid date"]
    _, wd, mo, day, yr = parse_written(title)
    issues = []
    if mo and mo != tab_date.month:
        issues.append(f"title month {mo} vs tab {tab_date.month}")
    if day and day != tab_date.day:
        issues.append(f"title day {day} vs tab {tab_date.day}")
    if yr and yr != tab_date.year:
        issues.append(f"title year {yr} vs season year {tab_date.year}")
    if wd and wd != WEEKDAYS[tab_date.weekday()]:
        issues.append(f"title says {wd.title()}, tab date is a {WEEKDAYS[tab_date.weekday()].title()}")
    return issues
