"""Athletic.net race results and a simple fitness reference.

Each XC result also gets xc_meet (the meet's name across years: no year, edition number or
"(AKA ...)"; rules/xc_meets.csv merges names that changed otherwise) and xc_course (xc_meet +
distance - only results with the same xc_course are the same course; a meet that changed its
distance between years is a different course, unless rules/xc_courses.csv names its distances as one
course: then xc_course is shared and xc_course_name is that course's name). duplicate_of marks a result Athletic.net posted
twice under two meet IDs (same athlete, kind, day, distance and time within 0.1 s): it holds the
meet ID of the copy that is kept (the one with more decimals).

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
        kept = []
        for r in sorted(rows, key=lambda r: (-_decimals(r["mark"]), r["meet_id"])):
            twin = next((k for k in kept if k["meet_id"] != r["meet_id"] and abs(k["seconds"] - r["seconds"]) <= 0.1), None)
            if twin:
                r["duplicate_of"] = twin["meet_id"]
            else:
                kept.append(r)


def load(race_dir, rules_dir=None, review=None):
    """-> (results list, {athlete_id: name}, {normalised name: athlete_id}). review: a list that gets a
    rule_unused item for each rules/xc_courses.csv row that can't be read or matches no result."""
    results, names = [], {}
    for f in sorted(Path(race_dir).glob("*.csv")):
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
                results.append({
                    "kind": kind,
                    "athlete_id": aid,
                    "athlete_name": r["athlete_name"],
                    "gender": r["gender"],
                    "grade": r["grade"],
                    "date": dt.date.fromisoformat(r["meet_date"]),
                    "meet_name": r["meet_name"],
                    "meet_id": r["meet_id"],
                    "event": event,
                    "race_name": r.get("race_name") or r.get("round_or_section", ""),
                    "distance_m": d,
                    "seconds": secs,
                    "mark": r.get("time") or r.get("mark", ""),
                    "place": r["place"],
                    "season_year": r["season_year"],
                    "source_url": r["source_url"],
                })
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
    _mark_duplicates(results)
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
