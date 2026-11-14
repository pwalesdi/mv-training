"""Build the training database from the workbooks and race files.

    python -m mvtrain.build                 # every workbook in data/workbooks
    python -m mvtrain.build --seasons 2526  # just 2025-26

Outputs (in data/build/, never committed):
    training.sqlite  - every table below
    sheets.csv, blocks.csv, prescriptions.csv, athlete_blocks.csv, reps.csv,
    days.csv, races.csv, weekly_mileage.csv, activities.csv, athlete_weeks.csv, review.csv
The workbooks themselves are only read, never modified.
"""
import argparse
import csv
import json
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

from . import classify, dates, mileage, races, strava, venues
from .cells import CHURCH_LAP_M, display
from .workbook import parse_sheet

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RULES = ROOT / "rules"
PRIVATE_RULES = DATA / "rules"  # rules that name athletes stay out of git


def load_aliases():
    with open(PRIVATE_RULES / "aliases.csv") as fh:
        return {races.norm(r["workbook_name"]): r for r in csv.DictReader(fh)}


LISTS = ("current", "historical")  # data/rules/athletes.csv statuses that pick the app's Athletes list


class AthleteRules:
    """data/rules/athletes.csv: workbook_name, athlete_id, season, status, note.

    A row with an athlete_id is for that Athletic.net athlete: every workbook spelling that resolves to the ID,
    their race results and their Strava weeks (workbook_name is then only a label). A row without one is for a
    workbook name, as written or as shown after aliases.csv (athletes with no Athletic.net match). A blank
    season means every season. status=exclude leaves the athlete out; status=current / historical puts them on
    that Athletes list in the app, whatever their sheets and races say (with a season: only while it is the
    newest season built - see listing); any other status (e.g. alum) is a note on their athlete-block rows, a
    season's own row before an every-season row.
    """

    def __init__(self, path):
        self.by_id, self.by_name, self.lines, self.used = defaultdict(list), defaultdict(list), [], set()
        self.known, self.left_out = set(), defaultdict(set)  # names seen on sheets; left out, by season ('' = all)
        self.ids_left_out = defaultdict(set)  # Athletic.net IDs of athletes left out on the sheets, by season ('' = all)
        self.listed_as = defaultdict(dict)  # current / historical row line -> {app key: (athlete_id, name shown)}
        with open(path) as fh:
            for line, r in enumerate(csv.DictReader(fh), start=2):
                r = {k: (v or "").strip() for k, v in r.items() if k}
                r["line"] = line
                self.lines.append(r)
                if r.get("athlete_id"):
                    self.by_id[r["athlete_id"]].append(r)
                elif r.get("workbook_name"):
                    first, last = _first_last(r["workbook_name"])  # 'Doe, Jane' (as some sheets write it) = 'Jane Doe'
                    for k in {races.norm(r["workbook_name"]), races.norm(f"{first} {last}")}:
                        self.by_name[k].append(r)

    def _matching(self, athlete_id, names):
        rows = list(self.by_id.get(athlete_id, ())) if athlete_id else []
        for n in dict.fromkeys(races.norm(n) for n in names):
            rows += self.by_name.get(n, ())
        return list({r["line"]: r for r in rows}.values())  # once each ('Doe, Jane' is filed under both orders)

    def _rows(self, season, athlete_id, names):
        """This athlete's rows for the season (not the current / historical ones), a season's own row first."""
        rows = [r for r in self._matching(athlete_id, names)
                if r.get("status") not in LISTS and r.get("season", "") in (season, "")]
        return sorted(rows, key=lambda r: r.get("season", "") == "")

    def listed(self, athlete_id, shown, names=()):
        """Note an athlete who is in the build (a workbook row or a race) and named by a current / historical row,
        under the key the app uses: the Athletic.net ID, or 'u:' + the name shown."""
        for r in self._matching(athlete_id, names):
            if r.get("status") in LISTS:
                self.listed_as[r["line"]][athlete_id or f"u:{shown}"] = (athlete_id, shown)

    def listing(self, newest, seasons):
        """The status=current / historical rows that apply -> (athlete_listing rows, review items; no names).

        A row applies while its season (blank = always) is the newest season built - the season the app's
        Current list is for. For one athlete a season's own row wins over an every-season row; two rows at the
        same level that disagree are both left unused (review: rule_conflict)."""
        out, review, by_key = [], [], defaultdict(list)

        def item(issue, r, detail):
            who = f"athlete_id {r['athlete_id']}" if r.get("athlete_id") else "workbook name"
            review.append({"issue": issue, "season": r.get("season", ""), "tab": "", "block": "",
                           "detail": f"data/rules/athletes.csv line {r['line']} ({who}, status={r['status']}) {detail}"})

        for r in self.lines:
            if r.get("status") not in LISTS:
                continue
            if r.get("season") and r["season"] != newest:
                item("rule_unused", r, f"is for {r['season']}, but the newest season built is {newest or 'none'} - not used")
            elif not self.listed_as.get(r["line"]):
                if seasons is None or r.get("season") in seasons:
                    item("rule_unused", r, "matched no workbook row or race")
            else:
                for key, who in self.listed_as[r["line"]].items():
                    by_key[key].append((r, who))
        for key, hits in by_key.items():
            level = min(not r.get("season") for r, _ in hits)  # False (0) when a season's own row names them
            top = [(r, who) for r, who in hits if (not r.get("season")) == level]
            if len({r["status"] for r, _ in top}) > 1:
                for r, _ in top:
                    item("rule_conflict", r, f"disagrees with line(s) {', '.join(str(o['line']) for o, _ in top if o is not r)} "
                                             f"for the same athlete - neither is used")
                continue
            r, (aid, shown) = top[0]
            out.append({"athlete_id": aid, "athlete_name": shown, "status": r["status"],
                        "season": r.get("season", ""), "rule_line": r["line"]})
        return out, review

    def status(self, season, athlete_id="", names=()):
        """'exclude' if any row for this athlete and season excludes them, else the status of their row ('')."""
        rows = self._rows(season, athlete_id, names)
        self.used.update(r["line"] for r in rows)
        if any(r.get("status") == "exclude" for r in rows):
            return "exclude"
        return rows[0].get("status", "") if rows else ""

    def excluded(self, season, athlete_id="", names=()):
        """For races and Strava weeks (after every sheet is read): also true for the Athletic.net ID of an athlete a
        workbook-name row leaves out - e.g. once a new race export matches their name to an ID."""
        if athlete_id and (athlete_id in self.ids_left_out[""] or athlete_id in self.ids_left_out[season]):
            return True
        return self.status(season, athlete_id, names) == "exclude"

    def seen(self, season, status, athlete_id, names):
        """Note an athlete's names as written on a sheet, and whether they're left out (for scrub_left_out) - with
        the Athletic.net ID their name matches, so a workbook-name row leaves out their races and Strava weeks too."""
        self.known |= set(names)
        if status == "exclude":
            everywhere = any(r.get("status") == "exclude" and not r.get("season")
                             for r in self._rows(season, athlete_id, names))
            self.left_out["" if everywhere else season] |= set(names)
            if athlete_id:
                self.ids_left_out["" if everywhere else season].add(athlete_id)

    def add_rule_names(self, id_names, aliases):
        """Left-out names from the rows themselves: the Athletic.net name and every alias of a left-out ID."""
        spellings = defaultdict(list)
        for a in aliases.values():
            spellings[a["athlete_id"]].append(a["workbook_name"])
        for r in self.lines:
            if r.get("status") == "exclude":
                aid = r.get("athlete_id")
                names = [r.get("workbook_name", "")] + ([id_names.get(aid, "")] + spellings[aid] if aid else [])
                self.left_out[r.get("season", "")] |= {n for n in names if n}
        for scope, ids in self.ids_left_out.items():  # IDs a workbook-name row reached through the sheets
            for aid in ids:
                self.left_out[scope] |= {n for n in [id_names.get(aid, "")] + spellings[aid] if n}

    def left_out_in(self, season):
        return self.left_out[""] | self.left_out[season]

    def unused(self, seasons):
        """Rows that matched nothing in the seasons built (seasons=None: all), by line number - no names.
        The current / historical rows are checked in listing."""
        return [r for r in self.lines if r["line"] not in self.used and r.get("status") not in LISTS
                and (seasons is None or (r.get("season") and r["season"] in seasons))]


def _first_last(name):
    """'Doe, Jane (800)' -> ('Jane', 'Doe'); 'Jane Doe' -> ('Jane Doe', '') (order unknown)."""
    n = re.sub(r"\s*\([^)]*\)", "", name or "").strip()
    if "," in n:
        last, first = (p.strip() for p in n.split(",", 1))
        return first, last
    return n, ""


def name_forms(names):
    """'Doe, Jane (800)' and 'Jane Doe' -> {'doejane', 'janedoe'}: a name either way round, letters only."""
    out = set()
    for n in names:
        first, last = _first_last(n)
        if last:
            out |= {races.norm(first + last), races.norm(last + first)}
        else:  # no comma: the order isn't known, so also with the first or last word moved to the other end
            out |= {races.norm(o) for o in name_orders(first, plain=True)}
    return {f for f in out if len(f) > 3}


def first_names(names):
    """First names, letters only: the word after the comma of 'Last, First', else the first word."""
    out = set()
    for n in names:
        first, last = _first_last(n)
        words = first.split()
        if words:
            out.add(races.norm(words[0]))
    return {f for f in out if len(f) > 2}


def mentions(text, forms):
    """Does the text contain one of these names (2-4 words in a row, either way round)? First names alone don't count."""
    words = (text or "").split()
    return any(races.norm(" ".join(words[i:j])) in forms
               for i in range(len(words)) for j in range(i + 2, min(i + 4, len(words)) + 1))


def scrub_left_out(T, athletes):
    """Text written beside one athlete's row sometimes names others - a pairing ('Doe, Jane | Smith, Alex',
    'Doe | Jane' over two cells) or a name with its own status or workout ('Jane Doe | Injured ?'). Cells naming
    an athlete left out in athletes.csv are dropped from the notes kept; after a cell that is just their name, the
    next cell (theirs) goes too unless it is another athlete's name. A cell that is only a first name goes when no
    athlete kept has that first name. Group note lines naming them are dropped. Label lines, titles and rep text
    are never edited: a left-out (full) name there only goes to review, without the name."""
    known = name_forms(athletes.known)
    kept_first = first_names([r[c] for t, c in (("athlete_blocks", "athlete_name"), ("athlete_blocks", "workbook_name"),
                                                ("races", "athlete_name")) for r in T[t]])
    by_season = {}

    def gone_in(season):  # (full-name forms, first names only left-out athletes have) for a season
        if season not in by_season:
            names = athletes.left_out_in(season)
            by_season[season] = (name_forms(names), first_names(names) - kept_first)
        return by_season[season]

    for r in T["athlete_blocks"]:
        gone, solo = gone_in(r["season"])
        cells, out, theirs, i = (r["notes"].split(" | ") if r["notes"] else []), [], False, 0
        while i < len(cells):
            cell, pair = cells[i], " ".join(cells[i:i + 2]) if i + 1 < len(cells) else ""
            if theirs and races.norm(cell) not in known:
                theirs, i = False, i + 1
                continue
            theirs = False
            if mentions(cell, gone):
                theirs, i = races.norm(cell) in gone, i + 1
            elif pair and races.norm(pair) in gone:  # last and first name in two cells
                theirs, i = True, i + 2
            elif len(cell.split()) == 1 and races.norm(cell) in solo:
                i += 1
            else:
                out.append(cell)
                i += 1
        r["notes"] = " | ".join(out)
    for b in T["blocks"]:
        gone = gone_in(b["season"])[0]
        b["notes"] = " || ".join(l for l in (b["notes"].split(" || ") if b["notes"] else []) if not mentions(l, gone))
    flagged = {}
    for table, cols in (("sheets", ("written_title", "session_note", "venue", "weather")), ("blocks", ("labels",)),
                        ("reps", ("raw",))):
        for r in T[table]:
            gone = gone_in(r["season"])[0]
            if any(mentions(line, gone) for c in cols for line in str(r.get(c) or "").split(" || ")):
                key = r.get("block_key") or r["sheet_key"]
                flagged[(r["season"], key)] = table
    for (season, key), table in flagged.items():
        T["review"].append({"issue": "left_out_name_in_text", "season": season, "tab": key.split("|")[-1].split("#")[0],
                            "block": key.split("#")[1] if "#" in key else "",
                            "detail": f"{table}: the text names an athlete left out in data/rules/athletes.csv; it is "
                                      f"shown as written (labels, titles and rep text are never edited)"})


AT_CHECK_PCT = 117  # AT lines whose targets ran faster than this % of mile pace get flagged


def rep_numbers(text):
    """'1-6' -> {1, ..., 6}; '1 3 5 7', '1,3,5,7' or '1-3; 7' -> those reps. None when it can't be read."""
    out = set()
    for part in re.split(r"[\s,;]+", re.sub(r"\s*-\s*", "-", text.strip())):
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m or int(m.group(1)) < 1 or (m.group(2) and int(m.group(2)) < int(m.group(1))):
            return None
        out.update(range(int(m.group(1)), int(m.group(2) or m.group(1)) + 1))
    return out or None


def load_overrides():
    """rules/line_overrides.csv -> (lines, reps).
    lines: {(season, tab, block, line): row} - one label line, or (line blank) a whole unlabeled block.
    reps: {(season, tab, block): [(rep numbers, row)]} - a row with `reps` ('1-6', '1 3 5 7') sets just those
    rep columns of the group, labeled or not. Each row also gets file_line and used (set once it matches a group)."""
    lines, reps = {}, defaultdict(list)
    with open(RULES / "line_overrides.csv") as fh:
        for n, r in enumerate(csv.DictReader(fh), start=2):
            r = {k: (v or "").strip() for k, v in r.items() if k}
            r.update(file_line=n, used=False)
            if r.get("reps"):
                reps[(r["season"], r["tab"], r["block"])].append((rep_numbers(r["reps"]), r))
            else:
                lines[(r["season"], r["tab"], r["block"], r.get("line", ""))] = r
    return lines, reps


def load_tab_fixes():
    """rules/tab_fixes.csv -> {(season, tab): row}. action = skip | copied_splits."""
    with open(RULES / "tab_fixes.csv") as fh:
        return {(r["season"], r["tab"]): r for r in csv.DictReader(fh)}


def _drop_copied_splits(blocks, src_blocks):
    """copied_splits: an athlete whose splits match their splits on the source tab rep for rep was
    carried over when the tab was copied - treat those splits as not recorded. Returns how many."""
    src = {races.norm(a["name"]): [r[1] for r in a["reps"]] for b in src_blocks for a in b["athletes"]}
    n = 0
    for b in blocks:
        for a in b["athletes"]:
            secs = [r[1] for r in a["reps"]]
            if any(s is not None for s in secs) and src.get(races.norm(a["name"])) == secs:
                a["reps"] = [("blank", None, "") for _ in a["reps"]]
                a["notes"] = " | ".join(x for x in (a["notes"], "splits copied from another tab - not recorded (rules/tab_fixes.csv)") if x)
                n += 1
    return n


def _rule_unused(review, season, tab, detail):
    review.append({"issue": "rule_unused", "season": season, "tab": tab, "block": "", "detail": detail})


def load_cell_fixes():
    """rules/cell_fixes.csv -> {(season, tab): [rows]}: one-off corrections to single cells."""
    out = defaultdict(list)
    with open(RULES / "cell_fixes.csv") as fh:
        for r in csv.DictReader(fh):
            out[(r["season"], r["tab"])].append(r)
    return out


def _cell_fixes(ws, rows, season, review):
    """Fixes whose raw_value still matches the sheet -> {(row_idx, col_idx): corrected}. Others go to review."""
    out = {}
    for r in rows:
        cell = ws[r["cell"]]
        have = display(cell.value)
        if have != r["raw_value"].strip():
            _rule_unused(review, season, ws.title, f"rules/cell_fixes.csv expects '{r['raw_value']}' in {r['cell']} "
                                                   f"but the sheet has '{have}' - not applied")
            continue
        out[(cell.row - 1, cell.column - 1)] = r["corrected_value"].strip()
    return out


def name_orders(name, plain=False):
    """The name as parsed, plus - for a name written without its comma ('Doe Jane') - the same words
    with the first or last word moved to the other end, since the order isn't known."""
    words = name.split()
    out = [name]
    if plain and len(words) > 1:
        out += [" ".join(words[1:] + words[:1]), " ".join(words[-1:] + words[:-1])]
    return list(dict.fromkeys(out))


def resolve(name, aliases, by_name, id_names, plain=False):
    for n in name_orders(name, plain):
        k = races.norm(n)
        if k in aliases:
            aid = aliases[k]["athlete_id"]
            if not aid:  # a spelling merged into another name with no ID given: that name's exact Athletic.net match, if any
                target = aliases[k]["athletic_net_name"] or name
                aid = by_name.get(races.norm(target), "")
                return (aid, id_names[aid], "alias") if aid else ("", target, "unmatched")
            return aid, id_names.get(aid, aliases[k]["athletic_net_name"]), "alias"
        if k in by_name:
            aid = by_name[k]
            return aid, id_names[aid], "exact"
    return "", name, "unmatched"


def build(seasons=None, out_dir=None, mileage_dir=None, strava_dir=None, races_dir=None):
    out_dir = Path(out_dir or DATA / "build")
    out_dir.mkdir(parents=True, exist_ok=True)
    types = classify.load_types()
    aliases = load_aliases()
    athletes = AthleteRules(PRIVATE_RULES / "athletes.csv")
    T = defaultdict(list)  # table name -> rows
    review = T["review"]
    race_rows, id_names, by_name = races.load(Path(races_dir or DATA / "races"), RULES, review)
    fitness = races.Fitness(race_rows)
    cutoffs = races.load_inference(RULES)
    overrides, rep_overrides = load_overrides()
    venue_rules = venues.Rules(RULES)
    tab_fixes = load_tab_fixes()
    cell_fixes = load_cell_fixes()

    books = sorted(DATA.joinpath("workbooks").glob("MV_Workouts.*.xlsx"))
    for book in books:
        season = re.search(r"Workouts\.(\d{4})", book.name).group(1)
        if seasons and season not in seasons:
            continue
        wb = openpyxl.load_workbook(book, data_only=True)
        tab_names = [ws.title for ws in wb.worksheets]
        tab_dates = dates.assign_dates(tab_names, season)
        for name in tab_names:
            fx = tab_fixes.get((season, name))
            if name not in tab_dates:
                T["skipped_tabs"].append({"season": season, "tab": name, "reason": "not a date tab"})
            elif fx and fx["action"] == "skip":
                T["skipped_tabs"].append({"season": season, "tab": name, "reason": f"rules/tab_fixes.csv: {fx['note']}"})
        for (s, tab), fx in tab_fixes.items():
            if s == season and tab not in tab_names:
                _rule_unused(review, season, tab, f"rules/tab_fixes.csv names tab '{tab}', which isn't in {book.name}")
        for (s, tab) in cell_fixes:
            if s == season and tab not in tab_names:
                _rule_unused(review, season, tab, f"rules/cell_fixes.csv names tab '{tab}', which isn't in {book.name}")
        for ws in wb.worksheets:
            fx = tab_fixes.get((season, ws.title))
            if ws.title not in tab_dates or (fx and fx["action"] == "skip"):
                continue
            copied_from = None
            if fx and fx["action"] == "copied_splits":
                if fx["from_tab"] in tab_names:
                    copied_from = wb[fx["from_tab"]]
                else:
                    _rule_unused(review, season, ws.title, f"rules/tab_fixes.csv: from_tab '{fx['from_tab']}' isn't in {book.name}")
            fixes = _cell_fixes(ws, cell_fixes.get((season, ws.title), []), season, review)
            _do_sheet(ws, season, book.name, tab_dates[ws.title], types, aliases, athletes,
                      by_name, id_names, fitness, cutoffs, overrides, T, copied_from, fixes, rep_overrides, venue_rules)
    for o in [*overrides.values(), *(o for rows in rep_overrides.values() for _, o in rows)]:
        if not o["used"] and (not seasons or o["season"] in seasons):
            _rule_unused(review, o["season"], o["tab"], f"rules/line_overrides.csv line {o['file_line']} (group {o['block']}"
                                                        f"{', line ' + o['line'] if o.get('line') else ''}) matched nothing on that tab")

    _days(T)
    _dt_sessions(T)
    m_rows, m_review = mileage.load(Path(mileage_dir or DATA / "mileage"), PRIVATE_RULES / "strava_aliases.csv",
                                    by_name, id_names, race_rows, excluded=lambda aid, s: athletes.excluded(s, aid))
    T["weekly_mileage"].extend(m_rows)
    T["review"].extend(m_review)
    acts, a_weeks, windows, a_review = strava.load(Path(strava_dir or DATA / "strava"), RULES,
                                                   PRIVATE_RULES / "strava_aliases.csv", PRIVATE_RULES / "strava_fixes.csv",
                                                   by_name, id_names, race_rows, excluded=lambda aid, s: athletes.excluded(s, aid))
    settings = strava.load_settings(RULES)
    strava.mark_long_runs(acts, a_weeks, strava.setting_miles(settings, "long_run_min_miles", a_review))
    weekly, w_review = strava.combine(a_weeks, m_rows, acts, windows, settings.get("weekly_source_when_both", "activities"))
    T["activities"].extend(acts)
    T["athlete_weeks"].extend(weekly)
    T["athlete_months"].extend(strava.months(weekly, acts))
    T["review"].extend(a_review + w_review)
    T["settings"].extend({"setting": k, "value": v} for k, v in settings.items())  # rules/mileage.csv, for the app
    for r in race_rows:
        if not athletes.excluded(mileage.season_of(r["date"]), r["athlete_id"]):
            T["races"].append({**r, "date": r["date"].isoformat()})
            athletes.listed(r["athlete_id"], r["athlete_name"])
    athletes.add_rule_names(id_names, aliases)
    scrub_left_out(T, athletes)
    for r in athletes.unused(seasons):
        review.append({"issue": "rule_unused", "season": r.get("season", ""), "tab": "", "block": "",
                       "detail": f"data/rules/athletes.csv line {r['line']} "
                                 f"({'athlete_id ' + r['athlete_id'] if r.get('athlete_id') else 'workbook name'}) "
                                 f"matched no workbook row, race or Strava week"})
    # data/rules/athletes.csv status=current / historical: which Athletes list the app puts someone on
    listing, l_review = athletes.listing(max((s["season"] for s in T["sheets"]), default=""), seasons)
    T["athlete_listing"].extend(listing)
    review.extend(l_review)
    _write(T, out_dir)
    return T


def _do_sheet(ws, season, book, date, types, aliases, athletes, by_name, id_names, fitness, cutoffs, overrides, T,
              copied_from=None, fixes=None, rep_overrides=None, venue_rules=None):
    review = T["review"]
    sheet_key = f"{season}|{ws.title}"
    meta, blocks = parse_sheet(ws, fixes)
    # copied splits are compared as written (no cell fixes) - the copy happened on the raw cells
    if copied_from is not None and not _drop_copied_splits(blocks, parse_sheet(copied_from)[1]):
        _rule_unused(review, season, ws.title, f"rules/tab_fixes.csv copied_splits from '{copied_from.title}' matched no athlete")
    _, _, suffix = dates.parse_tab(ws.title)
    session_no = meta["session_no"]
    if session_no is None and suffix:
        m = re.match(r"(\d)", suffix)
        session_no = int(m.group(1)) if m else None
    title = meta["title"]
    conflicts = dates.compare(date, title)
    church = "church" in meta["venue"].lower() or any(CHURCH_LAP_M in b["distances"] for b in blocks)
    sv = venue_rules.session(meta["venue"], [d for b in blocks for d in b["distances"]]) if venue_rules else \
        {"name": "", "kind": "", "source": "", "rows": []}

    T["sheets"].append({
        "sheet_key": sheet_key, "season": season, "workbook": book, "tab": ws.title,
        "date": date.isoformat() if date else "", "written_title": str(title or ""),
        "date_conflict": "; ".join(conflicts), "session_no": session_no or "",
        "session_note": meta["session_note"], "venue": meta["venue"], "start_time": meta["start_time"],
        "weather": meta["weather"], "church_loop": int(church), "n_blocks": len(blocks),
        "venue_name": sv["name"], "venue_kind": sv["kind"], "venue_source": sv["source"],
    })
    if conflicts:
        review.append({"issue": "date_mismatch", "season": season, "tab": ws.title, "block": "",
                       "detail": f"Using tab date {date}. Sheet title: '{title}'. " + "; ".join(conflicts)})
    if not blocks:
        review.append({"issue": "no_blocks_found", "season": season, "tab": ws.title, "block": "",
                       "detail": "No rep header row found on this sheet"})

    for b in blocks:
        bkey = f"{sheet_key}#{b['block_no']}"
        lines = [classify.classify_line(l, season, types, church) for l in b["labels"]]
        assign = classify.assign_reps(b["distances"], lines)
        # per-line / per-block corrections from rules/line_overrides.csv
        for li, line in enumerate(lines):
            o = overrides.get((season, ws.title, str(b["block_no"]), str(li + 1)))
            if o:
                o["used"] = True
                line.update(workout_type=o["workout_type"] or line["workout_type"], band=o["band"],
                            type_rule="override", band_rule=f"override: {o['note']}")
        block_override = overrides.get((season, ws.title, str(b["block_no"]), ""))
        if block_override:
            block_override["used"] = True
        # rows with `reps` set those rep columns (1 = the first rep column), whatever the label lines say
        n_cols = max([len(b["distances"])] + [len(a["reps"]) for a in b["athletes"]])
        rep_over = {}
        for nums, o in (rep_overrides or {}).get((season, ws.title, str(b["block_no"])), []):
            o["used"] = True
            if nums is None:
                _rule_unused(review, season, ws.title, f"rules/line_overrides.csv line {o['file_line']}: can't read reps "
                                                       f"'{o['reps']}' (write e.g. 1-6 or 1 3 5 7) - not applied")
                continue
            if max(nums) > n_cols:
                _rule_unused(review, season, ws.title, f"rules/line_overrides.csv line {o['file_line']}: group {b['block_no']} has "
                                                       f"{n_cols} rep columns, so reps {o['reps']} don't all exist")
            rep_over.update((n, o) for n in nums)

        def by_rule(rep_no):  # this rep's type comes from rules/line_overrides.csv, not from a label or its pace
            return rep_no in rep_over or bool(block_override)

        # athletes. Excluded athletes (data/rules/athletes.csv) still count for what describes the group - its
        # fitness for inference, shared splits, the AT check - so leaving someone out never changes anyone
        # else's rows; only their own rows (and any review line naming them) are dropped.
        resolved = []
        for a in b["athletes"]:
            plain = a.get("plain_name", False)
            aid, canon, how = resolve(a["name"], aliases, by_name, id_names, plain)
            names = name_orders(a["name"], plain) + [canon]
            status = athletes.status(season, aid, names)
            athletes.seen(season, status, aid, names + [a["raw"]])
            if status != "exclude":
                athletes.listed(aid, canon, names)
            resolved.append((a, aid, canon, how, status))
            if how == "unmatched" and status != "exclude":
                shown = f" (shown as {canon})" if races.norm(canon) != races.norm(a["name"]) else ""
                review.append({"issue": "unmatched_athlete", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"'{a['raw']}'{shown} has no Athletic.net match (add to rules/aliases.csv if it should)"})

        # inference for unlabeled blocks: target pace as % of mile fitness (own, else group median)
        athlete_mile = {aid: fitness.mile_equiv(aid, date) for (_, aid, *_ ) in resolved if aid}
        miles = [m for m in athlete_mile.values() if m]
        group_mile = statistics.median(miles) if miles else None

        def infer(d, t, mile, rep_no=None):
            pct = races.pct_of_mile(t, d, mile)
            o = rep_over.get(rep_no) or block_override
            if o:
                return o["workout_type"], o["band"], pct, ""
            wt, band = races.type_from_pct(pct, d, cutoffs)
            note = ""
            if venue_rules:  # rules/venues.csv: e.g. nothing inferred at the church loop is faster than threshold / LT
                wt, band, note = venue_rules.cap(wt, band, venue_rules.rep_row(sv, d))
            return wt, band, pct, note

        inferred = [("", "", None, "")] * len(b["distances"])
        all_by_rule = bool(b["distances"]) and all(by_rule(i + 1) for i in range(len(b["distances"])))
        if not lines:
            inferred = [infer(d, t, group_mile, i + 1) for i, (d, t) in enumerate(zip(b["distances"], b["targets"]))]
            left = [x for i, x in enumerate(inferred) if not by_rule(i + 1)]
            if left or not b["distances"]:  # a group whose every rep is set in rules/line_overrides.csv needs no review
                reason = "no label lines on block"
                if group_mile is None:
                    reason += "; no race marks for these athletes, so no inference"
                elif not any(b["targets"]):
                    reason += "; no target times, so no inference"
                review.append({"issue": "unlabeled_block", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": reason + ". Inferred: " + ", ".join(sorted({f"{w}{'/'+bd if bd else ''}" for w, bd, *_ in left if w}))
                               + (" (the other reps are set in rules/line_overrides.csv)" if len(left) < len(inferred) else "")})

        # block row
        chip_types = []
        for li, line in enumerate(lines):
            chip_types.append((line["workout_type"], line["band"]))
            T["prescriptions"].append({
                "block_key": bkey, "sheet_key": sheet_key, "date": date.isoformat(), "session_no": session_no or "",
                "line_no": li + 1,
                **{k: v for k, v in line.items() if k not in ("distances", "continuous")},
                "n_reps_assigned": sum(1 for ai, how in assign if ai == li and how != "guess"),
            })
            if line["type_status"] in ("open", "unmapped"):
                review.append({"issue": "type_not_mapped", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"'{line['label']}' is not one of Patrick's categories yet: {line['label_raw']}"})
            if line["workout_type"] == "threshold" and (not line["band"] or "UNRESOLVED" in line["band"]):
                review.append({"issue": "band_unresolved", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"{line['band_rule']}: {line['label_raw']}"})
        if not lines:
            chip_types = [(w, bd) for w, bd, *_ in inferred if w]
        else:  # a labeled group: rep columns set by a rule add their own types
            chip_types += [(rep_over[n]["workout_type"], rep_over[n]["band"]) for n in sorted(rep_over)]
        guesses = sum(1 for i, (_, how) in enumerate(assign) if how == "guess" and (i + 1) not in rep_over)
        if guesses:
            review.append({"issue": "rep_to_label_guess", "season": season, "tab": ws.title, "block": b["block_no"],
                           "detail": f"{guesses} of {len(assign)} reps aren't mentioned by any label line; "
                                     f"typed from target pace (type_source=inferred)"})

        type_source = "sheet" if lines else ("override" if block_override or all_by_rule else
                                             ("inferred" if any(w for w, *_ in inferred) else "none"))
        T["blocks"].append({
            "block_key": bkey, "sheet_key": sheet_key, "season": season, "date": date.isoformat(),
            "session_no": session_no or "", "block_no": b["block_no"], "header_row": b["header_row"],
            "labels": " || ".join(b["labels"]), "notes": " || ".join(b["notes"]),
            "n_reps": len(b["distances"]), "rep_distances": json.dumps(b["distances"]),
            "target_secs": json.dumps(b["targets"]), "rest": json.dumps(b["rest_raw"]),
            "total_vol_m": b["total_vol_m"], "n_athletes": len(b["athletes"]),
            "types": " + ".join(dict.fromkeys(f"{w}{'/' + bd if bd else ''}" for w, bd in chip_types)),
            "type_source": type_source, "church_loop": int(church or CHURCH_LAP_M in b["distances"]),
        })

        # shared splits: identical times across athletes on the same rep = likely group timing
        vals = Counter((ri, a["distances"][ri], rep[1]) for a, *_ in resolved
                       for ri, rep in enumerate(a["reps"]) if rep[0] == "timed")
        shared = {k for k, n in vals.items() if n >= 2}

        line_pcts = defaultdict(list)
        for a, aid, canon, how, status in resolved:
            keep = status != "exclude"  # an excluded athlete's rows are worked out like everyone's, then not written
            ab_out, reps_out = (T["athlete_blocks"], T["reps"]) if keep else ([], [])
            st = Counter(r[0] for r in a["reps"])
            ab_out.append({
                "block_key": bkey, "sheet_key": sheet_key, "season": season, "date": date.isoformat(),
                "session_no": session_no or "", "athlete_id": aid, "athlete_name": canon,
                "workbook_name": a["raw"], "match": how, "athlete_status": status,
                "cooldown_mi": a["cooldown_mi"], "total_vol_mi": a["total_vol_mi"],
                "n_reps": len(a["reps"]), "n_timed": st["timed"], "n_skipped": st["skipped"],
                "n_blank": st["blank"], "n_note": st["note"], "notes": a["notes"],
                "name_suffix": a["name_suffix"] or "",
            })
            for ri, (status_, secs, raw) in enumerate(a["reps"]):
                d = a["distances"][ri]
                tgt = a["targets"][ri]
                if ri < len(assign):
                    li, how_assigned = assign[ri]
                else:  # extra rep from a one-athlete variant row
                    li, how_assigned = (len(lines) - 1, "variant row") if lines else (None, "")
                # a rep no label line mentions is typed from its target pace, not from the line above it (Patrick)
                unmentioned = how_assigned == "guess"
                ro = rep_over.get(ri + 1)  # rules/line_overrides.csv set this rep column: wins over its label line
                line = lines[li] if li is not None and lines and not unmentioned and not ro else None
                own_mile = athlete_mile.get(aid) or group_mile
                pct = races.pct_of_mile(tgt, d, own_mile)  # reported per athlete
                # inference is per rep, from the block's group fitness: same rep, same type for everyone (Patrick)
                inf = infer(d, tgt, group_mile, ri + 1) if not line else ("", "", None, "")
                wtype = line["workout_type"] if line else inf[0]
                band = line["band"] if line else inf[1]
                # no target / no race marks: take the line above rather than leave it untyped - still inferred
                fallback = unmentioned and not wtype and li is not None
                if fallback:
                    wtype, band = lines[li]["workout_type"], lines[li]["band"]
                rest_raw = b["rest_raw"][ri] if ri < len(b["rest_raw"]) else ""
                rest_s = b["rest_secs"][ri] if ri < len(b["rest_secs"]) else None
                suspect = bool(secs and d and secs >= 3600 and d < 10000)
                if suspect and keep:
                    review.append({"issue": "suspect_split", "season": season, "tab": ws.title, "block": b["block_no"],
                                   "detail": f"{canon} rep {ri+1} ({d:g} m) = {raw}; over an hour, probably a fill error"})
                reps_out.append({
                    "block_key": bkey, "season": season, "date": date.isoformat(), "session_no": session_no or "",
                    "athlete_id": aid, "athlete_name": canon, "rep_no": ri + 1, "distance_m": d,
                    "target_s": round(tgt, 2) if tgt else None, "actual_s": round(secs, 2) if secs else None,
                    "status": status_, "raw": raw if status_ != "timed" else "",
                    "rest_after": rest_raw, "rest_after_s": rest_s,
                    "label_line": (li + 1) if line else "",
                    "label_match": how_assigned if line else "rules/line_overrides.csv" if ro else (
                        (f"not in any label; type from line {li + 1}" if fallback else "not in any label") if unmentioned else ""),
                    "workout_type": wtype, "band": band,
                    "type_source": "sheet" if line else (("override" if ro or block_override else "inferred") if wtype else ""),
                    "type_note": "" if line or fallback else inf[3],
                    "target_pct_of_mile": round(pct, 1) if pct else None,
                    "mile_ref_s": round(own_mile, 1) if own_mile else None,
                    "shared_split": int((ri, d, secs) in shared) if status_ == "timed" else 0,
                    "variant_row": int(a["variant"]),
                    "suspect_time": int(suspect),
                })
                if line is not None and pct and (not line["distances"] or classify.mentions(line, d)):
                    line_pcts[li].append(pct)

        # AT now means aerobic threshold (= DT); older sheets sometimes used it for anaerobic work
        for li, line in enumerate(lines):
            v = line_pcts.get(li)
            if line["anchor_from"] == "at" and line["band"].startswith("DT") and line["type_rule"] != "override" and v and statistics.median(v) < AT_CHECK_PCT:
                review.append({"issue": "at_looks_anaerobic", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"line {li+1} '{line['label_raw']}' is classed DT (AT = aerobic), but its targets ran "
                                         f"{statistics.median(v):.0f}% of mile pace, closer to LT/anaerobic. To re-label: "
                                         f"rules/line_overrides.csv -> {season},{ws.title},{b['block_no']},{li+1},,threshold,LT"})

        # a label line at a venue that caps inferred types (rules/venues.csv: the church loop) whose own type is faster
        # than the cap: the label is kept as written - listed in review so Patrick can decide
        for li, line in enumerate(lines if venue_rules else []):
            ds = [b["distances"][i] for i, (ai, how) in enumerate(assign) if ai == li and how != "guess"] or [None]
            for r in {id(r): r for r in (venue_rules.rep_row(sv, d) for d in ds) if r and r["cap"]}.values():
                if venue_rules.faster(line["workout_type"], line["band"], r["cap"]):
                    cap = f"{r['cap'][0]}{'/' + r['cap'][1] if r['cap'][1] else ''}"
                    review.append({"issue": "venue_cap_label", "season": season, "tab": ws.title, "block": b["block_no"],
                                   "detail": f"line {li+1} '{line['label_raw']}' is {line['workout_type']}"
                                             f"{'/' + line['band'] if line['band'] else ''} at the {r['venue']} ({sv['name']}): faster "
                                             f"than {cap}, the fastest type rules/venues.csv lets an inferred rep there be. Your "
                                             f"label is kept{' (set in rules/line_overrides.csv)' if line['type_rule'] == 'override' else ''}"
                                             f". To change it: rules/line_overrides.csv -> {season},{ws.title},{b['block_no']},{li+1},,"
                                             f"{r['cap'][0]},{r['cap'][1]}"})
                    break


def _days(T):
    by_date = defaultdict(list)
    for s in T["sheets"]:
        by_date[s["date"]].append(s)
    blocks_by_sheet = defaultdict(list)
    for b in T["blocks"]:
        blocks_by_sheet[b["sheet_key"]].append(b)
    ath = defaultdict(set)
    for a in T["athlete_blocks"]:
        ath[a["date"]].add(a["athlete_id"] or a["athlete_name"])
    for d, sheets in sorted(by_date.items()):
        season = sheets[0]["season"]
        sess = sorted({s["session_no"] for s in sheets if s["session_no"] != ""})
        double = season >= classify.DT_FIRST_SEASON and {1, 2} <= set(sess)
        chips = []
        for s in sorted(sheets, key=lambda s: (str(s["session_no"]), s["tab"])):
            for b in blocks_by_sheet[s["sheet_key"]]:
                for c in b["types"].split(" + "):
                    if c and c not in chips:
                        chips.append(c)
        T["days"].append({
            "date": d, "season": season, "tabs": " ; ".join(s["tab"] for s in sheets),
            "sessions": ",".join(map(str, sess)), "double_threshold": int(double),
            "chips": " + ".join(chips), "venues": " ; ".join(dict.fromkeys(s["venue"] for s in sheets if s["venue"])),
            "church_loop": int(any(s["church_loop"] for s in sheets)), "n_athletes": len(ath[d]),
        })


def _dt_sessions(T):
    """On double days, DT work is split AM (Session 1, ~2-2.5 mmol) / PM (Session 2, ~2.5-3.5 mmol)."""
    double = {d["date"] for d in T["days"] if d["double_threshold"]}
    for table in ("reps", "prescriptions"):
        for r in T[table]:
            detail = ""
            if r["date"] in double and r.get("band", "").startswith("DT"):
                detail = {1: "DT-AM", 2: "DT-PM"}.get(r["session_no"], "")
            r["band_detail"] = detail


def _write(T, out_dir):
    db_path = out_dir / "training.sqlite"
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    for name, rows in T.items():
        if not rows:
            continue
        cols = list(dict.fromkeys(k for r in rows for k in r))
        with open(out_dir / f"{name}.csv", "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        con.execute(f'CREATE TABLE "{name}" ({", ".join(chr(34) + c + chr(34) for c in cols)})')
        con.executemany(f'INSERT INTO "{name}" VALUES ({", ".join("?" for _ in cols)})',
                        [[_sql(r.get(c)) for c in cols] for r in rows])
    con.commit()
    con.close()


def _sql(v):
    if isinstance(v, (bool,)):
        return int(v)
    if isinstance(v, (list, dict, set)):
        return json.dumps(sorted(v) if isinstance(v, set) else v)
    return v


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", nargs="*", help="e.g. 2526 2425 (default: all workbooks)")
    ap.add_argument("--out", help="output folder (default data/build)")
    ap.add_argument("--mileage", help="folder of Strava weekly-mileage CSVs (default data/mileage)")
    ap.add_argument("--strava", help="folder of Strava activity CSVs (default data/strava)")
    ap.add_argument("--races", help="folder of Athletic.net result CSVs (default data/races)")
    args = ap.parse_args()
    T = build(set(args.seasons) if args.seasons else None, args.out, args.mileage, args.strava, args.races)
    print(f"sheets {len(T['sheets'])}  blocks {len(T['blocks'])}  athlete-blocks {len(T['athlete_blocks'])}  "
          f"reps {len(T['reps'])}  days {len(T['days'])}  activities {len(T['activities'])}  review items {len(T['review'])}")
    for issue, n in Counter(r["issue"] for r in T["review"]).most_common():
        print(f"  {issue}: {n}")


if __name__ == "__main__":
    main()
