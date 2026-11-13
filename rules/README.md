# Rules

Plain CSVs the parser reads on every run. Edit them, then re-run the parser
(`python -m mvtrain.build`). Nothing here is baked into the data: change a row,
rebuild, and every season is re-classified.

## The framework (Patrick, Nov 2026)

| Category | Race-pace anchor |
|---|---|
| Speed endurance | 800 RP and faster |
| Lactic capacity | 1200-2K RP |
| Aerobic power (VO2 max) | 3K-3200 RP |
| Lactate resistance (the "5K blend") | 5K RP - top-end anaerobic threshold / bottom-end aerobic power |
| Threshold - LT (= anaerobic threshold) | 10K-15K RP |
| Threshold - DT (= aerobic threshold) | HM / 20K / 25K / MP. On double days: AM (Session 1) ~2-2.5 mmol, PM (Session 2) ~2.5-3.5 mmol |

Easy runs ("Aerobic Run", "Aerobic Development Run") are **easy aerobic** - their own chip, outside the six categories.

Anchors written as letters: **AT = aerobic threshold (DT)**, **LT = anaerobic threshold**.
A double-threshold *day* (Session 1 + Session 2) only exists from 2025-26 on; the DT pace band applies to every season.

## Files

| File | What it controls |
|---|---|
| `pace_zones.csv` | Race-distance anchor (in km) -> category / band. This is the table above. |
| `pace_symbols.csv` | Letter anchors (`@ AT`, `@ LT`, `DT pace`, `Threshold Pace`) -> category / band. |
| `workout_types.csv` | Label text before the colon -> category. `pace_decides=yes` means the line's pace anchor picks the category (so "Aerobic Power @ 5K" becomes lactate resistance); `no` means the label wins. |
| `line_overrides.csv` | Fix one day by hand. `season,tab,block,line,workout_type,band,note` - e.g. `2526,4-21,1,4,threshold,DT,confirmed by Patrick: AT = aerobic threshold`. Leave `line` blank to set a whole unlabeled block. Wins over everything else. |
| `inference.csv` | Cutoffs (target pace as % of mile fitness) used to guess the category of unlabeled blocks. |
| `tab_fixes.csv` | Whole-tab decisions. `season,tab,action,from_tab,note`. `skip` leaves a tab out (a blank plan copy, or the same session saved in two workbooks). `copied_splits` + `from_tab`: athletes whose splits match their splits on `from_tab` rep for rep were carried over when the tab was copied, so those splits count as not recorded. A row that matches nothing shows up in review as `rule_unused`. |
| `cell_fixes.csv` | One-off corrections to a single cell. `season,tab,cell,raw_value,corrected_value,note` - e.g. `2425,9-18,O53,0;68,1:08,...`. Applied only while the cell still reads `raw_value` (otherwise it's reported as `rule_unused` and skipped). The workbook itself is never changed. Use this instead of a general parser rule when a typo could mean something else elsewhere. |

`status=proposed` / `default` marks a call Claude made that Patrick hasn't confirmed.
After each run, `data/build/review.csv` lists what still needs a decision, including
`at_looks_anaerobic` (lines written "@ AT" whose target paces look like LT work), with
the exact override row to paste if Patrick agrees.

## Rules that name athletes

These live in `data/rules/` (git-ignored) so no student names end up in the repo:

- `data/rules/aliases.csv` - workbook spelling/nickname -> Athletic.net athlete ID. Add a row whenever the review file reports an unmatched name.
- `data/rules/athletes.csv` - per-athlete, per-season notes (e.g. alumni training with the group). `status=exclude` leaves that athlete out of the build.
- `data/rules/strava_aliases.csv` - Strava display name -> Athletic.net athlete ID, for the weekly mileage exports.
