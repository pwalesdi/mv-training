"""Strava activities -> the activities table, each athlete's weekly run miles and cross-training, and the
longest run of each week.

Files: every .csv in data/strava/ with the activity columns, one row per activity: athlete (Strava display
name), date, sport, title, distance, unit, moving_time, moving_time_sec, pace_strava, pace_calc_min_per_mi,
elev_gain, activity_id, activity_url. Any other .csv there (e.g. a copy of a weekly export) is skipped and
listed in review.csv. A file name ending _<from>_to_<to>.csv (from = YYYY-MM or YYYY-MM-DD) gives the
period the export covers, so a week it only partly covers is marked partial; without one, the file's first
and last activity dates are used.

- Names: data/rules/strava_aliases.csv, then the exact Athletic.net name - as for the weekly exports. An
  unmatched name is kept without an athlete ID and listed in review.csv with possible matches.
- An activity ID in more than one file is kept once, from the newest file.
- Distance -> miles from the unit column (mi, km, m, yd). No distance (or 0) stays blank.
- rules/strava_sports.csv: what each sport counts as - run (weekly run miles), cross (cross-training,
  counted in minutes, never added to run miles) or other (kept, counted nowhere). A sport not listed
  there counts as other and is listed in review.csv.
- data/rules/strava_fixes.csv (activity_id, action, value, note) corrects single activities: exclude (left
  out of every total), sport (count it as that sport), miles (the right distance).
- A run of a mile or more faster than 4:00/mi or slower than 20:00/mi, or one over 20 miles, is counted as
  recorded and listed in review.csv - correct it in strava_fixes.csv if it's wrong.

Weeks run Monday-Sunday. An athlete has a row for a week when they recorded any activity in it: run miles
(runs with a distance), the number of runs, runs with no distance (their minutes - they add no miles),
cross-training minutes by sport, and the week's longest run, which is also flagged on the activity
(longest_of_week). A week with no activity at all has no row: it shows as no data, never as 0.

Long runs (rules/mileage.csv long_run_min_miles): the week's longest run is a long run (long_run) when it
is at least that many miles. Blank: no run is a long run yet; longest_of_week still marks the candidates.
"""
import csv
import datetime as dt
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import races
from .mileage import load_aliases, season_of, suggest

NEEDED = {"athlete", "date", "sport", "distance", "unit", "moving_time_sec", "activity_id"}
TO_MILES = {"mi": 1.0, "mile": 1.0, "miles": 1.0, "km": 0.621371, "kilometers": 0.621371, "kilometres": 0.621371,
            "m": 1 / 1609.344, "meters": 1 / 1609.344, "metres": 1 / 1609.344, "yd": 1 / 1760, "yards": 1 / 1760}
WINDOW = re.compile(r"_(\d{4}-\d{2}(?:-\d{2})?)_to_(\d{4}-\d{2}-\d{2})\.csv$", re.I)
FAST_S, SLOW_S, LONG_MI = 240, 1200, 20  # review flags (runs of 1+ mile): faster than 4:00/mi, slower than 20:00/mi
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def load_sports(rules_dir):
    """rules/strava_sports.csv -> {sport (lower case): run | cross | other}."""
    with open(Path(rules_dir) / "strava_sports.csv") as fh:
        return {r["sport"].strip().lower(): r["counts_as"].strip().lower() for r in csv.DictReader(fh) if r["sport"].strip()}


def load_settings(rules_dir):
    """rules/mileage.csv -> {setting: value}."""
    p = Path(rules_dir) / "mileage.csv"
    if not p.exists():
        return {}
    with open(p) as fh:
        return {r["setting"].strip(): r["value"].strip() for r in csv.DictReader(fh) if r["setting"].strip()}


def setting_miles(settings, key, review):
    """A mileage setting from rules/mileage.csv as a number, or None when blank (a non-number goes to review)."""
    v = (settings.get(key) or "").strip()
    if not v:
        return None
    x = _num(v)
    if x is None or x < 0:
        review.append(_item(f"rules/mileage.csv: {key} '{v}' isn't a number of miles - ignored", "rule_unused"))
    return x if x is not None and x >= 0 else None


def mark_long_runs(acts, weeks, min_miles):
    """Long run = the week's longest run, if it is at least min_miles (rules/mileage.csv long_run_min_miles).
    min_miles None: nothing is marked."""
    for a in acts:
        a["long_run"] = int(bool(a["longest_of_week"]) and min_miles is not None and (a["miles"] or 0) >= min_miles)
    for w in weeks:  # a week with no run that has a distance has no longest run, so no long run either
        w["long_run"] = int(min_miles is not None and w["longest_run_mi"] is not None and w["longest_run_mi"] >= min_miles)


def load_fixes(path):
    """data/rules/strava_fixes.csv -> {activity_id: row}."""
    p = Path(path)
    if not p.exists():
        return {}
    with open(p) as fh:
        return {r["activity_id"].strip(): {k: (v or "").strip() for k, v in r.items() if k} for r in csv.DictReader(fh)
                if (r.get("activity_id") or "").strip()}


def _num(v):
    try:
        return float(str(v).replace(",", "").strip())
    except ValueError:
        return None


def _feet(v):
    m = re.match(r"\s*([\d,.]+)\s*(ft|m)?\b", v or "")
    if not m:
        return None
    x = float(m.group(1).replace(",", ""))
    return round(x * 3.28084) if m.group(2) == "m" else x


def pace_text(s):
    """seconds per mile -> 'm:ss'."""
    s = round(s)
    return f"{s // 60}:{s % 60:02d}"


def _day(d):
    return f"{WD[d.weekday()]} {MON[d.month - 1]} {d.day}"


def _n(n):
    return f"{n} activit{'y' if n == 1 else 'ies'}"


def _item(detail, issue="strava_file", season=""):
    return {"issue": issue, "season": season, "tab": "", "block": "", "detail": detail}


def load(strava_dir, rules_dir, alias_path, fixes_path, by_name, id_names, results=(), excluded=None):
    """-> (activities, weeks, windows, review). windows: {athlete key: (first day, last day)} the exports cover;
    the athlete key is the Athletic.net ID, or '?' + the normalised Strava name for an unmatched name."""
    sports, fixes, aliases = load_sports(rules_dir), load_fixes(fixes_path), load_aliases(alias_path)
    raced = defaultdict(set)
    for r in results:
        raced[r["athlete_id"]].add(season_of(r["date"]))
    review, unknown_sport, unknown_unit = [], Counter(), Counter()

    def match(name):
        k = races.norm(name)
        if k in aliases and aliases[k]["athlete_id"]:
            return aliases[k]["athlete_id"], "alias"
        if k in by_name:
            return by_name[k], "exact"
        return "", "unmatched"

    folder = Path(strava_dir)
    files = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []
    best, twice, windows = {}, 0, defaultdict(list)
    for f in files:  # oldest first, so a newer file's copy of an activity replaces the older one
        with open(f, newline="", encoding="utf-8-sig") as fh:
            rd = csv.DictReader(fh)
            cols = {(c or "").strip().lower() for c in (rd.fieldnames or [])}
            rows = list(rd)
        if not NEEDED <= cols:
            weekly = bool(cols & {"week_start_mon", "week_start", "week"})
            review.append(_item(f"'{f.name}' in data/strava skipped: not an activities file"
                                + (" - it looks like a weekly mileage export; those go in data/mileage/" if weekly else
                                   f" (needs the columns {', '.join(sorted(NEEDED))})")))
            continue
        span = WINDOW.search(f.name)
        dates_by_key = defaultdict(list)
        for raw in rows:
            r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items() if k}
            try:
                d = dt.date.fromisoformat(r["date"][:10])
            except ValueError:
                review.append(_item(f"'{f.name}': activity {r['activity_id'] or '?'} has no readable date ('{r['date']}') - skipped"))
                continue
            name = r["athlete"]
            aid, how = match(name)
            if aid and excluded and excluded(aid, season_of(d)):
                continue
            key = aid or "?" + races.norm(name)
            dates_by_key[key].append(d)
            act_id = r["activity_id"]
            twice += act_id in best
            sport, fix, note = r["sport"], fixes.get(act_id), ""
            counted = sport
            if fix and fix["action"] == "sport" and fix.get("value"):
                counted, note = fix["value"], f"counted as {fix['value']}"
            counts_as = sports.get(counted.lower())
            if counts_as is None:
                unknown_sport[counted] += 1
                counts_as = "other"
            dist, miles = _num(r["distance"]), None
            if dist:
                factor = TO_MILES.get(r["unit"].lower())
                if factor is None:
                    unknown_unit[r["unit"]] += 1
                else:
                    miles = round(dist * factor, 3)
            if fix and fix["action"] == "miles" and _num(fix.get("value")) is not None:
                note = f"distance corrected from {miles if miles is not None else 'none'} mi"
                miles = _num(fix["value"]) or None
            if fix and fix["action"] == "exclude":
                counts_as, note = "excluded", "left out of every total"
            if fix:
                note = "; ".join(x for x in (note, fix.get("note", "")) if x) + " (data/rules/strava_fixes.csv)"
            moving = _num(r["moving_time_sec"])
            pace = moving / miles if counts_as == "run" and miles and moving else None
            flag = []
            if counts_as == "run" and miles and miles >= 1 and pace:
                if pace < FAST_S:
                    flag.append(f"faster than 4:00/mi over {miles:g} mi")
                elif pace > SLOW_S:
                    flag.append(f"slower than 20:00/mi over {miles:g} mi - a walk, or a watch left running?")
            if counts_as == "run" and miles and miles > LONG_MI:
                flag.append(f"longer than {LONG_MI} mi")
            best[act_id] = {
                "activity_id": act_id, "athlete_id": aid, "athlete_name": id_names.get(aid, name) if aid else name,
                "strava_name": name, "match": how, "date": d.isoformat(), "weekday": WD[d.weekday()],
                "week_start": (d - dt.timedelta(days=d.weekday())).isoformat(), "season": season_of(d),
                "sport": sport, "counts_as": counts_as, "fix": note, "title": r.get("title", ""),
                "distance": r["distance"], "unit": r["unit"], "miles": miles,
                "moving_time": r.get("moving_time", ""), "moving_s": moving,
                "pace_s_per_mi": round(pace, 1) if pace else None, "pace": pace_text(pace) if pace else "",
                "pace_strava": r.get("pace_strava", ""), "pace_calc": r.get("pace_calc_min_per_mi", ""),
                "elev_ft": _feet(r.get("elev_gain", "")), "url": r.get("activity_url", ""),
                "source_file": f.name, "check": "; ".join(flag), "longest_of_week": 0,
            }
        for key, ds in dates_by_key.items():
            if span:
                a = span.group(1)
                windows[key].append((dt.date.fromisoformat(a + "-01" if len(a) == 7 else a), dt.date.fromisoformat(span.group(2))))
            else:
                windows[key].append((min(ds), max(ds)))
    windows = {k: (min(a for a, _ in v), max(b for _, b in v)) for k, v in windows.items()}
    acts = sorted(best.values(), key=lambda a: (a["athlete_name"].lower(), a["date"], a["activity_id"]))

    if twice:
        review.append(_item(f"{twice} activit{'y was' if twice == 1 else 'ies were'} in more than one file - each kept once, from the newest file"))
    for s, n in unknown_sport.most_common():
        review.append(_item(f"Strava sport '{s}' ({_n(n)}) isn't in rules/strava_sports.csv - counted as other (not run, not cross-training)"))
    for u, n in unknown_unit.most_common():
        review.append(_item(f"{_n(n)} with the distance unit '{u}', which isn't known - distance left blank"))
    found = {a["activity_id"] for a in acts}
    for act_id in fixes:
        if act_id not in found:
            review.append(_item(f"data/rules/strava_fixes.csv: activity {act_id} isn't in data/strava - fix not used", "rule_unused"))
    for a in acts:
        if a["check"]:
            review.append(_item(f"{a['athlete_name']} {a['date']} {a['sport']} {a['miles']:g} mi in {a['moving_time']} "
                                f"({a['pace']}/mi): {a['check']} - counted as recorded; data/rules/strava_fixes.csv can exclude or "
                                f"correct it ({a['url']})", "strava_check", a["season"]))
    unmatched = defaultdict(list)
    for a in acts:
        if a["match"] == "unmatched":
            unmatched[a["strava_name"]].append(a["date"])
    for name, ds in sorted(unmatched.items()):
        hint = suggest(name, ds, id_names, raced, excluded)
        review.append(_item(f"Strava name '{name}' ({_n(len(ds))} in data/strava) has no Athletic.net match "
                            f"(add it to data/rules/strava_aliases.csv)" + (f"; possible: {', '.join(hint)}" if hint else ""),
                            "unmatched_strava_name"))
    return acts, _weeks(acts, windows), windows, review


def _weeks(acts, windows):
    by = defaultdict(list)
    for a in acts:
        if a["counts_as"] != "excluded":
            by[(a["athlete_id"] or "?" + races.norm(a["strava_name"]), a["week_start"])].append(a)
    out = []
    for (key, ws), xs in sorted(by.items()):
        runs = [a for a in xs if a["counts_as"] == "run"]
        dist = [a for a in runs if a["miles"]]
        nodist = [a for a in runs if not a["miles"]]
        cross = Counter()
        for a in xs:
            if a["counts_as"] == "cross":
                cross[a["sport"]] += a["moving_s"] or 0
        longest = min(dist, key=lambda a: (-a["miles"], a["date"], a["activity_id"])) if dist else None
        if longest:
            longest["longest_of_week"] = 1
        start = dt.date.fromisoformat(ws)
        end = start + dt.timedelta(days=6)
        w0, w1 = windows.get(key, (start, end))
        notes = ([f"export starts {_day(w0)}"] if start < w0 else []) + ([f"through {_day(w1)}"] if end > w1 else [])
        out.append({
            "athlete_id": xs[0]["athlete_id"], "athlete_name": xs[0]["athlete_name"], "strava_name": xs[0]["strava_name"],
            "week_start": ws, "week_end": end.isoformat(), "season": season_of(start),
            "run_miles": round(sum(a["miles"] for a in dist), 2), "n_runs": len(runs),
            "runs_no_distance": len(nodist), "run_min_no_distance": round(sum(a["moving_s"] or 0 for a in nodist) / 60),
            "cross_min": round(sum(cross.values()) / 60), "n_cross": sum(1 for a in xs if a["counts_as"] == "cross"),
            "cross_sports": "; ".join(f"{s} {round(v / 60)} min" for s, v in cross.most_common()),
            "longest_run_mi": longest["miles"] if longest else None, "longest_run_date": longest["date"] if longest else "",
            "longest_run_id": longest["activity_id"] if longest else "",
            "partial_week": int(bool(notes)), "week_note": "; ".join(notes),
            "covered_from": max(start, w0).isoformat(), "covered_to": min(end, w1).isoformat(),
        })
    return out


def _export_days(row):
    """(first, last) day a weekly-export row covers: the whole week, unless it's marked partial and its note
    says which days ('Aug 1-2 only', 'in progress, thru Nov 9'). None when partial and the days can't be read."""
    s, e = dt.date.fromisoformat(row["week_start"]), dt.date.fromisoformat(row["week_end"])
    if not row.get("partial_week"):
        return s, e
    note = row.get("week_note") or ""

    def day(mon, d):
        for y in (s.year, e.year):
            try:
                x = dt.date(y, MON.index(mon[:3].title()) + 1, int(d))
            except ValueError:
                continue
            if s <= x <= e:
                return x
        return None
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\s*[-–]\s*(\d{1,2})\s+only", note)
    if m and m.group(1)[:3].title() in MON:
        a, b = day(m.group(1), m.group(2)), day(m.group(1), m.group(3))
        return (a, b) if a and b else None
    m = re.search(r"\b(?:thru|through)\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})", note)
    if m and m.group(1)[:3].title() in MON:
        b = day(m.group(1), m.group(2))
        return (s, b) if b else None
    return None


def combine(weeks, export_rows, acts, windows, prefer="activities"):
    """The weekly series the app shows (athlete_weeks): one row per athlete-week that has Strava activities
    and/or a weekly-export row, for athletes matched to an Athletic.net ID. Where both exist, `prefer`
    (rules/mileage.csv weekly_source_when_both: activities | export) picks the run miles shown and both are
    kept. compare_miles is the activities' run miles over the days the export row covers (also when the
    athlete recorded nothing that week but their activity export covers it); export_minus_activities is the
    difference. -> (rows, review items for differences over 1 mile)."""
    daily = defaultdict(float)
    for a in acts:
        if a["counts_as"] == "run" and a["miles"] and a["athlete_id"]:
            daily[(a["athlete_id"], a["date"])] += a["miles"]
    act = {(w["athlete_id"], w["week_start"]): w for w in weeks if w["athlete_id"]}
    exp = {(m["athlete_id"], m["week_start"]): m for m in export_rows if m.get("athlete_id")}
    rows, review = [], []
    for key in sorted(act.keys() | exp.keys(), key=lambda k: (k[0], k[1])):
        w, m = act.get(key), exp.get(key)
        compare, diff, days = None, None, None
        win = windows.get(key[0])
        if m and win:
            days = _export_days(m)
            if days and win[0] <= days[0] and days[1] <= win[1]:
                n = (days[1] - days[0]).days + 1
                compare = round(sum(daily.get((key[0], (days[0] + dt.timedelta(days=i)).isoformat()), 0) for i in range(n)), 2)
                diff = round(float(m["miles"]) - compare, 2)
        use_act = bool(w) and (not m or prefer != "export")
        src = w if use_act else m
        if use_act:
            cov = (w["covered_from"], w["covered_to"])
        else:  # the days the export row covers: the whole week unless its note says otherwise
            ed = days if days is not None else _export_days(m)
            cov = (ed[0].isoformat(), ed[1].isoformat()) if ed else (m["week_start"], m["week_end"])
        rows.append({
            "athlete_id": key[0], "athlete_name": (w or m)["athlete_name"], "week_start": key[1],
            "week_end": src["week_end"], "season": season_of(key[1]),
            "run_miles": w["run_miles"] if use_act else float(m["miles"]), "source": "activities" if use_act else "export",
            "partial_week": int(bool(src["partial_week"])), "week_note": src["week_note"] or "",
            "covered_from": cov[0], "covered_to": cov[1],
            "activity_run_miles": w["run_miles"] if w else None, "export_miles": float(m["miles"]) if m else None,
            "export_file": m["source_file"] if m else "", "compare_miles": compare, "export_minus_activities": diff,
            **{c: (w[c] if w else None) for c in ("n_runs", "runs_no_distance", "run_min_no_distance", "cross_min", "n_cross",
                                                    "cross_sports", "longest_run_mi", "longest_run_date", "longest_run_id",
                                                    "long_run")},
        })
        if diff is not None and abs(diff) > 1:
            span = "the whole week" if days == (dt.date.fromisoformat(key[1]), dt.date.fromisoformat(m["week_end"])) \
                else f"{_day(days[0])} - {_day(days[1])}"
            review.append(_item(f"{rows[-1]['athlete_name']}, week of {key[1]}: weekly export {float(m['miles']):g} mi, "
                                f"Strava activities {compare:g} mi over the same days ({span}) - {rows[-1]['source']} shown "
                                f"(rules/mileage.csv)", "mileage_sources_differ", season_of(key[1])))
    return rows, review


def months(weeks, acts):
    """athlete_months: run miles per calendar month, from the weekly series the app shows (athlete_weeks). A week
    shown from Strava activities adds each run on its own date, so its miles land in the right month; a week shown
    from the weekly export has only a weekly total, which is spread evenly over the days it covers (export_days
    counts them - the month's number is approximate there). days_with_data: the month's days some week row covers.
    partial: fewer than all of the month's days - month_note says why (the data starts or ends inside the month,
    e.g. the month in progress, or weeks with no data). Cross-training minutes come from activities only."""
    daily = defaultdict(lambda: [0.0, 0, 0, 0.0])  # (athlete, day) -> run miles, runs, runs without distance, cross min
    for a in acts:
        if a["athlete_id"] and a["counts_as"] in ("run", "cross"):
            x = daily[(a["athlete_id"], a["date"])]
            if a["counts_as"] == "run":
                x[0] += a["miles"] or 0
                x[1] += 1
                x[2] += 0 if a["miles"] else 1
            else:
                x[3] += (a["moving_s"] or 0) / 60
    by, first, last = {}, {}, {}
    for w in weeks:
        aid = w["athlete_id"]
        s = dt.date.fromisoformat(w["week_start"])
        f, t = dt.date.fromisoformat(w["covered_from"]), dt.date.fromisoformat(w["covered_to"])
        first[aid], last[aid] = min(first.get(aid, f), f), max(last.get(aid, t), t)
        for i in range(7):
            d = s + dt.timedelta(days=i)
            covered, x = f <= d <= t, daily.get((aid, d.isoformat())) if w["source"] == "activities" else None
            if not covered and not x:
                continue
            m = by.setdefault((aid, f"{d.year}-{d.month:02d}"), {"name": w["athlete_name"], "run": 0.0, "n": 0, "nodist": 0,
                                                                   "cross": 0.0, "act_days": 0, "days": set(), "export_days": 0})
            if covered:
                m["days"].add(d)
            if w["source"] == "activities":
                m["act_days"] += covered
                if x:
                    m["run"] += x[0]
                    m["n"] += x[1]
                    m["nodist"] += x[2]
                    m["cross"] += x[3]
            elif covered:
                m["run"] += float(w["run_miles"]) / ((t - f).days + 1)
                m["export_days"] += 1
    out = []
    for (aid, ym), m in sorted(by.items()):
        y, mo = int(ym[:4]), int(ym[5:])
        start = dt.date(y, mo, 1)
        end = dt.date(y + mo // 12, mo % 12 + 1, 1) - dt.timedelta(days=1)
        n_days = (end - start).days + 1
        notes = []
        if start < first[aid] <= end:
            notes.append(f"data from {_day(first[aid])}")
        if start <= last[aid] < end:
            notes.append(f"data through {_day(last[aid])}")
        lo, hi = max(start, first[aid]), min(end, last[aid])
        gap = (hi - lo).days + 1 - sum(1 for d in m["days"] if lo <= d <= hi)
        if gap > 0:
            notes.append(f"no data for {gap} of the month's days")
        out.append({
            "athlete_id": aid, "athlete_name": m["name"], "month": ym, "month_start": start.isoformat(),
            "month_end": end.isoformat(), "season": season_of(start), "run_miles": round(m["run"], 2),
            "n_runs": m["n"] if m["act_days"] else None, "runs_no_distance": m["nodist"] if m["act_days"] else None,
            "cross_min": round(m["cross"]) if m["act_days"] else None, "days_with_data": len(m["days"]),
            "days_in_month": n_days, "export_days": m["export_days"], "partial": int(len(m["days"]) < n_days),
            "month_note": "; ".join(notes),
        })
    return out
