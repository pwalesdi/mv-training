"""Workout type and threshold band from the label lines Patrick writes.

    "Threshold Intervals: 5 x Lap @ 20k RP w/0:45 rec"
     ^ label (-> type)    ^ reps      ^ anchor (-> band)
"""
import csv
import re
from pathlib import Path

from .cells import CHURCH_LAP_M

RULES = Path(__file__).resolve().parent.parent / "rules"
DT_FIRST_SEASON = "2526"  # double-threshold *days* start in 2025-26 (Patrick); the DT pace band applies to every season

# anchor token -> equivalent race distance in km
NAMED_KM = {"mp": 42.195, "hmp": 21.0975, "hm": 21.0975}
SYMBOLIC = ("dt", "lt", "at", "threshold")


def load_types():
    with open(RULES / "workout_types.csv") as fh:
        return {r["label"]: r for r in csv.DictReader(fh)}


def _read(name):
    with open(RULES / name) as fh:
        return list(csv.DictReader(fh))


_ZONES = None
_SYMBOLS = None


def zones():
    global _ZONES
    if _ZONES is None:
        _ZONES = [{**r, "min": float(r["min_km"]), "max": float(r["max_km"])} for r in _read("pace_zones.csv")]
    return _ZONES


def symbols():
    global _SYMBOLS
    if _SYMBOLS is None:
        _SYMBOLS = {r["anchor"]: r for r in _read("pace_symbols.csv")}
    return _SYMBOLS


def place(val):
    """Anchor value -> (workout_type, band, rule) from rules/pace_zones.csv and rules/pace_symbols.csv."""
    if val is None:
        return None
    kind, x = val
    if kind == "sym":
        r = symbols().get(x)
        if not r or not r["workout_type"]:
            return None
        rule = f"anchor {x.upper()}" + (" (proposed)" if r["status"] == "proposed" else "")
        return r["workout_type"], r["band"], rule
    for z in zones():
        if z["min"] < x <= z["max"]:
            return z["workout_type"], z["band"], f"anchor {x:g}K -> {z['note'] or z['workout_type']}"
    return None


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


LAP_RE = re.compile(r"(?:\b|(?<=\d)|(?<=\dx))laps?\b")  # 'lap', '1 x Lap', and run-together '2lap', '5xLap'


def label_distances(prescription, church=False):
    """Rep distances a label line mentions (anchor phrases removed), used to match reps to lines."""
    s = ANCHOR_SEG.sub(" ", prescription)
    s = s.lower()
    out = set()
    for m in re.finditer(r"(\d{3,5})\s*m?\b", s):
        d = int(m.group(1))
        if 100 <= d <= 20000:
            out.add(float(d))
    if LAP_RE.search(s) or "church loop" in s:
        out.add(float(CHURCH_LAP_M))
        for m in re.finditer(r"(\d+)\s*laps?\b", s):  # '3 lap' / '2lap' = one rep that long; '5xLap' = five 1-lap reps
            out.add(float(CHURCH_LAP_M * int(m.group(1))))
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:mi|miles?)\b", s):
        out.add(round(float(m.group(1)) * 1609.34))
    return out


def classify_line(line, season, types, church=False, rep_m=None):
    """rep_m is kept for callers; current rules don't depend on rep length."""
    label, presc = split_label(line)
    t = types.get(label)
    wtype = t["workout_type"] if t else "UNMAPPED"
    fmt = t["format"] if t else ""
    status = t["status"] if t else "unmapped"
    anc = anchors(presc)
    start, end, prog = parse_anchor(anc[0]) if anc else (None, None, False)
    band, band_rule, type_rule = "", "", "label"
    pace_decides = t is not None and t.get("pace_decides") == "yes"
    p1, p2 = place(start), place(end)
    named = _named_band(label)
    if pace_decides and p1 and start[0] == "sym" and named:
        # "Anaerobic Threshold: lap @ AT pace" - the label names the band; the symbolic anchor doesn't override it
        wtype, band, type_rule = "threshold", named[0], named[1]
        band_rule = type_rule
    elif pace_decides and p1:
        wtype, band, type_rule = p1
        band_rule = type_rule
        if prog and p2 and p2[:2] != p1[:2]:
            if p2[0] == wtype and band and p2[1]:
                band = f"{band}->{p2[1]}"
            band_rule = f"progression: {p1[2]} / {p2[2]}"
    elif wtype == "threshold":
        # no usable anchor: the label's own words decide the band
        if named:
            band, band_rule = named
        else:
            band, band_rule = "UNRESOLVED", "no usable pace anchor" if start is None else f"anchor '{_fmt(start)}' not mapped"
    elif pace_decides and t.get("default_type"):
        wtype, type_rule = t["default_type"], "no race-distance anchor; label default"
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
        "continuous": continuous_total(label, presc),
    }


TOL = 0.02  # a rep matches a distance a line states within 2% (2.0 miles = 3219 m -> the 3200 column) (Patrick)


def mentions(line, d):
    """Does this label line state a distance within TOL of d?"""
    return d is not None and any(abs(d - x) <= TOL * x for x in line["distances"])


def continuous_total(label, presc):
    """(low, high) metres for a continuous line - 'x miles continuous', 'N lap(s) ... run', or a '... run'
    label with a plain distance ('Threshold Run: 6 Laps') - else None. Reps ('5 x Lap') aren't continuous."""
    s = presc.lower()
    if not (re.search(r"\bcontinuous\b", s) or re.search(r"\d\s*laps?\b[\w\s]*\brun\b", s)
            or (label.endswith(" run") and not re.search(r"\d\s*x", s))):
        return None
    s = ANCHOR_SEG.sub(" ", s)
    m = re.search(r"(\d+(?:\.\d+)?)(?:\s*-\s*(\d+(?:\.\d+)?))?\s*(?:mi|miles?)\b", s)
    if m:
        return float(m.group(1)) * 1609.34, float(m.group(2) or m.group(1)) * 1609.34
    m = re.search(r"(\d+)(?:\s*-\s*(\d+))?\s*laps?\b", s)
    if m:
        return int(m.group(1)) * CHURCH_LAP_M, int(m.group(2) or m.group(1)) * CHURCH_LAP_M
    m = re.search(r"(\d{3,5})\s*m\b", s)
    if m:
        return float(m.group(1)), float(m.group(1))
    return None


def continuous_span(distances, start, total):
    """How many columns from `start` make up one continuous run of length `total` (low, high): cumulative
    splits (400, 800, ... 3200) or segments (1725 x 6) that end at the total, within TOL. 0 if they don't."""
    lo, hi = total
    rest = distances[start:]
    cumulative = len(rest) > 1 and abs(rest[1] - 2 * rest[0]) <= TOL * rest[1]
    n, s, prev = 0, 0.0, None
    for d in rest:
        if cumulative and prev is not None and d <= prev:
            break
        if (d if cumulative else s + d) > hi * (1 + TOL):
            break
        n, s, prev = n + 1, s + d, d
    covered = prev if cumulative else s
    return n if n and covered >= lo * (1 - TOL) else 0


def _named_band(label):
    """Band named in the label itself, if any: lactate/anaerobic threshold = LT, aerobic threshold = DT."""
    if re.search(r"lactate threshold|an?a?erobic ?threshold", label) and not label.startswith("aerobic"):
        return "LT", "label says Lactate/Anaerobic Threshold (= LT)"
    if label.startswith("aerobic threshold"):
        return "DT", "label says Aerobic Threshold (= DT)"
    return None


def _fmt(v):
    if v is None:
        return ""
    kind, x = v
    return f"{x:g}K" if kind == "km" else x


def assign_reps(distances, lines):
    """Map each rep column to a label line by walking the reps in order.

    From the current line onward, the first line that either owns a continuous
    run starting here (all its splits) or mentions the rep's distance (within
    TOL) takes it. A continuous line is used up once its run is assigned.
    A rep no line takes stays with the current line, marked "guess" (the
    build types those from pace, not from the line).
    """
    if not lines:
        return [(None, "no label") for _ in distances]
    if len(lines) == 1:
        return [(0, "only label") for _ in distances]
    out, cur, i, done = [], 0, 0, set()
    while i < len(distances):
        d = distances[i]
        for j in range(cur, len(lines)):
            if j in done:
                continue
            span = continuous_span(distances, i, lines[j]["continuous"]) if lines[j].get("continuous") else 0
            if span:
                out += [(j, "continuous run")] * span
                done.add(j)
                cur, i = j, i + span
                break
            if mentions(lines[j], d):
                out.append((j, "distance match"))
                cur, i = j, i + 1
                break
        else:
            out.append((cur, "guess"))
            i += 1
    return out
