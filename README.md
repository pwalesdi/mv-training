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
  mileage/     weekly mileage exports from Strava (wide weeks x athletes, or one row per athlete-week)
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
group-timed splits), athlete page, and workouts by type (every session of a category/band,
newest first). The Varsity / All athletes toggle applies to the team views; varsity = ran a
varsity-level cross-country race that season (see `app/server.py`). Inferred types are
dashed, italic and labelled "inferred".

Athlete page (open it from the athlete list or by clicking a name anywhere - day view, races,
workouts):
- **Calendar** (default): the same month grid, for one season (picker), with only the days
  the athlete is on a workout sheet or raced. Chips are the types of their own reps; race days
  show meet, event and mark; 2× T means they did both sessions of a double day. Clicking a day
  opens the day view with their row highlighted (shown even under the varsity filter), and
  prev / next there step through their days.
- **History**: the timeline - sessions with every split, races, filters by season and type.
- **Mileage**: Strava weekly miles (Monday-Sunday) across every season in `data/mileage/`.
  Season starts (1 July) are marked, partial weeks are lighter, and weeks with no data are
  shaded and left empty - never filled from workbook volume. Hover (or focus the chart and use
  the arrow keys) for each week; per-season totals and every week are in tables below.

`python -m app --db other/training.sqlite --port 5051` runs it on another build (e.g. a test).

Updating during a season: save the current workbook into `data/workbooks/`
(same file name pattern), drop any new Athletic.net exports into `data/races/`,
and re-run. The build is from scratch every time and never edits the workbooks.

## Weekly mileage files (`data/mileage/`)

Every `.csv` in `data/mileage/` is read. When two files have the same athlete-week, the newer
file (by modified time) wins. Two layouts work; the build tells them apart by the header row.

- **Wide** (what the Strava group pull makes now): one row per Monday-Sunday week, one column
  per athlete (Strava display name).
  ```
  week_start_mon,week_end_sun,Jane D,Alex Smith
  2026-08-03,2026-08-09,44.42,38.10
  2026-11-09,2026-11-15 (in progress, thru Nov 9),5.50,0.00
  ```
  A note in parentheses after a date marks a partial week. Rows that don't start with a date
  (the averages / peak / totals footer) are skipped. A column headed `note` is a note for the
  week (kept, shown on hover), not an athlete; its text doesn't mark the week partial.
- **Long**: one row per athlete-week.
  ```
  name,week_start,miles,partial_week,note
  Jane D,2026-08-03,44.42,,
  Jane D,2026-11-09,5.5,1,in progress
  ```
  Required: `name` (or `athlete` / `strava_name`), `week_start` (or `week` / `week_of`),
  `miles` (or `mi`). Optional: `week_end` (default week_start + 6 days), `partial_week`
  (1 / yes / true), `note`. A note in parentheses after a date also marks the week partial.

Dates are YYYY-MM-DD (M/D/YYYY also works). Weeks should start on a Monday; other start days
are kept and listed in review.csv, as are rows listed twice and files whose header isn't
recognised. Names are matched through `data/rules/strava_aliases.csv`, then by exact
Athletic.net name. A name that doesn't match goes to review.csv (`unmatched_strava_name`) with
possible matches (same first name, last name starting with the Strava name's last word, raced
within a season of those weeks); nothing is matched automatically - add a row to
`strava_aliases.csv` to accept one. Mileage is joined to athletes by Athletic.net ID, so an
athlete without one (most of 2019-20) can't show mileage.

To try a folder of exports without touching `data/mileage/`:
`python -m mvtrain.build --mileage /path/to/folder --out /path/to/test-build`.

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
