"""Read-only prototype: a clickable calendar over data/build/training.sqlite.

    .venv/bin/python -m app              # then open http://127.0.0.1:5050

Binds to 127.0.0.1 only (student data). Never writes anything: the database is
opened read-only and reloaded when a new build replaces it.
"""
import bisect
import datetime as dt
import json
import re
import sqlite3
import threading
from collections import Counter, defaultdict
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "build" / "training.sqlite"
STATIC = Path(__file__).resolve().parent / "static"

# workout_type -> display name. The order is the legend order and the color-slot order in app.css.
CATEGORIES = {
    "threshold": "Threshold",
    "lactate_resistance": "Lactate resistance",
    "aerobic_power": "Aerobic power",
    "lactic_capacity": "Lactic capacity",
    "speed_endurance": "Speed endurance",
    "easy_aerobic": "Easy aerobic",
    "hills": "Hills",
}

VARSITY_RULE = (
    "Varsity = ran at least one varsity-level cross-country race that season (Jul-Jun): a race named "
    "'Varsity' (not Junior/Non Varsity), a CIF section/state 'Division' race, or a championship/sweepstakes "
    "race (Clovis, NXR, Woodbridge, Mariner). Frosh/soph, JV and open races don't count, and neither do "
    "track races (track 'Varsity' sections at dual meets include nearly everyone)."
)


def season_of(date):
    """'2025-09-16' -> '2526' (seasons run July-June)."""
    y, m = int(date[:4]), int(date[5:7])
    start = y if m >= 7 else y - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def is_xc_varsity(r):
    if r.get("kind") != "XC":
        return False
    name, meet = (r.get("race_name") or "").lower(), (r.get("meet_name") or "").lower()
    if re.search(r"junior varsity|non[- ]?varsity|\bjv\b|frosh|soph|fresh|9th|10th|\bfs\b|f/s", name) \
            or re.search(r"frosh|soph", meet):
        return False
    return bool(re.search(r"varsity|\bdivision\b|championship|sweep", name))


def akey(row):
    """Stable athlete key: Athletic.net ID, or the workbook name for unmatched athletes."""
    return row.get("athlete_id") or f"u:{row.get('athlete_name', '')}"


def chip(wtype, band="", detail="", src="sheet", **extra):
    """One workout chip. src: sheet (Patrick's label), override (his rule file), inferred (from pace)."""
    if not wtype:
        return None
    if wtype == "UNMAPPED":
        return {"type": "other", "band": "", "detail": "", "src": src, "label": "Unmapped", **extra}
    b = (band or "").replace("->", "→")
    if b == "UNRESOLVED":
        b = "band ?"
    if detail:  # double days: DT -> 'DT-AM' / 'DT-PM'; a DT->LT progression keeps its band: 'DT->LT (PM)'
        b = detail if band == "DT" else f"{b} ({detail.split('-')[-1]})"
    name = CATEGORIES.get(wtype, wtype.replace("_", " ").capitalize())
    return {"type": wtype if wtype in CATEGORIES else "other", "band": band or "", "detail": detail or "",
            "src": src, "label": f"{name} · {b}" if b else name, **extra}


def merge_chips(chips):
    """One chip per (type, band, detail); Patrick's own wins over an inferred one. Keeps first-seen order."""
    out = {}
    for c in chips:
        if not c:
            continue
        k = (c["type"], c["band"], c["detail"], c["label"])
        if k not in out or (out[k]["src"] == "inferred" and c["src"] != "inferred"):
            out[k] = c
    return list(out.values())


def venue_of(s):
    """A sheet's venue as the build decided it (rules/venues.csv): name to show, kind, and source - 'sheet' (written
    on it), 'laps' (no venue written; 1725 m laps = the church loop) or 'track_distances' (inferred: 'track?')."""
    return {"name": s.get("venue_name") or "", "kind": s.get("venue_kind") or "", "source": s.get("venue_source") or ""}


def venues_of(sheets):
    out = []
    for s in sheets:
        v = venue_of(s)
        if v["name"] and v not in out:
            out.append(v)
    return out


def jl(s):
    try:
        return json.loads(s) if s else []
    except (TypeError, ValueError):
        return []


def same_result(a, b):
    """Same athlete's same race listed twice: same day, same distance, times within 0.1 s."""
    return (a["date"] == b["date"] and a["seconds"] and b["seconds"] and a["distance_m"] and b["distance_m"]
            and abs(a["distance_m"] - b["distance_m"]) < 1 and abs(a["seconds"] - b["seconds"]) <= 0.1)


def distinct_races(rows):
    """Each race once, for calendars and race lists. Some results are in both the XC and the track export
    (Mid-Season Mania); the first copy is kept and remembers the other kind in 'also'."""
    out = []
    for r in rows:
        twin = next((o for o in out if o["kind"] != r["kind"] and same_result(o, r)), None)
        if twin:
            twin["also"] = r["kind"]
            continue
        out.append(dict(r))
    return out


def session_rank(n):
    return n if isinstance(n, int) else 99


def round_order(race_name):
    """Prelims before finals on the same day ('Varsity - P', 'Varsity - F')."""
    n = (race_name or "").lower()
    if re.search(r"(?:^|\W)p$|prelim|heat", n):
        return 0
    if re.search(r"(?:^|\W)f$|final", n):
        return 2
    return 1


class Data:
    def __init__(self, path):
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        names = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}

        def table(n):
            return [dict(r) for r in con.execute(f'select * from "{n}"')] if n in names else []

        self.sheets, self.blocks, self.presc = table("sheets"), table("blocks"), table("prescriptions")
        self.ab, self.reps, self.days = table("athlete_blocks"), table("reps"), table("days")
        self.races = table("races")
        # Strava: activities, and the weekly series the Mileage tab shows (activities and / or the weekly export)
        self.acts, self.weeks, self.months = table("activities"), table("athlete_weeks"), table("athlete_months")
        self.settings = {r["setting"]: r["value"] for r in table("settings")}  # rules/mileage.csv as built
        con.close()
        # a result Athletic.net posted twice (two meet IDs for one race) is marked by the build; show it once
        self.races = [r for r in self.races if not r.get("duplicate_of")]

        self.sheet = {s["sheet_key"]: s for s in self.sheets}
        self.block = {b["block_key"]: b for b in self.blocks}
        self.day_row = {d["date"]: d for d in self.days}
        self.sheets_by_date, self.blocks_by_sheet = defaultdict(list), defaultdict(list)
        self.presc_by_block, self.ab_by_block, self.reps_by_block = defaultdict(list), defaultdict(list), defaultdict(list)
        self.races_by_date, self.races_by_athlete = defaultdict(list), defaultdict(list)
        self.ab_by_athlete, self.reps_by_athlete_block = defaultdict(list), defaultdict(list)
        self.weeks_by_athlete, self.acts_by_athlete_date, self.act_dates = defaultdict(list), defaultdict(list), defaultdict(set)
        for s in self.sheets:
            self.sheets_by_date[s["date"]].append(s)
        for b in self.blocks:
            self.blocks_by_sheet[b["sheet_key"]].append(b)
        for p in self.presc:
            self.presc_by_block[p["block_key"]].append(p)
        for a in self.ab:
            self.ab_by_block[a["block_key"]].append(a)
            self.ab_by_athlete[akey(a)].append(a)
        for r in self.reps:
            self.reps_by_block[r["block_key"]].append(r)
            self.reps_by_athlete_block[(akey(r), r["block_key"])].append(r)
        for r in self.races:
            self.races_by_date[r["date"]].append(r)
            self.races_by_athlete[akey(r)].append(r)
        for w in self.weeks:
            self.weeks_by_athlete[w["athlete_id"]].append(w)
        for lst in self.weeks_by_athlete.values():
            lst.sort(key=lambda w: w["week_start"])
        self.months_by_athlete = defaultdict(list)
        for m in sorted(self.months, key=lambda m: m["month"]):
            self.months_by_athlete[m["athlete_id"]].append(m)
        for a in self.acts:
            if a.get("athlete_id"):
                self.acts_by_athlete_date[(a["athlete_id"], a["date"])].append(a)
                if a["counts_as"] in ("run", "cross"):  # a training day (a walk alone isn't)
                    self.act_dates[a["athlete_id"]].add(a["date"])
        for lst in self.blocks_by_sheet.values():
            lst.sort(key=lambda b: b["block_no"])
        for lst in self.presc_by_block.values():
            lst.sort(key=lambda p: p["line_no"])
        for lst in self.reps_by_athlete_block.values():
            lst.sort(key=lambda r: r["rep_no"])
        for k, lst in self.ab_by_athlete.items():
            lst.sort(key=lambda a: (a["date"], session_rank(a["session_no"]), self.block.get(a["block_key"], {}).get("block_no") or 0))
        for lst in self.races_by_athlete.values():
            lst.sort(key=lambda r: (r["date"], r["kind"], r["event"]))

        self.race_coverage = {k: [min(r["date"] for r in self.races if r["kind"] == k),
                                  max(r["date"] for r in self.races if r["kind"] == k)]
                              for k in sorted({r["kind"] for r in self.races})}
        self.seasons = sorted({s["season"] for s in self.sheets})
        self.varsity = defaultdict(set)
        self.raced = defaultdict(set)
        for r in self.races:
            s = season_of(r["date"])
            self.raced[s].add(akey(r))
            if is_xc_varsity(r):
                self.varsity[s].add(akey(r))

        # athlete directory
        self.athletes = {}
        for a in self.ab:
            k = akey(a)
            e = self.athletes.setdefault(k, {"key": k, "name": a["athlete_name"], "athlete_id": a["athlete_id"] or "",
                                             "seasons": set(), "sessions": 0, "races": 0, "matched": bool(a["athlete_id"])})
            e["seasons"].add(a["season"])
            e["sessions"] += 1
        for r in self.races:
            k = akey(r)
            e = self.athletes.setdefault(k, {"key": k, "name": r["athlete_name"], "athlete_id": r["athlete_id"],
                                             "seasons": set(), "sessions": 0, "races": 0, "matched": True})
            e["seasons"].add(season_of(r["date"]))
            e["races"] += 1
            e["name"] = r["athlete_name"]  # Athletic.net spelling wins
            e["gender"] = r.get("gender", "")
        for aid, dates in self.act_dates.items():  # a season with Strava activities is one of the athlete's seasons
            if aid in self.athletes:
                self.athletes[aid]["seasons"] |= {season_of(d) for d in dates}
        # current = on a workout sheet in the newest workbook's season, or raced for MV in that season (Jul-Jun)
        self.current_season = self.seasons[-1] if self.seasons else ""
        self.current = ({akey(a) for a in self.ab if a["season"] == self.current_season}
                        | {akey(r) for r in self.races if season_of(r["date"]) == self.current_season})
        for k, e in self.athletes.items():
            e["current"] = k in self.current
            e["strava"] = bool(e["athlete_id"] and self.weeks_by_athlete.get(e["athlete_id"]))

    # ---- helpers -------------------------------------------------------------------------------
    def filter_set(self, season, f):
        """Athlete keys shown under the varsity filter, or None for everyone (also when a season has no XC data)."""
        if f == "all" or not self.varsity.get(season):
            return None
        return self.varsity[season]

    def label_chips(self, block_key):
        out = []
        for p in self.presc_by_block.get(block_key, []):
            c = chip(p["workout_type"], p["band"], p["band_detail"], "sheet",
                     line=p["line_no"], override=p["type_rule"] == "override",
                     proposed=p["type_status"] == "proposed")
            if c and c["label"] == "Unmapped":
                c["label"] = "Unmapped: " + (p["label_raw"].split(":")[0][:30])
            out.append(c)
        return out

    def block_chips(self, block_key, F):
        """Label-line chips (Patrick's) plus inferred / override chips from the shown athletes' reps."""
        chips = self.label_chips(block_key)
        for r in self.reps_by_block.get(block_key, []):
            if r["type_source"] in ("inferred", "override") and (F is None or akey(r) in F):
                chips.append(chip(r["workout_type"], r["band"], r["band_detail"], r["type_source"]))
        return merge_chips(chips)

    def shown_athletes(self, block_key, F):
        rows = self.ab_by_block.get(block_key, [])
        return rows if F is None else [a for a in rows if akey(a) in F]

    def race_rows(self, rows, F):
        """Races shown, varsity-level races first, then by race and place."""
        def order(r):
            place = re.match(r"\d+", str(r["place"] or ""))
            return (not is_xc_varsity(r), r["gender"], r["race_name"], int(place.group()) if place else 999)
        by_athlete = defaultdict(list)
        for r in rows:
            by_athlete[akey(r)].append(r)
        rows = [r for lst in by_athlete.values() for r in distinct_races(lst)]
        return [{"athlete": r["athlete_name"], "key": akey(r), "kind": r["kind"], "meet": r["meet_name"],
                 "event": r["event"], "race": r["race_name"], "mark": r["mark"], "place": r["place"],
                 "url": r["source_url"], "date": r["date"]}
                for r in sorted(rows, key=order) if F is None or akey(r) in F]

    @staticmethod
    def session_order(s):
        """Session 1, Session 2, then sheets without a session number (e.g. a second group's 'b' tab)."""
        return (s["session_no"] if isinstance(s["session_no"], int) else 99, s["tab"])

    # ---- views ---------------------------------------------------------------------------------
    def meta(self):
        latest = self.seasons[-1] if self.seasons else ""
        combos = Counter()
        for p in self.presc:
            if p["workout_type"] and p["workout_type"] != "UNMAPPED":
                combos[(p["workout_type"], p["band"] or "", p["band_detail"] or "")] += 1
        for r in self.reps:
            if r["type_source"] == "inferred" and r["workout_type"]:
                combos[(r["workout_type"], r["band"] or "", r["band_detail"] or "")] += 0
        return {
            "seasons": [{"season": s, "label": f"20{s[:2]}-{s[2:]}", "varsity": len(self.varsity.get(s, ())),
                         "has_races": bool(self.raced.get(s))} for s in self.seasons],
            "default_season": latest,
            "default_month": f"20{latest[:2]}-08" if latest else "",
            "categories": [{"type": k, "name": v} for k, v in CATEGORIES.items()],
            "varsity_rule": VARSITY_RULE,
            "current_season": self.current_season,
        }

    def calendar(self, season, f):
        F = self.filter_set(season, f)
        dates = {s["date"] for s in self.sheets if s["season"] == season and s["date"]}
        dates |= {r["date"] for r in self.races if season_of(r["date"]) == season}
        days = {}

        def part(sheet_list, no, F):
            chips, shown, hidden, weights = [], 0, 0, Counter()
            for s in sheet_list:
                for b in self.blocks_by_sheet.get(s["sheet_key"], []):
                    if not self.shown_athletes(b["block_key"], F):
                        hidden += 1
                        continue
                    shown += 1
                    chips += self.block_chips(b["block_key"], F)
                    weights.update(self.group_weights(b["block_key"], F))
            return {"session_no": no, "venues": venues_of(sheet_list), "chips": merge_chips(chips), "groups": shown,
                    "hidden_groups": hidden, "main": self.main_type(weights)}, weights
        for d in sorted(dates):
            sheets = sorted((s for s in self.sheets_by_date.get(d, []) if s["season"] == season), key=self.session_order)
            nos = {s["session_no"] for s in sheets}
            split = {1, 2} <= nos  # Session 1 and Session 2: the box is split, AM on top
            parts = [part([s for s in sheets if s["session_no"] != 2], 1, F), part([s for s in sheets if s["session_no"] == 2], 2, F)] \
                if split else [part(sheets, None, F)]
            weights = sum((w for _, w in parts), Counter())
            races = self.race_rows(self.races_by_date.get(d, []), F)
            meets = Counter((r["meet"], r["kind"]) for r in races)
            row = self.day_row.get(d, {})
            days[d] = {
                "chips": merge_chips([c for p, _ in parts for c in p["chips"]]), "groups": sum(p["groups"] for p, _ in parts),
                "hidden_groups": sum(p["hidden_groups"] for p, _ in parts),
                "practice": bool(sheets), "double": bool(row.get("double_threshold")),
                "church": bool(row.get("church_loop")), "venues": venues_of(sheets), "main": self.main_type(weights),
                "split": split, "parts": [p for p, _ in parts] if split else [],
                "races": [{"meet": m, "kind": k, "n": n} for (m, k), n in meets.items()],
            }
        start = 2000 + int(season[:2])
        return {"season": season, "filter": "varsity" if F is not None else "all",
                "varsity_available": bool(self.varsity.get(season)), "varsity_count": len(self.varsity.get(season, ())),
                "first_month": f"{start}-07", "last_month": f"{start + 1}-06", "days": days}

    def day(self, date, f, hl=None):
        """hl: an athlete key to highlight (opened from that athlete's calendar). Their groups and races are
        shown even when the varsity filter would hide them, and prev / next step through their days."""
        sheets = self.sheets_by_date.get(date, [])
        season = sheets[0]["season"] if sheets else season_of(date)
        F = self.filter_set(season, f)
        hl = hl if hl in self.athletes else None
        if hl and F is not None:
            F = F | {hl}
        sessions = []
        for s in sorted(sheets, key=self.session_order):
            blocks, hidden = [], 0
            for b in self.blocks_by_sheet.get(s["sheet_key"], []):
                shown = self.shown_athletes(b["block_key"], F)
                if F is not None and not shown:
                    hidden += 1
                    continue
                blocks.append(self._block_detail(b, shown, F))
            sessions.append({
                "tab": s["tab"], "session_no": s["session_no"], "session_note": s["session_note"],
                "title": s["written_title"], "date_conflict": s["date_conflict"], "venue": s["venue"], "where": venue_of(s),
                "start_time": s["start_time"], "weather": s["weather"], "church": bool(s["church_loop"]),
                "blocks": blocks, "hidden_groups": hidden,
            })
        row = self.day_row.get(date, {})
        all_dates = self.athlete_dates(hl) if hl else sorted(set(self.sheets_by_date) | set(self.races_by_date))
        i = all_dates.index(date) if date in all_dates else -1
        if i < 0:  # a day not in the list: step from where it would sit
            j = bisect.bisect_left(all_dates, date)
            prev, nxt = (all_dates[j - 1] if j > 0 else None), (all_dates[j] if j < len(all_dates) else None)
        else:
            prev, nxt = (all_dates[i - 1] if i > 0 else None), (all_dates[i + 1] if i < len(all_dates) - 1 else None)
        return {
            "date": date, "season": season, "filter": "varsity" if F is not None else "all",
            "varsity_available": bool(self.varsity.get(season)),
            "double": bool(row.get("double_threshold")), "church": bool(row.get("church_loop")),
            "sessions": sessions, "races": self.race_rows(self.races_by_date.get(date, []), F),
            "prev": prev, "next": nxt,
            "highlight": {"key": hl, "name": self.athletes[hl]["name"], "varsity": hl in self.varsity.get(season, ()),
                          "activities": self.day_activities(hl, date)} if hl else None,
        }

    def _block_detail(self, b, shown, F):
        bk = b["block_key"]
        dists, targets, rests = jl(b["rep_distances"]), jl(b["target_secs"]), jl(b["rest"])
        reps = self.reps_by_block.get(bk, [])
        by_col = {}
        for r in reps:  # one representative rep per column (non-variant first) for the column's line/type
            if r["rep_no"] not in by_col or (by_col[r["rep_no"]]["variant_row"] and not r["variant_row"]):
                by_col[r["rep_no"]] = r
        n_cols = max([len(dists)] + [r["rep_no"] for r in reps]) if (dists or reps) else 0
        columns = []
        for i in range(1, n_cols + 1):
            r = by_col.get(i)
            columns.append({
                "rep_no": i,
                "distance": dists[i - 1] if i <= len(dists) else (r["distance_m"] if r else None),
                "target": targets[i - 1] if i <= len(targets) else (r["target_s"] if r else None),
                "rest": rests[i - 1] if i <= len(rests) else "",
                "line": r["label_line"] if r else "", "how": r["label_match"] if r else "",
                "chip": chip(r["workout_type"], r["band"], r["band_detail"], r["type_source"] or "sheet") if r else None,
            })
        athletes = []
        for a in shown:
            k = akey(a)
            rr = self.reps_by_athlete_block.get((k, bk), [])
            athletes.append({
                "key": k, "name": a["athlete_name"], "workbook_name": a["workbook_name"], "match": a["match"],
                "varsity": k in self.varsity.get(b["season"], ()), "status": a["athlete_status"],
                "cooldown": a["cooldown_mi"], "total_vol": a["total_vol_mi"], "notes": a["notes"],
                "reps": {r["rep_no"]: {"actual": r["actual_s"], "status": r["status"], "raw": r["raw"],
                                       "shared": bool(r["shared_split"]), "suspect": bool(r["suspect_time"]),
                                       "variant": bool(r["variant_row"]), "distance": r["distance_m"],
                                       "target": r["target_s"]} for r in rr},
            })
        lines = []
        label_chip = {c.get("line"): c for c in self.label_chips(bk)}
        for p in self.presc_by_block.get(bk, []):
            lines.append({"line": p["line_no"], "text": p["label_raw"], "chip": label_chip.get(p["line_no"]),
                          "reps": p["n_reps_assigned"]})
        return {
            "block_no": b["block_no"], "labels": lines, "notes": b["notes"], "type_source": b["type_source"],
            "chips": self.block_chips(bk, F), "columns": columns, "athletes": athletes,
            "total_vol_m": b["total_vol_m"], "n_athletes": b["n_athletes"],
        }

    def athlete_list(self):
        return sorted((self.head(k) for k in self.athletes), key=lambda e: e["name"].lower())

    def head(self, key):
        """An athlete's directory entry: name, Athletic.net ID, seasons (workbooks, races or Strava), varsity seasons."""
        e = self.athletes[key]
        return {**e, "seasons": sorted(e["seasons"]),
                "varsity_seasons": sorted(s for s in e["seasons"] if key in self.varsity.get(s, ()))}

    def athlete_block_chips(self, key, block_key):
        """The types of this athlete's own reps in a group (inferred stays inferred); the group's label
        chips when they have no reps there."""
        rr = self.reps_by_athlete_block.get((key, block_key), [])
        if rr:
            return merge_chips([chip(r["workout_type"], r["band"], r["band_detail"], r["type_source"] or "sheet") for r in rr])
        return merge_chips(self.label_chips(block_key))

    def athlete_block_weights(self, key, a):
        """{(type, own): metres} of this athlete's reps in one group; own = Patrick's (label line or rule file), not
        inferred. A rep with no distance counts 1. A group without rep columns counts its label types by the
        athlete's total volume that day (or 1)."""
        out = Counter()
        rr = self.reps_by_athlete_block.get((key, a["block_key"]), [])
        for r in rr:
            if r["workout_type"] in CATEGORIES:
                out[(r["workout_type"], r["type_source"] != "inferred")] += r["distance_m"] or 1
        if not rr:
            for c in self.label_chips(a["block_key"]):
                if c["type"] in CATEGORIES:
                    out[(c["type"], True)] += (a["total_vol_mi"] or 0) * 1609.34 or 1
        return out

    def group_weights(self, block_key, F):
        """{(type, own): metres} of a group's reps for the shown athletes (F None = everyone) - for the team calendar's
        tint; a group without rep columns counts its label types by each shown athlete's total volume."""
        out = Counter()
        for a in self.shown_athletes(block_key, F):
            out.update(self.athlete_block_weights(akey(a), a))
        return out

    @staticmethod
    def main_type(weights):
        """The day's main workout category: the workout category (not easy aerobic, unless that's all there is)
        with the most rep distance; src 'sheet' when Patrick's labels (or rule file) give that category any of it,
        'inferred' when all of it was inferred from pace - so the color is only striped when it's a guess."""
        by = Counter()
        for (t, _), w in weights.items():
            by[t] += w
        work = [t for t in by if t != "easy_aerobic"] or list(by)
        if not work:
            return None
        order = list(CATEGORIES)
        t = max(work, key=lambda t: (by[t], -order.index(t)))
        return {"type": t, "src": "sheet" if weights.get((t, True), 0) > 0 else "inferred",
                "name": CATEGORIES[t]}

    def athlete_dates(self, key):
        aid = self.athletes.get(key, {}).get("athlete_id")
        return sorted({a["date"] for a in self.ab_by_athlete.get(key, [])}
                      | {r["date"] for r in self.races_by_athlete.get(key, [])} | self.act_dates.get(aid, set()))

    def day_activities(self, key, date):
        """An athlete's Strava activities on one day (an activity left out by data/rules/strava_fixes.csv too, marked)."""
        aid = self.athletes.get(key, {}).get("athlete_id")
        acts = sorted(self.acts_by_athlete_date.get((aid, date), []), key=lambda a: a["activity_id"]) if aid else []
        return [{"sport": a["sport"], "counts_as": a["counts_as"], "miles": a["miles"], "pace": a["pace"],
                 "moving": a["moving_time"], "moving_s": a["moving_s"], "longest": bool(a["longest_of_week"]),
                 "long_run": bool(a.get("long_run")), "check": a["check"], "fix": a["fix"], "url": a["url"]} for a in acts]

    def long_run_rule(self):
        """rules/mileage.csv long_run_min_miles as built: a number of miles, or None when it isn't set."""
        try:
            return float(self.settings.get("long_run_min_miles") or "")
        except ValueError:
            return None

    def athlete_calendar(self, key, season=None):
        """Days this athlete trained (on a workout sheet or a Strava activity) or raced in one season, for the
        month grid. Each day's runs: distance, pace, and whether it was the week's longest."""
        if key not in self.athletes:
            return None
        head = self.head(key)
        seasons = head["seasons"]
        if season not in seasons:
            season = seasons[-1] if seasons else ""
        days = {}

        def day(d):
            return days.setdefault(d, {"chips": [], "groups": [], "church": False, "races": [], "runs": [], "cross": [],
                                       "weights": Counter(), "by_session": defaultdict(lambda: {"chips": [], "weights": Counter(),
                                                                                             "sheets": []})})
        for d in sorted(self.act_dates.get(head["athlete_id"], ())) if head["athlete_id"] else []:
            if season_of(d) != season:
                continue
            x = day(d)
            for a in self.day_activities(key, d):
                if a["counts_as"] == "run":
                    x["runs"].append({k: a[k] for k in ("miles", "pace", "moving", "moving_s", "longest", "long_run", "check")})
                elif a["counts_as"] == "cross":
                    x["cross"].append({"sport": a["sport"], "min": round((a["moving_s"] or 0) / 60)})
        for a in self.ab_by_athlete.get(key, []):
            if a["season"] != season:
                continue
            s, b = self.sheet.get(a["sheet_key"], {}), self.block.get(a["block_key"], {})
            x = day(a["date"])
            ch, w = self.athlete_block_chips(key, a["block_key"]), self.athlete_block_weights(key, a)
            x["chips"] += ch
            x["weights"].update(w)
            bs = x["by_session"][2 if a["session_no"] == 2 else 1]
            bs["chips"] += ch
            bs["weights"].update(w)
            if s and s not in bs["sheets"]:
                bs["sheets"].append(s)
            x["groups"].append({"session_no": a["session_no"], "block_no": b.get("block_no"), "tab": s.get("tab", ""),
                                "venue": s.get("venue", "")})
            x["church"] = x["church"] or bool(s.get("church_loop"))
        for r in distinct_races(self.races_by_athlete.get(key, [])):
            if season_of(r["date"]) != season:
                continue
            day(r["date"])["races"].append({"meet": r["meet_name"], "event": r["event"], "race": r["race_name"],
                                            "mark": r["mark"], "place": r["place"], "kind": r["kind"],
                                            "also": r.get("also", ""), "varsity_race": is_xc_varsity(r)})
        out = {}
        for d, x in sorted(days.items()):
            nos = {g["session_no"] for g in x["groups"] if isinstance(g["session_no"], int)}
            split = {1, 2} <= nos  # they did Session 1 and Session 2: the box is split, AM on top
            parts = [{"session_no": n, "venues": venues_of(x["by_session"][n]["sheets"]), "chips": merge_chips(x["by_session"][n]["chips"]),
                      "main": self.main_type(x["by_session"][n]["weights"])} for n in (1, 2)] if split else []
            out[d] = {"chips": merge_chips(x["chips"]), "groups": x["groups"], "practice": bool(x["groups"]),
                      "church": x["church"], "races": x["races"], "runs": x["runs"], "cross": x["cross"],
                      "double": bool(self.day_row.get(d, {}).get("double_threshold")) and split,
                      "main": self.main_type(x["weights"]), "split": split, "parts": parts,
                      "venues": venues_of([sh for n in sorted(x["by_session"]) for sh in x["by_session"][n]["sheets"]])}
        # each Monday-Sunday week's run miles (athlete_weeks) for the total at the end of the calendar's week rows
        weeks = {}
        for w in self.weeks_by_athlete.get(head["athlete_id"], []) if head["athlete_id"] else []:
            if season in (season_of(w["week_start"]), season_of(w["week_end"])):
                weeks[w["week_start"]] = {"miles": w["run_miles"], "partial": bool(w["partial_week"]), "note": w["week_note"] or "",
                                          "source": w["source"], "n_runs": w["n_runs"], "no_dist": w["runs_no_distance"],
                                          "long_run_mi": w["longest_run_mi"] if w.get("long_run") else None}
        return {"athlete": head, "season": season, "days": out, "weeks": weeks, "long_run_min_miles": self.long_run_rule()}

    def athlete(self, key, season=None, cat=None):
        e = self.athletes.get(key)
        if not e:
            return None
        items = []
        for a in self.ab_by_athlete.get(key, []):
            if season and a["season"] != season:
                continue
            b, s = self.block.get(a["block_key"]), self.sheet.get(a["sheet_key"])
            rr = self.reps_by_athlete_block.get((key, a["block_key"]), [])
            chips = self.athlete_block_chips(key, a["block_key"])
            if cat and not any(_combo_match(c, cat) for c in chips):
                continue
            items.append({
                "kind": "session", "date": a["date"], "season": a["season"], "tab": s["tab"] if s else "",
                "session_no": a["session_no"], "block_no": b["block_no"] if b else "",
                "labels": [p["label_raw"] for p in self.presc_by_block.get(a["block_key"], [])],
                "chips": chips, "cooldown": a["cooldown_mi"], "total_vol": a["total_vol_mi"], "notes": a["notes"],
                "venue": s["venue"] if s else "",
                "reps": [{"rep_no": r["rep_no"], "distance": r["distance_m"], "target": r["target_s"],
                          "actual": r["actual_s"], "status": r["status"], "raw": r["raw"],
                          "shared": bool(r["shared_split"]), "suspect": bool(r["suspect_time"]),
                          "line": r["label_line"], "src": r["type_source"],
                          "chip": chip(r["workout_type"], r["band"], r["band_detail"], r["type_source"] or "sheet")}
                         for r in rr],
            })
        for r in distinct_races(self.races_by_athlete.get(key, [])):
            if season and season_of(r["date"]) != season:
                continue
            items.append({"kind": "race", "date": r["date"], "season": season_of(r["date"]), "meet": r["meet_name"],
                          "event": r["event"], "race": r["race_name"], "mark": r["mark"], "place": r["place"],
                          "race_kind": r["kind"], "url": r["source_url"],
                          "varsity_race": is_xc_varsity(r)})
        items.sort(key=lambda x: (x["date"], 0 if x["kind"] == "race" else 1, str(x.get("session_no", ""))), reverse=True)
        return {**self.head(key), "items": items}

    def athlete_mileage(self, key):
        """Every week of Strava run miles for the athlete, all seasons (joined on the Athletic.net ID): from their
        activities, or the weekly export where there are none (rules/mileage.csv picks when both exist), with
        cross-training minutes beside it. Weeks with no row stay missing - nothing is filled in from the workbooks."""
        if key not in self.athletes:
            return None
        head = self.head(key)
        rows = self.weeks_by_athlete.get(head["athlete_id"], []) if head["athlete_id"] else []
        weeks = [{"week_start": w["week_start"], "week_end": w["week_end"], "miles": w["run_miles"], "source": w["source"],
                  "partial": bool(w["partial_week"]), "note": w["week_note"] or "", "activity_miles": w["activity_run_miles"],
                  "export_miles": w["export_miles"], "export_file": w["export_file"], "compare_miles": w["compare_miles"],
                  "export_diff": w["export_minus_activities"], "n_runs": w["n_runs"], "no_dist": w["runs_no_distance"],
                  "no_dist_min": w["run_min_no_distance"], "cross_min": w["cross_min"], "n_cross": w["n_cross"],
                  "cross_sports": w["cross_sports"] or "", "longest_mi": w["longest_run_mi"], "longest_date": w["longest_run_date"],
                  "long_run": bool(w.get("long_run"))}
                 for w in rows]
        span = (min(w["week_start"] for w in self.weeks), max(w["week_end"] for w in self.weeks)) if self.weeks else None
        aid = head["athlete_id"]
        acts = [a for d in sorted(self.act_dates.get(aid, ())) for a in self.acts_by_athlete_date[(aid, d)]
                if a["counts_as"] in ("run", "cross")] if aid else []
        both = [w for w in self.weeks if w["activity_run_miles"] is not None and w["export_miles"] is not None]
        months = [{"month": m["month"], "month_start": m["month_start"], "month_end": m["month_end"], "miles": m["run_miles"],
                   "n_runs": m["n_runs"], "no_dist": m["runs_no_distance"], "cross_min": m["cross_min"],
                   "days": m["days_with_data"], "days_in_month": m["days_in_month"], "export_days": m["export_days"],
                   "partial": bool(m["partial"]), "note": m["month_note"] or ""}
                  for m in (self.months_by_athlete.get(aid, []) if aid else [])]
        return {"athlete": head, "weeks": weeks, "months": months, "all_weeks_span": span, "n_athletes_with_mileage": len(self.weeks_by_athlete),
                "activities": {"first": acts[0]["date"], "last": acts[-1]["date"], "n": len(acts)} if acts else None,
                "prefer": "export" if any(w["source"] == "export" for w in both) else "activities",
                "long_run_min_miles": self.long_run_rule()}

    def athlete_races(self, key):
        """Track: every result per event, oldest first, with PRs (faster than everything before it in that
        event; 'best' = the lifetime PR). XC: courses (same meet AND distance, see mvtrain/races.py - or one
        course across distances when rules/xc_courses.csv says so) run more than once (two or more results with
        a time; a DNS / DNF doesn't count), and every XC race. Times only - nothing is converted between events
        or courses."""
        if key not in self.athletes:
            return None
        rows = self.races_by_athlete.get(key, [])
        tf, xc = [r for r in rows if r["kind"] == "TF"], [r for r in rows if r["kind"] == "XC"]

        def base(r):
            return {"date": r["date"], "season": season_of(r["date"]), "meet": r["meet_name"], "event": r["event"],
                    "race": r["race_name"], "mark": r["mark"], "seconds": r["seconds"], "place": r["place"],
                    "grade": r["grade"], "url": r["source_url"]}
        events = defaultdict(list)
        for r in tf:
            events[(round(r["distance_m"] or 0), r["event"])].append(r)
        track = []
        for (dist, ev), lst in sorted(events.items()):
            lst.sort(key=lambda r: (r["date"], round_order(r["race_name"])))
            best, out = None, []
            for r in lst:
                pr = bool(r["seconds"]) and (best is None or r["seconds"] < best)
                best = r["seconds"] if pr else best
                out.append({**base(r), "pr": pr, "also": "XC" if any(same_result(r, o) for o in xc) else ""})
            timed = [x for x in out if x["seconds"]]
            pb = min(timed, key=lambda x: x["seconds"]) if timed else None
            for x in out:
                x["best"] = x is pb
            track.append({"event": ev, "distance_m": dist, "results": out, "best": pb})
        courses = defaultdict(list)
        for r in xc:
            if r.get("xc_course"):
                courses[r["xc_course"]].append(r)
        repeated = []
        for k, lst in courses.items():
            if sum(1 for r in lst if r["seconds"]) < 2:  # run more than once = two results with a time
                continue
            lst.sort(key=lambda r: r["date"])
            prev, out = None, []
            for r in lst:
                delta = round(r["seconds"] - prev["seconds"], 2) if prev and r["seconds"] else None
                out.append({**base(r), "delta": delta, "varsity_race": is_xc_varsity(r),
                            "prev_event": prev["event"] if delta is not None and prev["event"] != r["event"] else ""})
                prev = r if r["seconds"] else prev
            # a course from rules/xc_courses.csv can have a different distance each year: name them all
            events = sorted({r["event"] for r in lst}, key=lambda e: min(r["distance_m"] or 0 for r in lst if r["event"] == e))
            repeated.append({"key": k, "meet": lst[-1].get("xc_course_name") or lst[-1]["xc_meet"], "event": lst[-1]["event"],
                             "events": events, "named": bool(lst[-1].get("xc_course_name")), "results": out})
        repeated.sort(key=lambda c: c["results"][-1]["date"], reverse=True)
        keys = {c["key"] for c in repeated}
        xc_list = sorted(({**base(r), "xc_meet": r.get("xc_meet", ""), "course": r.get("xc_course", "") if r.get("xc_course") in keys else "",
                           "varsity_race": is_xc_varsity(r), "also": "TF" if any(same_result(r, o) for o in tf) else ""}
                          for r in xc), key=lambda x: x["date"], reverse=True)
        return {"athlete": self.head(key), "track": track, "courses": repeated, "xc": xc_list,
                "coverage": self.race_coverage}

    def workout_combos(self):
        """Picker entries with the number of distinct groups (blocks), all athletes:
        each category; for threshold also each band (any session) and each exact band/session."""
        groups = defaultdict(set)  # combo -> block keys
        for b in self.blocks:
            for ch in self.block_chips(b["block_key"], None):
                if ch["type"] == "other":
                    continue
                groups[ch["type"]].add(b["block_key"])
                if ch["type"] == "threshold":
                    groups[f"threshold|{ch['band']}"].add(b["block_key"])
                    groups[f"threshold|{ch['band']}|{ch['detail']}"].add(b["block_key"])
        out = []
        for t, name in CATEGORIES.items():
            if t not in groups:
                continue
            out.append({"combo": t, "label": "Threshold (all)" if t == "threshold" else name, "blocks": len(groups[t])})
            if t != "threshold":
                continue
            bands = sorted({k.split("|")[1] for k in groups if k.count("|") == 1})
            for band in bands:
                exact = sorted(k for k in groups if k.count("|") == 2 and k.split("|")[1] == band)
                if len(exact) > 1:
                    out.append({"combo": f"threshold|{band}", "label": chip("threshold", band)["label"] + " (any session)",
                                "blocks": len(groups[f"threshold|{band}"])})
                for k in exact:
                    _, b_, d_ = k.split("|")
                    lbl = chip("threshold", b_, d_)["label"] + (" (no AM/PM)" if len(exact) > 1 and not d_ else "")
                    out.append({"combo": k, "label": lbl, "blocks": len(groups[k])})
        return out

    def workouts(self, combo, f):
        rows = []
        for b in self.blocks:
            F = self.filter_set(b["season"], f)
            shown = self.shown_athletes(b["block_key"], F)
            if not shown:
                continue
            chips = [c for c in self.block_chips(b["block_key"], F) if _combo_match(c, combo)]
            if not chips:
                continue
            s = self.sheet.get(b["sheet_key"], {})
            keys = {akey(a) for a in shown}
            reps = [r for r in self.reps_by_block.get(b["block_key"], [])
                    if akey(r) in keys and _combo_match(chip(r["workout_type"], r["band"], r["band_detail"]) or {}, combo)]
            rows.append({
                "date": b["date"], "season": b["season"], "tab": s.get("tab", ""), "session_no": b["session_no"],
                "block_no": b["block_no"], "venue": s.get("venue", ""),
                "labels": [p["label_raw"] for p in self.presc_by_block.get(b["block_key"], [])
                           if _combo_match(chip(p["workout_type"], p["band"], p["band_detail"]) or {}, combo)],
                "chips": chips, "src": "sheet" if any(c["src"] != "inferred" for c in chips) else "inferred",
                "group": [{"name": n, "key": k} for k, n in sorted({(akey(a), a["athlete_name"]) for a in shown},
                                                                    key=lambda x: x[1].lower())],
                "targets": _key_targets(reps),
            })
        rows.sort(key=lambda r: (r["date"], str(r["session_no"]), r["block_no"]), reverse=True)
        return rows


def _combo_match(c, combo):
    """combo = 'type', 'type|band' or 'type|band|detail'; every part given must match exactly
    ('threshold|DT|' is DT with no AM/PM detail; 'threshold|DT' is DT in any session)."""
    if not c or not combo:
        return bool(c)
    parts = combo.split("|")
    if c.get("type") != parts[0]:
        return False
    if len(parts) > 1 and c.get("band", "") != parts[1]:
        return False
    if len(parts) > 2 and c.get("detail", "") != parts[2]:
        return False
    return True


def _key_targets(reps):
    """'6 x 1725 m @ 5:40-5:50' style summary of the matching rep columns (targets as written)."""
    cols = {}
    for r in reps:
        cols.setdefault(r["rep_no"], (r["distance_m"], r["target_s"]))
    by_d = defaultdict(list)
    for _, (d, t) in sorted(cols.items()):
        by_d[d].append(t)
    out = []
    for d, ts in by_d.items():
        ts = [t for t in ts if t]
        out.append({"n": len(by_d[d]), "distance": d, "min": min(ts) if ts else None, "max": max(ts) if ts else None})
    return out


class Store:
    """Loads the database once and again whenever a rebuild replaces it."""

    def __init__(self, path):
        self.path, self.data, self.mtime, self.error = path, None, None, ""
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            try:
                m = self.path.stat().st_mtime
                if self.data is None or m != self.mtime:
                    self.data, self.mtime, self.error = Data(self.path), m, ""
            except (OSError, sqlite3.Error) as exc:
                if self.data is None:
                    self.error = f"Can't read {self.path}: {exc}. Run: python -m mvtrain.build"
            return self.data


def create_app(db_path=DB):
    app = Flask(__name__, static_folder=None)
    store = Store(Path(db_path))

    def data():
        d = store.get()
        if d is None:
            abort(503, store.error)
        return d

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:name>")
    def static_file(name):
        return send_from_directory(STATIC, name)

    @app.get("/api/meta")
    def api_meta():
        return jsonify(data().meta())

    @app.get("/api/calendar/<season>")
    def api_calendar(season):
        d = data()
        if season not in d.seasons:
            abort(404)
        return jsonify(d.calendar(season, request.args.get("f", "varsity")))

    @app.get("/api/day/<date>")
    def api_day(date):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            abort(404)
        return jsonify(data().day(date, request.args.get("f", "varsity"), request.args.get("a") or None))

    @app.get("/api/athletes")
    def api_athletes():
        return jsonify(data().athlete_list())

    @app.get("/api/athlete")
    def api_athlete():
        res = data().athlete(request.args.get("key", ""), request.args.get("season") or None,
                             request.args.get("cat") or None)
        if res is None:
            abort(404)
        return jsonify(res)

    @app.get("/api/athlete/calendar")
    def api_athlete_calendar():
        res = data().athlete_calendar(request.args.get("key", ""), request.args.get("season") or None)
        if res is None:
            abort(404)
        return jsonify(res)

    @app.get("/api/athlete/mileage")
    def api_athlete_mileage():
        res = data().athlete_mileage(request.args.get("key", ""))
        if res is None:
            abort(404)
        return jsonify(res)

    @app.get("/api/athlete/races")
    def api_athlete_races():
        res = data().athlete_races(request.args.get("key", ""))
        if res is None:
            abort(404)
        return jsonify(res)

    @app.get("/api/workout-types")
    def api_workout_types():
        return jsonify(data().workout_combos())

    @app.get("/api/workouts")
    def api_workouts():
        return jsonify(data().workouts(request.args.get("combo", ""), request.args.get("f", "varsity")))

    @app.after_request
    def no_cache(resp):
        resp.headers["Cache-Control"] = "no-store"
        return resp

    return app
