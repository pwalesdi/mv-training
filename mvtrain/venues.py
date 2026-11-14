"""Where each session (workbook tab) was run, from rules/venues.csv.

- The venue written on the sheet, if there is one (a time like '6:30 pm' or '@ 3:45pm' is dropped): its kind is
  the rules row whose written_contains word it contains - 'Church & Track' matches both; a venue that matches no
  row (SVP, MVHS, Newhall Park ...) is kept as written, kind 'other'.
- Otherwise it is inferred from the session's reps, rows in file order (inferred_from): laps = 1725 m church-loop
  laps or whole multiples of one; track_distances = 100-3200 m in steps of 100, 150 m or the mile.
  shown_inferred is what the app shows for it ('track?').
- A rep's venue: the session's; in a session written with two venues (Church & Track) the one whose
  inferred_from fits the rep (a lap -> church), else the first.
- max_inferred_type / max_inferred_band: the fastest type a rep there may be *inferred* as (rules/pace_zones.csv
  orders the types, fastest first); a faster inferred type is set to it. Labels and overrides are never changed.
"""
import csv
import re
from pathlib import Path

from .cells import CHURCH_LAP_M

TIME = re.compile(r"\s*(?:[@,:;-]\s*)?\b\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)(?![a-z])", re.I)


def clean(written):
    """'Church Loop 6:30 pm' -> 'Church Loop', 'MVHS @ 3:45pm' -> 'MVHS', 'Track : 9:00am' -> 'Track'."""
    s = TIME.sub("", written or "")
    return re.sub(r"[\s@,:;/-]+$", "", re.sub(r"^[\s@,:;/-]+", "", s)).strip()


def is_lap(d):
    """1725 m (one church-loop lap) or a whole number of laps."""
    return bool(d) and d >= CHURCH_LAP_M - 2 and abs(d - round(d / CHURCH_LAP_M) * CHURCH_LAP_M) <= 2


def is_track(d):
    """A track distance: 100-3200 m in steps of 100, 150 m, or the mile."""
    return bool(d) and not is_lap(d) and (d <= 3201 and abs(d - round(d / 100) * 100) <= 1
                                          or abs(d - 150) <= 1 or abs(d - 1609.34) <= 2)


TESTS = {"laps": is_lap, "track_distances": is_track}


class Rules:
    def __init__(self, rules_dir):
        rules_dir = Path(rules_dir)
        self.rows = []
        with open(rules_dir / "venues.csv") as fh:
            for r in csv.DictReader(fh):
                r = {k: (v or "").strip() for k, v in r.items() if k}
                if not r.get("venue"):
                    continue
                r["words"] = [w.strip().lower() for w in re.split(r"[;|]", r.get("written_contains", "")) if w.strip()]
                r["test"] = TESTS.get(r.get("inferred_from", ""))
                r["cap"] = (r.get("max_inferred_type", ""), r.get("max_inferred_band", "")) if r.get("max_inferred_type") else None
                self.rows.append(r)
        # speed order of types, fastest first (rules/pace_zones.csv: race-distance anchor, shortest first)
        with open(rules_dir / "pace_zones.csv") as fh:
            zones = sorted(csv.DictReader(fh), key=lambda z: float(z["min_km"]))
        self.order = [(z["workout_type"], z.get("band", "")) for z in zones]

    def rank(self, wtype, band=""):
        """0 = fastest; None for a type that isn't in rules/pace_zones.csv (easy aerobic, hills)."""
        if (wtype, band or "") in self.order:
            return self.order.index((wtype, band or ""))
        return next((i for i, (t, _) in enumerate(self.order) if t == wtype), None)

    def faster(self, wtype, band, than):
        a, b = self.rank(wtype, band), self.rank(*than)
        return a is not None and b is not None and a < b

    def session(self, written, distances):
        """-> {name, kind, source, rows}: what the app shows, the kind ('church', 'track', 'church+track', 'other',
        or '' when unknown), how it was decided ('sheet', or the inferred_from rule), the matching rules rows."""
        name = clean(written)
        if name:
            low = name.lower()
            rows = [r for r in self.rows if any(w in low for w in r["words"])]
            return {"name": name, "kind": "+".join(r["venue"] for r in rows) or "other", "source": "sheet", "rows": rows}
        for r in self.rows:
            if r["test"] and any(r["test"](d) for d in distances if d):
                return {"name": r.get("shown_inferred") or r["venue"], "kind": r["venue"], "source": r["inferred_from"], "rows": [r]}
        return {"name": "", "kind": "", "source": "", "rows": []}

    @staticmethod
    def rep_row(venue, d):
        """The rules row for one rep of the session (by its distance when the session has two venues)."""
        rows = venue["rows"]
        if len(rows) <= 1:
            return rows[0] if rows else None
        return next((r for r in rows if r["test"] and d and r["test"](d)), rows[0])

    def cap(self, wtype, band, row):
        """An inferred (type, band) at a venue -> (type, band, note): set to the venue's max_inferred type when faster."""
        if row and row["cap"] and wtype and self.faster(wtype, band, row["cap"]):
            t, b = row["cap"]
            return t, b, f"inferred {wtype}{'/' + band if band else ''} -> {t}{'/' + b if b else ''}: at the {row['venue']} (rules/venues.csv)"
        return wtype, band, ""
