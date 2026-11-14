"""Athletic.net race results and a simple fitness reference.

Each XC result also gets xc_meet (the meet's name across years: no year, edition number or
"(AKA ...)"; rules/xc_meets.csv merges names that changed otherwise) and xc_course (xc_meet +
distance - only results with the same xc_course are the same course; a meet that changed its
distance between years is a different course, unless rules/xc_courses.csv names its distances as one
course: then xc_course is shared and xc_course_name is that course's name). duplicate_of marks a result Athletic.net posted
twice under two meet IDs (same athlete, kind, day, distance and time within 0.1 s): it holds the
meet ID of the copy that is kept (the one with more decimals).

rules/race_kinds.csv makes every result of a meet one kind, whichever export it is in (Mid-Season Mania: a
track meet run in October that Athletic.net also posts as cross country). export_kind is the export a row
came from, kind_rule the rule line that set its kind. For a meet a rule names, each athlete's race is kept
once: the copy from the export of its kind, else one with a time - the others get duplicate_of. Distances are
spelled one way per kind, whichever export a row is in: track events as the track exports spell them ('1,600
Meters' -> '1600 Meters'), XC distances as the XC exports do ('5000 Meters' -> '5,000 Meters', '3.00 Miles' ->
'3 Miles'). season is the school year from the meet date (July-June: an October race is in that fall's school
year); Athletic.net's own season_year is kept as written but not used. hand_timed is 1 for a mark the export
marks hand-timed (TRUE), else 0 (FALSE or blank). unofficial is 1 where an export marks a result unofficial (a
column named like unofficial / official), else blank.

The fitness reference is each athlete's best 1600 m *equivalent* (Riegel,
exponent 1.06) from races in the 365 days before a date. Unlabeled blocks get
a likely workout type from the target pace expressed as a % of that mile
pace, using cutoffs calibrated on Patrick's own labeled 2025-26 reps
(rules/inference.csv). Every inferred value is marked as inferred.
"""
import csv
import datetime as dt
import re
from collections import defaultdict
from pathlib import Path

RIEGEL = 1.06


def _distance_m(s):
    s = s.lower().replace(",", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*miles?", s)
    if m:
        return float(m.group(1)) * 1609.34
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:meters|m)\b", s)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*k\b", s)
    if m:
        return float(m.group(1)) * 1000
    return None


def school_season(d):
    """date -> '2425' (school years run July-June)."""
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


METERS = re.compile(r"\s*(\d[\d,]*)\s*(?:meters?|m)\s*", re.I)


MILES = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(?:miles?|mi)\s*", re.I)


def track_event(raw):
    """A track event spelled as the track exports spell it: '1,600 Meters' / '1600 Meters' -> '1600 Meters'."""
    m = METERS.fullmatch(raw or "")
    return f"{int(m.group(1).replace(',', ''))} Meters" if m else raw


def xc_distance(raw):
    """An XC distance spelled as the XC exports spell it: '5000 Meters' -> '5,000 Meters', '3.00 Miles' -> '3 Miles'."""
    m = METERS.fullmatch(raw or "")
    if m:
        return f"{int(m.group(1).replace(',', '')):,} Meters"
    m = MILES.fullmatch(raw or "")
    if m:
        v = float(m.group(1))
        return f"{v:g} {'Mile' if v == 1 else 'Miles'}"
    return raw


def _hand_timed(row):
    """1 when the export marks the mark hand-timed (TRUE / yes / 1), else 0 (FALSE or blank)."""
    return 1 if (row.get("hand_timed") or "").strip().lower() in ("true", "yes", "y", "1", "x", "h") else 0


def _unofficial(row):
    """1 where the export marks the result unofficial (a column named like 'unofficial', or 'official' = no), else ''."""
    for k, v in row.items():
        name, v = (k or "").strip().lower(), (v or "").strip().lower()
        if "unofficial" in name and v in ("1", "true", "yes", "y", "x", "u", "unofficial"):
            return 1
        if "official" in name and "unofficial" not in name and v in ("0", "false", "no", "n", "unofficial"):
            return 1
    return ""


def load_kind_rules(rules_dir):
    """rules/race_kinds.csv: meet_contains,kind,note - a result whose meet name contains `meet_contains` (any case)
    is `kind` (TF = track, XC = cross country), whichever export it is in."""
    p = Path(rules_dir) / "race_kinds.csv" if rules_dir else None
    if not p or not p.exists():
        return []
    out = []
    with open(p) as fh:
        for n, r in enumerate(csv.DictReader(fh), start=2):
            r = {k: (v or "").strip() for k, v in r.items() if k}
            if r.get("meet_contains") or r.get("kind"):
                out.append({**r, "file_line": n, "used": False})
    return out


def _apply_kind_rules(results, rules):
    for r in results:
        low = r["meet_name"].lower()
        rule = next((k for k in rules if k.get("meet_contains") and k.get("kind") in ("TF", "XC")
                     and k["meet_contains"].lower() in low), None)
        if rule:
            rule["used"] = True
            r["kind"], r["kind_rule"] = rule["kind"], f"rules/race_kinds.csv line {rule['file_line']}"
        r["event"] = track_event(r["event"]) if r["kind"] == "TF" else xc_distance(r["event"])


def load_file_rules(rules_dir):
    """rules/race_files.csv: file,action,note - action=skip leaves that export in data/races out of the build."""
    p = Path(rules_dir) / "race_files.csv" if rules_dir else None
    if not p or not p.exists():
        return {}
    with open(p) as fh:
        return {r["file"].strip(): {**{k: (v or "").strip() for k, v in r.items() if k}, "file_line": n}
                for n, r in enumerate(csv.DictReader(fh), start=2) if (r.get("file") or "").strip()}


def load_meet_rules(rules_dir):
    """rules/xc_meets.csv: contains,meet,note - a meet name containing `contains` (any case) is `meet`."""
    p = Path(rules_dir) / "xc_meets.csv" if rules_dir else None
    if not p or not p.exists():
        return []
    with open(p) as fh:
        return [r for r in csv.DictReader(fh) if (r.get("contains") or "").strip()]


AKA = re.compile(r"\s*\(\s*a\.?k\.?a\.?\b[^)]*\)", re.I)
YEAR = re.compile(r"^\s*(?:19|20)\d{2}\s+")
EDITION = re.compile(r"^\s*\d+(?:st|nd|rd|th)\s+(?:annual\s+)?", re.I)


def xc_meet(name, rules=()):
    """'2024 CIF State Cross Country Championships' -> 'CIF State Cross Country Championships',
    '43rd Annual Woodbridge Cross Country Classic' -> 'Woodbridge Cross Country Classic',
    'Lowell Invitational (AKA San Francisco Invitational)' -> 'Lowell Invitational'."""
    low = name.lower()
    for r in rules:
        if r["contains"].strip().lower() in low:
            return r["meet"].strip()
    return EDITION.sub("", YEAR.sub("", AKA.sub("", name))).strip()


def meet_key(name):
    k = re.sub(r"[^a-z0-9]+", " ", name.lower()).strip()
    return re.sub(r"\bchampionships\b", "championship", k)


def course_distance(s):
    """'2.78 mi' / '2.78 Miles' / '5000 m' / '5,000 Meters' / '5k' -> meters; None without a unit."""
    s = (s or "").lower().replace(",", "").strip()
    for pat, factor in ((r"(\d+(?:\.\d+)?)\s*(?:mi|miles?)", 1609.34), (r"(\d+(?:\.\d+)?)\s*(?:m|meters?|metres?)", 1.0),
                        (r"(\d+(?:\.\d+)?)\s*km?", 1000.0)):
        m = re.fullmatch(pat, s)
        if m:
            return float(m.group(1)) * factor
    return None


def load_course_rules(rules_dir, meet_rules=()):
    """rules/xc_courses.csv: meet,distance,course,note - results of `meet` (named as the build names it, or as
    Athletic.net does) run at `distance` are the course `course`, whatever distance each year had."""
    p = Path(rules_dir) / "xc_courses.csv" if rules_dir else None
    if not p or not p.exists():
        return []
    out = []
    with open(p) as fh:
        for n, r in enumerate(csv.DictReader(fh), start=2):
            r = {k: (v or "").strip() for k, v in r.items() if k}
            if r.get("meet") or r.get("course"):
                out.append({**r, "file_line": n, "used": False, "meters": course_distance(r.get("distance")),
                            "meet_key": meet_key(xc_meet(r.get("meet", ""), meet_rules))})
    return out


def _courses(results, rules, courses=()):
    for r in results:
        r["xc_meet"] = xc_meet(r["meet_name"], rules) if r["kind"] == "XC" else ""
    newest = {}
    for r in sorted((r for r in results if r["xc_meet"]), key=lambda r: r["date"]):
        newest[meet_key(r["xc_meet"])] = r["xc_meet"]  # the newest spelling names the meet
    for r in results:
        if r["xc_meet"]:
            r["xc_meet"] = newest[meet_key(r["xc_meet"])]
        r["xc_course"] = f"{meet_key(r['xc_meet'])}|{round(r['distance_m'])}" if r["xc_meet"] and r["distance_m"] else ""
        r["xc_course_name"] = ""
        if r["xc_course"]:  # rules/xc_courses.csv: this meet's distances in different years are one course
            c = next((c for c in courses if c["meters"] and c["course"] and c["meet_key"] == meet_key(r["xc_meet"])
                      and abs(c["meters"] - r["distance_m"]) < 3), None)
            if c:
                c["used"] = True
                r["xc_course"], r["xc_course_name"] = f"{meet_key(c['course'])}|course", c["course"]


def _decimals(mark):
    m = re.search(r"\.(\d+)\s*$", mark or "")
    return len(m.group(1)) if m else 0


def _mark_duplicates(results):
    groups = defaultdict(list)
    for r in results:
        r["duplicate_of"] = ""
        if r["seconds"] and r["distance_m"]:
            groups[(r["kind"], r["athlete_id"], r["date"], round(r["distance_m"]))].append(r)
    for rows in groups.values():
        kept = []  # the copy from the export of its kind first (rules/race_kinds.csv), then the one with more decimals
        for r in sorted(rows, key=lambda r: (r["export_kind"] != r["kind"], -_decimals(r["mark"]), r["meet_id"])):
            twin = next((k for k in kept if k["meet_id"] != r["meet_id"] and abs(k["seconds"] - r["seconds"]) <= 0.1), None)
            if twin:
                r["duplicate_of"] = twin["meet_id"]
            else:
                kept.append(r)


def _one_per_race(results, review=None):
    """A meet rules/race_kinds.csv names can be in both exports: keep each athlete's race once - the copy from the
    export of its kind, then one with a time, then more decimals; the other copies get duplicate_of (also a DNS /
    DNF, which _mark_duplicates leaves alone). Copies whose times disagree go to review."""
    groups = defaultdict(list)
    for r in results:
        if r.get("kind_rule") and not r["duplicate_of"]:
            groups[(r["kind"], r["athlete_id"], r["date"], round(r["distance_m"] or 0))].append(r)
    for rows in groups.values():
        rows.sort(key=lambda r: (r["export_kind"] != r["kind"], not r["seconds"], -_decimals(r["mark"]), r["meet_id"]))
        keep = rows[0]
        for r in rows[1:]:
            if r["meet_id"] == keep["meet_id"]:
                continue  # the same meet twice in one export (a prelim and a final): not a copy
            r["duplicate_of"] = keep["meet_id"]
            differ = bool(r["seconds"]) != bool(keep["seconds"]) or (r["seconds"] and abs(r["seconds"] - keep["seconds"]) > 0.1)
            if differ and review is not None:
                review.append({"issue": "race_copies_differ", "season": keep["season"], "tab": "", "block": "",
                               "detail": f"athlete {keep['athlete_id']}, {keep['date'].isoformat()} {keep['meet_name']} {keep['event']}: "
                                         f"the {keep['export_kind']} export's {keep['mark'] or 'no mark'} (meet {keep['meet_id']}) is kept, "
                                         f"the {r['export_kind']} export's {r['mark'] or 'no mark'} (meet {r['meet_id']}) left out"})


def load(race_dir, rules_dir=None, review=None):
    """-> (results list, {athlete_id: name}, {normalised name: athlete_id}). review: a list that gets a
    rule_unused item for each rules/xc_courses.csv row that can't be read or matches no result."""
    results, names = [], {}
    files = load_file_rules(rules_dir)
    present = {f.name for f in Path(race_dir).glob("*.csv")}
    for name, fr in files.items():
        if review is None:
            continue
        if name not in present:
            review.append({"issue": "rule_unused", "season": "", "tab": "", "block": "",
                           "detail": f"rules/race_files.csv line {fr['file_line']}: {name} isn't in the races folder"})
        elif fr.get("action") == "skip":
            review.append({"issue": "race_file_left_out", "season": "", "tab": "", "block": "",
                           "detail": f"rules/race_files.csv line {fr['file_line']}: {name} is left out of the build - {fr.get('note', '')}"})
        else:
            review.append({"issue": "rule_unused", "season": "", "tab": "", "block": "",
                           "detail": f"rules/race_files.csv line {fr['file_line']}: action '{fr.get('action', '')}' isn't skip - not used"})
    for f in sorted(Path(race_dir).glob("*.csv")):
        if files.get(f.name, {}).get("action") == "skip":
            continue
        kind = "XC" if "_XC_" in f.name else "TF"
        with open(f) as fh:
            for r in csv.DictReader(fh):
                aid = r["athlete_id"]
                names[aid] = r["athlete_name"]
                if kind == "XC":
                    d, secs = _distance_m(r["race_distance"]), r["time_seconds"]
                    event = r["race_distance"]
                else:
                    d, secs = _distance_m(r["event"]), r["mark_seconds"]
                    event = r["event"]
                try:
                    secs = float(secs)
                except (TypeError, ValueError):
                    secs = None
                date = dt.date.fromisoformat(r["meet_date"])
                results.append({
                    "kind": kind,
                    "export_kind": kind,
                    "kind_rule": "",
                    "athlete_id": aid,
                    "athlete_name": r["athlete_name"],
                    "gender": r["gender"],
                    "grade": r["grade"],
                    "date": date,
                    "meet_name": r["meet_name"],
                    "meet_id": r["meet_id"],
                    "event": event,
                    "race_name": r.get("race_name") or r.get("round_or_section", ""),
                    "distance_m": d,
                    "seconds": secs,
                    "mark": r.get("time") or r.get("mark", ""),
                    "place": r["place"],
                    "hand_timed": _hand_timed(r),
                    "unofficial": _unofficial(r),
                    "season": school_season(date),
                    "season_year": r["season_year"],
                    "source_url": r["source_url"],
                })
    kind_rules = load_kind_rules(rules_dir)
    _apply_kind_rules(results, kind_rules)  # before _courses: a result a rule makes track is on no XC course
    meet_rules = load_meet_rules(rules_dir)
    courses = load_course_rules(rules_dir, meet_rules)
    _courses(results, meet_rules, courses)
    for c in courses:
        why = ("needs meet, distance and course" if not (c.get("meet") and c.get("course") and c.get("distance")) else
               f"can't read distance '{c['distance']}' (write e.g. 2.78 mi or 5000 m)" if not c["meters"] else
               "matched no cross-country result" if not c["used"] else "")
        if why and review is not None:
            review.append({"issue": "rule_unused", "season": "", "tab": "", "block": "",
                           "detail": f"rules/xc_courses.csv line {c['file_line']} ({c.get('meet', '')} {c.get('distance', '')}): {why}"})
    for k in kind_rules:
        why = ("needs meet_contains and kind" if not (k.get("meet_contains") and k.get("kind")) else
               f"kind '{k['kind']}' isn't TF or XC" if k["kind"] not in ("TF", "XC") else
               "matched no result" if not k["used"] else "")
        if why and review is not None:
            review.append({"issue": "rule_unused", "season": "", "tab": "", "block": "",
                           "detail": f"rules/race_kinds.csv line {k['file_line']} ({k.get('meet_contains', '')}): {why}"})
    _mark_duplicates(results)
    _one_per_race(results, review)
    by_name = {norm(n): aid for aid, n in names.items()}
    return results, names, by_name


def norm(name):
    return re.sub(r"[^a-z]", "", name.lower())


class Fitness:
    def __init__(self, results):
        self.by_athlete = defaultdict(list)
        for r in results:
            if r["seconds"] and r["distance_m"]:
                self.by_athlete[r["athlete_id"]].append(r)

    def mile_equiv(self, athlete_id, on_date, window_days=365):
        """Best 1600 m-equivalent seconds from races in the window before on_date (fallback: 120 days after)."""
        rs = self.by_athlete.get(athlete_id, [])
        pick = [r for r in rs if dt.timedelta(0) <= on_date - r["date"] <= dt.timedelta(days=window_days)]
        if not pick:
            pick = [r for r in rs if dt.timedelta(0) < r["date"] - on_date <= dt.timedelta(days=120)]
        if not pick:
            return None
        return min(r["seconds"] * (1600 / r["distance_m"]) ** RIEGEL for r in pick)


def pct_of_mile(target_s, rep_m, mile_s):
    """Target pace as % of the athlete's 1600-equivalent pace (100 = mile race pace, higher = slower)."""
    if not (target_s and rep_m and mile_s):
        return None
    return 100 * (target_s / rep_m) / (mile_s / 1600)


def load_inference(rules_dir):
    with open(Path(rules_dir) / "inference.csv") as fh:
        return sorted(({**r, "max": float(r["max_pct_of_mile_pace"])} for r in csv.DictReader(fh)),
                      key=lambda r: r["max"])


def type_from_pct(pct, rep_m, cutoffs):
    if pct is None:
        return "", ""
    for c in cutoffs:
        if pct <= c["max"]:
            if c["workout_type"] == "short_fast":  # speed endurance vs lactic capacity by rep length
                return ("speed_endurance" if rep_m and rep_m <= 400 else "lactic_capacity"), ""
            return c["workout_type"], c["band"]
    return "", ""
