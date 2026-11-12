"""Workout type and threshold band from the label lines Patrick writes.

    "Threshold Intervals: 5 x Lap @ 20k RP w/0:45 rec"
     ^ label (-> type)    ^ reps      ^ anchor (-> band)
"""
import csv
import re
from pathlib import Path

from .cells import CHURCH_LAP_M

RULES = Path(__file__).resolve().parent.parent / "rules"
DT_FIRST_SEASON = "2526"  # no double-threshold training before 2025-26 (Patrick)

# anchor token -> equivalent race distance in km
NAMED_KM = {"mp": 42.195, "hmp": 21.0975, "hm": 21.0975}
SYMBOLIC = ("dt", "lt", "at", "threshold")


def load_types():
    with open(RULES / "workout_types.csv") as fh:
        return {r["label"]: r for r in csv.DictReader(fh)}


def load_bands():
    with open(RULES / "pace_bands.csv") as fh:
        return {r["anchor"]: r for r in csv.DictReader(fh)}


_BANDS = None


def _bands():
    global _BANDS
    if _BANDS is None:
        _BANDS = load_bands()
    return _BANDS


def type_by_anchor(start, default):
    """Patrick's categories are defined by race pace, so lactic-family labels are placed by their anchor.

    <=800 RP speed endurance; 1000-2000 RP lactic capacity; 3K-5K RP aerobic power
    (5K: that's where his own Aerobic Power labels mostly sit); slower -> threshold.
    """
    if not start or start[0] != "km":
        return default, "no race-distance anchor; label default"
    km = start[1]
    if km <= 0.8:
        return "speed_endurance", f"anchor {km:g}K <= 800 RP"
    if km <= 2.0:
        return "lactic_capacity", f"anchor {km:g}K in 1000-2000 RP"
    if km <= 5.0:
        return "aerobic_power", f"anchor {km:g}K in 3K-5K RP"
    return "threshold", f"anchor {km:g}K slower than 5K RP"


def norm_label(label):
    s = re.sub(r"\s+", " ", label.lower()).strip()
    return s


def split_label(line):
    """'Threshold Intervals: 5 x Lap @ 20k RP' -> ('threshold intervals', '5 x Lap @ 20k RP')."""
    head, _, rest = line.partition(":")
    return norm_label(head), rest.strip()


ANCHOR_SEG = re.compile(r"(?:@|\bat\b)\s*([^@+/|()&]*?)(?=\s*(?:w/|\bw\b|\+|/|\||\(|\)|&|\brec\b|$))", re.I)


def anchors(prescription):
    """All pace-anchor phrases in a prescription, e.g. ['20k RP', 'HMP']."""
    out = []
    for m in ANCHOR_SEG.finditer(prescription):
        a = m.group(1).strip(" ,.-")
        if a and not re.fullmatch(r"\d+:\d+", a):
            out.append(a)
    if not out:  # "1x1600m DT pace" - anchor written without an @
        m = re.search(r"\b(dt|lt|at|hmp|mp|\d+(?:\.\d+)?k)\s*(?:rp|pace)\b", prescription, re.I)
        if m:
            out.append(m.group(0))
    return out


def _token_km(tok):
    """One anchor token -> ('km', value) | ('sym', name) | None."""
    t = tok.strip().lower()
    t = re.sub(r"\b(rp|gp|wp|pace|race pace|goal pace)\b", "", t).strip(" -")
    if not t:
        return None
    if re.search(r"\bhmp?\b|half", t):
        return ("km", NAMED_KM["hm"])
    if re.search(r"\bmp\b|marathon", t):
        return ("km", NAMED_KM["mp"])
    for s in SYMBOLIC:
        if re.search(r"\b" + s + r"\b", t) or (s == "threshold" and re.search(r"thr?e?shold", t)):
            return ("sym", s)
    if "mile" in t:
        return ("km", 1.609)
    m = re.search(r"(\d+(?:\.\d+)?)\s*k\b", t)
    if m:
        return ("km", float(m.group(1)))
    m = re.search(r"(\d{3,5})\s*m?\b", t)
    if m:
        return ("km", float(m.group(1)) / 1000)
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", t)
    if m or re.fullmatch(r"\d+(?:\.\d+)?(?:\s*-\s*\d+(?:\.\d+)?)?", t):
        return ("sym", "percent")
    if re.search(r"\d+:\d+", t):
        return ("sym", "explicit_pace")
    return None


def parse_anchor(phrase):
    """'20k RP --> 10k RP' -> (start, end, progression); '3k-5k RP' -> range midpoint; 'AT pace' -> symbol."""
    parts = re.split(r"-->|->|→", phrase)
    progression = len(parts) > 1
    vals = []
    for p in parts:
        rng = re.match(r"\s*(\d+(?:\.\d+)?)\s*k?\s*-\s*(\d+(?:\.\d+)?)\s*k\b", p.lower())
        if rng:  # "3k-5k", "10-15k", "4-5k"
            a, b = float(rng.group(1)), float(rng.group(2))
            vals.append(("km", (a + b) / 2))
            continue
        v = _token_km(p)
        if v:
            vals.append(v)
    if not vals:
        return None, None, progression
    return vals[0], vals[-1], progression


def band_for(val, season, rep_m=None, continuous=False):
    """Threshold band for one anchor value. Returns (band, rule)."""
    if val is None:
        return "UNRESOLVED", "no anchor"
    kind, x = val
    if kind == "sym":
        row = _bands().get(x)
        if not row or not row["band"] or row["band"] == "UNRESOLVED":
            return "UNRESOLVED", f"anchor '{x}' not mapped yet"
        band, rule = row["band"], f"anchor {x.upper()}"
        cut = float(row["short_rep_below_m"]) if row.get("short_rep_below_m") else None
        if cut and row.get("short_rep_band") and rep_m and rep_m < cut and not continuous:
            band, rule = row["short_rep_band"], f"anchor {x.upper()}, reps under {cut:g} m"
        if row.get("status") == "proposed":
            rule += " (proposed)"
    else:
        km = x
        if km >= 19:
            band, rule = "DT", f"{km:g}K >= HM-ish -> DT pace"
        elif km > 10:
            band, rule = "LT", f"{km:g}K between HM and 10K -> LT"
        elif km == 10:
            if continuous or (rep_m and rep_m >= 2000):
                band, rule = "LT", "10K, long reps/continuous -> LT (default)"
            else:
                band, rule = "AnT", "10K, short reps -> AnT (default)"
        elif km >= 5:
            band, rule = "AnT", f"{km:g}K between 10K and 5K -> AnT"
        else:
            band, rule = "faster_than_AnT", f"{km:g}K is faster than 5K"
    if band == "DT" and season < DT_FIRST_SEASON:
        return "LT", rule + "; pre-2025-26 so reported as LT"
    return band, rule


def label_distances(prescription, church=False):
    """Rep distances a label line mentions (anchor phrases removed), used to match reps to lines."""
    s = ANCHOR_SEG.sub(" ", prescription)
    s = s.lower()
    out = set()
    for m in re.finditer(r"(\d{3,5})\s*m?\b", s):
        d = int(m.group(1))
        if 100 <= d <= 20000:
            out.add(float(d))
    if re.search(r"\blaps?\b|church loop", s):
        out.add(float(CHURCH_LAP_M))
        for m in re.finditer(r"(\d+)\s*laps?\b", s):
            out.add(float(CHURCH_LAP_M * int(m.group(1))))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:mi|miles?)\b", s):
        out.add(round(float(m.group(1)) * 1609.34))
    return out


def classify_line(line, season, types, church=False, rep_m=None):
    label, presc = split_label(line)
    t = types.get(label)
    wtype = t["workout_type"] if t else "UNMAPPED"
    fmt = t["format"] if t else ""
    status = t["status"] if t else "unmapped"
    anc = anchors(presc)
    start, end, prog = parse_anchor(anc[0]) if anc else (None, None, False)
    band, band_rule = "", ""
    type_rule = "label"
    if wtype == "by_anchor":
        wtype, type_rule = type_by_anchor(start, t.get("default_type") or "UNMAPPED")
    if wtype == "threshold":
        cont = fmt == "continuous"
        # explicit band in the label wins when the anchor is symbolic or missing
        if "lactate threshold" in label and (start is None or start[0] == "sym"):
            band, band_rule = "LT", "label says Lactate Threshold"
        elif re.search(r"an?a?erobic ?threshold", label) and not label.startswith("aerobic") and (start is None or start[0] == "sym"):
            band, band_rule = "AnT", "label says Anaerobic Threshold"
        else:
            b1, r1 = band_for(start, season, rep_m, cont)
            if prog and end != start:
                b2, r2 = band_for(end, season, rep_m, cont)
                band, band_rule = (b1, r1) if b1 == b2 else (f"{b1}->{b2}", f"progression: {r1} / {r2}")
            else:
                band, band_rule = b1, r1
    return {
        "label_raw": line,
        "label": label,
        "workout_type": wtype,
        "type_rule": type_rule,
        "format": fmt,
        "type_status": status,
        "prescription": presc,
        "anchor_raw": anc[0] if anc else "",
        "anchors_all": " ; ".join(anc),
        "anchor_from": _fmt(start),
        "anchor_to": _fmt(end),
        "progression": prog,
        "band": band,
        "band_rule": band_rule,
        "distances": label_distances(presc, church),
    }


def _fmt(v):
    if v is None:
        return ""
    kind, x = v
    return f"{x:g}K" if kind == "km" else x


def assign_reps(distances, lines):
    """Map each rep column to a label line by walking the reps in order.

    A rep belongs to the current line if that line mentions its distance;
    otherwise to the next line that does; otherwise it stays with the current
    line and is marked as a guess.
    """
    if not lines:
        return [(None, "no label") for _ in distances]
    if len(lines) == 1:
        return [(0, "only label") for _ in distances]
    out, cur = [], 0
    for d in distances:
        if d in lines[cur]["distances"]:
            out.append((cur, "distance match"))
            continue
        nxt = next((j for j in range(cur + 1, len(lines)) if d in lines[j]["distances"]), None)
        if nxt is not None:
            cur = nxt
            out.append((cur, "distance match"))
        else:
            out.append((cur, "guess"))
    return out
