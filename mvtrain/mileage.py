"""Weekly mileage exports (Strava group pull).

Every .csv in data/mileage/ is read. Two layouts are accepted, told apart by the header row.

Wide - one row per Monday-Sunday week, one column per athlete (Strava display name):
    week_start_mon,week_end_sun,<Strava name>,<Strava name>,...
    2026-08-03,2026-08-09,44.42,38.10,...
  Rows whose first cell isn't a date (the averages / peak / totals footer) are ignored. A note in
  parentheses after a date marks a partial week, e.g. "2026-07-27 (Aug 1-2 only)" or
  "2026-11-15 (in progress, thru Nov 9)". A column headed "note" is a note for the week (kept in
  week_note), not an athlete.

Long - one row per athlete-week:
    name,week_start,miles                  optional columns: week_end, partial_week, note
  "name" may also be headed athlete / athlete_name / strava_name, "week_start" week /
  week_start_mon / week_of, and "miles" mi. week_end defaults to week_start + 6 days. A week is
  partial when partial_week is 1 / yes / true, or when its date carries a note in parentheses as
  in the wide layout.

Dates are YYYY-MM-DD (M/D/YYYY also works); weeks are meant to start on a Monday (others are kept
and flagged in review.csv). Strava names are matched to Athletic.net IDs through
data/rules/strava_aliases.csv, then by exact name. If several files cover the same athlete-week,
the newest file (by modified time) wins. Names that don't match go to review.csv, with possible
matches when there are any (same first name, last name starting with the Strava name's last word,
raced within a season of those weeks) - nothing is matched automatically.
"""
import csv
import datetime as dt
import re
from collections import defaultdict
from pathlib import Path

from . import races

DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\s*(?:\((.*)\))?\s*$")

NAME_COLS = {"name", "athlete", "athlete_name", "strava_name"}
WEEK_COLS = {"week_start", "week_start_mon", "week", "week_of"}
END_COLS = {"week_end", "week_end_sun"}
MILES_COLS = {"miles", "mi"}
NOTE_COLS = {"note", "notes", "week_note"}
PARTIAL_COLS = {"partial_week", "partial"}
TRUE = {"1", "y", "yes", "true", "x"}


def load_aliases(path):
    if not Path(path).exists():
        return {}
    with open(path) as fh:
        return {races.norm(r["strava_name"]): r for r in csv.DictReader(fh)}


def _head(h):
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def _date(cell):
    """'2026-08-03 (Aug 1-2 only)' -> ('2026-08-03', 'Aug 1-2 only'); not a date -> (None, '')."""
    m = DATE.match(cell or "")
    if not m:
        return None, ""
    d = m.group(1)
    try:
        if "/" in d:
            mo, da, yr = map(int, d.split("/"))
            d = dt.date(yr, mo, da).isoformat()
        else:
            dt.date.fromisoformat(d)
    except ValueError:
        return None, ""
    return d, (m.group(2) or "").strip()


def _layout(header):
    """-> ('long' | 'wide' | None, column positions)."""
    h = [_head(x) for x in header]

    def find(names):
        return next((i for i, x in enumerate(h) if x in names), None)
    name_i, week_i, miles_i = find(NAME_COLS), find(WEEK_COLS), find(MILES_COLS)
    if name_i is not None and week_i is not None and miles_i is not None:
        return "long", {"name": name_i, "week": week_i, "miles": miles_i, "end": find(END_COLS),
                        "note": find(NOTE_COLS), "partial": find(PARTIAL_COLS)}
    if h and h[0] in WEEK_COLS:
        end_i = 1 if len(h) > 1 and h[1] in END_COLS else None
        notes = {i for i, x in enumerate(h) if x in NOTE_COLS}
        athletes = [(i, header[i]) for i in range(1, len(header))
                    if i != end_i and i not in notes and header[i].strip()]
        return "wide", {"end": end_i, "note": min(notes) if notes else None, "athletes": athletes}
    return None, {}


def _cell(row, i):
    return row[i] if i is not None and i < len(row) else ""


def season_of(d):
    """date or 'YYYY-MM-DD' -> '2526' (seasons run July-June)."""
    d = d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)[:10])
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def _near(season):
    y = int(season[:2])
    return {f"{(y + k) % 100:02d}{(y + k + 1) % 100:02d}" for k in (-1, 0, 1)}


def suggest(name, weeks, id_names, raced):
    """Possible Athletic.net athletes for an unmatched Strava name ('Jane D' -> Jane Doe):
    same first name, last name starting with the Strava name's last word, raced within a season
    of these weeks (or no race seasons known). Only a hint for review.csv."""
    words = re.findall(r"[a-z]+", name.lower())
    if not words:
        return []
    first, last = words[0], (words[-1] if len(words) > 1 else "")
    near = set().union(*(_near(season_of(w)) for w in weeks)) if weeks else set()
    out = []
    for aid, full in id_names.items():
        fw = re.findall(r"[a-z]+", full.lower())
        if not fw or fw[0] != first or (last and not fw[-1].startswith(last)):
            continue
        if raced.get(aid) and near and not raced[aid] & near:
            continue
        out.append(f"{full} ({aid})")
    return sorted(out)[:3]


def load(mileage_dir, alias_path, by_name, id_names, results=()):
    """-> (rows, review_items). results (race rows) only feed the suggestions for unmatched names."""
    aliases = load_aliases(alias_path)
    raced = defaultdict(set)
    for r in results:
        raced[r["athlete_id"]].add(season_of(r["date"]))

    def match(name):
        k = races.norm(name)
        if k in aliases and aliases[k]["athlete_id"]:
            return aliases[k]["athlete_id"], "alias"
        if k in by_name:
            return by_name[k], "exact"
        return "", "unmatched"

    best, review = {}, []
    files = sorted(Path(mileage_dir).glob("*.csv"), key=lambda p: p.stat().st_mtime)
    for f in files:  # oldest first, so a newer file overwrites the same athlete-week
        with open(f, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        kind, cols = _layout(rows[0] if rows else [])
        if not kind:
            review.append(_item(f"mileage file '{f.name}' skipped: header row not recognised (expected the wide "
                                f"week_start_mon,week_end_sun,<names> layout or name,week_start,miles - see mvtrain/mileage.py)"))
            continue
        seen, twice, not_monday = set(), 0, []

        def put(name, start, end, miles, paren_notes, partial, note):
            nonlocal twice
            aid, how = match(name)
            k = (aid or "?" + races.norm(name), start)
            twice += k in seen
            seen.add(k)
            if dt.date.fromisoformat(start).weekday() != 0:
                not_monday.append(start)
            notes = paren_notes + ([note] if note else [])
            best[k] = {"athlete_id": aid, "athlete_name": id_names.get(aid, name), "match": how,
                       "strava_name": name, "week_start": start, "week_end": end, "miles": miles,
                       "partial_week": int(bool(paren_notes) or partial), "week_note": "; ".join(notes),
                       "source_file": f.name}

        for row in rows[1:]:
            if not row:
                continue
            if kind == "wide":
                start, n1 = _date(row[0])
                end, n2 = _date(_cell(row, cols["end"])) if cols["end"] is not None else (None, "")
                if not start or (cols["end"] is not None and not end):
                    continue  # footer rows (averages, peak, totals)
                for i, name in cols["athletes"]:
                    try:
                        miles = float(_cell(row, i))
                    except ValueError:
                        continue
                    put(name, start, end or _plus6(start), miles, [n for n in (n1, n2) if n], False,
                        _cell(row, cols["note"]).strip())
            else:
                name = _cell(row, cols["name"]).strip()
                start, n1 = _date(_cell(row, cols["week"]))
                end, n2 = _date(_cell(row, cols["end"]))
                try:
                    miles = float(_cell(row, cols["miles"]))
                except ValueError:
                    continue
                if not (name and start):
                    continue
                put(name, start, end or _plus6(start), miles, [n for n in (n1, n2) if n],
                    _cell(row, cols["partial"]).strip().lower() in TRUE, _cell(row, cols["note"]).strip())
        if twice:
            review.append(_item(f"mileage file '{f.name}': {twice} athlete-week(s) listed twice - the later row was used"))
        if not_monday:
            review.append(_item(f"mileage file '{f.name}': {len(not_monday)} week(s) don't start on a Monday "
                                f"(e.g. {sorted(not_monday)[0]}) - kept as given; weeks should run Monday-Sunday"))

    out = sorted(best.values(), key=lambda r: (r["week_start"], r["strava_name"]))
    unmatched = defaultdict(list)
    for r in out:
        if r["match"] == "unmatched":
            unmatched[r["strava_name"]].append(r["week_start"])
    for name in sorted(unmatched):
        hint = suggest(name, unmatched[name], id_names, raced)
        review.append(_item(f"Strava name '{name}' has no Athletic.net match (add to data/rules/strava_aliases.csv)"
                            + (f"; possible: {', '.join(hint)}" if hint else "")))
        review[-1]["issue"] = "unmatched_strava_name"
    return out, review


def _plus6(start):
    return (dt.date.fromisoformat(start) + dt.timedelta(days=6)).isoformat()


def _item(detail):
    return {"issue": "mileage_file", "season": "", "tab": "", "block": "", "detail": detail}
