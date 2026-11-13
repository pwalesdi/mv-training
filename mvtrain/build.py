"""Build the training database from the workbooks and race files.

    python -m mvtrain.build                 # every workbook in data/workbooks
    python -m mvtrain.build --seasons 2526  # just 2025-26

Outputs (in data/build/, never committed):
    training.sqlite  - every table below
    sheets.csv, blocks.csv, prescriptions.csv, athlete_blocks.csv, reps.csv,
    days.csv, races.csv, review.csv
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

from . import classify, dates, mileage, races
from .cells import CHURCH_LAP_M, display
from .workbook import parse_sheet

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RULES = ROOT / "rules"
PRIVATE_RULES = DATA / "rules"  # rules that name athletes stay out of git


def load_aliases():
    with open(PRIVATE_RULES / "aliases.csv") as fh:
        return {races.norm(r["workbook_name"]): r for r in csv.DictReader(fh)}


def load_athlete_notes():
    with open(PRIVATE_RULES / "athletes.csv") as fh:
        return {(races.norm(r["workbook_name"]), r["season"]): r for r in csv.DictReader(fh)}


AT_CHECK_PCT = 117  # AT lines whose targets ran faster than this % of mile pace get flagged


def load_overrides():
    with open(RULES / "line_overrides.csv") as fh:
        return {(r["season"], r["tab"], r["block"], r["line"]): r for r in csv.DictReader(fh)}


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


def resolve(name, aliases, by_name, id_names):
    k = races.norm(name)
    if k in aliases:
        aid = aliases[k]["athlete_id"]
        return aid, id_names.get(aid, aliases[k]["athletic_net_name"]), "alias"
    if k in by_name:
        aid = by_name[k]
        return aid, id_names[aid], "exact"
    return "", name, "unmatched"


def build(seasons=None, out_dir=None):
    out_dir = Path(out_dir or DATA / "build")
    out_dir.mkdir(parents=True, exist_ok=True)
    types = classify.load_types()
    aliases = load_aliases()
    ath_notes = load_athlete_notes()
    race_rows, id_names, by_name = races.load(DATA / "races")
    fitness = races.Fitness(race_rows)
    cutoffs = races.load_inference(RULES)
    overrides = load_overrides()
    tab_fixes = load_tab_fixes()
    cell_fixes = load_cell_fixes()

    T = defaultdict(list)  # table name -> rows
    review = T["review"]

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
            _do_sheet(ws, season, book.name, tab_dates[ws.title], types, aliases, ath_notes,
                      by_name, id_names, fitness, cutoffs, overrides, T, copied_from, fixes)

    _days(T)
    _dt_sessions(T)
    m_rows, m_review = mileage.load(DATA / "mileage", PRIVATE_RULES / "strava_aliases.csv", by_name, id_names)
    T["weekly_mileage"].extend(m_rows)
    T["review"].extend(m_review)
    for r in race_rows:
        T["races"].append({**r, "date": r["date"].isoformat()})
    _write(T, out_dir)
    return T


def _do_sheet(ws, season, book, date, types, aliases, ath_notes, by_name, id_names, fitness, cutoffs, overrides, T,
              copied_from=None, fixes=None):
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

    T["sheets"].append({
        "sheet_key": sheet_key, "season": season, "workbook": book, "tab": ws.title,
        "date": date.isoformat() if date else "", "written_title": str(title or ""),
        "date_conflict": "; ".join(conflicts), "session_no": session_no or "",
        "session_note": meta["session_note"], "venue": meta["venue"], "start_time": meta["start_time"],
        "weather": meta["weather"], "church_loop": int(church), "n_blocks": len(blocks),
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
                line.update(workout_type=o["workout_type"] or line["workout_type"], band=o["band"],
                            type_rule="override", band_rule=f"override: {o['note']}")
        block_override = overrides.get((season, ws.title, str(b["block_no"]), ""))

        # athletes
        resolved = []
        for a in b["athletes"]:
            aid, canon, how = resolve(a["name"], aliases, by_name, id_names)
            note = ath_notes.get((races.norm(a["name"]), season))
            if note and note["status"] == "exclude":
                continue  # out of scope (data/rules/athletes.csv)
            resolved.append((a, aid, canon, how, note["status"] if note else ""))
            if how == "unmatched":
                review.append({"issue": "unmatched_athlete", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"'{a['raw']}' has no Athletic.net match (add to rules/aliases.csv if it should)"})

        # inference for unlabeled blocks: target pace as % of mile fitness (own, else group median)
        athlete_mile = {aid: fitness.mile_equiv(aid, date) for (_, aid, *_ ) in resolved if aid}
        miles = [m for m in athlete_mile.values() if m]
        group_mile = statistics.median(miles) if miles else None

        def infer(d, t, mile):
            pct = races.pct_of_mile(t, d, mile)
            if block_override:
                return block_override["workout_type"], block_override["band"], pct
            wt, band = races.type_from_pct(pct, d, cutoffs)
            return wt, band, pct

        inferred = [("", "", None)] * len(b["distances"])
        if not lines:
            inferred = [infer(d, t, group_mile) for d, t in zip(b["distances"], b["targets"])]
            reason = "no label lines on block"
            if group_mile is None:
                reason += "; no race marks for these athletes, so no inference"
            elif not any(b["targets"]):
                reason += "; no target times, so no inference"
            review.append({"issue": "unlabeled_block", "season": season, "tab": ws.title, "block": b["block_no"],
                           "detail": reason + ". Inferred: " + ", ".join(sorted({f"{w}{'/'+bd if bd else ''}" for w, bd, _ in inferred if w})) })

        # block row
        chip_types = []
        for li, line in enumerate(lines):
            chip_types.append((line["workout_type"], line["band"]))
            T["prescriptions"].append({
                "block_key": bkey, "sheet_key": sheet_key, "date": date.isoformat(), "session_no": session_no or "",
                "line_no": li + 1,
                **{k: v for k, v in line.items() if k != "distances"},
                "n_reps_assigned": sum(1 for ai, _ in assign if ai == li),
            })
            if line["type_status"] in ("open", "unmapped"):
                review.append({"issue": "type_not_mapped", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"'{line['label']}' is not one of Patrick's categories yet: {line['label_raw']}"})
            if line["workout_type"] == "threshold" and (not line["band"] or "UNRESOLVED" in line["band"]):
                review.append({"issue": "band_unresolved", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"{line['band_rule']}: {line['label_raw']}"})
        if not lines:
            chip_types = [(w, bd) for w, bd, _ in inferred if w]
        guesses = sum(1 for _, how in assign if how == "guess")
        if guesses:
            review.append({"issue": "rep_to_label_guess", "season": season, "tab": ws.title, "block": b["block_no"],
                           "detail": f"{guesses} of {len(assign)} reps couldn't be matched to a label line by distance"})

        type_source = "sheet" if lines else ("override" if block_override else
                                             ("inferred" if any(w for w, _, _ in inferred) else "none"))
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
            st = Counter(r[0] for r in a["reps"])
            T["athlete_blocks"].append({
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
                line = lines[li] if li is not None and lines else None
                own_mile = athlete_mile.get(aid) or group_mile
                pct = races.pct_of_mile(tgt, d, own_mile)
                inf = infer(d, tgt, own_mile) if not line else ("", "", None)
                wtype = line["workout_type"] if line else inf[0]
                band = line["band"] if line else inf[1]
                rest_raw = b["rest_raw"][ri] if ri < len(b["rest_raw"]) else ""
                rest_s = b["rest_secs"][ri] if ri < len(b["rest_secs"]) else None
                suspect = bool(secs and d and secs >= 3600 and d < 10000)
                if suspect:
                    review.append({"issue": "suspect_split", "season": season, "tab": ws.title, "block": b["block_no"],
                                   "detail": f"{canon} rep {ri+1} ({d:g} m) = {raw}; over an hour, probably a fill error"})
                T["reps"].append({
                    "block_key": bkey, "season": season, "date": date.isoformat(), "session_no": session_no or "",
                    "athlete_id": aid, "athlete_name": canon, "rep_no": ri + 1, "distance_m": d,
                    "target_s": round(tgt, 2) if tgt else None, "actual_s": round(secs, 2) if secs else None,
                    "status": status_, "raw": raw if status_ != "timed" else "",
                    "rest_after": rest_raw, "rest_after_s": rest_s,
                    "label_line": (li + 1) if line else "", "label_match": how_assigned if line else "",
                    "workout_type": wtype, "band": band,
                    "type_source": "sheet" if line else (("override" if block_override else "inferred") if wtype else ""),
                    "target_pct_of_mile": round(pct, 1) if pct else None,
                    "mile_ref_s": round(own_mile, 1) if own_mile else None,
                    "shared_split": int((ri, d, secs) in shared) if status_ == "timed" else 0,
                    "variant_row": int(a["variant"]),
                    "suspect_time": int(suspect),
                })
                if line is not None and pct and (not line["distances"] or d in line["distances"]):
                    line_pcts[li].append(pct)

        # AT now means aerobic threshold (= DT); older sheets sometimes used it for anaerobic work
        for li, line in enumerate(lines):
            v = line_pcts.get(li)
            if line["anchor_from"] == "at" and line["band"].startswith("DT") and line["type_rule"] != "override" and v and statistics.median(v) < AT_CHECK_PCT:
                review.append({"issue": "at_looks_anaerobic", "season": season, "tab": ws.title, "block": b["block_no"],
                               "detail": f"line {li+1} '{line['label_raw']}' is classed DT (AT = aerobic), but its targets ran "
                                         f"{statistics.median(v):.0f}% of mile pace, closer to LT/anaerobic. To re-label: "
                                         f"rules/line_overrides.csv -> {season},{ws.title},{b['block_no']},{li+1},threshold,LT"})


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
    args = ap.parse_args()
    T = build(set(args.seasons) if args.seasons else None, args.out)
    print(f"sheets {len(T['sheets'])}  blocks {len(T['blocks'])}  athlete-blocks {len(T['athlete_blocks'])}  "
          f"reps {len(T['reps'])}  days {len(T['days'])}  review items {len(T['review'])}")
    for issue, n in Counter(r["issue"] for r in T["review"]).most_common():
        print(f"  {issue}: {n}")


if __name__ == "__main__":
    main()
