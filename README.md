# MV Training

Monte Vista distance training history: every workout from Patrick's season
workbooks, joined to Athletic.net race results, built into one database that
the calendar app (coming later) reads.

## Layout

```
mvtrain/     the parser (Python)
rules/       editable decisions: workout types, pace bands, inference cutoffs
             (rules that name athletes - aliases - live in data/rules/, git-ignored)
data/        NOT in git - workbooks, race CSVs, and the built database
  workbooks/   MV_Workouts.YYZZ.xlsx (one per season, e.g. 2526 = 2025-26)
  races/       Athletic.net exports (MV_XC_*.csv, MV_TF_*.csv)
  mileage/     weekly mileage exports from the Strava group (weeks x athletes)
  rules/       aliases.csv, athletes.csv, strava_aliases.csv
  build/       output: training.sqlite + CSVs (regenerated every run)
```

## Run it

```
pip install -r requirements.txt
python -m mvtrain.build                 # all seasons
python -m mvtrain.build --seasons 2526  # one season
```

Updating during a season: save the current workbook into `data/workbooks/`
(same file name pattern), drop any new Athletic.net exports into `data/races/`,
and re-run. The build is from scratch every time and never edits the workbooks.

## What comes out (`data/build/`)

| Table | One row per |
|---|---|
| `days` | calendar day: sessions, double-threshold flag, workout chips, venue |
| `sheets` | workbook tab: date (from the tab name), session, venue, weather, date conflicts |
| `blocks` | group within a sheet: rep distances, targets, rest, label lines |
| `prescriptions` | label line ("Threshold Intervals: 5 x Lap @ 20k RP"): type, anchor, band |
| `athlete_blocks` | athlete in a block: Athletic.net ID, cooldown, total volume, notes |
| `reps` | athlete x rep: distance, target, actual, status, type/band, shared-split flag |
| `races` | Athletic.net result |
| `weekly_mileage` | athlete x week (Strava): miles, partial-week flag |
| `review` | anything the parser couldn't settle - read this after each run |

## Rules that came from Patrick

- The **tab name is the date**; the date written in the sheet is only checked against it.
- **Church loop** = 1725 m. Venue "Church" or any 1725 m rep means the church loop.
- **Double threshold day** = a 2025-26+ day with Session 1 and Session 2.
- Categories and pace bands (speed endurance / lactic capacity / aerobic power /
  lactate resistance / LT / DT) are in `rules/README.md`. Any single day can be
  corrected in `rules/line_overrides.csv`.

## Not parsed yet

- **Lactate readings** stay in the workbooks but are deliberately not parsed yet -
  they're a separate project for later. They're in the 2025-26 `Lactate` tab (skipped
  as a non-date tab) and inline in 2026-27 tab `7-9` (a col-A row that is just
  "Lactate:", which the parser ignores - not a label, block or review item).
  For when that project starts: the church loop is 1725 m; the 1710 in the
  `Lactate` tab is a typo.
