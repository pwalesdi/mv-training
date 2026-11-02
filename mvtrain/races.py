"""Athletic.net race results and a simple fitness reference.

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


def load(race_dir):
    """-> (results list, {athlete_id: name}, {normalised name: athlete_id})."""
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
            if c["workout_type"] == "short_fast":
                return ("speed_endurance" if rep_m and rep_m <= 400 else "lactic_capacity"), ""
            return c["workout_type"], c["band"]
    return "", ""
