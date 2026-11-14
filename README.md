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
  strava/      Strava activity exports (one row per activity, one or more files per athlete)
  rules/       aliases.csv, athletes.csv, strava_aliases.csv, strava_fixes.csv
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
group-timed splits), the athletes list and athlete pages, and workouts by type (every session of a
category/band, newest first). The Varsity / All athletes toggle belongs to the team calendar (and its
day and workout views; it's hidden on the athlete pages); varsity = ran a varsity-level
cross-country race that season (see `app/server.py`). Inferred types are dashed, italic and
labelled "inferred".

**Athletes** (top bar, between Calendar and Workouts): **Current** by default - everyone on a
workout sheet in the newest workbook's season, or who raced for MV in that season (July-June) -
and **Historical** for everyone else. The search box searches both lists (each tab shows how many
match). Click a name for the athlete page; a name anywhere else (day view, races, workouts) links
to it too.

Athlete page:
- **Calendar** (default): the same month grid, for one season (picker), with only the days
  the athlete is on a workout sheet, raced, or recorded a Strava run or cross-training - compact,
  so a month fits on a laptop screen without scrolling. Each day is tinted by its main workout:
  the category (same colors as the chips) with the most rep distance that day, easy aerobic only
  when there's nothing else - solid when it comes from your label or `rules/line_overrides.csv`,
  striped and lighter when it was inferred from pace. The Week column at the end of each row is
  that Monday-Sunday week's Strava run miles (blank: no data; * partial week). Chips are the types
  of their own reps; race days show meet, event and mark; 2× T means they did both sessions of a
  double day. Strava runs show distance · pace per mile (a busy day: the number of runs and their
  total, then the biggest); the week's longest run reads "Long run" when it is at least
  `long_run_min_miles` in `rules/mileage.csv` (0 = every week's longest run; blank = none, and the
  longest is just bold). Clicking a day opens the day view with
  their row highlighted (shown even under the varsity filter) and that day's Strava activities
  listed; prev / next there step through their days.
- **History**: the timeline - sessions with every split, races, filters by season and type.
- **Mileage**: run miles across every season with data, **Weekly** (Monday-Sunday) or **Monthly**
  (calendar months) - a toggle above the charts. From the athlete's Strava activities
  (`data/strava/`), or the weekly export (`data/mileage/`) for weeks without them;
  `rules/mileage.csv` picks when a week is in both, and the other number is kept in the hover and
  the table. In the monthly view each run counts in the month it was run; a weekly-export week
  (a weekly total only) is spread evenly over its days, which the hover says. Runs only;
  cross-training (elliptical, rides, strength ...) is a second chart on the same periods, in hours,
  never added to the run miles (a note instead when there's none). Season starts (1 July) are
  marked; partial weeks and months are lighter, and the month in progress is labelled "so far"; a
  dot marks weeks that also had runs with no distance (time only - they add no miles); periods with
  no data are shaded and left empty - never filled from workbook volume. An athlete with no
  Strava data gets a note, not an empty chart. Hover (or focus the chart and use the arrow keys)
  for each bar; per-season totals and every week / month are in tables below.
- **Races**: track - one panel per event (800 / 1600 / 3200) on a shared time axis, faster
  higher, dots filled where the race was a PR when it was run, the lifetime PR labelled, hover for
  meet / mark / place. Cross country - every course the athlete ran more than once (two results
  with a time - a DNS doesn't count; same meet *and* distance, unless `rules/xc_courses.csv` makes
  a meet's distances one course, e.g. Lowell / San Francisco Invitational: raw times, each race's
  distance in the hover and the table), with a small chart on a time axis and each year's time,
  and every XC race as a plain list. Where the athlete has Strava data, their weekly run miles are
  drawn as faded bars behind the race marks (no second axis: the tallest bar is their biggest
  week, named in the key; the hover gives the race week's miles and the four weeks before). Times
  only: nothing converts between events or courses (no mile equivalents).

`python -m app --db other/training.sqlite --port 5051` runs it on another build (e.g. a test).

Leaving athletes out: add a row to `data/rules/athletes.csv` with `status=exclude` (by Athletic.net
ID, or by workbook name for an athlete with no match; a blank season = every season) and re-run.
Their workouts, races and Strava weeks drop out of the build and the app; the workbooks and race
files are untouched, and nobody else's rows change. Details in `rules/README.md`.

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

## Strava activities (`data/strava/`)

Every `.csv` in `data/strava/` with one row per activity and the columns `athlete` (Strava
display name), `date`, `sport`, `title`, `distance`, `unit`, `moving_time`, `moving_time_sec`,
`pace_strava`, `pace_calc_min_per_mi`, `elev_gain`, `activity_id`, `activity_url`. Any other file
there is skipped and listed in review.csv (weekly exports belong in `data/mileage/`). A file name
ending `_<from>_to_<to>.csv` (e.g. `jane_d_activities_2024-08_to_2026-11-13.csv`) says what period
the export covers, so a week it only partly covers is marked partial.

- Names are matched like the weekly exports (`data/rules/strava_aliases.csv`, then the exact
  Athletic.net name); unmatched names go to review.csv with possible matches.
- An activity in two files (same `activity_id`) is kept once, from the newest file.
- Distance becomes miles from the `unit` column (mi, km, m, yd).
- `rules/strava_sports.csv` says what each sport counts as: `run` (weekly run miles), `cross`
  (cross-training minutes) or `other` (kept, counted nowhere). A new sport counts as other until
  it's added there, and review.csv says so.
- `data/rules/strava_fixes.csv` (`activity_id,action,value,note`) corrects one activity:
  `exclude`, `sport` (count it as another sport) or `miles` (the right distance). Runs with an
  odd pace (faster than 4:00/mi or slower than 20:00/mi over a mile or more) or over 20 miles are
  counted as recorded and listed in review.csv (`strava_check`) so they can be fixed there.
- Each athlete-week with any activity gets run miles, the number of runs, runs with no distance
  and their minutes, cross-training minutes by sport, and the week's longest run (also flagged on
  the activity, `longest_of_week`). A week with no activity has no row - it's no data, not 0.
- Long run: the week's longest run when it is at least `long_run_min_miles` miles
  (`rules/mileage.csv`; 0 = every week's longest run, as now; blank = none marked) - `long_run` on
  the activity and the week.
- Where a week is also in the weekly export, both numbers are kept; a difference of more than a
  mile over the same days goes to review.csv (`mileage_sources_differ`).

`python -m mvtrain.build --strava /path/to/folder --out /path/to/test-build` tries another folder.

## What comes out (`data/build/`)

| Table | One row per |
|---|---|
| `days` | calendar day: sessions, double-threshold flag, workout chips, venue |
| `sheets` | workbook tab: date (from the tab name), session, venue as written, weather, date conflicts; `venue_name` / `venue_kind` / `venue_source` - the session's venue as the app shows it (`rules/venues.csv`: written on the sheet, or inferred from laps / track distances) |
| `blocks` | group within a sheet: rep distances, targets, rest, label lines |
| `prescriptions` | label line ("Threshold Intervals: 5 x Lap @ 20k RP"): type, anchor, band |
| `athlete_blocks` | athlete in a block: Athletic.net ID, cooldown, total volume, notes |
| `reps` | athlete x rep: distance, target, actual, status, type/band, shared-split flag; `type_note` when an inferred type was changed by a rule (the church-loop cap in `rules/venues.csv`) |
| `races` | Athletic.net result. XC results also carry `xc_meet` (the meet across years) and `xc_course` (meet + distance: the same course - or, from `rules/xc_courses.csv`, one course across distances, named in `xc_course_name`); `duplicate_of` marks a result Athletic.net posted twice |
| `weekly_mileage` | athlete x week from the weekly exports (`data/mileage/`): miles, partial-week flag |
| `activities` | Strava activity: date, sport, what it counts as (run / cross / other / excluded), miles, moving time, pace, longest run of its week, any fix or check |
| `athlete_weeks` | athlete x week the Mileage tab shows: run miles and their source (activities / export, both kept), runs, runs with no distance, cross-training minutes, longest run, partial-week flag, the days the data covers (`covered_from` / `covered_to`) |
| `athlete_months` | athlete x calendar month, from `athlete_weeks`: run miles (each run on its own date; a weekly-export week spread evenly over its days - `export_days`), runs, cross-training minutes, days with data, `partial` + `month_note` when the data doesn't cover the whole month |
| `review` | anything the parser couldn't settle - read this after each run |

## Rules that came from Patrick

- The **tab name is the date**; the date written in the sheet is only checked against it.
- **Church loop** = 1725 m. Venue "Church" or any 1725 m rep means the church loop.
- **Venue per session** (`rules/venues.csv`): the venue written on the sheet; with none, 1725 m laps =
  the church loop, track distances = the track (inferred, "track?").
- **At the church loop nothing is lactate resistance**: a type *inferred* faster than threshold / LT
  becomes threshold / LT there (`rules/venues.csv`; the rep's `type_note` says so). Your own labels
  are never changed - one that comes out lactate resistance or faster is listed in review.csv
  (`venue_cap_label`) with the override row to paste if you want to change it.
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
