"""Read one workout sheet into blocks.

A sheet is a stack of blocks. Each block looks like:

    Label: prescription          <- 0..n lines, col A, "Type: ..."
    (blank) | 1725 | 1725 | ... | Cooldown | Total Vol.   <- header (rep distances)
    (blank) | 7:10 | 7:10 | ...                          <- target times
    Last, First | split | split | ... | 0.5 miles | 6.0 miles
    ...
    Rest: | 0:45 | 0:45 | ...
    Total Vol (m) | 8625 | ...   <- summary rows, ignored except total volume

Row 1 holds the written date (col A), start time (col H) and venue (col I);
row 2 holds weather (cols H-I). A "SESSION 1/2" marker sits in the top rows on
double days.
"""
import re

from .cells import blank, duration_text, text, to_distance, to_miles, to_seconds

NAME_RE = re.compile(r"^\s*([A-Za-z][A-Za-z'\-\. ]*?)\s*,\s*([A-Za-z][A-Za-z'\-\. ]*?)\s*(\(.*\))?\s*$")
SESSION_RE = re.compile(r"^\s*session\s*(\d)\s*:?\s*(.*)$", re.I)
SUMMARY_PREFIXES = ("total vol", "workout duration", "rest duration", "total duration", "density", "pace (")
# A col-A row that is just "Lactate:" holds lactate readings (2627 tab 7-9). Kept in the workbook,
# deliberately not parsed yet (see README): not a label, not a block, not a review item.
# Only the bare word - "Lactate Resistance: ..." etc. are real workout labels.
LACTATE_RE = re.compile(r"^\s*lactate\s*:?\s*$", re.I)
MAX_COL = 30


def _grid(ws):
    rows = []
    for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=MAX_COL, values_only=True):
        rows.append(list(row) + [None] * (MAX_COL - len(row)))
    return rows


def _a(row):
    return row[0] if row else None


def _numeric_distance(v):
    """Rep distances in headers are numbers (or numeric text); '2.0 miles' cells are cooldown/volume, not reps."""
    if isinstance(v, str) and re.search(r"mi", v, re.I):
        return None
    return to_distance(v)


def _is_header(row):
    """Block header: blank col A and a rep distance in col B (or an unfilled header with only Cooldown/Total Vol)."""
    if not blank(_a(row)):
        return False
    if _numeric_distance(row[1]) is not None:
        return True
    return blank(row[1]) and any(isinstance(c, str) and re.search(r"cooldown|total vol", c, re.I) for c in row[2:])


def _is_variant_header(row):
    """Mid-block header for one athlete's variation: blank cols A-B, distances further right."""
    return (blank(_a(row)) and blank(row[1])
            and sum(1 for c in row[2:] if _numeric_distance(c) is not None) >= 1)


def parse_name(v):
    """'Last, First' -> ('First Last', None); '(8/6)'-style suffixes returned separately."""
    if not isinstance(v, str):
        return None
    m = NAME_RE.match(v)
    if not m:
        return None
    last, first, extra = m.group(1).strip(), m.group(2).strip(), m.group(3)
    if last.lower().startswith(SUMMARY_PREFIXES) or last.lower() in ("rest", "note", "notes"):
        return None
    return f"{first} {last}", (extra.strip("() ") if extra else None)


def _rep_cell(v):
    """-> (status, seconds, raw)."""
    if blank(v):
        return "blank", None, ""
    secs = to_seconds(v)
    if secs is not None:
        return "timed", secs, text(v)
    raw = text(v)
    if raw.upper() in ("N/A", "NA", "-", "X"):
        return "skipped", None, raw
    return "note", None, raw


def parse_sheet(ws):
    g = _grid(ws)
    meta = {
        "title": g[0][0] if g else None,
        "start_time": text(g[0][7]) if g else "",
        "venue": text(g[0][8]) if g else "",
        "weather": " ".join(t for t in (text(g[1][7]), text(g[1][8])) if t) if len(g) > 1 else "",
        "session_no": None,
        "session_note": "",
    }
    for r in range(min(6, len(g))):
        a = _a(g[r])
        if isinstance(a, str):
            m = SESSION_RE.match(a)
            if m:
                meta["session_no"] = int(m.group(1))
                meta["session_note"] = m.group(2).strip()

    header_rows = [r for r, row in enumerate(g) if r >= 2 and _is_header(row)]
    blocks = []
    prev_end = 1  # labels can start on row 3 (index 2)
    for bi, hr in enumerate(header_rows):
        header = g[hr]
        cool_col = next((c for c, v in enumerate(header) if isinstance(v, str) and "cooldown" in v.lower()), None)
        tot_col = next((c for c, v in enumerate(header) if isinstance(v, str) and "total vol" in v.lower()), None)
        last_rep_col = min(c for c in (cool_col, tot_col, MAX_COL) if c is not None)
        rep_cols = [c for c in range(1, last_rep_col) if _numeric_distance(header[c]) is not None]
        distances = [_numeric_distance(header[c]) for c in rep_cols]

        # labels + notes: col A text between the previous block and this header
        labels, notes = [], []
        for r in range(prev_end + 1, hr):
            a = _a(g[r])
            if blank(a) or not isinstance(a, str):
                continue
            s = a.strip()
            if (SESSION_RE.match(s) or parse_name(s) or s.lower().startswith(SUMMARY_PREFIXES)
                    or s.lower().startswith("rest") or LACTATE_RE.match(s)):
                continue
            # "Last, First   600m:" is an athlete-specific note, not a workout label
            if re.match(r"^\**\s*notes?\b", s, re.I) or ":" not in s or "," in s.split(":")[0]:
                notes.append(s)
            else:
                labels.append(s)

        # target row
        tr = hr + 1
        targets = [None] * len(rep_cols)
        if tr < len(g) and blank(_a(g[tr])):
            targets = [to_seconds(g[tr][c]) for c in rep_cols]
            r = tr + 1
        else:
            r = hr + 1

        next_header = header_rows[bi + 1] if bi + 1 < len(header_rows) else len(g)
        athletes, rest_raw, rest_secs, total_vol_m, end = [], [], [], None, r
        variant = None  # {col: (distance, target)} from a mid-block sub-header
        while r < next_header:
            a = _a(g[r])
            row = g[r]
            sa = a.strip().lower() if isinstance(a, str) else ""
            nm = parse_name(a)
            if _is_variant_header(row):
                tgt_row = g[r + 1] if r + 1 < len(g) and blank(_a(g[r + 1])) else [None] * MAX_COL
                variant = {c: (_numeric_distance(row[c]), to_seconds(tgt_row[c]))
                           for c in range(2, MAX_COL) if _numeric_distance(row[c]) is not None}
                r += 2 if tgt_row[0] is None and any(tgt_row[1:]) else 1
                continue
            if nm:
                cols = list(rep_cols)
                a_dist = list(distances)
                a_tgt = list(targets)
                if variant:
                    for c, (d, t) in variant.items():
                        if c in cols:
                            i = cols.index(c)
                            a_dist[i], a_tgt[i] = d, t
                        else:
                            cols.append(c)
                            a_dist.append(d)
                            a_tgt.append(t)
                reps = [_rep_cell(row[c]) for c in cols]
                extra = []
                for c in range(1, MAX_COL):
                    if c in cols or c in (cool_col, tot_col):
                        continue
                    t = text(row[c])
                    if t and isinstance(row[c], str):
                        extra.append(t)
                athletes.append({
                    "row": r + 1,
                    "raw": a.strip(),
                    "name": nm[0],
                    "name_suffix": nm[1],
                    "reps": reps,
                    "distances": a_dist,
                    "targets": a_tgt,
                    "variant": bool(variant),
                    "cooldown_mi": to_miles(row[cool_col]) if cool_col is not None else None,
                    "total_vol_mi": to_miles(row[tot_col]) if tot_col is not None else None,
                    "notes": " | ".join(extra),
                })
                end = r
            elif re.match(r"^rest\s*:?\s*$", sa):
                rest_raw = [duration_text(row[c]) for c in rep_cols]
                rest_secs = [to_seconds(row[c]) for c in rep_cols]
                end = r
            elif sa.startswith("total vol"):
                v = row[1]
                total_vol_m = float(v) if isinstance(v, (int, float)) else None
                end = r
            elif sa.startswith(SUMMARY_PREFIXES):
                end = r
            elif LACTATE_RE.match(sa):
                pass  # lactate readings row inside a block: skip it and keep reading (Rest:, Total Vol follow)
            elif isinstance(a, str) and a.strip() and ":" in a and not sa.startswith(SUMMARY_PREFIXES):
                break  # label line for the next block
            r += 1
        prev_end = end
        blocks.append({
            "block_no": bi + 1,
            "header_row": hr + 1,
            "labels": labels,
            "notes": notes,
            "distances": distances,
            "targets": targets,
            "rest_raw": rest_raw,
            "rest_secs": rest_secs,
            "total_vol_m": total_vol_m,
            "athletes": athletes,
        })
    return meta, blocks
