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
| `line_overrides.csv` | Fix one day by hand. `season,tab,block,line,reps,workout_type,band,note` - e.g. `2526,4-21,1,4,,threshold,DT,confirmed by Patrick: AT = aerobic threshold` sets label line 4 of group 1. Leave `line` blank to set a whole unlabeled block. Put rep numbers in `reps` (`1-6`, or a list: `1 3 5 7`; 1 = the group's first rep column) to set just those reps, labeled group or not - e.g. `2627,9-24 (2),3,,1-6,threshold,LT,...` for the 1000s and 600s and `...,7-9,speed_endurance,,...` for the 300s. Wins over everything else; those reps get `type_source=override`. A row that matches nothing (or names reps the group doesn't have) shows up in review as `rule_unused`. |
| `inference.csv` | Cutoffs (target pace as % of mile fitness) used to guess the category of unlabeled blocks, and of reps in labeled blocks that no label line mentions (e.g. a finishing 1600). Both are marked `type_source=inferred`. |
| `xc_meets.csv` | Cross-country meets whose Athletic.net name changes between years in ways the build can't strip itself. `contains,meet,note`: any XC meet name containing `contains` (any case) is treated as `meet`. The build already drops a leading year, an edition number ("43rd Annual"), an "(AKA ...)" and Championship/Championships. Results are compared across years only within the same meet **and** distance (`xc_course` in races.csv), unless `xc_courses.csv` says otherwise. |
| `venues.csv` | Venue per session (workbook tab) and what can be inferred there. `venue,written_contains,inferred_from,shown_inferred,max_inferred_type,max_inferred_band,note`. A venue written on the sheet is used as written (a time like "6:30 pm" dropped); its kind is the row whose `written_contains` words (`;`-separated, any case) it contains - both church and track ("Church & Track") means lap reps at the church loop, the rest at the track; no match = other (SVP, MVHS ...). With nothing written, rows are tried in order by `inferred_from`: `laps` (1725 m laps or whole laps), `track_distances` (100-3200 m in 100s, 150 m, the mile); `shown_inferred` is what the app shows ("track?"). `max_inferred_type` / `max_inferred_band`: the fastest type a rep there may be *inferred* as, by the speed order in `pace_zones.csv` - church: threshold / LT, so nothing inferred at the church loop is lactate resistance. Labels and overrides are never changed: a label line there that comes out faster goes to review as `venue_cap_label`. |
| `xc_courses.csv` | One cross-country course whose distance changed between years. `meet,distance,course,note`: results of `meet` (as the app names it, e.g. `Lowell Invitational`) run at `distance` (`2.78 mi`, `5000 m`) are the course `course` - one row per distance. Those results share one course card and chart: raw times, with each race's actual distance in the hover and the table. A row that matches no result goes to review as `rule_unused`. |
| `strava_sports.csv` | What each Strava sport counts as: `run` (weekly run miles), `cross` (cross-training, counted in minutes and never added to run miles) or `other` (kept, counted nowhere). `sport,counts_as,note`. A sport missing here counts as other and is listed in review.csv. |
| `mileage.csv` | `setting,value,note`. `weekly_source_when_both`: when an athlete-week has both Strava activities and a weekly export, which run miles the app shows (`activities` or `export`); the other number is kept beside it. `long_run_min_miles`: a long run is the week's longest run if it is at least this many miles - 0 (now) = every week's longest run; blank = no long runs marked (the week's longest run is still flagged as a candidate). |
| `tab_fixes.csv` | Whole-tab decisions. `season,tab,action,from_tab,note`. `skip` leaves a tab out (a blank plan copy, or the same session saved in two workbooks). `copied_splits` + `from_tab`: athletes whose splits match their splits on `from_tab` rep for rep were carried over when the tab was copied, so those splits count as not recorded. A row that matches nothing shows up in review as `rule_unused`. |
| `cell_fixes.csv` | One-off corrections to a single cell. `season,tab,cell,raw_value,corrected_value,note` - e.g. `2425,9-18,O53,0;68,1:08,...`. Applied only while the cell still reads `raw_value` (otherwise it's reported as `rule_unused` and skipped). The workbook itself is never changed. Use this instead of a general parser rule when a typo could mean something else elsewhere. |

`status=proposed` / `default` marks a call Claude made that Patrick hasn't confirmed.
After each run, `data/build/review.csv` lists what still needs a decision, including
`at_looks_anaerobic` (lines written "@ AT" whose target paces look like LT work), with
the exact override row to paste if Patrick agrees.

## Rules that name athletes

These live in `data/rules/` (git-ignored) so no student names end up in the repo:

- `data/rules/aliases.csv` - workbook spelling/nickname -> Athletic.net athlete ID. Add a row whenever the review file reports an unmatched name. For an athlete with no Athletic.net match, leave `athlete_id` blank to merge one workbook spelling into another: `athletic_net_name` is then the name shown, and the athlete stays unmatched.
- `data/rules/athletes.csv` - `workbook_name, athlete_id, season, status, note`: per-athlete notes (e.g. `alum`) and exclusions. A row with an `athlete_id` covers that Athletic.net athlete - every workbook spelling, their races and their Strava weeks; without one it covers a workbook name (as written, or as shown after aliases.csv). A blank `season` means every season. `status=exclude` leaves the athlete out: their athlete-block rows, reps, races and Strava weeks aren't written, and side notes on other athletes' rows that name them are dropped. Everything about the group is still worked out with them in it (its fitness for inferred types, group-timed splits), so excluding someone never changes anyone else's rows. A left-out name in a label line, sheet title or rep cell is not edited - it goes to review as `left_out_name_in_text`. A row that matches nothing goes to review as `rule_unused`.
- `data/rules/strava_aliases.csv` - Strava display name -> Athletic.net athlete ID, for the weekly mileage exports and the activity exports.
- `data/rules/strava_fixes.csv` - `activity_id,action,value,note`: corrects one Strava activity - `exclude` (left out of every total), `sport` (count it as that sport), `miles` (the right distance).
