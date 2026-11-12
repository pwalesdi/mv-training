"""Weekly mileage exports (Strava group pull).

Expected shape: one row per Monday-Sunday week, one column per athlete
(Strava display name), miles in the cells. Rows whose first cell isn't a date
(the averages/peak/total footer) are ignored. Notes in parentheses after a date
mark partial weeks, e.g. "2026-07-27 (Aug 1-2 only)" or
"2026-11-15 (in progress, thru Nov 9)".

Strava names are matched to Athletic.net IDs through data/rules/strava_aliases.csv,
then by exact name. If several files cover the same athlete-week, the newest
file wins.
"""
import csv
import re
from pathlib import Path

from . import races

DATE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\s*(?:\((.*)\))?\s*$")


def load_aliases(path):
    if not Path(path).exists():
        return {}
    with open(path) as fh:
        return {races.norm(r["strava_name"]): r for r in csv.DictReader(fh)}


def load(mileage_dir, alias_path, by_name, id_names):
    """-> (rows, review_items)."""
    aliases = load_aliases(alias_path)
    best = {}
    review = []
    files = sorted(Path(mileage_dir).glob("*.csv"), key=lambda p: p.stat().st_mtime)
    for f in files:
        with open(f, newline="") as fh:
            rd = csv.reader(fh)
            header = next(rd)
            athletes = header[2:]
            for row in rd:
                if not row:
                    continue
                ms, me = DATE.match(row[0] or ""), DATE.match(row[1] if len(row) > 1 else "")
                if not (ms and me):
                    continue  # footer rows (averages, peak, totals)
                notes = "; ".join(n for n in (ms.group(2), me.group(2)) if n)
                for name, cell in zip(athletes, row[2:]):
                    try:
                        miles = float(cell)
                    except ValueError:
                        continue
                    best[(name, ms.group(1))] = {
                        "strava_name": name, "week_start": ms.group(1), "week_end": me.group(1),
                        "miles": miles, "partial_week": int(bool(notes)), "week_note": notes,
                        "source_file": f.name,
                    }
    rows, unmatched = [], set()
    for (name, _), r in sorted(best.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        k = races.norm(name)
        if k in aliases and aliases[k]["athlete_id"]:
            aid, how = aliases[k]["athlete_id"], "alias"
        elif k in by_name:
            aid, how = by_name[k], "exact"
        else:
            aid, how = "", "unmatched"
            unmatched.add(name)
        rows.append({"athlete_id": aid, "athlete_name": id_names.get(aid, name), "match": how, **r})
    for name in sorted(unmatched):
        review.append({"issue": "unmatched_strava_name", "season": "", "tab": "", "block": "",
                       "detail": f"Strava name '{name}' has no Athletic.net match (add to data/rules/strava_aliases.csv)"})
    return rows, review
