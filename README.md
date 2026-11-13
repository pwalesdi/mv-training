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

## Calendar prototype (read-only)

```
.venv/bin/python -m app            # then open http://127.0.0.1:5050  (--port to change)
```

Reads `data/build/training.sqlite` (run the build first) and picks up a rebuild without a
restart. It only ever binds to 127.0.0.1 - the data is student data. Views: team calendar
(default; season picker, month grid, chips, double-threshold / church-loop / race markers),
day view (every session and group with your label text, athletes x reps, targets, rest,
group-timed splits), athlete view (history timeline with races and weekly mileage), and
workouts by type (every session of a category/band, newest first). The Varsity / All
athletes toggle applies everywhere; varsity = ran a varsity-level cross-country race that
season (see `app/server.py`). Inferred types are dashed, italic and labelled "inferred".

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
- A rep belongs to a label line that states its distance, within ~2% (2.0 miles = the
  3200 column). A continuous line ("2.0 miles continuous", "3 Lap Threshold Run",
  "Threshold Run: 6 Laps") owns all of its split columns. A line covers as many columns
  as it counts ("6x400m" = 6, "3x(2x200m)" = 6, "1 x 2lap" = 2 lap columns); once it
  has them, the next line that states that distance takes over.
- A rep no label line mentions is typed from its target pace, marked `inferred`.
  Inference is per rep, from the block's median mile fitness, so every athlete gets
  the same type for the same rep. With no target or no race marks it takes the type
  of the line above, still marked `inferred`.
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
